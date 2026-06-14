"""Unit tests for environment_detect (host OS + target device probes)."""

from __future__ import annotations

from unittest.mock import patch

from deepagents_code import environment_detect as ed

_OS_RELEASE_UBUNTU = (
    'NAME="Ubuntu"\n'
    'VERSION_ID="22.04"\n'
    'ID=ubuntu\n'
    'PRETTY_NAME="Ubuntu 22.04.4 LTS"\n'
    'VERSION_CODENAME=jammy\n'
)


def test_host_os_string_from_os_release() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_read_text", return_value=_OS_RELEASE_UBUNTU),
        patch.object(ed.platform, "release", return_value="6.5.0-35-generic"),
        patch.object(ed.platform, "machine", return_value="x86_64"),
        patch.object(ed, "_run", return_value=None),
        patch.object(ed.os.path, "exists", return_value=False),
    ):
        info = ed.detect_host_os()

    assert info.os_id == "ubuntu"
    assert info.os_version == "22.04"
    assert info.host_os_string == "ubuntu22.04"
    assert info.arch == "x86_64"
    assert info.is_wsl is False
    assert info.kernel == "6.5.0-35-generic"
    assert info.is_docker is False


def test_host_os_detects_wsl() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_read_text", return_value=_OS_RELEASE_UBUNTU),
        patch.object(
            ed.platform, "release", return_value="5.15.146.1-microsoft-standard-WSL2"
        ),
        patch.object(ed.platform, "machine", return_value="x86_64"),
        patch.object(ed, "_run", return_value=None),
        patch.object(ed.os.path, "exists", return_value=False),
    ):
        info = ed.detect_host_os()

    assert info.is_wsl is True
    assert info.arch == "x86_64-wsl"


def test_host_os_windows_build_maps_to_win11() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Windows"),
        patch.object(ed.platform, "version", return_value="10.0.22631"),
        patch.object(ed.platform, "machine", return_value="AMD64"),
        patch.object(ed.platform, "platform", return_value="Windows-11-10.0.22631-SP0"),
    ):
        info = ed.detect_host_os()

    assert info.host_os_string == "windows11"
    assert info.arch == "x86_64"


_LSUSB_ORIN_NANO_RECOVERY = (
    "Bus 001 Device 012: ID 0955:7523 NVIDIA Corp. APX\n"
)
_LSUSB_DEBUG_PORT = (
    "Bus 001 Device 013: ID 0955:7045 NVIDIA Corp.\n"
)


def test_target_device_orin_nano_recovery() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=_LSUSB_ORIN_NANO_RECOVERY),
    ):
        info = ed.detect_target_device()

    assert info.scan_method == "lsusb"
    assert len(info.devices) == 1
    dev = info.devices[0]
    assert dev.vid_pid == "0955:7523"
    assert dev.mode == "recovery"
    assert "Orin Nano" in dev.board


def test_target_device_debug_port_classified() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=_LSUSB_DEBUG_PORT),
    ):
        info = ed.detect_target_device()

    assert info.devices[0].mode == "debug"


def test_target_device_none_detected() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=""),
    ):
        info = ed.detect_target_device()

    assert info.devices == []
    assert info.scan_method == "lsusb"


def test_target_device_lsusb_unavailable() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=None),
    ):
        info = ed.detect_target_device()

    assert info.devices == []
    assert info.scan_method == "unavailable"
    assert info.note is not None


_LSUSB_DOCA = "Bus 002 Device 003: ID 22dc:0214 NVIDIA Corp. BlueField\n"
_LSUSB_NORMAL = "Bus 001 Device 014: ID 0955:7020 NVIDIA Corp.\n"


def test_target_device_doca_kept() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=_LSUSB_DOCA),
    ):
        info = ed.detect_target_device()

    assert info.scan_method == "lsusb"
    assert len(info.devices) == 1
    assert info.devices[0].vid_pid == "22dc:0214"
    assert "DOCA" in info.devices[0].board


def test_target_device_normal_mode_classified() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Linux"),
        patch.object(ed, "_run", return_value=_LSUSB_NORMAL),
    ):
        info = ed.detect_target_device()

    assert info.devices[0].mode == "normal"
    assert "normal boot" in info.devices[0].board


def test_target_device_windows_no_device() -> None:
    with (
        patch.object(ed.platform, "system", return_value="Windows"),
        patch.object(ed.shutil, "which", return_value="C:/Windows/powershell.exe"),
        patch.object(ed, "_run", return_value=None),
    ):
        info = ed.detect_target_device()

    assert info.scan_method == "get-pnpdevice"
    assert info.devices == []


def test_summary_and_prompt_block_no_device() -> None:
    ed.reset_environment_cache()
    host = ed.HostOSInfo(
        host_os_string="ubuntu22.04",
        pretty_name="Ubuntu 22.04.4 LTS",
        arch="x86_64",
        is_wsl=False,
        cpu_count=16,
        total_ram_gb=32.0,
    )
    dev = ed.TargetDeviceInfo(devices=[], scan_method="lsusb")
    with (
        patch.object(ed, "detect_host_os", return_value=host),
        patch.object(ed, "detect_target_device", return_value=dev),
    ):
        summary = ed.get_environment_summary()
        block = ed.render_prompt_block()

    assert "ubuntu22.04" in summary
    assert "x86_64" in summary
    assert "none detected" in summary.lower()
    assert block.startswith("<environment_detection>")
    assert "ubuntu22.04" in block
    assert block.rstrip().endswith("</environment_detection>")


def test_summary_is_memoized() -> None:
    ed.reset_environment_cache()
    with (
        patch.object(ed, "detect_host_os", return_value=ed.HostOSInfo()) as host_mock,
        patch.object(
            ed, "detect_target_device", return_value=ed.TargetDeviceInfo()
        ) as dev_mock,
    ):
        ed.get_environment_summary()
        ed.get_environment_summary()

    host_mock.assert_called_once()
    dev_mock.assert_called_once()


def test_tool_detect_host_os_returns_dict() -> None:
    from deepagents_code.tools import detect_host_os as tool_detect_host_os

    with patch.object(
        ed, "detect_host_os", return_value=ed.HostOSInfo(host_os_string="ubuntu22.04")
    ):
        result = tool_detect_host_os()

    assert isinstance(result, dict)
    assert result["host_os_string"] == "ubuntu22.04"


def test_tool_detect_target_device_returns_dict() -> None:
    from deepagents_code.tools import detect_target_device as tool_detect_target_device

    sample = ed.TargetDeviceInfo(
        devices=[ed.TargetDevice(vid_pid="0955:7523", mode="recovery", board="Orin Nano")],
        scan_method="lsusb",
    )
    with patch.object(ed, "detect_target_device", return_value=sample):
        result = tool_detect_target_device()

    assert isinstance(result, dict)
    assert result["scan_method"] == "lsusb"
    assert result["devices"][0]["vid_pid"] == "0955:7523"
