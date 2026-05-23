from src.models import InstallConfig
from src.command_gen import generate_command


def test_basic_command():
    cfg = InstallConfig(product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS")
    cmd = generate_command(cfg)
    assert "sdkmanager --cli" in cmd
    assert "--action install" in cmd
    assert "--product Jetson" in cmd
    assert "--version 6.1" in cmd
    assert "--target JETSON_ORIN_NX_TARGETS" in cmd
    assert "--target-os Linux" in cmd


def test_additional_sdks_quoted():
    cfg = InstallConfig(
        product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
        additional_sdks=["DeepStream 7.0", "Isaac ROS 3.2"],
    )
    cmd = generate_command(cfg)
    assert "--additional-sdk 'DeepStream 7.0'" in cmd
    assert "--additional-sdk 'Isaac ROS 3.2'" in cmd


def test_flash_flag():
    cfg = InstallConfig(product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS", flash=True)
    cmd = generate_command(cfg)
    assert "--flash" in cmd
