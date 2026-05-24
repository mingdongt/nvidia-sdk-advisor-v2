"""Extract concise, error-relevant snippets from the 5 zips in
data/sample_logs/. These snippets replace synthesized log_inline strings
in tests/eval_cases/troubleshoot.jsonl with real OP-uploaded log content.

Strategy: for each zip, scan all .log entries directly (bypassing the
200-line tail) and pull out lines containing strong failure signals
(Error Code, Failed, error:, ERROR, abort, succeeded: false). Print a
trimmed sample so we can paste into the jsonl.
"""
import sys
import zipfile
from pathlib import Path

ZIPS_AND_CASES = [
    ("case 5 (kernel-image-not-gzip, 321524)",
     r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs\SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip"),
    ("case 6 (WSL Orin Nano, 318733)",
     r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs\SDKM_logs_2025-01-03_13-01-22.zip"),
    ("case 7 (error code 11 / 1011, 327911)",
     r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs\SDKM_logs_JetPack_6.2_Linux_for_Jetson_Orin_Nano_[8GB_developer_kit_version]_2025-03-21_12-48-45.zip"),
    ("case 8 (exec_command flash failure, 308377)",
     r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs\SDKM_logs_JetPack_6.1_Linux_for_Jetson_AGX_Orin_modules_2024-09-30_16-09-17.zip"),
    ("case 9 (MCU firmware AGX Orin, 366168)",
     r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs\SDKM_logs_JetPack_6.2.2_Linux_for_Jetson_AGX_Orin_modules_2026-04-10_10-51-27.zip"),
]

STRONG_SIGNALS = [
    "Error Code:", "Failed - ", "Failed in", "succeeded: false",
    "*** ERROR", "gzip:", "DependencyFailure",
    "install process failure", "completeSetup failed",
    "[ Component Install Finished with Error ]",
    " - error: ",  # SDK Manager error log lines
]

# Lines we explicitly want to skip even if they contain error keywords
NOISE_SIGNALS = [
    "cannot get component by id undefined",  # noise spam
    "Failed to validate GA4 event",          # telemetry noise
    "GA install failure feedback",
    "Send GA",
]

def is_strong_failure_line(ln: str) -> bool:
    if any(noise in ln for noise in NOISE_SIGNALS):
        return False
    return any(sig in ln for sig in STRONG_SIGNALS)

for label, zp in ZIPS_AND_CASES:
    print("=" * 80)
    print(label)
    print("=" * 80)
    seen = set()
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith((".log", ".txt")):
                continue
            with zf.open(info) as f:
                text = f.read().decode("utf-8", errors="replace")
            for ln in text.splitlines():
                ln_s = ln.strip()
                if not ln_s:
                    continue
                if is_strong_failure_line(ln) and ln_s not in seen:
                    seen.add(ln_s)
                    # Truncate very long lines (some have huge embedded JSON)
                    if len(ln_s) > 220:
                        ln_s = ln_s[:217] + "..."
                    print(f"  [{info.filename}] {ln_s}")
    print()
