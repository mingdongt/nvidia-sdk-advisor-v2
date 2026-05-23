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
