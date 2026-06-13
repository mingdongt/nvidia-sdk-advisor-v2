"""Host-OS and NVIDIA target-device detection for the SDK Manager copilot.

Pure-read, cross-platform (Linux / WSL / Windows) environment probes mirroring the
preflight checks NVIDIA SDK Manager runs before an install. No sudo, no device
writes. Consumed by: the detect_host_os / detect_target_device tools, the
EnvironmentDetectionMiddleware (system-prompt injection), and the welcome banner.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass, field

_PROBE_TIMEOUT = 5  # seconds, per subprocess probe


# --- low-level helpers (patched in tests) ----------------------------------


def _read_text(path: str) -> str | None:
    """Read a small text file, returning None on any error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _run(cmd: list[str]) -> str | None:
    """Run a read-only command; return stdout (even on nonzero exit), or None on
    exception, timeout, or empty output."""
    try:
        proc = subprocess.run(  # noqa: S603  # fixed arg lists, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout or None


# --- data types ------------------------------------------------------------


@dataclass
class HostOSInfo:
    """Structured host operating-system facts."""

    os_id: str | None = None
    os_version: str | None = None
    host_os_string: str | None = None  # e.g. "ubuntu22.04" / "windows11"
    pretty_name: str | None = None
    kernel: str | None = None
    arch: str | None = None  # x86_64 | aarch64 | x86_64-wsl
    is_wsl: bool = False
    is_docker: bool = False
    is_vm: bool = False
    cpu_count: int | None = None
    total_ram_gb: float | None = None


# --- host OS detection -----------------------------------------------------


def _norm_arch(machine: str) -> str:
    lowered_machine = (machine or "").lower()
    if lowered_machine in {"x86_64", "amd64"}:
        return "x86_64"
    if lowered_machine in {"aarch64", "arm64"}:
        return "aarch64"
    return machine or "unknown"


def _parse_os_release(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, val = line.partition("=")
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
            v = v[1:-1]
        out[key.strip()] = v
    return out


def _total_ram_gb() -> float | None:
    """Best-effort total RAM in GB (POSIX via sysconf; None elsewhere)."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, AttributeError, OSError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return round(pages * page_size / (1024**3), 1)


def _detect_host_os_linux() -> HostOSInfo:
    info = HostOSInfo()
    release = platform.release()
    info.kernel = release
    info.arch = _norm_arch(platform.machine())
    info.cpu_count = os.cpu_count()
    info.total_ram_gb = _total_ram_gb()

    osr = _read_text("/etc/os-release")
    if osr:
        fields = _parse_os_release(osr)
        info.os_id = fields.get("ID")
        info.os_version = fields.get("VERSION_ID")
        info.pretty_name = fields.get("PRETTY_NAME")
        if info.os_id and info.os_version:
            info.host_os_string = f"{info.os_id}{info.os_version}"

    lowered = (release or "").lower()
    info.is_wsl = "microsoft" in lowered or "wsl" in lowered
    if info.is_wsl and info.arch == "x86_64":
        info.arch = "x86_64-wsl"

    info.is_docker = os.path.exists("/.dockerenv") or (
        ":/docker/" in (_read_text("/proc/1/cgroup") or "")
    )

    virt = _run(["systemd-detect-virt", "--vm"])
    if virt is not None:
        virt = virt.strip()
        info.is_vm = bool(virt) and virt != "none"

    return info


def _detect_host_os_windows() -> HostOSInfo:
    info = HostOSInfo()
    info.kernel = platform.version()
    info.arch = _norm_arch(platform.machine())
    info.cpu_count = os.cpu_count()
    info.pretty_name = platform.platform()

    build = 0
    parts = (platform.version() or "").split(".")
    if len(parts) >= 3 and parts[2].isdigit():
        build = int(parts[2])
    is_server = "server" in (info.pretty_name or "").lower()
    if is_server:
        info.os_id = "windows_server"
        info.host_os_string = "windows_server"
    elif build >= 22000:
        info.os_id = "windows"
        info.os_version = "11"
        info.host_os_string = "windows11"
    elif build >= 10240:
        info.os_id = "windows"
        info.os_version = "10"
        info.host_os_string = "windows10"
    else:
        info.os_id = "windows"
        info.host_os_string = "windows"
    return info


def detect_host_os() -> HostOSInfo:
    """Detect the host operating system, architecture, runtime shape and resources.

    Pure-read and never raises. Mirrors SDK Manager's host-OS gate
    (``${ID}${VERSION_ID}`` on Linux; Windows build map).
    """
    system = platform.system()
    if system == "Windows":
        return _detect_host_os_windows()
    return _detect_host_os_linux()
