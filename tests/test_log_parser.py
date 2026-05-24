"""Tests for the structural log reader.

The parser does NOT classify errors — it only extracts metadata from the
filename and the tail of log content. Tests verify those two responsibilities,
nothing else.
"""
import tempfile
import zipfile
from pathlib import Path

from src.log_parser import parse_install_log
from src.models import LogExcerpt

_FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_returns_log_excerpt():
    e = parse_install_log(str(_FIXTURES / "apt_missing_package.log"))
    assert isinstance(e, LogExcerpt)


def test_parse_single_log_file_populates_tail():
    e = parse_install_log(str(_FIXTURES / "apt_missing_package.log"))
    assert e.file_count == 1
    assert e.total_size_bytes > 0
    assert "Unable to locate package" in e.tail_text


def test_parse_nonexistent_returns_empty_excerpt():
    e = parse_install_log("/path/that/does/not/exist.log")
    assert isinstance(e, LogExcerpt)
    assert e.tail_text == ""
    assert e.file_count == 0


def test_parse_with_real_export_filename_extracts_metadata():
    """The SDK Manager export filename encodes target / JetPack / host / timestamp.
    Verify the parser extracts these directly from the filename."""
    with tempfile.TemporaryDirectory() as td:
        # Create a .zip with the real SDK Manager export filename pattern
        zip_path = Path(td) / "SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("install.log", "fake content\nE: Unable to locate package nvidia-jetpack\n")

        e = parse_install_log(str(zip_path))
        assert e.target == "JETSON_AGX_ORIN_TARGETS"
        assert e.jetpack_version == "6.2"
        assert e.host_os == "linux"
        assert e.timestamp == "2025-01-26 11:41:13"
        assert "Unable to locate package" in e.tail_text


def test_parse_zip_with_multiple_log_files():
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "logs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.log", "log a line 1\nlog a line 2\n")
            zf.writestr("b.log", "log b line 1\n")
            zf.writestr("not-a-log.bin", "binary blob, should be skipped")

        e = parse_install_log(str(zip_path))
        assert e.file_count == 2  # only .log files
        assert "log a line 1" in e.tail_text
        assert "log b line 1" in e.tail_text
        assert "binary blob" not in e.tail_text


def test_parse_tail_caps_at_200_lines():
    """Long logs should be truncated to ~200 lines in tail_text."""
    with tempfile.TemporaryDirectory() as td:
        log_path = Path(td) / "long.log"
        log_path.write_text("\n".join(f"line {i}" for i in range(500)) + "\n", encoding="utf-8")

        e = parse_install_log(str(log_path))
        tail_lines = e.tail_text.splitlines()
        assert len(tail_lines) <= 200
        # Tail should be the LAST 200 lines, so line 499 must be present, line 0 absent
        assert any("line 499" in ln for ln in tail_lines)
        assert not any(ln == "line 0" for ln in tail_lines)


def test_parse_filename_without_pattern_leaves_metadata_none():
    """If the filename doesn't match the SDK Manager export pattern, the
    metadata fields should be None — we don't guess."""
    e = parse_install_log(str(_FIXTURES / "apt_missing_package.log"))
    # apt_missing_package.log is not in the SDK Manager export naming convention,
    # so filename-derived fields stay None (the in-file content header was the
    # old convention, deliberately not parsed any more).
    assert e.target is None
    assert e.jetpack_version is None
    assert e.host_os is None
    assert e.timestamp is None


def test_parse_source_path_preserved():
    path = str(_FIXTURES / "apt_missing_package.log")
    e = parse_install_log(path)
    assert e.source_path == path
