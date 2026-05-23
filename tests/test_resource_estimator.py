from src.models import InstallConfig
from src.resource_estimator import estimate_resources, check_constraints


def test_estimate_basic_jetpack_only():
    cfg = InstallConfig(product="Jetson", version="6.1", target="JETSON_ORIN_NANO_TARGETS")
    r = estimate_resources(cfg)
    assert r["host_disk_gb"] == 35
    assert r["target_disk_gb"] == 17
    assert "JetPack 6.1" in str(r["breakdown"])


def test_estimate_with_deepstream():
    cfg = InstallConfig(
        product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
        additional_sdks=["DeepStream 7.0"],
    )
    r = estimate_resources(cfg)
    assert r["target_disk_gb"] == 17 + 4.0
    assert r["runtime_ram_gb"] >= 1.8


def test_check_constraints_fits():
    cfg = InstallConfig(product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS")
    result = check_constraints(cfg, available_disk_gb=64, available_ram_gb=8)
    assert result["fits"] is True
    assert result["violations"] == []


def test_check_constraints_storage_too_small():
    cfg = InstallConfig(
        product="Jetson", version="6.1", target="JETSON_ORIN_NANO_TARGETS",
        additional_sdks=["Isaac ROS 3.2", "DeepStream 7.0"],
    )
    result = check_constraints(cfg, available_disk_gb=18, available_ram_gb=8)
    assert result["fits"] is False
    assert any("disk" in v.lower() for v in result["violations"])
