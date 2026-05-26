"""A1 — Correctness scorer (deterministic).

Replaces the regex-on-final-text approach from tests/run_smoke_eval.py with
a structured check on the generated artifacts:

  1. Extract the .ini code block and parse it as configparser-style INI.
     Required sections: [client_arguments], [pre-flash-settings], [post-flash-settings].
     A missing section is a schema violation, scored separately from
     content-level checks.

  2. Extract the sdkmanager command line and parse its --flag args.
     Fields checked: --product, --version, --target, --additional-sdk (repeated).

  3. Compare extracted (product/version/target/additional_sdks) against
     case.expected. Each field is a binary pass/fail; the overall score is
     num_correct / num_expected_fields.

This is strictly more rigorous than the legacy regex extractor — that one
would happily match `--product Jetson` even if it appeared in a markdown
explanation, not in an actual command. Here we anchor on the fenced ```bash
or ```ini code block, and we only count a field if it parses cleanly.
"""
from __future__ import annotations

import configparser
import io
import re
import shlex
from typing import Any


# ─── extraction ────────────────────────────────────────────────────────

def _extract_code_blocks(text: str) -> dict[str, list[str]]:
    """Return all fenced code blocks grouped by language tag.

    A block with no language tag goes under "".
    """
    blocks: dict[str, list[str]] = {}
    for m in re.finditer(r"```([^\n]*)\n(.*?)```", text, re.DOTALL):
        lang = m.group(1).strip().lower()
        body = m.group(2).strip()
        blocks.setdefault(lang, []).append(body)
    return blocks


def _find_ini_block(blocks: dict[str, list[str]]) -> str | None:
    """Find the INI code block. Prefer ```ini, fall back to any block that
    starts with [client_arguments]."""
    for body in blocks.get("ini", []):
        return body
    for bodies in blocks.values():
        for body in bodies:
            if body.startswith("[client_arguments]"):
                return body
    return None


def _find_command_block(blocks: dict[str, list[str]]) -> str | None:
    """Find the sdkmanager command. Prefer ```bash, fall back to any block
    that starts with 'sdkmanager'."""
    for body in blocks.get("bash", []):
        if "sdkmanager" in body[:200]:
            return body
    for body in blocks.get("sh", []):
        if "sdkmanager" in body[:200]:
            return body
    for bodies in blocks.values():
        for body in bodies:
            if body.lstrip().startswith("sdkmanager"):
                return body
    return None


# ─── INI parsing ───────────────────────────────────────────────────────

REQUIRED_SECTIONS = ("client_arguments", "pre-flash-settings", "post-flash-settings")


def _parse_ini(ini_text: str) -> tuple[configparser.ConfigParser | None, list[str]]:
    """Parse INI text; return (parser, violations).

    violations: list of human-readable issues — missing sections, parse errors.
    """
    violations: list[str] = []
    # configparser doesn't like multi-line continuation backslashes from
    # markdown rendering; clean those first.
    cleaned = ini_text.replace("\\\n", "")
    parser = configparser.ConfigParser()
    try:
        parser.read_file(io.StringIO(cleaned))
    except configparser.Error as e:
        violations.append(f"ini-parse-error: {type(e).__name__}: {e}")
        return None, violations

    present = set(parser.sections())
    for section in REQUIRED_SECTIONS:
        if section not in present:
            violations.append(f"missing-section: {section}")
    return parser, violations


def _ini_extract_fields(parser: configparser.ConfigParser | None) -> dict[str, Any]:
    """Pull product / version / target / target-os from a parsed INI."""
    if parser is None or "client_arguments" not in parser:
        return {}
    ca = parser["client_arguments"]
    return {
        "product": ca.get("product"),
        "version": ca.get("version"),
        "target": ca.get("target"),
        "target_os": ca.get("target-os"),
    }


# ─── command parsing ───────────────────────────────────────────────────

def _parse_command(cmd_text: str) -> dict[str, Any]:
    """Parse `sdkmanager --flag value --flag2 'value with space' --boolflag` into a dict.

    Uses shlex to tokenize so quoted values with spaces (e.g.
    'DeepStream 7.0') stay intact. Distinguishes boolean flags (no value
    following) from key-value flags by peeking at the next token: if it
    starts with `--`, the current flag was boolean.

    Repeated flags accumulate into a list (e.g. --additional-sdk used
    multiple times → additional_sdks: [v1, v2]).
    """
    # Collapse line continuations BEFORE shlex so the trailing `\\` doesn't
    # confuse it. We feed shlex a single line.
    flat = cmd_text.replace("\\\n", " ")
    try:
        tokens = shlex.split(flat)
    except ValueError:
        return {}

    out: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--"):
            flag = t[2:]
            has_value = i + 1 < len(tokens) and not tokens[i + 1].startswith("--")
            if has_value:
                value = tokens[i + 1]
                if flag == "additional-sdk":
                    out.setdefault("additional_sdks", []).append(value)
                else:
                    out[flag.replace("-", "_")] = value
                i += 2
            else:
                out[flag.replace("-", "_")] = True
                i += 1
        else:
            i += 1
    return out


# ─── scoring ───────────────────────────────────────────────────────────

def score_correctness(output_text: str, expected: dict) -> dict:
    """Score the agent's final text against case.expected. Returns:

        {
            "score": float (0.0-1.0),
            "ini_violations": list[str],
            "extracted": {"ini": {...}, "command": {...}},
            "field_checks": list[{"field", "want", "got", "passed"}],
        }

    score = num_passed_field_checks / num_field_checks. INI schema violations
    are reported but do not directly affect the score — they're a separate
    quality dimension (you can have correct command + broken INI, or
    matching command/INI but missing a section header).

    expected schema (one of these keys per check):
      - "product": str — exact match (case-insensitive)
      - "target":  str — exact match (case-insensitive)
      - "version_starts_with": str — prefix match
      - "additional_sdks_contains": str — substring match across all sdks
    """
    blocks = _extract_code_blocks(output_text)
    ini_text = _find_ini_block(blocks)
    cmd_text = _find_command_block(blocks)

    parser, ini_violations = (_parse_ini(ini_text) if ini_text else (None, ["missing-ini-block"]))
    ini_fields = _ini_extract_fields(parser)
    cmd_fields = _parse_command(cmd_text) if cmd_text else {}

    # Merge: command flags are authoritative since they're what the user
    # would actually run. INI is cross-checked for consistency but command
    # wins on disagreement.
    def _get(name: str) -> str | None:
        return cmd_fields.get(name) or ini_fields.get(name)

    extracted = {
        "product": _get("product"),
        "version": _get("version"),
        "target": _get("target"),
        "additional_sdks": cmd_fields.get("additional_sdks") or [],
    }

    field_checks: list[dict] = []

    if "product" in expected:
        got = (extracted.get("product") or "").lower()
        want = expected["product"].lower()
        field_checks.append({
            "field": "product", "want": expected["product"],
            "got": extracted.get("product"), "passed": got == want,
        })

    if "target" in expected:
        got = (extracted.get("target") or "").lower()
        want = expected["target"].lower()
        field_checks.append({
            "field": "target", "want": expected["target"],
            "got": extracted.get("target"), "passed": got == want,
        })

    if "version_starts_with" in expected:
        got = extracted.get("version") or ""
        want_prefix = expected["version_starts_with"]
        field_checks.append({
            "field": "version_starts_with", "want": want_prefix,
            "got": got, "passed": got.startswith(want_prefix),
        })

    if "additional_sdks_contains" in expected:
        want = expected["additional_sdks_contains"]
        sdks = extracted.get("additional_sdks") or []
        # Match if the want string appears as substring in any sdk
        found = any(want.lower() in s.lower() for s in sdks)
        field_checks.append({
            "field": "additional_sdks_contains", "want": want,
            "got": sdks, "passed": found,
        })

    passed_count = sum(1 for c in field_checks if c["passed"])
    score = round(passed_count / len(field_checks), 2) if field_checks else 0.0

    return {
        "score": score,
        "ini_violations": ini_violations,
        "extracted": extracted,
        "field_checks": field_checks,
    }
