"""Wrapper around NvSDKManager.exe --list-connected for hardware auto-detection."""
import os
import re
import shutil
import subprocess

_BINARY_NAMES = ("NvSDKManager.exe", "sdkmanager")
_DEFAULT_INSTALL = r"C:\Program Files\NVIDIA Corporation\SDK Manager\NvSDKManager.exe"


def _locate_binary() -> str | None:
    for name in _BINARY_NAMES:
        path = shutil.which(name)
        if path:
            return path
    if os.path.exists(_DEFAULT_INSTALL):
        return _DEFAULT_INSTALL
    return None


def _parse_devices(stdout: str) -> list[dict]:
    devices = []
    for line in stdout.splitlines():
        m = re.match(r"\s*-\s*(.+?)\s*\((.+?)\)\s*$", line)
        if m:
            devices.append({"name": m.group(1).strip(), "port": m.group(2).strip()})
    return devices


def detect_connected_hardware() -> dict:
    """Return {available, devices, reason?}. Never raises."""
    binary = _locate_binary()
    if not binary:
        return {"available": False, "devices": [], "reason": "NvSDKManager.exe not found on PATH or default install"}
    try:
        proc = subprocess.run(
            [binary, "--list-connected", "all"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"available": False, "devices": [], "reason": f"subprocess error: {e}"}
    if proc.returncode != 0:
        return {"available": False, "devices": [], "reason": f"NvSDKManager exited {proc.returncode}: {proc.stderr[:200]}"}
    return {"available": True, "devices": _parse_devices(proc.stdout)}
