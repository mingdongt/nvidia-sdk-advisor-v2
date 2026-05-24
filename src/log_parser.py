"""SDK Manager install log structural reader.

Reads a .zip (the real SDK Manager export format), .tar.gz (legacy / manually
packaged), or a single .log file. Extracts metadata from the filename and the
tail of the log content. Returns a LogExcerpt.

Deliberately does NOT classify errors, assign stages, or pre-curate search
terms. That is the agent's job: it reads tail_text directly and uses web
search to identify the actual failure and find expert fixes. This module's
only job is correct, deterministic ingestion.

Format notes (verified against real SDK Manager exports posted on
forums.developer.nvidia.com):
  - Archive is .zip. Filename pattern:
      SDKM_logs_JetPack_<ver>_<host>_for_Jetson_<board>_<date>_<time>.zip
    e.g. SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip
    All metadata we need (target, JetPack version, host OS, timestamp) is in
    the filename — more reliable than scanning log content for headers.
  - Inside the archive there are multiple .log files. We concatenate all of
    them and take the tail. We do not parse the internal structure (the
    archive layout is not publicly documented; the agent reads what's there).
"""
from __future__ import annotations

import re
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

from src.models import LogExcerpt

_TAIL_LINES = 200

# Real SDK Manager export filename pattern.
_FILENAME_RE = re.compile(
    r"SDKM_logs_JetPack_(?P<jp>[\d.]+)_"
    r"(?P<host>Linux|Windows|Ubuntu\S*)_"
    r"for_Jetson_(?P<board>[A-Za-z0-9_]+?)_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<time>\d{2}-\d{2}-\d{2})"
    r"\.(?:zip|tar\.gz|tgz)$",
    re.IGNORECASE,
)

# Filename board fragment ('AGX_Orin_64GB') -> canonical target id.
_FILENAME_BOARD_MAP = {
    "agx_orin": "JETSON_AGX_ORIN_TARGETS",
    "orin_nx": "JETSON_ORIN_NX_TARGETS",
    "orin_nano": "JETSON_ORIN_NANO_TARGETS",
    "agx_xavier": "JETSON_AGX_XAVIER_TARGETS",
    "xavier_nx": "JETSON_XAVIER_NX_TARGETS",
    "agx_thor": "JETSON_AGX_THOR_TARGETS",
    "nano": "JETSON_NANO_TARGETS",
    "tx2": "JETSON_TX2_TARGETS",
    "tx1": "JETSON_TX1_TARGETS",
}


def _board_from_filename(fragment: str) -> Optional[str]:
    """Map filename board fragment ('AGX_Orin_64GB') to canonical target id."""
    f = fragment.lower()
    f = re.sub(r"_\d+gb$", "", f)
    for key, target_id in _FILENAME_BOARD_MAP.items():
        if key in f:
            return target_id
    return None


def _parse_filename(path: Path) -> dict:
    """Extract structured metadata from an SDK Manager export filename.
    Returns dict with target / host_os / jetpack_version / timestamp.
    Any field may be None if the filename does not match the pattern.
    """
    out = {"target": None, "host_os": None, "jetpack_version": None, "timestamp": None}
    m = _FILENAME_RE.search(path.name)
    if not m:
        return out
    out["jetpack_version"] = m.group("jp")
    out["host_os"] = m.group("host").lower()
    out["target"] = _board_from_filename(m.group("board"))
    out["timestamp"] = f"{m.group('date')} {m.group('time').replace('-', ':')}"
    return out


def _read_archive_contents(path: Path) -> list[str]:
    """Return list of text chunks from the archive, one per .log/.txt file."""
    chunks: list[str] = []

    if path.suffix.lower() == ".zip" or (path.is_file() and zipfile.is_zipfile(path)):
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.filename.lower().endswith((".log", ".txt")):
                        with zf.open(info) as f:
                            chunks.append(f.read().decode("utf-8", errors="replace"))
        except zipfile.BadZipFile:
            return []
        return chunks

    if path.suffix.lower() in (".gz", ".tgz") or ".tar." in path.name.lower():
        try:
            with tarfile.open(path, "r:*") as tf:
                for member in tf.getmembers():
                    if member.name.lower().endswith((".log", ".txt")):
                        f = tf.extractfile(member)
                        if f:
                            chunks.append(f.read().decode("utf-8", errors="replace"))
        except tarfile.TarError:
            return []
        return chunks

    # Single text file
    try:
        return [path.read_text(encoding="utf-8", errors="replace")]
    except (OSError, UnicodeDecodeError):
        return []


def _tail(text: str, n_lines: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n_lines:])


def parse_install_log(log_path_or_archive: str) -> LogExcerpt:
    """Read an SDK Manager log archive (.zip / .tar.gz) or single .log file.

    Returns a LogExcerpt with:
      - target / host_os / jetpack_version / timestamp parsed from filename
      - tail_text: last ~200 lines of concatenated log content
      - file_count / total_size_bytes: how much was read

    Does NOT classify errors. The agent reads tail_text and decides.
    """
    path = Path(log_path_or_archive)
    if not path.exists():
        return LogExcerpt(source_path=str(path))

    chunks = _read_archive_contents(path)
    full_text = "\n".join(chunks)

    meta = _parse_filename(path)
    return LogExcerpt(
        target=meta["target"],
        host_os=meta["host_os"],
        jetpack_version=meta["jetpack_version"],
        timestamp=meta["timestamp"],
        tail_text=_tail(full_text, _TAIL_LINES),
        file_count=len(chunks),
        total_size_bytes=sum(len(c.encode("utf-8")) for c in chunks),
        source_path=str(path),
    )
