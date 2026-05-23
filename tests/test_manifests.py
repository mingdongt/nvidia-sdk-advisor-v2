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


def test_lookup_target_id_canonical_name():
    kb = KnowledgeBase(_MANIFEST_DIR)
    r = kb.lookup_target_id("Jetson Orin Nano 8GB")
    assert r is not None
    assert r["target_id"] == "JETSON_ORIN_NANO_TARGETS"
    assert r["matched_on"] in ("canonical", "alias", "fuzzy")


def test_lookup_target_id_alias():
    kb = KnowledgeBase(_MANIFEST_DIR)
    r = kb.lookup_target_id("orin nx")
    assert r is not None
    assert r["target_id"] == "JETSON_ORIN_NX_TARGETS"


def test_lookup_target_id_unknown_returns_none():
    kb = KnowledgeBase(_MANIFEST_DIR)
    assert kb.lookup_target_id("Mars Rover Edge") is None


def test_validate_combo_unknown_version():
    kb = KnowledgeBase(_MANIFEST_DIR)
    r = kb.validate_combo("Jetson", "999.999")
    assert r["valid"] is False
    assert "not found" in r["reason"].lower()


def test_validate_combo_unsupported_target():
    kb = KnowledgeBase(_MANIFEST_DIR)
    # JetPack 7.1 supports JETSON_AGX_THOR_TARGETS only (verified earlier)
    r = kb.validate_combo("Jetson", "7.1", target="JETSON_ORIN_NANO_TARGETS")
    assert r["valid"] is False
    assert "supportedHardware" in r["reason"] or "supported" in r["reason"].lower()


def test_validate_combo_valid():
    kb = KnowledgeBase(_MANIFEST_DIR)
    r = kb.validate_combo("Jetson", "6.1", target="JETSON_ORIN_NX_TARGETS")
    assert r["valid"] is True
