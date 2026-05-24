"""Quick test of redact_logs.redact_text against known leak patterns."""
import sys
sys.path.insert(0, r"C:\onmyway\nvidia-sdk-advisor-v2\scripts")
from redact_logs import redact_text

CASES = [
    # (input, expected output)
    ("/home/SENSETIME/wangmingke/.nvsdkm/foo",
     "/home/REDACTED/.nvsdkm/foo"),
    ("/home/SENSETIME/wangmingke/nvidia/nvidia_sdk/x",
     "/home/REDACTED/nvidia/nvidia_sdk/x"),
    ("/home/arrow/.nvsdkm/x",
     "/home/REDACTED/.nvsdkm/x"),
    ("/home/arrow/nvidia_sdk/x",
     "/home/REDACTED/nvidia_sdk/x"),
    ("/home/justuser/somefile",
     "/home/REDACTED/somefile"),
    ("/tmp/tmp_NV_L4T_FLASH_JETSON_LINUX_COMP.arrow.sh",
     "/tmp/tmp_NV_L4T_FLASH_JETSON_LINUX_COMP.REDACTED.sh"),
    ("/tmp/tmp_NV_L4T_FILE_SYSTEM_AND_OS_COMP.bob.smith.sh",
     "/tmp/tmp_NV_L4T_FILE_SYSTEM_AND_OS_COMP.REDACTED.sh"),
    (r"C:\Users\alice\AppData",
     r"C:\Users\REDACTED\AppData"),
    # Should NOT mangle these
    ("Linux_for_Tegra/kernel/Image",  # no /home/ prefix
     "Linux_for_Tegra/kernel/Image"),
    ("/home/REDACTED/already_redacted",
     "/home/REDACTED/already_redacted"),
    # NVIDIA recovery IP must be preserved
    ("device at 192.168.55.1 detected",
     "device at 192.168.55.1 detected"),
    # Public IP gets X.X.X.X
    ("connecting to 8.8.8.8 ...",
     "connecting to X.X.X.X ..."),
]

ok = True
for inp, expected in CASES:
    actual = redact_text(inp)
    pass_ = actual == expected
    ok &= pass_
    mark = "OK  " if pass_ else "FAIL"
    print(f"{mark} {inp!r}")
    if not pass_:
        print(f"      expected: {expected!r}")
        print(f"      actual:   {actual!r}")
print()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
