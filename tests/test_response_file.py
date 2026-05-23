import configparser
from pathlib import Path
import pytest

from src.models import InstallConfig
from src.response_file import generate_response_file, validate_against_official_sample


_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "data" / "response_templates"


def test_generated_has_three_sections():
    cfg = InstallConfig(
        product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
        additional_sdks=["DeepStream 7.0"], flash=True,
    )
    ini = generate_response_file(cfg)
    parser = configparser.ConfigParser(strict=False)
    parser.read_string(ini)
    assert "client_arguments" in parser.sections()
    assert "pre-flash-settings" in parser.sections()
    assert "post-flash-settings" in parser.sections()


def test_generated_uses_array_syntax_for_additional_sdks():
    cfg = InstallConfig(
        product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS",
        additional_sdks=["DeepStream 7.0", "Isaac ROS 3.2"],
    )
    ini = generate_response_file(cfg)
    assert "additional-sdk[] = DeepStream 7.0" in ini
    assert "additional-sdk[] = Isaac ROS 3.2" in ini


def test_generated_matches_official_template_structure():
    """All section + key names in our output must also appear in NVIDIA's sample."""
    cfg = InstallConfig(
        product="Jetson", version="7.0", target="JETSON_AGX_THOR_TARGETS",
        additional_sdks=["DeepStream"], flash=True,
    )
    ini = generate_response_file(cfg)
    result = validate_against_official_sample(ini, "Jetson", _TEMPLATE_DIR)
    assert result["matches"] is True, f"Unknown keys: {result['extra_keys']}, missing sections: {result['missing_sections']}"


def test_action_install_is_default():
    cfg = InstallConfig(product="Jetson", version="6.1", target="JETSON_ORIN_NX_TARGETS")
    ini = generate_response_file(cfg)
    assert "action = install" in ini
