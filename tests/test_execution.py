from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

from src.execution import run_dry_run_mode_for_file, _stream_subprocess


def test_stream_subprocess_collects_lines():
    """_stream_subprocess should iterate stdout lines from a fake Popen."""
    fake_proc = MagicMock()
    fake_proc.stdout.__iter__.return_value = iter([
        "Parsing response file...\n",
        "Would install: Jetson 6.1\n",
        "OK\n",
    ])
    fake_proc.wait.return_value = 0
    collected = []
    rc = _stream_subprocess(fake_proc, on_line=collected.append)
    assert rc == 0
    assert "Would install" in "".join(collected)


def test_dry_run_uses_response_file_arg():
    """run_dry_run_mode_for_file should pass --response-file to NvSDKManager.exe."""
    fake_proc = MagicMock(returncode=0)
    fake_proc.stdout.__iter__.return_value = iter([])
    fake_proc.wait.return_value = 0
    with tempfile.NamedTemporaryFile(suffix=".ini", delete=False) as f:
        plan_path = Path(f.name)
        f.write(b"[client_arguments]\n")
    try:
        with patch("src.execution.subprocess.Popen", return_value=fake_proc) as mock_popen, \
             patch("src.execution._locate_sdkmanager_binary", return_value="C:/fake/NvSDKManager.exe"):
            run_dry_run_mode_for_file(plan_path)
        called_args = mock_popen.call_args[0][0]
        assert "--response-file" in called_args
        assert str(plan_path) in called_args
        assert "--query" in called_args
    finally:
        plan_path.unlink(missing_ok=True)


def test_classify_event_install_line():
    from src.execution import _classify_event
    assert _classify_event("Installing JetPack Components...") == "installing"
    assert _classify_event("Downloading nvidia-jetpack-runtime") == "downloading"
    assert _classify_event("Flashing target board...") == "flashing"
    assert _classify_event("Error: failed to flash") == "error"
    assert _classify_event("Just a normal log line") == "info"


def test_find_latest_install_log_picks_session_log_when_no_tarball(tmp_path, monkeypatch):
    """When user hasn't run --export-logs, the function still finds raw .log files
    in SDK Manager's session log directory.
    """
    fake_home = tmp_path / "home"
    nvsdkm = fake_home / ".nvsdkm-logs" / "session-2026-05-24"
    nvsdkm.mkdir(parents=True)
    log_file = nvsdkm / "target_install.log"
    log_file.write_text("apt failed\n", encoding="utf-8")

    # Force Path.home() to point at our fake home
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # Force cwd() too so glob in other branches doesn't accidentally hit real cwd
    monkeypatch.chdir(tmp_path)

    from src.execution import _find_latest_install_log
    result = _find_latest_install_log()
    assert result is not None
    assert result.name == "target_install.log"


def test_find_latest_install_log_prefers_most_recent(tmp_path, monkeypatch):
    """If multiple candidates exist, the one with the newest mtime wins."""
    import time
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Older tarball
    older_tar = fake_home / "sdkm-export-logs-old.tar.gz"
    older_tar.write_text("old", encoding="utf-8")
    time.sleep(0.05)
    # Newer .log file in session dir
    nvsdkm = fake_home / ".nvsdkm-logs" / "session-x"
    nvsdkm.mkdir(parents=True)
    newer_log = nvsdkm / "flash.log"
    newer_log.write_text("new", encoding="utf-8")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(tmp_path)

    from src.execution import _find_latest_install_log
    result = _find_latest_install_log()
    assert result.name == "flash.log"


def test_find_latest_install_log_returns_none_when_empty(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(tmp_path)

    from src.execution import _find_latest_install_log
    assert _find_latest_install_log() is None
