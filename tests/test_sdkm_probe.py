from unittest.mock import patch, MagicMock
from src.sdkm_probe import detect_connected_hardware


def test_detect_when_binary_missing():
    with patch("src.sdkm_probe.shutil.which", return_value=None), \
         patch("src.sdkm_probe.os.path.exists", return_value=False):
        result = detect_connected_hardware()
    assert result["available"] is False
    assert "not found" in result["reason"].lower()
    assert result["devices"] == []


def test_detect_when_binary_succeeds_no_devices():
    fake = MagicMock(returncode=0, stdout="No connected devices found.\n", stderr="")
    with patch("src.sdkm_probe.shutil.which", return_value="C:/fake/NvSDKManager.exe"), \
         patch("src.sdkm_probe.subprocess.run", return_value=fake):
        result = detect_connected_hardware()
    assert result["available"] is True
    assert result["devices"] == []


def test_detect_when_binary_returns_one_device():
    output = (
        "Connected NVIDIA devices:\n"
        "  - Jetson Orin Nano 8GB (USB 1-4)\n"
    )
    fake = MagicMock(returncode=0, stdout=output, stderr="")
    with patch("src.sdkm_probe.shutil.which", return_value="C:/fake/NvSDKManager.exe"), \
         patch("src.sdkm_probe.subprocess.run", return_value=fake):
        result = detect_connected_hardware()
    assert result["available"] is True
    assert len(result["devices"]) == 1
    assert "Orin Nano" in result["devices"][0]["name"]
