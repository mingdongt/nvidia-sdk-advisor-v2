"""Grounded, read-only query tools over the NVIDIA SDK Manager manifest database.

These seven tools let the agent answer SDK Manager install questions from a
deterministic SQLite store (``manifest.db``) instead of inventing facts. Every
version, size, compatibility relationship and component listing returned here comes
straight from parsed SDK Manager manifests — the model must treat them as ground
truth and never fabricate or "round" them.

All tools are READ-ONLY (no install, download or flash happens here), so they are
not gated behind human approval; the actual install runs through the separately
interrupt-gated shell. Each tool returns a JSON-serializable ``dict`` and degrades
to ``{"error": ...}`` rather than raising. Heavy imports are done lazily inside the
functions to keep startup fast.

Detected host facts (``host_os``, ``arch``, ``board``) act as always-on filters: pass
the user's real host OS / CPU arch / target board so sizes and compatibility reflect
their actual machine.

Typical flow: ``find_releases`` (what fits this board+host) -> ``search_components``
(intent -> components) -> ``component_detail`` / ``footprint`` / ``resolve_deps`` ->
``build_plan``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

_DB_ENV_VAR = "DEEPAGENTS_MANIFEST_DB"

#: The manifest.db shipped inside the package (built from data/sdkm/manifests/).
_PACKAGED_DB = Path(__file__).parent / "data" / "sdkm" / "manifest.db"


def _db_path() -> Path | None:
    """Resolve the manifest.db path.

    Uses the ``DEEPAGENTS_MANIFEST_DB`` env var as an explicit override, otherwise the
    manifest.db bundled with the package.

    Returns:
        The existing database path, or ``None`` if the override is set but missing and
        no bundled database is present.
    """
    raw = os.environ.get(_DB_ENV_VAR)
    if raw:
        path = Path(raw)
        return path if path.exists() else None
    return _PACKAGED_DB if _PACKAGED_DB.exists() else None


def _open() -> sqlite3.Connection | dict[str, str]:
    """Open the manifest database, or return an error dict if unavailable.

    Returns:
        An open connection, or ``{"error": ...}`` when no database is configured.
    """
    path = _db_path()
    if path is None:
        return {
            "error": (
                "manifest database not found. Set the DEEPAGENTS_MANIFEST_DB "
                "environment variable (or settings.manifest_db_path) to a "
                "manifest.db built by deepagents_code.manifest_db."
            )
        }
    from deepagents_code import (
        manifest_db,
    )

    return manifest_db.connect(path)


def find_releases(
    product: str | None = None,
    host_os: str | None = None,
    board: str | None = None,
    arch: str | None = None,
) -> dict[str, Any]:
    """Find NVIDIA SDK releases compatible with a host machine and target board.

    Use this first to ground "which version works on my board / host" questions.
    Facts come from the manifest database — do not invent versions or compatibility.

    Args:
        product: Product line to filter by, e.g. "Jetson", "DOCA" (omit for all).
        host_os: The user's host OS, e.g. "ubuntu22.04", "windows11" (the detected
            current OS).
        board: Target hardware series, e.g. "JETSON_ORIN_NANO_TARGETS" (the detected
            target).
        arch: Host CPU architecture, e.g. "x86_64".

    Returns:
        Dict with "releases": a list of {release_id, product, version, title, min_sdkm,
        is_primary}, newest first. Empty list means nothing matches the given filters.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        return {
            "releases": manifest_db.find_releases(con, product, host_os, board, arch)
        }
    except Exception as exc:
        logger.exception("find_releases failed")
        return {"error": f"find_releases failed: {exc}"}
    finally:
        con.close()


def list_components(
    release_id: str,
    installed_on: str | None = None,
    section: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """List the components a given release installs (what you actually get).

    Args:
        release_id: Release identifier from find_releases, e.g. "Jetson:6.2.2".
        installed_on: Filter by install side: "host" (your machine) or "target"
            (the device).
        section: Filter by UI section title, e.g. "Jetson SDK Components".
        board: Detected target board series, e.g. "JETSON_ORIN_NANO_TARGETS". Pass it
            to hide components that ship no payload for the user's board.

    Returns:
        Dict with "components": list of {comp_id, name, version, section, group_name,
        installed_on}, or {"error": ...}.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        return {
            "components": manifest_db.list_components(
                con, release_id, installed_on, section, board
            )
        }
    except Exception as exc:
        logger.exception("list_components failed")
        return {"error": f"list_components failed: {exc}"}
    finally:
        con.close()


def footprint(
    release_id: str,
    host_os: str,
    arch: str,
    comp_ids: list[str] | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Compute total install and download size for a release on a specific host.

    Size varies by host OS, architecture and target board (a component can ship a
    different payload per board), so pass the user's detected host_os, arch and board.
    Sizes come from the manifest — report them as-is, do not estimate.

    Args:
        release_id: Release identifier, e.g. "Jetson:6.2.2".
        host_os: Host OS, e.g. "ubuntu22.04".
        arch: Host CPU architecture, e.g. "x86_64".
        comp_ids: Optional subset of component ids to size (omit to size the full
            release).
        board: Detected target board series, e.g. "JETSON_ORIN_NANO_TARGETS". Pass it
            so per-board components are sized for the user's board, not double-counted.

    Returns:
        Dict with {components, install_mb, download_b}, or {"error": ...}.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        return manifest_db.footprint(con, release_id, host_os, arch, comp_ids, board)
    except Exception as exc:
        logger.exception("footprint failed")
        return {"error": f"footprint failed: {exc}"}
    finally:
        con.close()


def search_components(
    query: str,
    product: str | None = None,
    installed_on: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Find components by intent/topic, e.g. "object detection" or "containers".

    Semantic search over component name + group + description (falls back to
    substring match when no embedding backend is configured, so it always works
    offline). Use it to turn a fuzzy user need into concrete component ids, then call
    component_detail / footprint / build_plan for the grounded facts.

    Args:
        query: Natural-language need or topic, e.g. "deep learning inference".
        product: Optional product filter, e.g. "Jetson".
        installed_on: Optional "host" or "target" filter.
        board: Optional target board series; drops components with no payload for it.

    Returns:
        Dict with "matches": list of {comp_uid, release_id, comp_id, name, group_name,
        installed_on, description}, or {"error": ...}.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        try:
            from deepagents_code import manifest_vector

            matches = manifest_vector.search(con, query, product, installed_on, board)
        except Exception:  # noqa: BLE001 - no vector backend -> substring fallback
            from deepagents_code import manifest_db

            matches = manifest_db.search_substring(
                con, query, product, installed_on, board
            )
    except Exception as exc:
        logger.exception("search_components failed")
        return {"error": f"search_components failed: {exc}"}
    else:
        return {"matches": matches}
    finally:
        con.close()


def component_detail(
    release_id: str,
    component: str,
    host_os: str | None = None,
    arch: str | None = None,
    board: str | None = None,
) -> dict[str, Any]:
    """Get one component's full record: id, version, size, install side, deps.

    Args:
        release_id: Release identifier, e.g. "Jetson:6.2.2".
        component: Component id (e.g. "NV_CUDA_HOST_COMP") or display name (e.g.
            "CUDA on Host").
        host_os: Optional host OS to pick the matching platform's size, e.g.
            "ubuntu22.04".
        arch: Optional host architecture, e.g. "x86_64".
        board: Optional target board series (e.g. "JETSON_ORIN_NANO_TARGETS") to pick
            the platform size for the user's board when a component is board-specific.

    Returns:
        Dict with the component fields (name, version, section, group_name,
        installed_on, description, license_id, platforms, depends_on [required
        dependencies only], optional_depends_on), or {"error": ...} if not found.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        detail = manifest_db.component_detail(
            con, release_id, component, host_os, arch, board
        )
        if detail is None:
            return {"error": f"component '{component}' not found in {release_id}"}
    except Exception as exc:
        logger.exception("component_detail failed")
        return {"error": f"component_detail failed: {exc}"}
    else:
        return detail
    finally:
        con.close()


def resolve_deps(
    release_id: str, comp_ids: list[str], include_optional: bool = False
) -> dict[str, Any]:
    """Expand a component selection to its full dependency closure within a release.

    Only required dependencies are followed by default — SDK Manager does not
    install optional dependencies for a default selection, so including them would
    over-report the install set and any size/plan computed from it.

    Args:
        release_id: Release identifier, e.g. "Jetson:6.2.2".
        comp_ids: Component ids the user wants to install.
        include_optional: Also pull optional dependencies (off by default).

    Returns:
        Dict with "components": the sorted set of component ids needed (selection +
        dependencies), or {"error": ...}. (Named "components" rather than "required"
        because the set also includes optional deps when include_optional=True.)
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        return {
            "components": manifest_db.resolve_deps(
                con, release_id, comp_ids, include_optional
            )
        }
    except Exception as exc:
        logger.exception("resolve_deps failed")
        return {"error": f"resolve_deps failed: {exc}"}
    finally:
        con.close()


def build_plan(
    release_id: str,
    host_os: str,
    arch: str,
    comp_ids: list[str],
    board: str | None = None,
) -> dict[str, Any]:
    """Assemble a grounded install plan (components + download files + size).

    Read-only: this only describes what an install would download/do; it does not
    install, download or flash anything (that stays behind the human-approved shell).
    Pass the user's detected host_os, arch and board so the plan matches their machine
    and target (per-board components resolve to the one payload they actually install).

    Args:
        release_id: Release identifier, e.g. "Jetson:6.2.2".
        host_os: Host OS, e.g. "ubuntu22.04".
        arch: Host architecture, e.g. "x86_64".
        comp_ids: Component ids to include (run resolve_deps first to add
            dependencies).
        board: Detected target board series, e.g. "JETSON_ORIN_NANO_TARGETS".

    Returns:
        Dict with {release_id, host_os, arch, footprint, files: [{comp_id, name,
        file_name, url, size, checksum, checksum_type}]}, or {"error": ...}.
    """
    con = _open()
    if isinstance(con, dict):
        return con
    try:
        from deepagents_code import manifest_db

        return {
            "release_id": release_id,
            "host_os": host_os,
            "arch": arch,
            "footprint": manifest_db.footprint(
                con, release_id, host_os, arch, comp_ids, board
            ),
            "files": manifest_db.build_plan_rows(
                con, release_id, host_os, arch, comp_ids, board
            ),
        }
    except Exception as exc:
        logger.exception("build_plan failed")
        return {"error": f"build_plan failed: {exc}"}
    finally:
        con.close()


#: The agent-callable manifest tools, in the order a query typically flows through them.
MANIFEST_TOOLS = [
    find_releases,
    list_components,
    footprint,
    search_components,
    component_detail,
    resolve_deps,
    build_plan,
]
