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
class LogExcerpt:
    """Structured extraction from an SDK Manager log archive.

    Deliberately does NOT classify errors or assign stages — that is the
    agent's job based on reading tail_text + external web search. This
    layer is purely deterministic: open the archive, parse the filename,
    take the tail. Everything else is downstream.
    """
    target: Optional[str] = None           # from filename (canonical target_id)
    host_os: Optional[str] = None          # from filename
    jetpack_version: Optional[str] = None  # from filename
    timestamp: Optional[str] = None        # from filename
    tail_text: str = ""                    # last ~200 lines of concatenated log content
    file_count: int = 0                    # number of .log/.txt files found in archive
    total_size_bytes: int = 0              # total bytes scanned
    source_path: str = ""                  # input path
