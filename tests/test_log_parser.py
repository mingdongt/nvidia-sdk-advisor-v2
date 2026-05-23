from pathlib import Path

from src.log_parser import parse_install_log
from src.models import LogDiagnosis

_FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_apt_missing_package():
    diag = parse_install_log(str(_FIXTURES / "apt_missing_package.log"))
    assert isinstance(diag, LogDiagnosis)
    assert diag.failed_stage == "apt"
    assert diag.error_class == "apt-missing-package"
    assert "nvidia-jetpack" in diag.error_signature
    assert diag.target == "JETSON_ORIN_NX_TARGETS"
    assert diag.host_os == "ubuntu22.04"
    assert diag.jetpack_version == "6.1"
    assert len(diag.search_terms) > 0


def test_parse_flash_failed():
    diag = parse_install_log(str(_FIXTURES / "flash_failed.log"))
    assert diag.failed_stage == "flash"
    assert diag.error_class == "flash-failure"
    assert "1042" in diag.error_signature


def test_parse_postinst_kernel():
    diag = parse_install_log(str(_FIXTURES / "postinst_kernel_mismatch.log"))
    assert diag.failed_stage == "postinst"
    assert diag.error_class in ("kernel-module-mismatch", "kernel-module-load-fail")


def test_parse_network_timeout():
    diag = parse_install_log(str(_FIXTURES / "network_timeout.log"))
    assert diag.failed_stage == "download"
    assert diag.error_class == "network-timeout"


def test_parse_success_log_returns_unknown():
    """Success logs should not match any error pattern."""
    diag = parse_install_log(str(_FIXTURES / "success.log"))
    assert diag.failed_stage == "unknown"


def test_parse_provides_raw_excerpt():
    diag = parse_install_log(str(_FIXTURES / "apt_missing_package.log"))
    assert "Unable to locate" in diag.raw_excerpt
    # ±5 lines around the match means roughly 10-11 lines total
    assert diag.raw_excerpt.count("\n") >= 3
