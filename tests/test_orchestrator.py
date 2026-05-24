"""Tests for --full mode orchestrator (mock helpers).

The full end-to-end orchestration requires Anthropic API + MCP server stdio,
which is covered by the smoke eval pipeline. These tests cover the
deterministic mock pieces only.
"""
from pathlib import Path

from src.orchestrator import (
    MOCK_FAILURE_LOG,
    MOCK_SUCCESS_LOG,
    _extract_code_blocks,
    _query_to_filename,
    _save_agent_artifacts,
    _write_mock_log,
    run_mock_install,
)


def test_mock_failure_log_contains_apt_error():
    """The failure log must have the canonical apt-missing-package signature
    so log_parser + troubleshoot can recognize and act on it."""
    assert "E: Unable to locate package nvidia-jetpack" in MOCK_FAILURE_LOG
    assert "target_install (apt)" in MOCK_FAILURE_LOG


def test_mock_success_log_marks_completion():
    """The retry log must explicitly mark success so the orchestrator can
    detect the chain succeeded."""
    assert "SUCCESS" in MOCK_SUCCESS_LOG
    assert "Install complete" in MOCK_SUCCESS_LOG


def test_write_mock_log_uses_sdkm_export_naming(tmp_path, monkeypatch):
    """Mock log filename must match SDK Manager's official export convention
    so log_parser._FILENAME_RE extracts target / JetPack / host_os / timestamp.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log_path = _write_mock_log("hello world", retry=False)
    name = Path(log_path).name
    assert name.startswith("SDKM_logs_JetPack_")
    assert "for_Jetson_" in name
    assert name.endswith(".log")
    # Round-trip through log_parser to confirm metadata is recoverable.
    from src.log_parser import parse_install_log
    excerpt = parse_install_log(log_path)
    assert excerpt.target is not None
    assert excerpt.jetpack_version is not None
    assert excerpt.host_os is not None


def test_run_mock_install_failure(monkeypatch, tmp_path):
    """First call (retry=False) returns exit 100 and the failure log."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    rc, log_path = run_mock_install(retry=False)
    assert rc == 100
    assert "E: Unable to locate" in Path(log_path).read_text(encoding="utf-8")


def test_run_mock_install_retry(monkeypatch, tmp_path):
    """Retry call returns exit 0 and the success log."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    rc, log_path = run_mock_install(retry=True)
    assert rc == 0
    assert "SUCCESS" in Path(log_path).read_text(encoding="utf-8")


def test_query_to_filename_sanitizes():
    """Free-text queries become safe filename stems. Leading/trailing
    underscores from punctuation get stripped so filenames look clean."""
    assert _query_to_filename("Orin NX 16GB, YOLOv8 at 30fps") == "orin_nx_16gb__yolov8_at_30fps"
    # Hyphen is preserved; trailing punctuation strips to nothing
    assert _query_to_filename("hi-there!") == "hi-there"
    # Empty / pathological inputs fall back to a sensible default.
    assert _query_to_filename("") == "plan"
    assert _query_to_filename("!!!") == "plan"


def test_extract_code_blocks_recognizes_ini_and_command():
    """Pull sdkmanager command + .ini from a typical agent response."""
    text = """
Here's your setup:

```bash
sdkmanager --cli --action install --product Jetson --version 6.1
```

And the response file:

```ini
[client_arguments]
action = install
product = Jetson
```
"""
    blocks = _extract_code_blocks(text)
    assert blocks["command"].startswith("sdkmanager --cli")
    assert "[client_arguments]" in blocks["ini"]


def test_save_agent_artifacts_uses_query_derived_filename(tmp_path, monkeypatch):
    """After --full configure phase, files in output/ should match the user query."""
    monkeypatch.chdir(tmp_path)
    response = (
        "```bash\nsdkmanager --cli --product Jetson --version 6.1\n```\n"
        "```ini\n[client_arguments]\naction = install\n```"
    )
    saved = _save_agent_artifacts(response, "Orin NX YOLOv8")
    assert saved["command"] is not None
    assert saved["ini"] is not None
    assert "orin_nx_yolov8" in saved["command"].name
    assert "orin_nx_yolov8" in saved["ini"].name
    assert saved["ini"].read_text(encoding="utf-8").startswith("[client_arguments]")
