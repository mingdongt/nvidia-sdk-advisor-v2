"""Tests for the read-only manifest query tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from deepagents_code import manifest_db, manifest_tools

if TYPE_CHECKING:
    from pathlib import Path


def _sdkml3() -> dict[str, Any]:
    """A minimal sdkml3 document with one host component."""
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "title": "JetPack 9.9",
                "releaseVersion": "9.9",
                "targetOS": "Linux",
                "showInMainList": True,
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {"targetGroups": ["ubuntu22.04"]},
                "supportedHardware": {"seriesIds": ["TEST_BOARD_TARGETS"]},
            }
        },
        "sections": [
            {"id": "S1", "title": "Host SDK Components", "groups": ["G_CUDA"]}
        ],
        "groups": [
            {
                "id": "G_CUDA",
                "name": "CUDA",
                "description": "CUDA toolkit for GPU-accelerated development.",
                "installedOn": "host",
                "versions": [{"version": "1", "components": [{"id": "C_CUDA"}]}],
            }
        ],
        "components": {
            "C_CUDA": {
                "id": "C_CUDA",
                "name": "CUDA on Host",
                "version": "12.6",
                "licenseIds": ["L1"],
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 1000.0,
                        "downloadFiles": [
                            {
                                "url": "http://example.com/cuda.deb",
                                "fileName": "cuda.deb",
                                "size": 500,
                            }
                        ],
                    }
                ],
            }
        },
        "licenses": {"L1": {"name": "Test CUDA EULA"}},
    }


@pytest.fixture
def manifest_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build a manifest.db and point the tools at it via DEEPAGENTS_MANIFEST_DB."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_test.json").write_text(json.dumps(_sdkml3()), encoding="utf-8")
    db_path = tmp_path / "manifest.db"
    manifest_db.build_manifest_db(src, db_path)
    monkeypatch.setenv("DEEPAGENTS_MANIFEST_DB", str(db_path))


@pytest.mark.usefixtures("manifest_env")
def test_find_releases_returns_release() -> None:
    """find_releases returns matching releases as a dict."""
    result = manifest_tools.find_releases(product="Jetson", host_os="ubuntu22.04")
    assert [r["release_id"] for r in result["releases"]] == ["Jetson:9.9"]


@pytest.mark.usefixtures("manifest_env")
def test_list_components_filters_install_side() -> None:
    """list_components returns the host component."""
    result = manifest_tools.list_components("Jetson:9.9", installed_on="host")
    assert result["components"][0]["name"] == "CUDA on Host"


@pytest.mark.usefixtures("manifest_env")
def test_footprint_sums_size() -> None:
    """Footprint returns summed install size for the host/arch."""
    result = manifest_tools.footprint("Jetson:9.9", "ubuntu22.04", "x86_64")
    assert result["install_mb"] == 1000.0


@pytest.mark.usefixtures("manifest_env")
def test_search_components_substring_fallback() -> None:
    """With no embedder, search_components falls back to substring and still matches."""
    result = manifest_tools.search_components("GPU-accelerated", product="Jetson")
    assert any(m["comp_id"] == "C_CUDA" for m in result["matches"])


@pytest.mark.usefixtures("manifest_env")
def test_component_detail_found_and_missing() -> None:
    """component_detail returns the record, or an error dict when absent."""
    found = manifest_tools.component_detail(
        "Jetson:9.9", "CUDA on Host", host_os="ubuntu22.04", arch="x86_64"
    )
    assert found["version"] == "12.6"
    missing = manifest_tools.component_detail("Jetson:9.9", "Nonexistent")
    assert "error" in missing


@pytest.mark.usefixtures("manifest_env")
def test_build_plan_lists_files() -> None:
    """build_plan returns the footprint and the per-component download files."""
    plan = manifest_tools.build_plan("Jetson:9.9", "ubuntu22.04", "x86_64", ["C_CUDA"])
    assert plan["files"][0]["file_name"] == "cuda.deb"
    assert plan["footprint"]["components"] == 1


def test_missing_db_degrades_to_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured-but-absent manifest.db yields an error dict, not an exception."""
    monkeypatch.setenv("DEEPAGENTS_MANIFEST_DB", str(tmp_path / "nonexistent.db"))
    result = manifest_tools.find_releases(product="Jetson")
    assert "error" in result
    assert "manifest database not found" in result["error"]
