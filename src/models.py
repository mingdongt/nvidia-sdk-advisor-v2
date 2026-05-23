from dataclasses import dataclass, field
from typing import Optional

_VALID_LOGIN_TYPES = {"devzone", "nvonline", "offline"}
_VALID_ACTIONS = {"install", "uninstall", "downloadonly"}


@dataclass
class InstallConfig:
    """Configuration for a single SDK Manager installation plan."""
    product: str
    version: str
    target: str
    target_os: str = "Linux"
    host: bool = True
    flash: bool = False
    additional_sdks: list[str] = field(default_factory=list)
    login_type: str = "devzone"
    action: str = "install"

    def __post_init__(self):
        for name in ("product", "version", "target"):
            if not getattr(self, name) or not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be blank")
        if self.login_type not in _VALID_LOGIN_TYPES:
            raise ValueError(f"login_type must be one of {_VALID_LOGIN_TYPES}, got {self.login_type!r}")
        if self.action not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of {_VALID_ACTIONS}, got {self.action!r}")


@dataclass
class LogDiagnosis:
    """Result of parsing an SDK Manager install/error log."""
    failed_stage: str           # "download" | "flash" | "apt" | "postinst" | "license" | "unknown"
    error_signature: str        # the line that matched a pattern (or "" if unknown)
    error_class: str            # short tag, e.g. "apt-missing-package", "flash-failure"
    target: Optional[str] = None
    host_os: Optional[str] = None
    jetpack_version: Optional[str] = None
    timestamp: Optional[str] = None
    last_successful_step: Optional[str] = None
    raw_excerpt: str = ""        # ±5 lines around the failure
    search_terms: list[str] = field(default_factory=list)
