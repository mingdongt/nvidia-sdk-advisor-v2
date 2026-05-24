"""Verify the redacted zips in data/sample_logs/ have no leftover sensitive
content. Catches the leak patterns that v1 of redact_logs.py missed:
  - multi-level /home/<org>/<user>/ paths (wangmingke leak)
  - /tmp/tmp_*.<user>.sh script filenames (arrow leak)
"""
import re
import sys
import zipfile
from pathlib import Path

DEST_DIR = Path(r"C:\onmyway\nvidia-sdk-advisor-v2\data\sample_logs")

# Known usernames previously leaked — explicit recall test
KNOWN_LEAKED_USERS = ["sensetime", "wangmingke", "arrow"]

PATTERNS = {
    "linux_home_not_redacted": re.compile(r"/home/(?!REDACTED[/.])(?!REDACTED$)([\w.+-]+)/"),
    "win_user_not_redacted":   re.compile(r"[Cc]:\\Users\\(?!REDACTED)([^\\]+)\\"),
    "tmp_user_script":         re.compile(r"/tmp/tmp_[A-Z][A-Z0-9_]+\.(?!REDACTED\.)([\w.+-]+?)\.(?:sh|bat|ps1|cmd)\b"),
    "company_name_sensetime":  re.compile(r"(?i)sensetime"),
    "email_not_redacted":      re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}

# IPs that should still be present after redaction (NVIDIA constants)
NVIDIA_CONSTANT_IPS = {"192.168.55.1", "192.168.55.100", "0.0.0.0", "127.0.0.1"}
ANY_IP = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

zips = sorted(DEST_DIR.glob("*.zip"))
total_size = 0
all_clean = True
for zp in zips:
    size = zp.stat().st_size
    total_size += size
    print(f"\n### {zp.name}  ({size:,} bytes)")
    findings = {k: 0 for k in PATTERNS}
    finding_samples = {k: set() for k in PATTERNS}
    leaked_user_hits = {u: 0 for u in KNOWN_LEAKED_USERS}
    non_redacted_ips = set()
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith((".log", ".txt")):
                continue
            with zf.open(info) as f:
                text = f.read().decode("utf-8", errors="replace")
                for k, rx in PATTERNS.items():
                    matches = rx.findall(text)
                    findings[k] += len(matches)
                    if matches:
                        sample = matches[0]
                        if isinstance(sample, tuple):
                            sample = sample[0]
                        finding_samples[k].add(sample)
                lower = text.lower()
                for u in KNOWN_LEAKED_USERS:
                    leaked_user_hits[u] += lower.count(u)
                for ip in ANY_IP.findall(text):
                    if ip != "X.X.X.X" and ip not in NVIDIA_CONSTANT_IPS:
                        non_redacted_ips.add(ip)

    clean = True
    for k, v in findings.items():
        if v:
            sample = next(iter(finding_samples[k]), "")
            print(f"  ! {k}: {v} matches remaining (e.g. {sample!r})")
            clean = False
    for u, n in leaked_user_hits.items():
        if n:
            print(f"  ! known-leaked username '{u}': {n} occurrences")
            clean = False
    if non_redacted_ips:
        print(f"  ! {len(non_redacted_ips)} non-redacted IPs leaked, sample: {list(non_redacted_ips)[:5]}")
        clean = False
    if clean:
        print(f"  + CLEAN")
    all_clean &= clean

print(f"\nTotal size of 5 redacted zips: {total_size:,} bytes ({total_size/1024/1024:.2f} MB)")
print("\nALL CLEAN" if all_clean else "\nSOME ZIPS STILL HAVE LEAKS")
sys.exit(0 if all_clean else 1)
