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
