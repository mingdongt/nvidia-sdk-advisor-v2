"""Offline, grounded SQLite store for NVIDIA SDK Manager component manifests.

This module is self-contained (stdlib ``sqlite3`` only, no agent imports) so it can
be built and tested in isolation. It parses captured SDK Manager ``sdkml3_*.json``
component manifests into a normalized ``manifest.db`` whose facts (version / size /
compatibility / components) the agent tools read deterministically — the LLM never
invents them.

A captured ``sdkml3`` is self-describing: ``information.release`` carries the
release-level compatibility (host OS, target boards, architectures, min SDK Manager
version) and ``sections``/``groups``/``components`` carry the component detail
(descriptions, per-platform install size, download URLs/checksums, licenses). Parsing
the ``sdkml3`` files alone is therefore enough to populate every table.

Schema (4 logical groups):
  - compatibility:  product, release, release_host_os, release_board, release_arch
  - knowledge:      component, component_platform, dependency
  - execution:      component_file, license  (payload; only ``build_plan`` reads these)
The ``component`` primary key is ``release_id:comp_id`` because the same component id
(e.g. ``NV_CUDA_HOST_COMP``) appears across many releases with different versions/sizes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS product (
    product      TEXT PRIMARY KEY,
    target_os    TEXT,
    server_type  TEXT
);
CREATE TABLE IF NOT EXISTS release (
    release_id    TEXT PRIMARY KEY,
    product       TEXT,
    version       TEXT,
    title         TEXT,
    min_sdkm      TEXT,
    is_primary    INTEGER,
    comp_repo_url TEXT,
    src           TEXT
);
CREATE TABLE IF NOT EXISTS release_host_os (release_id TEXT, host_os TEXT);
CREATE TABLE IF NOT EXISTS release_board   (release_id TEXT, board   TEXT);
CREATE TABLE IF NOT EXISTS release_arch    (release_id TEXT, arch    TEXT);
CREATE TABLE IF NOT EXISTS component (
    comp_uid     TEXT PRIMARY KEY,
    release_id   TEXT,
    comp_id      TEXT,
    name         TEXT,
    version      TEXT,
    section      TEXT,
    group_name   TEXT,
    installed_on TEXT,
    description  TEXT,
    license_id   TEXT,
    use_cases    TEXT
);
CREATE TABLE IF NOT EXISTS component_platform (
    comp_uid         TEXT,
    os               TEXT,
    arch             TEXT,
    install_mb       REAL,
    download_b       INTEGER,
    board_series     TEXT,
    excluded_devices TEXT
);
CREATE TABLE IF NOT EXISTS dependency (comp_uid TEXT, depends_on_comp_id TEXT);
CREATE TABLE IF NOT EXISTS component_file (
    comp_uid         TEXT,
    os               TEXT,
    arch             TEXT,
    url              TEXT,
    file_name        TEXT,
    size             INTEGER,
    checksum         TEXT,
    checksum_type    TEXT,
    board_series     TEXT,
    excluded_devices TEXT
);
CREATE TABLE IF NOT EXISTS license (license_id TEXT PRIMARY KEY, name TEXT);
CREATE INDEX IF NOT EXISTS ix_comp_release   ON component(release_id);
CREATE INDEX IF NOT EXISTS ix_plat_uid       ON component_platform(comp_uid);
CREATE INDEX IF NOT EXISTS ix_file_uid       ON component_file(comp_uid);
CREATE INDEX IF NOT EXISTS ix_host_os        ON release_host_os(release_id);
CREATE INDEX IF NOT EXISTS ix_board          ON release_board(release_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open ``db_path`` read-mostly with ``sqlite3.Row`` rows.

    Args:
        db_path: Filesystem path to the manifest database.

    Returns:
        A connection whose ``row_factory`` yields mapping-style rows.
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _as_list(value: object) -> list[Any]:
    """Coerce ``value`` to a list (``None`` -> ``[]``, scalar -> single-item).

    Returns:
        ``value`` unchanged if it is already a list, ``[]`` for ``None``,
        otherwise a single-item list wrapping ``value``.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _platform_rows(
    comp: dict[str, Any],
) -> Iterator[tuple[str, str, float, int, list[dict[str, Any]], str, str]]:
    """Yield ``(os, arch, install_mb, download_bytes, files, series, excluded)``.

    Each ``platforms[]`` entry can list several operating systems and architectures;
    the cross product is expanded so callers can filter by an exact ``(os, arch)`` pair.
    A component often ships a *different* payload per board on the same ``(os, arch)``
    (a board-specific entry plus a catch-all), so the entry's
    ``supportedHardware.seriesIds`` / ``excludedDeviceIds`` are carried through as
    comma-joined strings (empty ``series`` means the entry applies to any board) — the
    read helpers use them to pick the one entry that installs on a given board instead
    of summing every variant.
    """
    for plat in _as_list(comp.get("platforms")):
        oses = _as_list(plat.get("operatingSystems")) or [""]
        arches = _as_list(plat.get("architectures")) or [""]
        install_mb = float(plat.get("installSizeMB") or 0)
        files = _as_list(plat.get("downloadFiles"))
        download_b = sum(int(f.get("size") or 0) for f in files)
        hardware = plat.get("supportedHardware") or {}
        series = ",".join(_as_list(hardware.get("seriesIds")))
        excluded = ",".join(_as_list(hardware.get("excludedDeviceIds")))
        for os_name in oses:
            for arch in arches:
                yield (
                    str(os_name),
                    str(arch),
                    install_mb,
                    download_b,
                    files,
                    series,
                    excluded,
                )


def _component_ids_of_group(group: dict[str, Any]) -> list[str]:
    """Return the component ids a group lists via ``versions[].components[].id``."""
    out: list[str] = []
    for ver in _as_list(group.get("versions")):
        for comp in _as_list(ver.get("components")):
            cid = comp.get("id")
            if cid and cid not in out:
                out.append(cid)
    return out


def _ingest_sdkml3(
    con: sqlite3.Connection, data: dict[str, Any], src: str, comp_repo_url: str | None
) -> None:
    """Insert one parsed ``sdkml3`` document into all tables."""
    rel = (data.get("information") or {}).get("release") or {}
    product = str(
        rel.get("productCategory") or rel.get("productDisplayName") or "Unknown"
    )
    version = str(rel.get("releaseVersion") or rel.get("title") or "")
    release_id = f"{product}:{version}"

    con.execute(
        "INSERT OR REPLACE INTO product(product, target_os, server_type)"
        " VALUES (?,?,?)",
        (product, rel.get("targetOS"), ",".join(_as_list(rel.get("serverType")))),
    )
    con.execute(
        "INSERT OR REPLACE INTO release"
        "(release_id, product, version, title, min_sdkm, is_primary,"
        " comp_repo_url, src)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            release_id,
            product,
            version,
            rel.get("title"),
            rel.get("minSDKMVer"),
            1 if rel.get("showInMainList") else 0,
            comp_repo_url,
            src,
        ),
    )
    host = rel.get("hostOperatingSystemsSupportFor") or {}
    host_os = _as_list(host.get("targetGroups")) or _as_list(host.get("hostGroups"))
    con.executemany(
        "INSERT INTO release_host_os(release_id, host_os) VALUES (?,?)",
        [(release_id, o) for o in host_os],
    )
    boards = _as_list((rel.get("supportedHardware") or {}).get("seriesIds"))
    con.executemany(
        "INSERT INTO release_board(release_id, board) VALUES (?,?)",
        [(release_id, b) for b in boards],
    )
    con.executemany(
        "INSERT INTO release_arch(release_id, arch) VALUES (?,?)",
        [(release_id, a) for a in _as_list(rel.get("architectures"))],
    )

    components: dict[str, Any] = data.get("components") or {}
    groups_by_id = {
        g.get("id"): g for g in _as_list(data.get("groups")) if isinstance(g, dict)
    }
    licenses: dict[str, Any] = data.get("licenses") or {}
    con.executemany(
        "INSERT OR REPLACE INTO license(license_id, name) VALUES (?,?)",
        [
            (lid, (lic or {}).get("name") or (lic or {}).get("title"))
            for lid, lic in licenses.items()
        ],
    )

    # Walk sections -> groups -> components so each component carries its
    # section + group + description.
    for section in _as_list(data.get("sections")):
        sec_title = section.get("title") or section.get("name")
        for gid in _as_list(section.get("groups")):
            group = groups_by_id.get(gid) or {}
            for cid in _component_ids_of_group(group):
                comp = components.get(cid)
                if not isinstance(comp, dict):
                    continue
                comp_uid = f"{release_id}:{cid}"
                license_ids = _as_list(comp.get("licenseIds"))
                con.execute(
                    "INSERT OR IGNORE INTO component"
                    "(comp_uid, release_id, comp_id, name, version, section,"
                    " group_name, installed_on, description, license_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        comp_uid,
                        release_id,
                        cid,
                        comp.get("name"),
                        comp.get("version"),
                        sec_title,
                        group.get("name"),
                        group.get("installedOn") or group.get("groupType"),
                        group.get("description"),
                        license_ids[0] if license_ids else None,
                    ),
                )
                for (
                    os_name,
                    arch,
                    install_mb,
                    download_b,
                    files,
                    series,
                    excluded,
                ) in _platform_rows(comp):
                    con.execute(
                        "INSERT INTO component_platform"
                        "(comp_uid, os, arch, install_mb, download_b,"
                        " board_series, excluded_devices)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (
                            comp_uid,
                            os_name,
                            arch,
                            install_mb,
                            download_b,
                            series,
                            excluded,
                        ),
                    )
                    for f in files:
                        con.execute(
                            "INSERT INTO component_file"
                            "(comp_uid, os, arch, url, file_name, size,"
                            " checksum, checksum_type, board_series, excluded_devices)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                comp_uid,
                                os_name,
                                arch,
                                f.get("url"),
                                f.get("fileName"),
                                int(f.get("size") or 0),
                                f.get("checksum"),
                                f.get("checksumType"),
                                series,
                                excluded,
                            ),
                        )
                for dep in _as_list(comp.get("dependencies")):
                    dep_id = dep.get("id") if isinstance(dep, dict) else dep
                    if dep_id:
                        con.execute(
                            "INSERT INTO dependency(comp_uid, depends_on_comp_id)"
                            " VALUES (?,?)",
                            (comp_uid, dep_id),
                        )


def _apply_use_cases(con: sqlite3.Connection, tags_path: Path) -> int:
    """Apply LLM-generated use-case tags (intent vocabulary) onto matching components.

    ``tags_path`` is a JSON ``{"tags": [{"name", "group", "use_cases": [...]}]}``
    produced offline. Tags are matched by ``(name, group_name)`` and written as a
    comma-joined string to ``component.use_cases`` so search can map intent to comps.

    Returns:
        The number of ``(name, group)`` tag rows applied; a missing file is a no-op (0).
    """
    if not tags_path.exists():
        return 0
    try:
        tags = json.loads(tags_path.read_text(encoding="utf-8")).get("tags", [])
    except (OSError, json.JSONDecodeError):
        logger.warning("manifest_db: unreadable use-case tags %s", tags_path.name)
        return 0
    applied = 0
    for tag in tags:
        # Store as human-readable phrases ("object detection") so space-separated
        # queries match the substring fallback and read naturally for the embedder.
        use_cases = ", ".join(
            uc.replace("-", " ") for uc in (tag.get("use_cases") or [])
        )
        if not use_cases:
            continue
        con.execute(
            "UPDATE component SET use_cases = ? WHERE name = ? AND group_name = ?",
            (use_cases, tag.get("name"), tag.get("group")),
        )
        applied += 1
    return applied


def build_manifest_db(src_dir: str | Path, db_path: str | Path) -> dict[str, int]:
    """Build ``manifest.db`` from every ``sdkml3_*.json`` file under ``src_dir``.

    Args:
        src_dir: Directory holding captured ``sdkml3_*.json`` manifests (an optional
            sibling ``<file>.url`` records the source compRepoURL).
        db_path: Output SQLite database path (overwritten).

    Returns:
        Row counts per table, e.g. ``{"release": 12, "component": 344, ...}``.
    """
    src = Path(src_dir)
    out = Path(db_path)
    if out.exists():
        out.unlink()
    con = connect(out)
    try:
        con.executescript(SCHEMA)
        files = sorted(src.glob("sdkml3_*.json"))
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("manifest_db: skipping unreadable %s", path.name)
                continue
            url_side = path.with_suffix(path.suffix + ".url")
            comp_repo_url = (
                url_side.read_text(encoding="utf-8").strip()
                if url_side.exists()
                else None
            )
            _ingest_sdkml3(con, data, path.name, comp_repo_url)
        con.commit()
        _apply_use_cases(con, Path(src_dir).parent / "use_cases.json")
        con.commit()
        counts = {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed table names
            for t in (
                "release",
                "component",
                "component_platform",
                "component_file",
                "license",
            )
        }
        logger.info(
            "manifest_db: built %s from %d files -> %s", counts, len(files), out
        )
        return counts
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Read helpers — parameterized SQL only. The agent tools (manifest_tools.py)
# wrap these.
# ---------------------------------------------------------------------------


def _rows(
    con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()
) -> list[dict[str, Any]]:
    """Run ``sql`` and return rows as plain dicts.

    Returns:
        The query result rows, each converted to a plain ``dict``.
    """
    return [dict(r) for r in con.execute(sql, tuple(params)).fetchall()]


def _board_score(row: dict[str, Any], board: str | None) -> int:
    """Rank how well one platform/file row fits ``board`` (higher = better match).

    A component frequently ships several rows for the same ``(os, arch)`` — one per
    board (a board-specific entry plus a catch-all). For a given board only one is
    actually installed, so callers keep the single highest-scoring row instead of
    summing them all.

    Returns:
        ``2`` for a board-specific match, ``1`` for a catch-all that applies to any
        board, ``0`` for a board-specific entry when no board was given, and ``-1``
        when the row does not apply to ``board`` at all.
    """
    series = {s for s in (row.get("board_series") or "").split(",") if s}
    excluded = {s for s in (row.get("excluded_devices") or "").split(",") if s}
    if board and board in excluded:
        return -1  # explicitly excluded from this board
    if board and series:
        return 2 if board in series else -1  # board-specific entry
    if not series:
        return 1  # catch-all: applies to any board
    return 0  # board-specific entry but no board given -> least preferred


def _pick_board_row(
    rows: list[dict[str, Any]], board: str | None, size_key: str = "install_mb"
) -> dict[str, Any]:
    """Return the single row from ``rows`` that installs on ``board``.

    ``rows`` are the candidate platform/file rows for one ``(comp_uid, os, arch)``.
    The best board match wins; ties break toward the larger ``size_key`` so an
    unknown board still yields a deterministic, non-duplicated choice.
    """
    applicable = [r for r in rows if _board_score(r, board) >= 0] or rows
    return max(applicable, key=lambda r: (_board_score(r, board), r.get(size_key) or 0))


def find_releases(
    con: sqlite3.Connection,
    product: str | None = None,
    host_os: str | None = None,
    board: str | None = None,
    arch: str | None = None,
) -> list[dict[str, Any]]:
    """Return releases matching the given compatibility filters (all optional)."""
    where: list[str] = []
    params: list[Any] = []
    if product:
        where.append("r.product = ?")
        params.append(product)
    if host_os:
        where.append(
            "EXISTS (SELECT 1 FROM release_host_os h"
            " WHERE h.release_id=r.release_id AND h.host_os=?)"
        )
        params.append(host_os)
    if board:
        where.append(
            "EXISTS (SELECT 1 FROM release_board b"
            " WHERE b.release_id=r.release_id AND b.board=?)"
        )
        params.append(board)
    if arch:
        where.append(
            "EXISTS (SELECT 1 FROM release_arch a"
            " WHERE a.release_id=r.release_id AND a.arch=?)"
        )
        params.append(arch)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    # S608: identifiers are controlled (static columns + an internally built
    # WHERE clause); all user values go through ``?`` params.
    sql = (
        "SELECT r.release_id, r.product, r.version, r.title, r.min_sdkm,"  # noqa: S608
        " r.is_primary"
        f" FROM release r{clause} ORDER BY r.product, r.version DESC"
    )
    return _rows(con, sql, params)


def list_components(
    con: sqlite3.Connection,
    release_id: str,
    installed_on: str | None = None,
    section: str | None = None,
) -> list[dict[str, Any]]:
    """Return components of ``release_id`` (filtered by install side / section)."""
    where = ["release_id = ?"]
    params: list[Any] = [release_id]
    if installed_on:
        where.append("installed_on = ?")
        params.append(installed_on)
    if section:
        where.append("section = ?")
        params.append(section)
    # S608: WHERE is built from controlled column predicates; values use ``?``.
    return _rows(
        con,
        "SELECT comp_id, name, version, section, group_name, installed_on"  # noqa: S608
        f" FROM component WHERE {' AND '.join(where)}"
        " ORDER BY section, group_name, name",
        params,
    )


def footprint(
    con: sqlite3.Connection,
    release_id: str,
    host_os: str,
    arch: str,
    comp_ids: list[str] | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Sum install/download size for a release on a given ``(host_os, arch)``.

    A component can list several platform rows for one ``(os, arch)`` — one per board
    — of which exactly one is installed. Each component therefore contributes only its
    ``board``-appropriate row (see ``_pick_board_row``); summing every row would
    over-report the footprint.

    Returns:
        ``{"components", "install_mb", "download_b"}`` totals (zeros if nothing
        matches the given filters).
    """
    params: list[Any] = [release_id, host_os, arch]
    extra = ""
    if comp_ids:
        placeholders = ",".join("?" for _ in comp_ids)
        extra = f" AND c.comp_id IN ({placeholders})"
        params.extend(comp_ids)
    # S608: ``extra`` is a placeholder list built internally; values use ``?``.
    rows = _rows(
        con,
        "SELECT c.comp_uid AS comp_uid, cp.install_mb AS install_mb,"  # noqa: S608
        " cp.download_b AS download_b, cp.board_series AS board_series,"
        " cp.excluded_devices AS excluded_devices"
        " FROM component c JOIN component_platform cp ON cp.comp_uid = c.comp_uid"
        f" WHERE c.release_id = ? AND cp.os = ? AND cp.arch = ?{extra}",
        params,
    )
    by_comp: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_comp.setdefault(row["comp_uid"], []).append(row)
    install_mb = 0.0
    download_b = 0
    for candidates in by_comp.values():
        chosen = _pick_board_row(candidates, board)
        install_mb += chosen["install_mb"] or 0
        download_b += chosen["download_b"] or 0
    return {
        "components": len(by_comp),
        "install_mb": round(install_mb, 1),
        "download_b": download_b,
    }


def component_detail(
    con: sqlite3.Connection,
    release_id: str,
    comp: str,
    host_os: str | None = None,
    arch: str | None = None,
    board: str | None = None,
) -> dict[str, Any] | None:
    """Return one component's full record by ``comp_id`` or (case-insensitive) name."""
    row = con.execute(
        "SELECT * FROM component"
        " WHERE release_id = ? AND (comp_id = ? OR name = ? COLLATE NOCASE)"
        " LIMIT 1",
        (release_id, comp, comp),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    plat_where = "comp_uid = ?"
    plat_params: list[Any] = [out["comp_uid"]]
    if host_os:
        plat_where += " AND os = ?"
        plat_params.append(host_os)
    if arch:
        plat_where += " AND arch = ?"
        plat_params.append(arch)
    # S608: ``plat_where`` is built from controlled predicates; values use ``?``.
    plat_rows = _rows(
        con,
        "SELECT os, arch, install_mb, download_b, board_series, excluded_devices"  # noqa: S608
        f" FROM component_platform WHERE {plat_where}",
        plat_params,
    )
    # Collapse the per-board variants of each (os, arch) to the one that installs
    # on ``board`` so a single platform never shows two conflicting sizes.
    by_os_arch: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for plat in plat_rows:
        by_os_arch.setdefault((plat["os"], plat["arch"]), []).append(plat)
    out["platforms"] = [
        {
            k: _pick_board_row(variants, board)[k]
            for k in ("os", "arch", "install_mb", "download_b")
        }
        for variants in by_os_arch.values()
    ]
    out["depends_on"] = [
        r["depends_on_comp_id"]
        for r in _rows(
            con,
            "SELECT depends_on_comp_id FROM dependency WHERE comp_uid = ?",
            [out["comp_uid"]],
        )
    ]
    return out


def resolve_deps(
    con: sqlite3.Connection, release_id: str, comp_ids: list[str]
) -> list[str]:
    """Return the transitive dependency closure of ``comp_ids`` in ``release_id``."""
    seen: set[str] = set()
    stack = list(comp_ids)
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        rows = con.execute(
            "SELECT depends_on_comp_id FROM dependency WHERE comp_uid = ?",
            (f"{release_id}:{cid}",),
        ).fetchall()
        stack.extend(r[0] for r in rows if r[0] not in seen)
    return sorted(seen)


def search_substring(
    con: sqlite3.Connection,
    query: str,
    product: str | None = None,
    installed_on: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Offline fallback for ``search_components``: match over name + description.

    Returns:
        Up to ``limit`` matching component rows as plain dicts.
    """
    where = [
        (
            "(c.name LIKE ? COLLATE NOCASE"
            " OR c.description LIKE ? COLLATE NOCASE"
            " OR c.group_name LIKE ? COLLATE NOCASE"
            " OR c.use_cases LIKE ? COLLATE NOCASE)"
        )
    ]
    like = f"%{query}%"
    params: list[Any] = [like, like, like, like]
    if product:
        where.append("c.release_id LIKE ?")
        params.append(f"{product}:%")
    if installed_on:
        where.append("c.installed_on = ?")
        params.append(installed_on)
    params.append(limit)
    # S608: WHERE is built from controlled predicates; all values use ``?``.
    return _rows(
        con,
        "SELECT DISTINCT c.comp_uid, c.release_id, c.comp_id, c.name,"  # noqa: S608
        " c.group_name, c.installed_on, c.description, c.use_cases"
        f" FROM component c WHERE {' AND '.join(where)} LIMIT ?",
        params,
    )


def build_plan_rows(
    con: sqlite3.Connection,
    release_id: str,
    host_os: str,
    arch: str,
    comp_ids: list[str],
    board: str | None = None,
) -> list[dict[str, Any]]:
    """Return the execution payload (download files) for an install selection.

    A component may publish a different file per board on the same ``(os, arch)``; only
    the ``board``-appropriate platform entry is actually downloaded, so the others are
    dropped rather than listed alongside it.
    """
    if not comp_ids:
        return []
    placeholders = ",".join("?" for _ in comp_ids)
    params = [release_id, *comp_ids, host_os, arch]
    # S608: ``placeholders`` is a ``?``-list built internally; values use ``?``.
    rows = _rows(
        con,
        "SELECT c.comp_uid, c.comp_id, c.name, f.file_name, f.url, f.size,"  # noqa: S608
        " f.checksum, f.checksum_type, f.board_series, f.excluded_devices"
        " FROM component c JOIN component_file f ON f.comp_uid = c.comp_uid"
        f" WHERE c.release_id = ? AND c.comp_id IN ({placeholders})"
        " AND f.os = ? AND f.arch = ?"
        " ORDER BY c.comp_id",
        params,
    )
    by_comp: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_comp.setdefault(row["comp_uid"], []).append(row)
    fields = (
        "comp_id",
        "name",
        "file_name",
        "url",
        "size",
        "checksum",
        "checksum_type",
    )
    out: list[dict[str, Any]] = []
    for candidates in by_comp.values():
        winner = _pick_board_row(candidates, board, size_key="size")
        signature = (winner["board_series"], winner["excluded_devices"])
        out.extend(
            {field: row[field] for field in fields}
            for row in candidates
            if (row["board_series"], row["excluded_devices"]) == signature
        )
    return out


def _main() -> None:
    """CLI: ``python -m deepagents_code.manifest_db build <src_dir> <db_path>``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Build manifest.db from captured sdkml3 files."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("src_dir")
    build.add_argument("db_path")
    args = parser.parse_args()
    if args.cmd == "build":
        counts = build_manifest_db(args.src_dir, args.db_path)
        print(json.dumps(counts, indent=2))  # noqa: T201 - CLI output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _main()
