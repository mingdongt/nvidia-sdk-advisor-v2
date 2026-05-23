"""Generate and validate SDK Manager response (.ini) files.

Format matches NVIDIA's official template:
  responsefiles/Linux/sdkm_responsefile_sample_jetson.ini
which has three sections: [client_arguments], [pre-flash-settings], [post-flash-settings]
and array syntax for repeated keys (additional-sdk[]).
"""
import configparser
import re
from pathlib import Path
from src.models import InstallConfig


def generate_response_file(cfg: InstallConfig) -> str:
    lines = ["[client_arguments]"]
    lines.append(f"action = {cfg.action}")
    lines.append(f"login-type = {cfg.login_type}")
    lines.append(f"product = {cfg.product}")
    lines.append(f"version = {cfg.version}")
    lines.append(f"target-os = {cfg.target_os}")
    lines.append(f"host = {'true' if cfg.host else 'false'}")
    lines.append(f"target = {cfg.target}")
    lines.append(f"flash = {'true' if cfg.flash else 'false'}")
    for sdk in cfg.additional_sdks:
        lines.append(f"additional-sdk[] = {sdk}")
    lines.append("")

    lines.append("[pre-flash-settings]")
    lines.append("recovery = manual")
    lines.append("")

    lines.append("[post-flash-settings]")
    lines.append("post-flash = install")
    lines.append("ip-type = ipv4")
    lines.append("ip = 192.168.55.1")
    lines.append("user = device_username")
    lines.append("password = device_password")
    lines.append("retries = 2")
    lines.append("")

    return "\n".join(lines)


# Matches a key in both active lines ("key = val") and commented-out lines ("; key = val" or ";; key = val")
# Section header pattern
_SECTION_RE = re.compile(r"^\[([^\]]+)\]")
# Key pattern: optional leading semicolons + whitespace, then key name (may end with [])
_KEY_RE = re.compile(r"^;*\s*([A-Za-z][A-Za-z0-9\-_]*(?:\[\])?)\s*=")


def _parse_template_keys(template_path: Path) -> dict[str, set[str]]:
    """Return {section: {keys}} for a template.

    Extracts key names from both active lines and commented-out lines so that
    optional array keys like ``additional-sdk[]`` (which NVIDIA ships commented
    out) are still recognised as valid.  Array suffix ``[]`` is stripped so
    ``additional-sdk[]`` and ``additional-sdk`` both normalise to
    ``additional-sdk``.
    """
    out: dict[str, set[str]] = {}
    current_section: str | None = None

    with template_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()

            # Section header (must not start with ;)
            sec_m = _SECTION_RE.match(line)
            if sec_m and not line.startswith(";"):
                current_section = sec_m.group(1)
                out.setdefault(current_section, set())
                continue

            if current_section is None:
                continue

            key_m = _KEY_RE.match(line)
            if key_m:
                raw_key = key_m.group(1)
                # Normalise array syntax: additional-sdk[] -> additional-sdk
                key = raw_key.rstrip("[]") if raw_key.endswith("[]") else raw_key
                out[current_section].add(key)

    return out


def validate_against_official_sample(generated_ini: str, product: str, template_dir: Path) -> dict:
    """Compare structure of generated INI against NVIDIA's sample for the product."""
    template_path = template_dir / f"{product.lower()}_linux.ini"
    if not template_path.exists():
        return {
            "matches": False,
            "reason": f"no template at {template_path}",
            "missing_sections": [],
            "extra_keys": [],
        }

    template = _parse_template_keys(template_path)

    parser = configparser.ConfigParser(strict=False, allow_no_value=True)
    parser.read_string(generated_ini)
    generated: dict[str, set[str]] = {}
    for section in parser.sections():
        keys: set[str] = set()
        for k in parser.options(section):
            keys.add(k.rstrip("[]") if k.endswith("[]") else k)
        generated[section] = keys

    extra_keys: list[str] = []
    missing_sections: list[str] = []
    for section, keys in generated.items():
        if section not in template:
            missing_sections.append(section)
            continue
        for k in keys:
            if k not in template[section]:
                extra_keys.append(f"{section}.{k}")

    return {
        "matches": not extra_keys and not missing_sections,
        "extra_keys": extra_keys,
        "missing_sections": missing_sections,
        "template": str(template_path.name),
    }
