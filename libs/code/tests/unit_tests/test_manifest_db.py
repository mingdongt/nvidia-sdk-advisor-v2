"""Tests for the manifest database builder and read helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from deepagents_code import manifest_db

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


def _tiny_sdkml3() -> dict[str, Any]:
    """A minimal but faithful sdkml3 document (one release, two components)."""
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "title": "JetPack 9.9",
                "releaseVersion": "9.9",
                "targetOS": "Linux",
                "minSDKMVer": "2.4.0",
                "showInMainList": True,
                "serverType": ["DEVZONE"],
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {
                    "targetGroups": ["ubuntu22.04", "windows11"]
                },
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
                "versions": [
                    {"version": "1", "components": [{"id": "C_CUDA"}, {"id": "C_DRV"}]}
                ],
            }
        ],
        "components": {
            "C_CUDA": {
                "id": "C_CUDA",
                "name": "CUDA on Host",
                "version": "12.6",
                "licenseIds": ["L1"],
                "dependencies": [{"id": "C_DRV"}],
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
                                "checksum": "abc",
                                "checksumType": "md5",
                            }
                        ],
                    }
                ],
            },
            "C_DRV": {
                "id": "C_DRV",
                "name": "Driver",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 50.0,
                        "downloadFiles": [],
                    }
                ],
            },
        },
        "licenses": {"L1": {"name": "Test CUDA EULA"}},
    }


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db from the tiny fixture and yield an open connection."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_test.json").write_text(json.dumps(_tiny_sdkml3()), encoding="utf-8")
    counts = manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    assert counts["release"] == 1
    assert counts["component"] == 2
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_build_materializes_all_tables(db: sqlite3.Connection) -> None:
    """The parser populates compat junctions, components, platforms and licenses."""
    assert db.execute("SELECT COUNT(*) FROM release_host_os").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM release_board").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM component_platform").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM component_file").fetchone()[0] == 1
    assert (
        db.execute("SELECT name FROM license WHERE license_id='L1'").fetchone()[0]
        == "Test CUDA EULA"
    )


def test_find_releases_filters_by_compat(db: sqlite3.Connection) -> None:
    """A matching host+board yields the release; a non-matching board yields nothing."""
    assert [
        r["release_id"]
        for r in manifest_db.find_releases(db, board="TEST_BOARD_TARGETS")
    ] == ["Jetson:9.9"]
    assert manifest_db.find_releases(db, host_os="ubuntu22.04") != []
    assert manifest_db.find_releases(db, board="NOPE_TARGETS") == []


def test_component_detail_picks_platform_size(db: sqlite3.Connection) -> None:
    """component_detail resolves by name and filters platforms by host_os/arch."""
    d = manifest_db.component_detail(
        db, "Jetson:9.9", "CUDA on Host", host_os="ubuntu22.04", arch="x86_64"
    )
    assert d is not None
    assert d["installed_on"] == "host"
    assert d["description"].startswith("CUDA toolkit")
    assert d["platforms"] == [
        {"os": "ubuntu22.04", "arch": "x86_64", "install_mb": 1000.0, "download_b": 500}
    ]
    assert d["depends_on"] == ["C_DRV"]


def test_footprint_sums_install_size(db: sqlite3.Connection) -> None:
    """Footprint sums install_mb across components for the given host/arch."""
    fp = manifest_db.footprint(db, "Jetson:9.9", "ubuntu22.04", "x86_64")
    assert fp["components"] == 2
    assert fp["install_mb"] == 1050.0


def test_resolve_deps_closure(db: sqlite3.Connection) -> None:
    """resolve_deps returns the selection plus its transitive dependencies."""
    assert manifest_db.resolve_deps(db, "Jetson:9.9", ["C_CUDA"]) == ["C_CUDA", "C_DRV"]


def test_search_substring_matches_description(db: sqlite3.Connection) -> None:
    """Substring search matches on description as well as name."""
    hits = manifest_db.search_substring(db, "GPU-accelerated")
    assert any(h["comp_id"] == "C_CUDA" for h in hits)


def test_build_plan_rows_returns_files(db: sqlite3.Connection) -> None:
    """build_plan_rows returns the download files for the selected components."""
    rows = manifest_db.build_plan_rows(
        db, "Jetson:9.9", "ubuntu22.04", "x86_64", ["C_CUDA"]
    )
    assert rows == [
        {
            "comp_id": "C_CUDA",
            "name": "CUDA on Host",
            "file_name": "cuda.deb",
            "url": "http://example.com/cuda.deb",
            "size": 500,
            "checksum": "abc",
            "checksum_type": "md5",
        }
    ]


def _board_split_sdkml3() -> dict[str, Any]:
    """An sdkml3 whose one component ships a different payload per board.

    On the same (os, arch) the driver has two ``platforms[]`` entries:
    a board-specific one (BOARD_A only, 100 MB / a.deb) and a catch-all one
    (any other board, 150 MB / b.deb). SDK Manager installs exactly one of
    them for a given board — never the sum.
    """
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {"targetGroups": ["ubuntu22.04"]},
                "supportedHardware": {
                    "seriesIds": ["BOARD_A_TARGETS", "BOARD_B_TARGETS"]
                },
            }
        },
        "sections": [{"id": "S1", "title": "Drivers", "groups": ["G_DRV"]}],
        "groups": [
            {
                "id": "G_DRV",
                "name": "Drivers",
                "installedOn": "target",
                "versions": [{"version": "1", "components": [{"id": "C_DRV"}]}],
            }
        ],
        "components": {
            "C_DRV": {
                "id": "C_DRV",
                "name": "L4T Drivers",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 100.0,
                        "supportedHardware": {"seriesIds": ["BOARD_A_TARGETS"]},
                        "downloadFiles": [
                            {"url": "http://x/a.deb", "fileName": "a.deb", "size": 10}
                        ],
                    },
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 150.0,
                        "downloadFiles": [
                            {"url": "http://x/b.deb", "fileName": "b.deb", "size": 20}
                        ],
                    },
                ],
            }
        },
    }


@pytest.fixture
def board_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db from the board-split fixture and yield a connection."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_board.json").write_text(
        json.dumps(_board_split_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_footprint_picks_one_entry_per_board(board_db: sqlite3.Connection) -> None:
    """Footprint selects the board-appropriate payload, never the sum of both."""
    on_a = manifest_db.footprint(
        board_db, "Jetson:6.0", "ubuntu22.04", "x86_64", board="BOARD_A_TARGETS"
    )
    # BOARD_A matches the board-specific entry (100 MB / 10 B), not 100+150.
    assert on_a["components"] == 1
    assert on_a["install_mb"] == 100.0
    assert on_a["download_b"] == 10

    on_b = manifest_db.footprint(
        board_db, "Jetson:6.0", "ubuntu22.04", "x86_64", board="BOARD_B_TARGETS"
    )
    # BOARD_B is not in the specific entry's series -> only the catch-all applies.
    assert on_b["install_mb"] == 150.0
    assert on_b["download_b"] == 20


def test_footprint_no_board_does_not_double_count(
    board_db: sqlite3.Connection,
) -> None:
    """Without a board, footprint still picks one entry rather than summing both."""
    fp = manifest_db.footprint(board_db, "Jetson:6.0", "ubuntu22.04", "x86_64")
    assert fp["components"] == 1
    assert fp["install_mb"] == 150.0  # catch-all preferred when board is unknown


def test_build_plan_rows_picks_board_payload(board_db: sqlite3.Connection) -> None:
    """build_plan lists only the selected board's file, not every variant."""
    rows = manifest_db.build_plan_rows(
        board_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        ["C_DRV"],
        board="BOARD_A_TARGETS",
    )
    assert [r["file_name"] for r in rows] == ["a.deb"]
