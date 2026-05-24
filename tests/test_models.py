import pytest
from src.models import InstallConfig, LogExcerpt


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


def test_log_excerpt_minimal():
    """LogExcerpt should construct cleanly with all defaults."""
    e = LogExcerpt()
    assert e.target is None
    assert e.host_os is None
    assert e.jetpack_version is None
    assert e.timestamp is None
    assert e.tail_text == ""
    assert e.file_count == 0
    assert e.total_size_bytes == 0
    assert e.source_path == ""


def test_log_excerpt_full():
    e = LogExcerpt(
        target="JETSON_AGX_ORIN_TARGETS",
        host_os="linux",
        jetpack_version="6.2",
        timestamp="2025-01-26 11:41:13",
        tail_text="...some log content...",
        file_count=3,
        total_size_bytes=8192,
        source_path="/tmp/sdkm-export.zip",
    )
    assert e.target == "JETSON_AGX_ORIN_TARGETS"
    assert e.jetpack_version == "6.2"
    assert e.file_count == 3
