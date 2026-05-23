from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile

from ingest.fetch_ngc_catalog import fetch_container_metadata, save_catalog


def test_fetch_one_container_metadata():
    fake_resp = MagicMock(
        status_code=200,
        json=lambda: {
            "displayName": "DeepStream 7.0 for L4T",
            "description": "DeepStream SDK 7.0 for NVIDIA Jetson",
            "labels": {"com.nvidia.jetpack.version": "6.1+"},
            "imageInfo": {"compressedSize": 4521000000},
        },
    )
    with patch("ingest.fetch_ngc_catalog.requests.get", return_value=fake_resp):
        result = fetch_container_metadata("nvidia/deepstream-l4t")
    assert result["name"] == "nvidia/deepstream-l4t"
    assert result["display_name"] == "DeepStream 7.0 for L4T"
    assert "jetpack" in str(result["labels"]).lower()


def test_save_catalog_writes_jsonl():
    records = [{"name": "x", "display_name": "X"}, {"name": "y", "display_name": "Y"}]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "containers.jsonl"
        save_catalog(records, out_path)
        lines = out_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["name"] == "x"
