from pathlib import Path
from src.manifests import KnowledgeBase

_MANIFEST_DIR = Path(__file__).resolve().parents[1] / "data" / "manifests"


def test_load_products():
    kb = KnowledgeBase(_MANIFEST_DIR)
    products = kb.list_products()
    names = [p["name"] for p in products]
    assert "Jetson" in names
    assert len(products) >= 10  # at least 10 product entries across categories


def test_load_jetson_releases():
    kb = KnowledgeBase(_MANIFEST_DIR)
    releases = kb.list_releases("Jetson")
    versions = [r["releaseVersion"] for r in releases]
    assert "6.1" in versions
    assert "7.0" in versions or "7.1" in versions


def test_get_release_returns_supported_hardware():
    kb = KnowledgeBase(_MANIFEST_DIR)
    rel = kb.get_release("Jetson", "6.1")
    assert rel is not None
    series = rel.get("supportedHardware", {}).get("seriesIds", [])
    assert any("JETSON_ORIN" in s for s in series)


def test_list_hardware_jetson():
    kb = KnowledgeBase(_MANIFEST_DIR)
    hw = kb.list_hardware("Jetson")
    assert len(hw) >= 7
    ids = [h["id"] for h in hw]
    assert "JETSON_AGX_ORIN_TARGETS" in ids


def test_unknown_product_returns_empty():
    kb = KnowledgeBase(_MANIFEST_DIR)
    assert kb.list_releases("NotARealProduct") == []
    assert kb.get_release("Jetson", "999.999") is None
