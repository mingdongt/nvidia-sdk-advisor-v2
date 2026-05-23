from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile

from ingest.fetch_manifests import fetch_manifest_tree, MASTER_PRODUCT_URL, MASTER_HW_URL


def test_fetch_master_index_and_resolve_one_child():
    """Verifies recursive fetch logic: master index -> per-product manifest."""
    master = {"sdkml1_release": [
        {"productDisplayName": "Jetson", "releasesIndexURL": "../Jetson/Linux/sdkml2_jetson_linux.json"},
    ]}
    child = {"productDisplayName": "Jetson", "releases": [{"title": "JetPack 6.1"}]}

    responses = {
        MASTER_PRODUCT_URL: master,
        MASTER_HW_URL: {"sdkml1_release_hw": []},
        "https://developer.download.nvidia.com/sdkmanager/sdkm-config/Jetson/Linux/sdkml2_jetson_linux.json": child,
    }

    def fake_get(url, timeout):
        m = MagicMock()
        m.json.return_value = responses[url]
        m.raise_for_status = MagicMock()
        return m

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        with patch("ingest.fetch_manifests.requests.get", side_effect=fake_get):
            fetch_manifest_tree(out_dir)

        assert (out_dir / "sdkml1_repo.json").exists()
        assert (out_dir / "sdkml1_repo_hw.json").exists()
        jetson_file = out_dir / "Jetson" / "Linux" / "sdkml2_jetson_linux.json"
        assert jetson_file.exists()
        assert json.loads(jetson_file.read_text())["productDisplayName"] == "Jetson"


def test_fetch_handles_new_productCategories_schema():
    """The real NVIDIA CDN uses productCategories[].productLines[] not sdkml1_release."""
    master = {
        "productCategories": [
            {"categoryName": "Jetson", "productLines": [
                {"targetOS": "Linux", "releasesIndexURL": "../Jetson/Linux/sdkml2_jetson_linux.json"},
            ]},
        ],
    }
    child = {"productCategories": [{"categoryName": "Jetson"}]}

    responses = {
        MASTER_PRODUCT_URL: master,
        MASTER_HW_URL: {"families": []},
        "https://developer.download.nvidia.com/sdkmanager/sdkm-config/Jetson/Linux/sdkml2_jetson_linux.json": child,
    }

    def fake_get(url, timeout):
        m = MagicMock()
        m.json.return_value = responses[url]
        m.raise_for_status = MagicMock()
        return m

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        with patch("ingest.fetch_manifests.requests.get", side_effect=fake_get):
            fetch_manifest_tree(out_dir)
        jetson_file = out_dir / "Jetson" / "Linux" / "sdkml2_jetson_linux.json"
        assert jetson_file.exists()
