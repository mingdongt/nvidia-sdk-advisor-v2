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
