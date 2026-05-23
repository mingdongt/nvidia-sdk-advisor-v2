import pytest
from src.models import InstallConfig


def test_basic_construction():
    cfg = InstallConfig(
        product="Jetson",
        version="6.1",
        target="JETSON_ORIN_NX_TARGETS",
    )
    assert cfg.product == "Jetson"
    assert cfg.target_os == "Linux"
    assert cfg.host is True
    assert cfg.flash is False
    assert cfg.additional_sdks == []
    assert cfg.login_type == "devzone"


def test_blank_required_field_rejected():
    with pytest.raises(ValueError):
        InstallConfig(product="", version="6.1", target="JETSON_ORIN_NX_TARGETS")
    with pytest.raises(ValueError):
        InstallConfig(product="Jetson", version=" ", target="JETSON_ORIN_NX_TARGETS")
    with pytest.raises(ValueError):
        InstallConfig(product="Jetson", version="6.1", target="")


def test_additional_sdks_list():
    cfg = InstallConfig(
        product="Jetson",
        version="6.1",
        target="JETSON_ORIN_NX_TARGETS",
        additional_sdks=["DeepStream 7.0", "Isaac ROS 3.2"],
    )
    assert cfg.additional_sdks == ["DeepStream 7.0", "Isaac ROS 3.2"]


def test_invalid_login_type_rejected():
    with pytest.raises(ValueError):
        InstallConfig(
            product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
            login_type="oauth",
        )


def test_invalid_action_rejected():
    with pytest.raises(ValueError):
        InstallConfig(
            product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
            action="rebuild",
        )


from src.models import LogDiagnosis


def test_log_diagnosis_minimal():
    d = LogDiagnosis(failed_stage="apt", error_signature="E: Unable to locate package", error_class="apt-missing-package")
    assert d.failed_stage == "apt"
    assert d.target is None
    assert d.raw_excerpt == ""


def test_log_diagnosis_full():
    d = LogDiagnosis(
        failed_stage="flash",
        error_signature="Error: failed to flash, errCode 1042",
        error_class="flash-failure",
        target="JETSON_ORIN_NX_TARGETS",
        host_os="ubuntu22.04",
        jetpack_version="6.1",
        timestamp="2026-05-22 14:33:21",
        last_successful_step="recovery_mode_entry",
        raw_excerpt="...some context...",
    )
    assert d.error_class == "flash-failure"
    assert d.last_successful_step == "recovery_mode_entry"
