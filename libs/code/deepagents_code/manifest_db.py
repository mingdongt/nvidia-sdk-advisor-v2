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
    edition       TEXT,
    revision      INTEGER,
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
    plat_idx         INTEGER,
    os               TEXT,
    arch             TEXT,
    install_mb       REAL,
    download_b       INTEGER,
    board_series     TEXT,
    board_devices    TEXT,
    excluded_devices TEXT
);
CREATE TABLE IF NOT EXISTS dependency (
    comp_uid           TEXT,
    depends_on_comp_id TEXT,
    dep_type           TEXT NOT NULL DEFAULT 'required'
);
CREATE TABLE IF NOT EXISTS component_file (
    comp_uid         TEXT,
    plat_idx         INTEGER,
    os               TEXT,
    arch             TEXT,
    url              TEXT,
    file_name        TEXT,
    size             INTEGER,
    checksum         TEXT,
    checksum_type    TEXT,
    board_series     TEXT,
    board_devices    TEXT,
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
) -> Iterator[tuple[int, str, str, float, int, list[dict[str, Any]], str, str, str]]:
    """Yield per-platform install/download rows for ``comp``.

    Each row is ``(plat_idx, os, arch, install_mb, download_bytes, files, series,
    devices, excluded)``. Each ``platforms[]`` entry can list several operating
    systems and architectures;
    the cross product is expanded so callers can filter by an exact ``(os, arch)`` pair.
    A component often ships a *different* payload per board on the same ``(os, arch)``
    (a board-specific entry plus a catch-all), so the entry's
    ``supportedHardware`` board scoping is carried through as comma-joined strings:
    ``seriesIds`` (series-level), ``deviceIds`` (device-level — finer than a series),
    and ``excludedDeviceIds``. An entry with NO seriesIds and NO deviceIds applies to
    any board (a true catch-all); an entry scoped by ``deviceIds`` is device-specific
    and must NOT be treated as a catch-all (see ``_board_score``). ``plat_idx`` is the
    index of the originating ``platforms[]`` entry, so a chosen platform row maps back
    to exactly its own download files even when two entries share the same board
    signature.
    """
    # The common shape carries per-platform rows under ``platforms``; the alternate
    # (SDK Manager) shape carries them under ``versions`` instead.
    entries = _as_list(comp.get("platforms")) or _as_list(comp.get("versions"))
    for plat_idx, plat in enumerate(entries):
        oses = _as_list(plat.get("operatingSystems")) or [""]
        arches = _as_list(plat.get("architectures")) or [""]
        install_mb = _parse_mb(plat.get("installSizeMB"))
        files = _as_list(plat.get("downloadFiles"))
        download_b = sum(int(f.get("size") or 0) for f in files)
        hardware = plat.get("supportedHardware") or {}
        series = ",".join(_as_list(hardware.get("seriesIds")))
        devices = ",".join(_as_list(hardware.get("deviceIds")))
        excluded = ",".join(_as_list(hardware.get("excludedDeviceIds")))
        for os_name in oses:
            for arch in arches:
                yield (
                    plat_idx,
                    str(os_name),
                    str(arch),
                    install_mb,
                    download_b,
                    files,
                    series,
                    devices,
                    excluded,
                )


def _parse_mb(value: object) -> float:
    """Parse an install size in MB from a number or a unit-suffixed string.

    Most manifests store ``installSizeMB`` as a number; the alternate (SDK Manager)
    shape stores a string like ``"382M"``.

    Returns:
        The size in MB as a float (``"1.5G"`` -> 1536.0), or 0.0 when unparseable.
    """
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    mult = {"G": 1024.0, "M": 1.0, "K": 1.0 / 1024.0}.get(text[-1].upper())
    if mult is not None:
        text = text[:-1].strip()
    else:
        mult = 1.0
    try:
        return float(text) * mult
    except ValueError:
        return 0.0


def _component_ids_of_group(group: dict[str, Any]) -> list[str]:
    """Return the component ids a group lists.

    Handles both shapes: components nested under ``versions[].components[]`` (the
    common shape) and listed directly under ``group.components[]`` (the alternate
    SDK Manager shape).
    """
    out: list[str] = []

    def _add(comp: Any) -> None:  # noqa: ANN401 - dict or bare id string
        cid = comp.get("id") if isinstance(comp, dict) else comp
        if cid and cid not in out:
            out.append(cid)

    for comp in _as_list(group.get("components")):
        _add(comp)
    for ver in _as_list(group.get("versions")):
        for comp in _as_list(ver.get("components")):
            _add(comp)
    return out


def _ingest_sdkml3(
    con: sqlite3.Connection, data: dict[str, Any], src: str, comp_repo_url: str | None
) -> None:
    """Insert one parsed ``sdkml3`` document into all tables.

    Raises:
        ValueError: If the document's ``release_id`` collides with one already
            ingested (two distinct manifests sharing product:version:edition).
    """
    rel = (data.get("information") or {}).get("release") or {}
    product = str(
        rel.get("productCategory") or rel.get("productDisplayName") or "Unknown"
    )
    version = str(rel.get("releaseVersion") or rel.get("title") or "")
    # releaseEdition disambiguates manifests that share product:version (e.g. the
    # base vs "MD"/multi-DPU DOCA editions); without it they collide on the
    # release primary key and silently fuse into one Frankenstein release.
    # releaseRevision is kept as a column (it does not currently disambiguate any
    # real collision, so folding it into the key would only churn every key).
    edition = str(rel.get("releaseEdition") or "").strip()
    revision = rel.get("releaseRevision")
    release_id = f"{product}:{version}" + (f":{edition}" if edition else "")

    # A genuine duplicate key (same product:version:edition from two files) means
    # two distinct manifests would overwrite/merge — fail loudly instead of
    # silently coalescing, so the data never describes a release that ships nowhere.
    if con.execute(
        "SELECT 1 FROM release WHERE release_id = ?", (release_id,)
    ).fetchone():
        existing_src = con.execute(
            "SELECT src FROM release WHERE release_id = ?", (release_id,)
        ).fetchone()
        msg = (
            f"duplicate release key {release_id!r} from {src!r} "
            f"(already built from {existing_src[0]!r}); "
            "add releaseEdition/releaseRevision to disambiguate."
        )
        raise ValueError(msg)

    con.execute(
        "INSERT OR REPLACE INTO product(product, target_os, server_type)"
        " VALUES (?,?,?)",
        (product, rel.get("targetOS"), ",".join(_as_list(rel.get("serverType")))),
    )
    con.execute(
        "INSERT INTO release"
        "(release_id, product, version, edition, revision, title, min_sdkm,"
        " is_primary, comp_repo_url, src)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            release_id,
            product,
            version,
            edition,
            revision,
            rel.get("title"),
            rel.get("minSDKMVer"),
            1 if rel.get("showInMainList") else 0,
            comp_repo_url,
            src,
        ),
    )
    host = rel.get("hostOperatingSystemsSupportFor") or {}
    # The host filter must be the OSes that can RUN SDK Manager (hostGroups), not
    # targetGroups — the latter also lists target-only OSes (e.g. JetPack's
    # windows10/11) that are not valid hosts. Fall back to targetGroups only when a
    # manifest omits hostGroups (e.g. the CUDA Toolkit manifest).
    host_os = _as_list(host.get("hostGroups")) or _as_list(host.get("targetGroups"))
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
    # ``groups`` is usually a (possibly id-interleaved) list of group objects, but the
    # alternate SDK Manager shape keys them in a dict — normalize both to objects.
    raw_groups = data.get("groups")
    group_objs = (
        list(raw_groups.values())
        if isinstance(raw_groups, dict)
        else _as_list(raw_groups)
    )
    groups_by_id = {
        g.get("id"): g for g in group_objs if isinstance(g, dict) and g.get("id")
    }
    licenses: dict[str, Any] = data.get("licenses") or {}
    con.executemany(
        "INSERT OR REPLACE INTO license(license_id, name) VALUES (?,?)",
        [
            (lid, (lic or {}).get("name") or (lic or {}).get("title"))
            for lid, lic in licenses.items()
        ],
    )

    # Walk groups -> components so each component carries its section + group +
    # description. Real manifests use one of two shapes: a ``sections`` layer that
    # references group ids (JetPack/DOCA), or no ``sections`` at all (CUDA Toolkit,
    # DOCA-on-BlueField) — in the latter case every group is ingested directly,
    # otherwise the whole product would silently lose all of its components.
    def _ingest_group(group: dict[str, Any], sec_title: str | None) -> None:
        for cid in _component_ids_of_group(group):
            comp = components.get(cid)
            if not isinstance(comp, dict):
                continue
            comp_uid = f"{release_id}:{cid}"
            # ``licenseIds`` (plural) is common; the alt shape uses ``licenseId``.
            license_ids = _as_list(comp.get("licenseIds") or comp.get("licenseId"))
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
                plat_idx,
                os_name,
                arch,
                install_mb,
                download_b,
                files,
                series,
                devices,
                excluded,
            ) in _platform_rows(comp):
                con.execute(
                    "INSERT INTO component_platform"
                    "(comp_uid, plat_idx, os, arch, install_mb, download_b,"
                    " board_series, board_devices, excluded_devices)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        comp_uid,
                        plat_idx,
                        os_name,
                        arch,
                        install_mb,
                        download_b,
                        series,
                        devices,
                        excluded,
                    ),
                )
                for f in files:
                    con.execute(
                        "INSERT INTO component_file"
                        "(comp_uid, plat_idx, os, arch, url, file_name, size,"
                        " checksum, checksum_type, board_series, board_devices,"
                        " excluded_devices)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            comp_uid,
                            plat_idx,
                            os_name,
                            arch,
                            f.get("url"),
                            f.get("fileName"),
                            int(f.get("size") or 0),
                            f.get("checksum"),
                            f.get("checksumType"),
                            series,
                            devices,
                            excluded,
                        ),
                    )
            for dep in _as_list(comp.get("dependencies")):
                if isinstance(dep, dict):
                    dep_id = dep.get("id")
                    # sdkml3 marks each edge "required" or "optional"; keep it so the
                    # closure can exclude optional deps (which SDK Manager does not
                    # install by default) instead of over-reporting them as mandatory.
                    dep_type = str(dep.get("type") or "required")
                else:
                    dep_id = dep
                    dep_type = "required"
                if not dep_id:
                    continue
                # A dependency target can be a GROUP id (a grouping indirection)
                # rather than a component id; expand it to the group's member
                # components so the closure pulls real, installable ids instead of a
                # bogus group id (and never silently drops the group's members).
                dep_targets = (
                    _component_ids_of_group(groups_by_id[dep_id])
                    if dep_id in groups_by_id
                    else [dep_id]
                )
                for target in dep_targets:
                    con.execute(
                        "INSERT INTO dependency"
                        "(comp_uid, depends_on_comp_id, dep_type) VALUES (?,?,?)",
                        (comp_uid, target, dep_type),
                    )

    sections = _as_list(data.get("sections"))
    if sections:
        for section in sections:
            sec_title = section.get("title") or section.get("name")
            for gid in _as_list(section.get("groups")):
                _ingest_group(groups_by_id.get(gid) or {}, sec_title)
    else:
        for group in groups_by_id.values():
            _ingest_group(group, None)


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
        # Surface releases that ingested no components (e.g. a section/group shape
        # the parser missed, or a genuinely component-less updater release) — a
        # silent 0-component product is the worst failure for a grounding store.
        empty_releases = [
            r[0]
            for r in con.execute(
                "SELECT release_id FROM release r WHERE NOT EXISTS"
                " (SELECT 1 FROM component c WHERE c.release_id = r.release_id)"
                " ORDER BY release_id"
            ).fetchall()
        ]
        if empty_releases:
            logger.warning(
                "manifest_db: %d release(s) ingested 0 components: %s",
                len(empty_releases),
                ", ".join(empty_releases),
            )
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

    ``supportedHardware`` scopes an entry three ways: ``seriesIds`` (series-level,
    same granularity as the ``board`` argument), ``deviceIds`` (device-level — finer
    than a series), and ``excludedDeviceIds``. An entry with NEITHER seriesIds NOR
    deviceIds is a true catch-all (applies to any board). A ``deviceIds``-scoped entry
    is device-specific: the ``board`` argument is a series id and the manifests ship
    no series->device map, so such an entry cannot be confirmed to apply to a series
    board — it is treated as not-applicable for a series board, and as board-dependent
    (not a safe default) when no board is given. This is what stops a device-only
    payload from masquerading as a catch-all.

    Returns:
        ``2`` for a series-specific match, ``1`` for a true catch-all, ``0`` for an
        entry that is board-dependent but might apply when no board was given, and
        ``-1`` when the entry does not apply to the given ``board``.
    """
    series = {s for s in (row.get("board_series") or "").split(",") if s}
    devices = {s for s in (row.get("board_devices") or "").split(",") if s}
    excluded = {s for s in (row.get("excluded_devices") or "").split(",") if s}
    # NOTE: ``excluded``/``devices`` hold DEVICE ids (e.g. JETSON_ORIN_NANO_8GB_DEVKIT)
    # while ``board``/``series`` are SERIES ids (e.g. JETSON_ORIN_NANO_TARGETS). The
    # manifests ship no series->device membership map, so device-level scoping cannot
    # be resolved against a series board; those entries are kept board-dependent rather
    # than silently treated as catch-alls.
    if board and board in excluded:
        return -1  # explicitly excluded from this board
    if board and board in series:
        return 2  # series-specific match
    if board and series:
        return -1  # series-specific to OTHER series
    if devices:
        # Device-specific entry. A series board cannot confirm it; with no board it is
        # board-dependent (not a safe default), never a catch-all.
        return -1 if board else 0
    if series:
        return 0  # series-specific entry, no board given -> board-dependent
    return 1  # no seriesIds and no deviceIds -> true catch-all (any board)


def _pick_board_row(
    rows: list[dict[str, Any]], board: str | None
) -> dict[str, Any] | None:
    """Return the single row from ``rows`` that installs on ``board``.

    ``rows`` are the candidate platform/file rows for one ``(comp_uid, os, arch)``.
    The best board match wins; ties break toward the later ``platforms[]`` entry
    (higher ``plat_idx``). Tie-breaking on ``plat_idx`` — rather than a size field —
    guarantees ``footprint`` and ``build_plan_rows`` pick the *same* entry even
    though they rank different size columns, so a plan's files always match its
    footprint.

    Returns ``None`` when *no* row applies to ``board`` (every variant is
    board-specific to other boards or explicitly excludes this one) — the component
    is genuinely not installed on that board, so callers drop it rather than
    counting an inapplicable payload.
    """
    applicable = [r for r in rows if _board_score(r, board) >= 0]
    if not applicable:
        return None
    return max(
        applicable, key=lambda r: (_board_score(r, board), r.get("plat_idx") or 0)
    )


def _release_boards(con: sqlite3.Connection, release_id: str) -> list[str]:
    """Return the board-series ids ``release_id`` supports (may be empty).

    Returns:
        The release's supported board series, or ``[]`` when the release declares no
        boards (e.g. CUDA Toolkit / DOCA, which are not board-scoped).
    """
    return [
        r[0]
        for r in con.execute(
            "SELECT board FROM release_board WHERE release_id = ?", (release_id,)
        ).fetchall()
    ]


def _resolve_for_release(
    variants: list[dict[str, Any]],
    board: str | None,
    boards: list[str],
) -> tuple[dict[str, Any] | None, bool]:
    """Pick the install variant for a component, or report that the board is needed.

    ``variants`` are one component's candidate platform/file rows for an ``(os, arch)``.

    - With a ``board``, the board-appropriate row is chosen (``None`` if the component
      installs nothing on it).
    - With no board, a *universal* variant — one that applies to EVERY board the
      release supports — is preferred (a ``seriesIds=[all release boards]`` entry or a
      true catch-all), since every board would install it. When no single variant
      covers all the release's boards the choice genuinely depends on the board, so the
      component is reported as board-dependent (the caller surfaces ``needs_board``)
      rather than guessing one board's payload. A component that installs on none of
      the release's boards is dropped, not flagged.

    Returns:
        ``(chosen_row_or_None, board_dependent)``. ``board_dependent`` is ``True`` only
        when no variant is universal yet some board would install one.
    """
    if board is not None:
        return _pick_board_row(variants, board), False
    if not boards:
        # Release is not board-scoped (e.g. CUDA/DOCA): no board dimension to resolve.
        return _pick_board_row(variants, None), False
    universal = [v for v in variants if all(_board_score(v, b) >= 0 for b in boards)]
    if universal:
        return _pick_board_row(universal, None), False
    if any(_board_score(v, b) >= 0 for v in variants for b in boards):
        return None, True  # some board installs a variant, but none is release-wide
    return None, False  # installs on no board of this release -> drop


def _comp_applies_to_board(con: sqlite3.Connection, comp_uid: str, board: str) -> bool:
    """Whether ``comp_uid`` ships any payload installable on ``board``.

    A component with at least one board-applicable platform row applies. A
    component with no platform rows at all is treated as board-agnostic (a meta/
    placeholder component) and also applies; only a component whose every platform
    row is board-specific to other boards (or excludes this one) is filtered out.

    Returns:
        ``True`` if the component installs something on ``board`` (or is
        board-agnostic), ``False`` if every variant excludes it.
    """
    plats = con.execute(
        "SELECT board_series, board_devices, excluded_devices FROM component_platform"
        " WHERE comp_uid = ?",
        (comp_uid,),
    ).fetchall()
    if not plats:
        return True
    return any(
        _board_score(
            {
                "board_series": p["board_series"],
                "board_devices": p["board_devices"],
                "excluded_devices": p["excluded_devices"],
            },
            board,
        )
        >= 0
        for p in plats
    )


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
        "SELECT r.release_id, r.product, r.version, r.edition, r.revision,"  # noqa: S608
        " r.title, r.min_sdkm, r.is_primary"
        f" FROM release r{clause} ORDER BY r.product, r.version DESC"
    )
    return _rows(con, sql, params)


def list_components(
    con: sqlite3.Connection,
    release_id: str,
    installed_on: str | None = None,
    section: str | None = None,
    board: str | None = None,
) -> list[dict[str, Any]]:
    """Return components of ``release_id`` (filtered by install side / section / board).

    When ``board`` is given, components whose every platform variant is specific to
    other boards (or excludes this one) are dropped, so the listing reflects what the
    given board actually installs.
    """
    where = ["release_id = ?"]
    params: list[Any] = [release_id]
    if installed_on:
        where.append("installed_on = ?")
        params.append(installed_on)
    if section:
        where.append("section = ?")
        params.append(section)
    # S608: WHERE is built from controlled column predicates; values use ``?``.
    rows = _rows(
        con,
        "SELECT comp_uid, comp_id, name, version, section, group_name,"  # noqa: S608
        " installed_on"
        f" FROM component WHERE {' AND '.join(where)}"
        " ORDER BY section, group_name, name",
        params,
    )
    if board:
        rows = [r for r in rows if _comp_applies_to_board(con, r["comp_uid"], board)]
    for r in rows:
        r.pop("comp_uid", None)
    return rows


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

    When ``board`` is unknown, a component whose every variant is board-specific (no
    catch-all) cannot be sized without guessing a board. Such components are NOT
    counted; their ids are returned in ``board_dependent`` with ``needs_board=True`` so
    the caller can ask the user for their board instead of reporting a board-specific
    size as if it were the default.

    Returns:
        ``{"components", "install_mb", "download_b", "board_dependent", "needs_board"}``
        (zeros / empty if nothing matches the given filters).
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
        "SELECT c.comp_uid AS comp_uid, c.comp_id AS comp_id,"  # noqa: S608
        " cp.plat_idx AS plat_idx, cp.install_mb AS install_mb,"
        " cp.download_b AS download_b, cp.board_series AS board_series,"
        " cp.board_devices AS board_devices, cp.excluded_devices AS excluded_devices"
        " FROM component c JOIN component_platform cp ON cp.comp_uid = c.comp_uid"
        f" WHERE c.release_id = ? AND cp.os = ? AND cp.arch = ?{extra}",
        params,
    )
    boards = _release_boards(con, release_id)
    by_comp: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_comp.setdefault(row["comp_uid"], []).append(row)
    install_mb = 0.0
    download_b = 0
    components = 0
    board_dependent: set[str] = set()
    for candidates in by_comp.values():
        chosen, dependent = _resolve_for_release(candidates, board, boards)
        if dependent:
            board_dependent.add(candidates[0]["comp_id"])
            continue  # size depends on the board -> don't guess, flag it
        if chosen is None:
            continue  # component ships no payload for this board -> not installed
        components += 1
        install_mb += chosen["install_mb"] or 0
        download_b += chosen["download_b"] or 0
    return {
        "components": components,
        "install_mb": round(install_mb, 1),
        "download_b": download_b,
        "board_dependent": sorted(board_dependent),
        "needs_board": bool(board_dependent),
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
        "SELECT plat_idx, os, arch, install_mb, download_b,"  # noqa: S608
        " board_series, board_devices, excluded_devices"
        f" FROM component_platform WHERE {plat_where}",
        plat_params,
    )
    # Collapse the per-board variants of each (os, arch) to the one that installs
    # on ``board`` so a single platform never shows two conflicting sizes. With no
    # board, prefer the release-wide variant and omit an (os, arch) whose size is
    # genuinely board-dependent rather than showing one board's payload as the size.
    boards = _release_boards(con, release_id)
    by_os_arch: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for plat in plat_rows:
        by_os_arch.setdefault((plat["os"], plat["arch"]), []).append(plat)
    platforms: list[dict[str, Any]] = []
    for variants in by_os_arch.values():
        chosen, _dependent = _resolve_for_release(variants, board, boards)
        if chosen is None:
            continue  # not installed on this board, or board-dependent without a board
        platforms.append(
            {k: chosen[k] for k in ("os", "arch", "install_mb", "download_b")}
        )
    out["platforms"] = platforms
    out["depends_on"] = [
        r["depends_on_comp_id"]
        for r in _rows(
            con,
            "SELECT depends_on_comp_id FROM dependency"
            " WHERE comp_uid = ? AND dep_type = 'required'",
            [out["comp_uid"]],
        )
    ]
    out["optional_depends_on"] = [
        r["depends_on_comp_id"]
        for r in _rows(
            con,
            "SELECT depends_on_comp_id FROM dependency"
            " WHERE comp_uid = ? AND dep_type <> 'required'",
            [out["comp_uid"]],
        )
    ]
    return out


def resolve_deps(
    con: sqlite3.Connection,
    release_id: str,
    comp_ids: list[str],
    include_optional: bool = False,
) -> list[str]:
    """Return the transitive dependency closure of ``comp_ids`` in ``release_id``.

    By default only ``required`` edges are followed — SDK Manager does not install
    optional dependencies for a default selection, so including them would
    over-report the install set (and any size/plan derived from it). Pass
    ``include_optional=True`` to also pull optional dependencies.
    """
    sql = "SELECT depends_on_comp_id FROM dependency WHERE comp_uid = ?"
    if not include_optional:
        sql += " AND dep_type = 'required'"
    seen: set[str] = set()
    stack = list(comp_ids)
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        rows = con.execute(sql, (f"{release_id}:{cid}",)).fetchall()
        stack.extend(r[0] for r in rows if r[0] not in seen)
    return sorted(seen)


def search_substring(
    con: sqlite3.Connection,
    query: str,
    product: str | None = None,
    installed_on: str | None = None,
    board: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Offline fallback for ``search_components``: match over name + description.

    When ``board`` is given, matches whose every platform variant is specific to
    other boards are dropped so results reflect the user's target board.

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
    # Board filtering happens in Python (CSV columns), so over-fetch first to keep
    # roughly ``limit`` results after filtering.
    params.append(limit * 4 if board else limit)
    # S608: WHERE is built from controlled predicates; all values use ``?``.
    rows = _rows(
        con,
        "SELECT DISTINCT c.comp_uid, c.release_id, c.comp_id, c.name,"  # noqa: S608
        " c.group_name, c.installed_on, c.description, c.use_cases"
        f" FROM component c WHERE {' AND '.join(where)} LIMIT ?",
        params,
    )
    if board:
        rows = [r for r in rows if _comp_applies_to_board(con, r["comp_uid"], board)][
            :limit
        ]
    return rows


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
    dropped rather than listed alongside it. When ``board`` is unknown, a component
    whose every variant is board-specific (no catch-all) is skipped rather than
    guessing which file to download — ``footprint`` reports it under
    ``board_dependent`` / ``needs_board`` so the caller asks for the board.
    """
    if not comp_ids:
        return []
    placeholders = ",".join("?" for _ in comp_ids)
    params = [release_id, *comp_ids, host_os, arch]
    # S608: ``placeholders`` is a ``?``-list built internally; values use ``?``.
    rows = _rows(
        con,
        "SELECT c.comp_uid, c.comp_id, c.name, f.plat_idx, f.file_name,"  # noqa: S608
        " f.url, f.size, f.checksum, f.checksum_type, f.board_series,"
        " f.board_devices, f.excluded_devices"
        " FROM component c JOIN component_file f ON f.comp_uid = c.comp_uid"
        f" WHERE c.release_id = ? AND c.comp_id IN ({placeholders})"
        " AND f.os = ? AND f.arch = ?"
        " ORDER BY c.comp_id",
        params,
    )
    boards = _release_boards(con, release_id)
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
        winner, dependent = _resolve_for_release(candidates, board, boards)
        if dependent or winner is None:
            continue  # which file to download depends on the board, or none applies
        # Emit exactly the winning platform entry's files (grouped by plat_idx),
        # so two entries that share a board signature never double-list.
        out.extend(
            {field: row[field] for field in fields}
            for row in candidates
            if row["plat_idx"] == winner["plat_idx"]
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
