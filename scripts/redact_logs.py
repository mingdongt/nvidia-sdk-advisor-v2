"""Redact privacy-sensitive content from 5 forum-sourced zips and re-pack
into the repo's data/sample_logs/ directory.

Redaction rules:
  - /home/<anything>/         -> /home/REDACTED/
  - C:\\Users\\<anything>\\   -> C:\\Users\\REDACTED\\
  - Public IPs (not NVIDIA-known) -> X.X.X.X
  - SenseTime, company-name-ish patterns -> REDACTED

Preserves all error messages, error codes, target IDs, JetPack versions,
component names — the agent needs all of those.
"""
import re
import zipfile
import shutil
from pathlib import Path

SRC_ZIPS = [
    r"C:\Users\mingdongtan\Downloads\SDKM_logs_JetPack_6.1_Linux_for_Jetson_AGX_Orin_modules_2024-09-30_16-09-17.zip",
    r"C:\Users\mingdongtan\Downloads\SDKM_logs_JetPack_6.2.2_Linux_for_Jetson_AGX_Orin_modules_2026-04-10_10-51-27.zip",
    r"C:\Users\mingdongtan\Downloads\SDKM_logs_2025-01-03_13-01-22.zip",
    r"C:\Users\mingdongtan\Downloads\SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip",
    r"C:\Users\mingdongtan\Downloads\SDKM_logs_JetPack_6.2_Linux_for_Jetson_Orin_Nano_[8GB_developer_kit_version]_2025-03-21_12-48-45.zip",
]

DEST_DIR = Path(r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs")

# Patterns to redact (replacement preserves length-ish where possible)
LINUX_HOME = re.compile(r"/home/([\w.+-]+)/")
WIN_USER = re.compile(r"([Cc]):\\Users\\([^\\]+)\\")
# Don't touch the NVIDIA recovery USB IP — it's a known constant, not personal
NVIDIA_CONSTANT_IPS = {"192.168.55.1", "192.168.55.100", "0.0.0.0", "127.0.0.1"}
IP_PATTERN = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
# Watch for company/org names
COMPANY_NAMES = ["SENSETIME", "sensetime"]
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact_text(text: str) -> str:
    out = LINUX_HOME.sub("/home/REDACTED/", text)
    out = WIN_USER.sub(lambda m: f"{m.group(1)}:\\Users\\REDACTED\\", out)
    # IPs: replace any not in the NVIDIA constant set
    out = IP_PATTERN.sub(lambda m: m.group(1) if m.group(1) in NVIDIA_CONSTANT_IPS else "X.X.X.X", out)
    out = EMAIL_PATTERN.sub("REDACTED@example.com", out)
    for name in COMPANY_NAMES:
        out = out.replace(name, "REDACTED")
    return out


def redact_zip(src: Path, dest: Path) -> tuple[int, int]:
    """Read src .zip, redact .log/.txt entries, write to dest .zip.
    Returns (files_redacted, bytes_redacted)."""
    files_done = 0
    total_bytes = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.is_dir():
                continue
            data = zin.read(info.filename)
            if info.filename.lower().endswith((".log", ".txt")):
                text = data.decode("utf-8", errors="replace")
                redacted = redact_text(text)
                data = redacted.encode("utf-8")
                files_done += 1
                total_bytes += len(data)
            zout.writestr(info, data)
    return files_done, total_bytes


def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    for src_path in SRC_ZIPS:
        src = Path(src_path)
        if not src.exists():
            print(f"SKIP {src.name} (missing)")
            continue
        dest = DEST_DIR / src.name
        files, total = redact_zip(src, dest)
        print(f"OK   {src.name}")
        print(f"       -> {dest}")
        print(f"       {files} log files redacted ({total:,} bytes)")


if __name__ == "__main__":
    main()
