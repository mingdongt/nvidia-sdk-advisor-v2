"""Build the `sdkmanager --cli` command string from an InstallConfig."""
from src.models import InstallConfig


def generate_command(cfg: InstallConfig) -> str:
    parts = [
        "sdkmanager --cli",
        f"--action {cfg.action}",
        f"--login-type {cfg.login_type}",
        f"--product {cfg.product}",
        f"--version {cfg.version}",
        f"--target-os {cfg.target_os}",
        f"--target {cfg.target}",
    ]
    if cfg.host:
        parts.append("--host")
    if cfg.flash:
        parts.append("--flash")
    for sdk in cfg.additional_sdks:
        parts.append(f"--additional-sdk '{sdk}'")
    parts.append("--licenses accept")
    parts.append("--exit-on-finish")
    return " \\\n  ".join(parts)
