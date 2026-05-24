"""Tests for --full mode orchestrator (mock helpers).

The full end-to-end orchestration requires Anthropic API + MCP server stdio,
which is covered by the smoke eval pipeline. These tests cover the
deterministic mock pieces only.
"""
from pathlib import Path

from src.orchestrator import (
    MOCK_FAILURE_LOG,
    MOCK_SUCCESS_LOG,
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


def test_write_mock_log_writes_to_disk(tmp_path, monkeypatch):
    """Verify _write_mock_log creates a file under the mock dir.

    Patch Path.home() to redirect ~/.sdk-advisor-mock into tmp_path so the
    test doesn't pollute the user's home.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log_path = _write_mock_log("hello world", "test-label")
    assert Path(log_path).exists()
    assert Path(log_path).read_text(encoding="utf-8") == "hello world"
    assert "test-label" in Path(log_path).name


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
