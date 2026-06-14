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
import shutil
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


# --- target device detection ----------------------------------------------


@dataclass
class TargetDevice:
    """A single detected NVIDIA USB device."""

    vid_pid: str  # "0955:7523"
    mode: str  # recovery | normal | storage | debug | unknown
    board: str
    bus_port: str | None = None


@dataclass
class TargetDeviceInfo:
    """Result of a target-device scan (empty list => none detected)."""

    devices: list[TargetDevice] = field(default_factory=list)
    scan_method: str = "unavailable"  # lsusb | get-pnpdevice | unavailable
    note: str | None = None


# NVIDIA Tegra recovery VID. Board map is a VERIFIED curated subset; the full
# catalog lives in SDK Manager's hwdata/families/*/devices/*.json (not bundled).
_NVIDIA_VID = "0955"
_DOCA_VID = "22dc"
_BOARD_MAP = {
    "0955:7523": "Jetson Orin Nano / Orin NX (devkit, recovery)",
    "0955:7019": "Jetson AGX Xavier (recovery)",
    "0955:7020": "Jetson (normal boot mode)",
    "0955:7035": "Jetson (USB mass-storage mode)",
    "0955:7100": "Jetson (USB mass-storage mode)",
    "0955:7045": "Jetson (debug / UART port)",
}
_NORMAL_PIDS = {"0955:7020"}
_STORAGE_PIDS = {"0955:7035", "0955:7100"}
_DEBUG_PIDS = {"0955:7045"}

_LSUSB_LINE = re.compile(
    r"Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})"
)


def _classify(vid_pid: str) -> str:
    if vid_pid in _DEBUG_PIDS:
        return "debug"
    if vid_pid in _STORAGE_PIDS:
        return "storage"
    if vid_pid in _NORMAL_PIDS:
        return "normal"
    if vid_pid.startswith(_NVIDIA_VID + ":"):
        return "recovery"
    return "unknown"


def _resolve_board(vid_pid: str) -> str:
    if vid_pid in _BOARD_MAP:
        return _BOARD_MAP[vid_pid]
    if vid_pid.startswith(_DOCA_VID + ":"):
        return f"NVIDIA networking/DOCA device (unknown model {vid_pid})"
    return f"NVIDIA Tegra device (unknown model {vid_pid})"


def _detect_target_device_linux() -> TargetDeviceInfo:
    out = _run(["lsusb"])
    if out is None:
        return TargetDeviceInfo(scan_method="unavailable", note="lsusb not found")
    devices: list[TargetDevice] = []
    for line in out.splitlines():
        m = _LSUSB_LINE.search(line)
        if not m:
            continue
        bus, _dev, vid, pid = m.groups()
        vid, pid = vid.lower(), pid.lower()
        if vid not in {_NVIDIA_VID, _DOCA_VID}:
            continue
        vid_pid = f"{vid}:{pid}"
        devices.append(
            TargetDevice(
                vid_pid=vid_pid,
                mode=_classify(vid_pid),
                board=_resolve_board(vid_pid),
                bus_port=f"bus {bus}",
            )
        )
    return TargetDeviceInfo(devices=devices, scan_method="lsusb")


def _detect_target_device_windows() -> TargetDeviceInfo:
    if shutil.which("powershell") is None:
        return TargetDeviceInfo(scan_method="unavailable", note="powershell not found")
    # NOTE: this filter covers only the Jetson recovery VID (0955); the DOCA VID
    # (22dc) is not matched here. Add a second Where-Object clause if DOCA device
    # detection on Windows is needed.
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-PnpDevice -PresentOnly | "
            "Where-Object { $_.InstanceId -match 'VID_0955' } | "
            "Select-Object -ExpandProperty InstanceId",
        ]
    )
    devices: list[TargetDevice] = []
    for line in (out or "").splitlines():
        m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", line)
        if not m:
            continue
        vid_pid = f"{m.group(1).lower()}:{m.group(2).lower()}"
        devices.append(
            TargetDevice(
                vid_pid=vid_pid, mode=_classify(vid_pid), board=_resolve_board(vid_pid)
            )
        )
    return TargetDeviceInfo(devices=devices, scan_method="get-pnpdevice")


def detect_target_device() -> TargetDeviceInfo:
    """Scan for connected NVIDIA target devices (Jetson recovery, DOCA/DPU).

    Pure-read USB-bus scan; never raises. Empty ``devices`` => none detected.
    """
    if platform.system() == "Windows":
        return _detect_target_device_windows()
    return _detect_target_device_linux()


# --- rendering + memoized startup snapshot ---------------------------------

_cache: dict[str, str] = {}


def reset_environment_cache() -> None:
    """Clear the memoized startup snapshot (used by tests and on refresh)."""
    _cache.clear()


def _host_line(host: HostOSInfo) -> str:
    name = host.pretty_name or host.host_os_string or "unknown OS"
    # Include host_os_string as a compact tag when it adds information
    if host.host_os_string and host.host_os_string not in name:
        name = f"{name} [{host.host_os_string}]"
    bits = [f"Host: {name}"]
    if host.arch:
        bits.append(f"({host.arch})")
    extra = []
    if host.is_wsl:
        extra.append("WSL2=yes")
    if host.is_docker:
        extra.append("Docker=yes")
    if host.is_vm:
        extra.append("VM=yes")
    if host.cpu_count:
        extra.append(f"{host.cpu_count} cores")
    if host.total_ram_gb:
        extra.append(f"{host.total_ram_gb:g} GB RAM")
    line = " ".join(bits)
    if extra:
        line += ". " + ", ".join(extra) + "."
    return line


def _device_line(info: TargetDeviceInfo) -> str:
    if info.scan_method == "unavailable":
        return f"NVIDIA target device: scan unavailable ({info.note})."
    recovery = [d for d in info.devices if d.mode == "recovery"]
    if recovery:
        d = recovery[0]
        return (
            f"NVIDIA target device: {d.board} [{d.vid_pid}] in recovery mode"
            f"{(' on ' + d.bus_port) if d.bus_port else ''}."
        )
    if info.devices:
        d = info.devices[0]
        return f"NVIDIA target device: {d.board} [{d.vid_pid}] ({d.mode} mode)."
    return "NVIDIA target device: none detected in USB recovery mode."


def get_environment_summary() -> str:
    """One-line-per-fact host + device summary (memoized for the session)."""
    if "summary" not in _cache:
        host = detect_host_os()
        dev = detect_target_device()
        _cache["summary"] = _host_line(host) + "\n" + _device_line(dev)
    return _cache["summary"]


def render_prompt_block() -> str:
    """The ``<environment_detection>`` block appended to the system prompt."""
    if "block" not in _cache:
        body = get_environment_summary()
        _cache["block"] = f"<environment_detection>\n{body}\n</environment_detection>"
    return _cache["block"]
