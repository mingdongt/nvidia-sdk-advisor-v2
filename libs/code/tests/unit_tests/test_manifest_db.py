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


def _doca_like(edition: str, comp_id: str, revision: int = 0) -> dict[str, Any]:
    """A minimal DOCA 1.5.1 manifest with a given edition + one component.

    Two of these with different editions collide on ``product:version`` under the
    old keying scheme; the fix must keep them as two distinct releases.
    """
    return {
        "information": {
            "release": {
                "productCategory": "DOCA",
                "releaseVersion": "1.5.1",
                "releaseEdition": edition,
                "releaseRevision": revision,
                "showInMainList": True,
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "sections": [{"id": "S1", "title": "DOCA", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "DOCA SDK",
                "installedOn": "host",
                "versions": [{"version": "1", "components": [{"id": comp_id}]}],
            }
        ],
        "components": {
            comp_id: {
                "id": comp_id,
                "name": comp_id,
                "version": "1.5.1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 10.0,
                        "downloadFiles": [],
                    }
                ],
            }
        },
    }


def test_release_edition_disambiguates_collision(tmp_path: Path) -> None:
    """Two manifests sharing product:version but differing in edition stay distinct."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_doca_base.json").write_text(
        json.dumps(_doca_like("", "C_BASE")), encoding="utf-8"
    )
    (src / "sdkml3_doca_md.json").write_text(
        json.dumps(_doca_like("MD", "C_MD")), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        releases = sorted(r["release_id"] for r in manifest_db.find_releases(con))
        assert releases == ["DOCA:1.5.1", "DOCA:1.5.1:MD"]
        editions = dict(
            con.execute("SELECT release_id, edition FROM release").fetchall()
        )
        assert editions["DOCA:1.5.1"] == ""
        assert editions["DOCA:1.5.1:MD"] == "MD"
        # Each edition keeps only its own component (no first-write-wins union).
        base = manifest_db.list_components(con, "DOCA:1.5.1")
        md = manifest_db.list_components(con, "DOCA:1.5.1:MD")
        assert [c["comp_id"] for c in base] == ["C_BASE"]
        assert [c["comp_id"] for c in md] == ["C_MD"]
    finally:
        con.close()


def test_duplicate_release_key_raises(tmp_path: Path) -> None:
    """A genuine duplicate release key (same product:version:edition) fails build."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_a.json").write_text(
        json.dumps(_doca_like("", "C_A")), encoding="utf-8"
    )
    (src / "sdkml3_b.json").write_text(
        json.dumps(_doca_like("", "C_B")), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate release"):
        manifest_db.build_manifest_db(src, tmp_path / "manifest.db")


def test_find_releases_exposes_edition_revision(tmp_path: Path) -> None:
    """find_releases surfaces edition/revision so callers needn't parse release_id."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_base.json").write_text(
        json.dumps(_doca_like("", "C_BASE", revision=0)), encoding="utf-8"
    )
    (src / "sdkml3_md.json").write_text(
        json.dumps(_doca_like("MD", "C_MD", revision=2)), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        by_id = {r["release_id"]: r for r in manifest_db.find_releases(con)}
        assert by_id["DOCA:1.5.1"]["edition"] == ""
        assert by_id["DOCA:1.5.1"]["revision"] == 0
        assert by_id["DOCA:1.5.1:MD"]["edition"] == "MD"
        assert by_id["DOCA:1.5.1:MD"]["revision"] == 2
    finally:
        con.close()


def _inverted_tiebreak_sdkml3() -> dict[str, Any]:
    """One component, two catch-all entries whose install/file size order disagree.

    entry0 installs 200 MB but its file is 10 B; entry1 installs 100 MB but its file
    is 20 B. footprint (ranks by install_mb) and build_plan (ranks by file size) must
    still pick the SAME platform entry, or a plan's files won't match its footprint.
    """
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "sections": [{"id": "S1", "title": "Drivers", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "Drivers",
                "installedOn": "target",
                "versions": [{"version": "1", "components": [{"id": "C_DRV"}]}],
            }
        ],
        "components": {
            "C_DRV": {
                "id": "C_DRV",
                "name": "Driver",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 200.0,
                        "downloadFiles": [
                            {"url": "http://x/a.deb", "fileName": "a.deb", "size": 10}
                        ],
                    },
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 100.0,
                        "downloadFiles": [
                            {"url": "http://x/b.deb", "fileName": "b.deb", "size": 20}
                        ],
                    },
                ],
            }
        },
    }


def test_footprint_and_build_plan_agree_on_tiebreak(tmp_path: Path) -> None:
    """Footprint and build_plan resolve a size tie to the same platform entry."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_inv.json").write_text(
        json.dumps(_inverted_tiebreak_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        fp = manifest_db.footprint(con, "Jetson:6.0", "ubuntu22.04", "x86_64")
        plan = manifest_db.build_plan_rows(
            con, "Jetson:6.0", "ubuntu22.04", "x86_64", ["C_DRV"]
        )
        # Tie-break on the later platform entry (plat_idx) so both agree on entry1.
        assert fp["install_mb"] == 100.0
        assert [r["file_name"] for r in plan] == ["b.deb"]
    finally:
        con.close()


def _sectionless_sdkml3() -> dict[str, Any]:
    """A manifest with NO ``sections`` (like the CUDA Toolkit / DOCA-on-BlueField).

    ``groups`` is the flattened ``[group_id, group_obj, ...]`` shape real manifests
    use. The component walk must still reach the group's components instead of
    dropping the whole product.
    """
    return {
        "information": {
            "release": {
                "productCategory": "CUDA",
                "releaseVersion": "12.1",
                "showInMainList": True,
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "groups": [
            "NV_CUDA_TARGET_GROUP",
            {
                "id": "NV_CUDA_TARGET_GROUP",
                "name": "CUDA",
                "groupType": "target",
                "installedOn": "target",
                "description": "CUDA Toolkit.",
                "versions": [
                    {
                        "version": "12.1",
                        "components": [{"id": "C_CUDA"}, {"id": "C_NPP"}],
                    }
                ],
            },
        ],
        "components": {
            "C_CUDA": {
                "id": "C_CUDA",
                "name": "CUDA Toolkit",
                "version": "12.1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 2000.0,
                        "downloadFiles": [],
                    }
                ],
            },
            "C_NPP": {"id": "C_NPP", "name": "NPP", "version": "12.1", "platforms": []},
        },
    }


def test_sectionless_manifest_ingests_components(tmp_path: Path) -> None:
    """A manifest without ``sections`` still has its group's components ingested."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_cuda.json").write_text(
        json.dumps(_sectionless_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        comps = sorted(
            c["comp_id"] for c in manifest_db.list_components(con, "CUDA:12.1")
        )
        assert comps == ["C_CUDA", "C_NPP"]
        # installed_on/group provenance still flows from the group.
        assert manifest_db.list_components(con, "CUDA:12.1", installed_on="target")
    finally:
        con.close()


def _alt_schema_sdkml3() -> dict[str, Any]:
    """The alternate sdkml3 shape used by the SDK Manager self-update manifest.

    ``groups`` is a dict keyed by id; the group lists its components directly under
    ``components`` (no ``versions`` wrapper); the component carries ``versions[]``
    (not ``platforms[]``) where each version is a platform with a STRING
    ``installSizeMB`` ("382M"); and the license is ``licenseId`` (singular).
    """
    return {
        "information": {
            "release": {
                "productCategory": "SDK Manager",
                "releaseVersion": "2.4.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "groups": {
            "G_SDKM": {
                "id": "G_SDKM",
                "name": "SDK Manager",
                "installedOn": "host",
                "description": "NVIDIA SDK Manager.",
                "components": [{"id": "NV_SDKM_APP_COMP", "version": "2.4.0"}],
            }
        },
        "components": {
            "NV_SDKM_APP_COMP": {
                "id": "NV_SDKM_APP_COMP",
                "name": "SDK Manager",
                "licenseId": "NV_SW_License",
                "versions": [
                    {
                        "version": "2.4.0",
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": "382M",
                        "downloadFiles": [
                            {
                                "url": "/Linux/sdkmanager.deb",
                                "fileName": "sdkmanager.deb",
                                "size": 89088620,
                            }
                        ],
                    }
                ],
            }
        },
        "licenses": {"NV_SW_License": {"name": "NVIDIA SW License"}},
    }


def test_alt_schema_dict_groups_and_versions_ingested(tmp_path: Path) -> None:
    """The dict-groups / direct-components / versions-as-platforms shape is parsed."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_alt.json").write_text(
        json.dumps(_alt_schema_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        comps = manifest_db.list_components(con, "SDK Manager:2.4.0")
        assert [c["comp_id"] for c in comps] == ["NV_SDKM_APP_COMP"]
        assert comps[0]["installed_on"] == "host"
        # "382M" string parsed to 382 MB; download bytes summed from the file.
        fp = manifest_db.footprint(con, "SDK Manager:2.4.0", "ubuntu22.04", "x86_64")
        assert fp["components"] == 1
        assert fp["install_mb"] == 382.0
        assert fp["download_b"] == 89088620
        plan = manifest_db.build_plan_rows(
            con, "SDK Manager:2.4.0", "ubuntu22.04", "x86_64", ["NV_SDKM_APP_COMP"]
        )
        assert [r["file_name"] for r in plan] == ["sdkmanager.deb"]
        detail = manifest_db.component_detail(
            con, "SDK Manager:2.4.0", "NV_SDKM_APP_COMP"
        )
        assert detail is not None
        assert detail["license_id"] == "NV_SW_License"
    finally:
        con.close()


def test_zero_component_release_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A release that ingests 0 components is surfaced as a warning, not silence."""
    src = tmp_path / "src"
    src.mkdir()
    empty = {
        "information": {
            "release": {
                "productCategory": "SDK Manager",
                "releaseVersion": "2.4.0",
                "showInMainList": True,
            }
        },
        "groups": [],
        "components": {},
    }
    (src / "sdkml3_empty.json").write_text(json.dumps(empty), encoding="utf-8")
    with caplog.at_level("WARNING"):
        manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    assert any(
        "0 component" in rec.message and "SDK Manager:2.4.0" in rec.message
        for rec in caplog.records
    )


def test_host_os_uses_host_groups_not_targets(tmp_path: Path) -> None:
    """release_host_os comes from hostGroups; target-only OSes (windows) aren't hosts.

    JetPack lists windows under targetGroups but only Ubuntu under hostGroups; a
    Windows machine cannot host the install, so find_releases(host_os='windows11')
    must not return the release.
    """
    doc = {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.2",
                "showInMainList": True,
                "architectures": ["x86_64"],
                "hostOperatingSystemsSupportFor": {
                    "hostGroups": ["ubuntu22.04"],
                    "targetGroups": ["ubuntu22.04", "windows11"],
                },
            }
        },
        "sections": [{"id": "S1", "title": "X", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "G",
                "installedOn": "host",
                "versions": [{"version": "1", "components": [{"id": "C"}]}],
            }
        ],
        "components": {"C": {"id": "C", "name": "C", "version": "1", "platforms": []}},
    }
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_jp.json").write_text(json.dumps(doc), encoding="utf-8")
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        hosts = sorted(r[0] for r in con.execute("SELECT host_os FROM release_host_os"))
        assert hosts == ["ubuntu22.04"]
        assert manifest_db.find_releases(con, host_os="windows11") == []
        assert manifest_db.find_releases(con, host_os="ubuntu22.04") != []
    finally:
        con.close()


def test_host_os_falls_back_to_targets_when_no_host_groups(tmp_path: Path) -> None:
    """When a manifest has no hostGroups (CUDA Toolkit), targetGroups are used."""
    doc = {
        "information": {
            "release": {
                "productCategory": "CUDA",
                "releaseVersion": "12.1",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"targetGroups": ["ubuntu22.04"]},
            }
        },
        "sections": [{"id": "S1", "title": "X", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "G",
                "installedOn": "host",
                "versions": [{"version": "1", "components": [{"id": "C"}]}],
            }
        ],
        "components": {"C": {"id": "C", "name": "C", "version": "1", "platforms": []}},
    }
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_cuda.json").write_text(json.dumps(doc), encoding="utf-8")
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        assert manifest_db.find_releases(con, host_os="ubuntu22.04") != []
    finally:
        con.close()


def _dep_typed_sdkml3() -> dict[str, Any]:
    """A manifest whose component has one required and one optional dependency."""
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "sections": [{"id": "S1", "title": "X", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "G",
                "installedOn": "host",
                "versions": [
                    {
                        "version": "1",
                        "components": [
                            {"id": "C_APP"},
                            {"id": "C_LIB"},
                            {"id": "C_EXTRA"},
                        ],
                    }
                ],
            }
        ],
        "components": {
            "C_APP": {
                "id": "C_APP",
                "name": "App",
                "version": "1",
                "dependencies": [
                    {"type": "required", "id": "C_LIB"},
                    {"type": "optional", "id": "C_EXTRA"},
                ],
                "platforms": [],
            },
            "C_LIB": {"id": "C_LIB", "name": "Lib", "version": "1", "platforms": []},
            "C_EXTRA": {
                "id": "C_EXTRA",
                "name": "Extra",
                "version": "1",
                "platforms": [],
            },
        },
    }


@pytest.fixture
def dep_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db from the typed-dependency fixture."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_dep.json").write_text(
        json.dumps(_dep_typed_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_resolve_deps_excludes_optional_by_default(dep_db: sqlite3.Connection) -> None:
    """resolve_deps follows only required edges unless optional is requested."""
    assert manifest_db.resolve_deps(dep_db, "Jetson:6.0", ["C_APP"]) == [
        "C_APP",
        "C_LIB",
    ]
    assert manifest_db.resolve_deps(
        dep_db, "Jetson:6.0", ["C_APP"], include_optional=True
    ) == ["C_APP", "C_EXTRA", "C_LIB"]


def test_component_detail_separates_optional_deps(dep_db: sqlite3.Connection) -> None:
    """component_detail reports required and optional dependencies separately."""
    d = manifest_db.component_detail(dep_db, "Jetson:6.0", "C_APP")
    assert d is not None
    assert d["depends_on"] == ["C_LIB"]
    assert d["optional_depends_on"] == ["C_EXTRA"]


def _group_dep_sdkml3() -> dict[str, Any]:
    """A component whose dependency targets a GROUP id, not a component id.

    SDK Manager lets a component depend on a whole group; the closure must expand
    that to the group's member components, never emit the group id itself (which
    matches no component row) nor drop the members.
    """
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
            }
        },
        "sections": [{"id": "S1", "title": "X", "groups": ["G_MAIN", "G_EXTRA"]}],
        "groups": [
            {
                "id": "G_MAIN",
                "name": "Main",
                "installedOn": "target",
                "versions": [{"version": "1", "components": [{"id": "C_APP"}]}],
            },
            {
                "id": "G_EXTRA",
                "name": "Extra Setup",
                "installedOn": "target",
                "versions": [
                    {"version": "1", "components": [{"id": "C_M1"}, {"id": "C_M2"}]}
                ],
            },
        ],
        "components": {
            "C_APP": {
                "id": "C_APP",
                "name": "App",
                "version": "1",
                "dependencies": [{"type": "required", "id": "G_EXTRA"}],
                "platforms": [],
            },
            "C_M1": {"id": "C_M1", "name": "M1", "version": "1", "platforms": []},
            "C_M2": {"id": "C_M2", "name": "M2", "version": "1", "platforms": []},
        },
    }


def test_resolve_deps_expands_group_dependency(tmp_path: Path) -> None:
    """A dependency on a group id resolves to the group's member components."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_gd.json").write_text(
        json.dumps(_group_dep_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        closure = manifest_db.resolve_deps(con, "Jetson:6.0", ["C_APP"])
        assert closure == ["C_APP", "C_M1", "C_M2"]
        # The group id itself must never appear as a (bogus) required component.
        assert "G_EXTRA" not in closure
        # component_detail surfaces the expanded members, not the group id.
        detail = manifest_db.component_detail(con, "Jetson:6.0", "C_APP")
        assert detail is not None
        assert sorted(detail["depends_on"]) == ["C_M1", "C_M2"]
    finally:
        con.close()


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


def _double_catchall_sdkml3() -> dict[str, Any]:
    """A component with TWO catch-all platform entries on the same (os, arch).

    Both entries apply to any board (no supportedHardware), e.g. two revisions of
    the same payload. SDK Manager installs exactly one; build_plan must not list
    both files just because they share an (empty) board signature.
    """
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
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


def test_build_plan_does_not_double_list_same_signature(tmp_path: Path) -> None:
    """Two catch-all entries on one (os, arch) yield ONE file, not both."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_dc.json").write_text(
        json.dumps(_double_catchall_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    try:
        rows = manifest_db.build_plan_rows(
            con, "Jetson:6.0", "ubuntu22.04", "x86_64", ["C_DRV"]
        )
        assert [r["file_name"] for r in rows] == ["b.deb"]
    finally:
        con.close()


def _board_specific_only_sdkml3() -> dict[str, Any]:
    """A component that only ships a payload for BOARD_Y (no catch-all)."""
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
                "supportedHardware": {
                    "seriesIds": ["BOARD_Y_TARGETS", "BOARD_X_TARGETS"]
                },
            }
        },
        "sections": [{"id": "S1", "title": "Drivers", "groups": ["G_DRV"]}],
        "groups": [
            {
                "id": "G_DRV",
                "name": "Drivers",
                "installedOn": "target",
                "versions": [{"version": "1", "components": [{"id": "C_Y"}]}],
            }
        ],
        "components": {
            "C_Y": {
                "id": "C_Y",
                "name": "Board-Y Driver",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 100.0,
                        "supportedHardware": {"seriesIds": ["BOARD_Y_TARGETS"]},
                        "downloadFiles": [
                            {"url": "http://x/y.deb", "fileName": "y.deb", "size": 10}
                        ],
                    }
                ],
            }
        },
    }


@pytest.fixture
def board_specific_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db whose only component is BOARD_Y-specific."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_by.json").write_text(
        json.dumps(_board_specific_only_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_footprint_drops_component_excluded_from_board(
    board_specific_db: sqlite3.Connection,
) -> None:
    """A component with no payload for the target board is not counted at all."""
    on_x = manifest_db.footprint(
        board_specific_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        board="BOARD_X_TARGETS",
    )
    assert on_x["components"] == 0
    assert on_x["install_mb"] == 0
    on_y = manifest_db.footprint(
        board_specific_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        board="BOARD_Y_TARGETS",
    )
    assert on_y["components"] == 1
    assert on_y["install_mb"] == 100.0


def test_build_plan_drops_component_excluded_from_board(
    board_specific_db: sqlite3.Connection,
) -> None:
    """build_plan lists no files for a board the component does not ship for."""
    rows = manifest_db.build_plan_rows(
        board_specific_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        ["C_Y"],
        board="BOARD_X_TARGETS",
    )
    assert rows == []


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


def _board_mixed_sdkml3() -> dict[str, Any]:
    """Two components: one BOARD_A-specific, one catch-all (any board)."""
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
                "supportedHardware": {
                    "seriesIds": ["BOARD_A_TARGETS", "BOARD_B_TARGETS"]
                },
            }
        },
        "sections": [{"id": "S1", "title": "Drivers", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "Drivers",
                "installedOn": "target",
                "versions": [
                    {"version": "1", "components": [{"id": "C_A"}, {"id": "C_UNIV"}]}
                ],
            }
        ],
        "components": {
            "C_A": {
                "id": "C_A",
                "name": "Board A Driver",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 50.0,
                        "supportedHardware": {"seriesIds": ["BOARD_A_TARGETS"]},
                        "downloadFiles": [],
                    }
                ],
            },
            "C_UNIV": {
                "id": "C_UNIV",
                "name": "Universal Driver",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 60.0,
                        "downloadFiles": [],
                    }
                ],
            },
        },
    }


@pytest.fixture
def mixed_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db with one board-specific and one catch-all component."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_mixed.json").write_text(
        json.dumps(_board_mixed_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_list_components_filters_by_board(mixed_db: sqlite3.Connection) -> None:
    """list_components(board=) hides components with no payload for that board."""
    all_ids = sorted(
        c["comp_id"] for c in manifest_db.list_components(mixed_db, "Jetson:6.0")
    )
    assert all_ids == ["C_A", "C_UNIV"]
    on_a = sorted(
        c["comp_id"]
        for c in manifest_db.list_components(
            mixed_db, "Jetson:6.0", board="BOARD_A_TARGETS"
        )
    )
    assert on_a == ["C_A", "C_UNIV"]
    on_b = sorted(
        c["comp_id"]
        for c in manifest_db.list_components(
            mixed_db, "Jetson:6.0", board="BOARD_B_TARGETS"
        )
    )
    assert on_b == ["C_UNIV"]  # C_A is BOARD_A-specific


def test_search_substring_filters_by_board(mixed_db: sqlite3.Connection) -> None:
    """search_substring(board=) drops components that don't ship for the board."""
    both = sorted(
        h["comp_id"] for h in manifest_db.search_substring(mixed_db, "Driver")
    )
    assert both == ["C_A", "C_UNIV"]
    on_b = sorted(
        h["comp_id"]
        for h in manifest_db.search_substring(
            mixed_db, "Driver", board="BOARD_B_TARGETS"
        )
    )
    assert on_b == ["C_UNIV"]


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


def _two_series_split_sdkml3() -> dict[str, Any]:
    """A release spanning TWO board series, exercising the board-resolution rules.

    - ``C_SPLIT`` ships an A-only build and a B-only build — NO variant covers both
      boards, so with no board the size/file is genuinely undecidable.
    - ``C_BOTH`` ships one variant scoped to BOTH series — release-wide, so it is sized
      without a board.
    - ``C_PLAIN`` is a true catch-all (no supportedHardware).
    - ``C_DEV`` is a device-only variant (``deviceIds``); a series board cannot resolve
      it, so it installs on no series board. Used to assert ``deviceIds`` is ingested.

    Mirrors the real shape where a release spans several Jetson families and the
    series board (not a device id) is what the agent can pass.
    """
    return {
        "information": {
            "release": {
                "productCategory": "Jetson",
                "releaseVersion": "6.0",
                "showInMainList": True,
                "hostOperatingSystemsSupportFor": {"hostGroups": ["ubuntu22.04"]},
                "supportedHardware": {
                    "seriesIds": ["BOARD_A_TARGETS", "BOARD_B_TARGETS"]
                },
            }
        },
        "sections": [{"id": "S1", "title": "Drivers", "groups": ["G"]}],
        "groups": [
            {
                "id": "G",
                "name": "Drivers",
                "installedOn": "target",
                "versions": [
                    {
                        "version": "1",
                        "components": [
                            {"id": "C_SPLIT"},
                            {"id": "C_BOTH"},
                            {"id": "C_PLAIN"},
                            {"id": "C_DEV"},
                        ],
                    }
                ],
            }
        ],
        "components": {
            "C_SPLIT": {
                "id": "C_SPLIT",
                "name": "Split",
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
                        "supportedHardware": {"seriesIds": ["BOARD_B_TARGETS"]},
                        "downloadFiles": [
                            {"url": "http://x/b.deb", "fileName": "b.deb", "size": 20}
                        ],
                    },
                ],
            },
            "C_BOTH": {
                "id": "C_BOTH",
                "name": "Both",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 70.0,
                        "supportedHardware": {
                            "seriesIds": ["BOARD_A_TARGETS", "BOARD_B_TARGETS"]
                        },
                        "downloadFiles": [
                            {"url": "http://x/o.deb", "fileName": "o.deb", "size": 7}
                        ],
                    }
                ],
            },
            "C_PLAIN": {
                "id": "C_PLAIN",
                "name": "Common",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 50.0,
                        "downloadFiles": [
                            {"url": "http://x/p.deb", "fileName": "p.deb", "size": 5}
                        ],
                    }
                ],
            },
            "C_DEV": {
                "id": "C_DEV",
                "name": "DevKit",
                "version": "1",
                "platforms": [
                    {
                        "operatingSystems": ["ubuntu22.04"],
                        "architectures": ["x86_64"],
                        "installSizeMB": 30.0,
                        "supportedHardware": {"deviceIds": ["BOARD_A_8GB_DEVKIT"]},
                        "downloadFiles": [
                            {"url": "http://x/d.deb", "fileName": "d.deb", "size": 3}
                        ],
                    }
                ],
            },
        },
    }


@pytest.fixture
def split_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a manifest.db from the two-series-split fixture and yield a connection."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sdkml3_split.json").write_text(
        json.dumps(_two_series_split_sdkml3()), encoding="utf-8"
    )
    manifest_db.build_manifest_db(src, tmp_path / "manifest.db")
    con = manifest_db.connect(tmp_path / "manifest.db")
    yield con
    con.close()


def test_device_ids_are_ingested(split_db: sqlite3.Connection) -> None:
    """supportedHardware.deviceIds is stored in board_devices, not dropped.

    Without this a device-only entry is indistinguishable from a catch-all.
    """
    dev = split_db.execute(
        "SELECT board_series, board_devices FROM component_platform"
        " WHERE comp_uid = 'Jetson:6.0:C_DEV'"
    ).fetchone()
    assert dev["board_series"] == ""
    assert dev["board_devices"] == "BOARD_A_8GB_DEVKIT"
    a = split_db.execute(
        "SELECT board_series, board_devices FROM component_platform"
        " WHERE comp_uid = 'Jetson:6.0:C_SPLIT' AND install_mb = 100.0"
    ).fetchone()
    assert a["board_series"] == "BOARD_A_TARGETS"
    assert a["board_devices"] == ""


def test_footprint_flags_no_universal_component_when_board_unknown(
    split_db: sqlite3.Connection,
) -> None:
    """With no board, a component with no release-wide variant is not guessed.

    It is excluded from the totals and surfaced via board_dependent / needs_board,
    while a release-wide variant (C_BOTH) and a catch-all (C_PLAIN) stay counted.
    A device-only component (C_DEV) installs on no series board, so it is dropped.
    """
    fp = manifest_db.footprint(split_db, "Jetson:6.0", "ubuntu22.04", "x86_64")
    assert fp["needs_board"] is True
    assert fp["board_dependent"] == ["C_SPLIT"]
    assert fp["components"] == 2  # C_BOTH + C_PLAIN
    assert fp["install_mb"] == 120.0  # 70 + 50


def test_footprint_resolves_precisely_when_board_given(
    split_db: sqlite3.Connection,
) -> None:
    """With the board, each component resolves to its board payload; nothing flagged."""
    fp = manifest_db.footprint(
        split_db, "Jetson:6.0", "ubuntu22.04", "x86_64", board="BOARD_A_TARGETS"
    )
    assert fp["needs_board"] is False
    assert fp["board_dependent"] == []
    assert fp["components"] == 3  # C_SPLIT(A) + C_BOTH + C_PLAIN; C_DEV is device-only
    assert fp["install_mb"] == 220.0  # 100 + 70 + 50


def test_build_plan_skips_undecidable_files_when_board_unknown(
    split_db: sqlite3.Connection,
) -> None:
    """build_plan omits a no-universal component's file when the board is unknown.

    With no board the undecidable component's file is omitted; with the board every
    component resolves precisely.
    """
    rows = manifest_db.build_plan_rows(
        split_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        ["C_SPLIT", "C_BOTH", "C_PLAIN", "C_DEV"],
    )
    assert sorted(r["file_name"] for r in rows) == ["o.deb", "p.deb"]
    rows_a = manifest_db.build_plan_rows(
        split_db,
        "Jetson:6.0",
        "ubuntu22.04",
        "x86_64",
        ["C_SPLIT", "C_BOTH", "C_PLAIN", "C_DEV"],
        board="BOARD_A_TARGETS",
    )
    assert sorted(r["file_name"] for r in rows_a) == ["a.deb", "o.deb", "p.deb"]
