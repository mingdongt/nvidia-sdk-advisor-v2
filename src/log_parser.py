"""SDK Manager install log parser.

Scans a .log file or .tar.gz of logs, matches the LAST few hundred lines
against `data/log_patterns.yaml`, extracts header context, returns LogDiagnosis.
"""
from __future__ import annotations

import re
import tarfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from src.models import LogDiagnosis

_PATTERNS_PATH = Path(__file__).resolve().parents[1] / "data" / "log_patterns.yaml"
_TAIL_LINES = 300       # how many lines from the end to scan
_EXCERPT_CONTEXT = 5    # lines before/after match in raw_excerpt


@lru_cache(maxsize=1)
def _load_patterns() -> dict:
    # explicit utf-8 — Windows default GBK chokes on unicode chars in the YAML
    return yaml.safe_load(_PATTERNS_PATH.read_text(encoding="utf-8"))


def _read_log_text(log_path_or_archive: str) -> str:
    """Read a single .log file OR concatenate .log files from a .tar.gz archive."""
    p = Path(log_path_or_archive)
    if not p.exists():
        return ""
    if p.suffix in (".gz", ".tgz") or ".tar." in p.name:
        chunks = []
        try:
            with tarfile.open(p, "r:*") as tf:
                for member in tf.getmembers():
                    if member.name.endswith(".log") or member.name.endswith(".txt"):
                        f = tf.extractfile(member)
                        if f:
                            chunks.append(f.read().decode("utf-8", errors="replace"))
        except tarfile.TarError:
            return ""
        return "\n".join(chunks)
    return p.read_text(encoding="utf-8", errors="replace")


def _extract_header(text: str) -> dict:
    """Pull target/host_os/jetpack_version/timestamp from log header."""
    out: dict[str, Optional[str]] = {
        "target": None, "host_os": None, "jetpack_version": None, "timestamp": None,
    }
    head = "\n".join(text.splitlines()[:50])
    for hp in _load_patterns().get("header_patterns", []):
        m = re.search(hp["regex"], head)
        if m:
            out[hp["field"]] = m.group(1)
    return out


def _make_excerpt(lines: list[str], match_index: int) -> str:
    """±_EXCERPT_CONTEXT lines around match_index."""
    lo = max(0, match_index - _EXCERPT_CONTEXT)
    hi = min(len(lines), match_index + _EXCERPT_CONTEXT + 1)
    return "\n".join(lines[lo:hi])


def _find_last_success(lines: list[str], failure_index: int) -> Optional[str]:
    """Scan backward from failure to find the most recent 'OK' / 'completed' / 'succeeded' line."""
    pattern = re.compile(r"(?i)\b(ok|completed|succeeded|complete)\b")
    for i in range(failure_index - 1, max(0, failure_index - 100), -1):
        if pattern.search(lines[i]):
            return lines[i].strip()[:120]
    return None


def parse_install_log(log_path_or_archive: str) -> LogDiagnosis:
    """Top-level: read log, scan from end backward, return LogDiagnosis."""
    text = _read_log_text(log_path_or_archive)
    if not text:
        return LogDiagnosis(
            failed_stage="unknown", error_signature="", error_class="log-not-readable",
            raw_excerpt="(log file empty or unreadable)",
        )

    lines = text.splitlines()
    header = _extract_header(text)

    # Scan the tail (failures typically near the end).
    # Iterate forward through the tail; later, more-specific patterns override the
    # generic ones at the bottom of the patterns file.
    tail_start = max(0, len(lines) - _TAIL_LINES)
    patterns = _load_patterns().get("patterns", [])

    last_winner = None  # (line_index, line, pattern)
    for i in range(tail_start, len(lines)):
        line = lines[i]
        for p in patterns:
            if re.search(p["regex"], line):
                # Skip the generic catch-all if a more specific pattern matched this line
                # Specific patterns appear EARLIER in the list. The first match wins for THIS line.
                last_winner = (i, line, p)
                break  # don't let other patterns also match this same line

    if last_winner is None:
        return LogDiagnosis(
            failed_stage="unknown", error_signature="", error_class="no-pattern-matched",
            target=header["target"], host_os=header["host_os"],
            jetpack_version=header["jetpack_version"], timestamp=header["timestamp"],
            raw_excerpt="\n".join(lines[-20:]),
        )

    idx, sig_line, pat = last_winner
    return LogDiagnosis(
        failed_stage=pat["stage"],
        error_signature=sig_line.strip(),
        error_class=pat["error_class"],
        target=header["target"],
        host_os=header["host_os"],
        jetpack_version=header["jetpack_version"],
        timestamp=header["timestamp"],
        last_successful_step=_find_last_success(lines, idx),
        raw_excerpt=_make_excerpt(lines, idx),
        search_terms=pat.get("search_terms", []),
    )
