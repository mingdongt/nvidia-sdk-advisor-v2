"""Semantic component search over the manifest database (intent -> components).

Substring search (``manifest_db.search_substring``) only matches literal terms;
abstract intent like "object detection" or "low-latency inference" needs embeddings.
This module indexes each component's "card" (name + group + description) into a
``sqlite-vec`` virtual table and answers nearest-neighbour queries.

It is strictly OPTIONAL and import-safe: the vector STORE (``sqlite-vec``) already
resolves transitively via ``langgraph-checkpoint-sqlite``, but no embedding MODEL
ships by default. ``search`` raises if either the store or an embedder is
unavailable, and the caller (``manifest_tools.search_components``) catches that and
falls back to substring — so the copilot still works fully offline, just with
literal-term matching until an embedder is wired.

Embedder resolution order (first that works):
  1. ``DEEPAGENTS_MANIFEST_EMBEDDER`` env var, "provider:model" (e.g.
     "openai:text-embedding-3-small", "nvidia:NV-Embed-QA") -> the matching
     ``langchain_*`` Embeddings class.
  2. A locally installed ``sentence-transformers`` model (fully offline) if present.
Build the index once after building manifest.db: ``build_index(connect(db_path))``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_EMBEDDER_ENV_VAR = "DEEPAGENTS_MANIFEST_EMBEDDER"
_VEC_TABLE = "vec_cards"


class _Embedder(Protocol):
    """Minimal embeddings interface shared by LangChain backends and the local shim."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...


class _VectorUnavailableError(RuntimeError):
    """Raised when the vector store or an embedder is unavailable.

    The caller treats this as the signal to fall back to substring search.
    """


def _load_sqlite_vec(con: sqlite3.Connection) -> None:
    """Load the ``sqlite-vec`` extension into ``con``.

    Raises:
        _VectorUnavailableError: If ``sqlite-vec`` is not importable or the SQLite
            build lacks loadable-extension support.
    """
    try:
        import sqlite_vec
    except ImportError as exc:  # pragma: no cover - depends on env
        msg = f"sqlite-vec not importable: {exc}"
        raise _VectorUnavailableError(msg) from exc
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError) as exc:  # pragma: no cover
        msg = f"sqlite build lacks loadable-extension support: {exc}"
        raise _VectorUnavailableError(msg) from exc


def _embedder() -> _Embedder:
    """Resolve an embeddings backend (a LangChain provider or a local model).

    Returns:
        An object exposing ``embed_documents`` and ``embed_query``.

    Raises:
        _VectorUnavailableError: If no embedder can be resolved.
    """
    spec = os.environ.get(_EMBEDDER_ENV_VAR)
    if spec and ":" in spec:
        provider, model = spec.split(":", 1)
        loaders = {
            "openai": ("langchain_openai", "OpenAIEmbeddings"),
            "nvidia": ("langchain_nvidia_ai_endpoints", "NVIDIAEmbeddings"),
            "google": ("langchain_google_genai", "GoogleGenerativeAIEmbeddings"),
        }
        if provider in loaders:
            mod_name, cls_name = loaders[provider]
            try:
                mod = __import__(mod_name, fromlist=[cls_name])
                return getattr(mod, cls_name)(model=model)
            except Exception as exc:
                msg = f"embedder '{spec}' unavailable: {exc}"
                raise _VectorUnavailableError(msg) from exc
    try:  # offline option
        from sentence_transformers import (  # ty: ignore[unresolved-import]
            SentenceTransformer,
        )

        model_name = os.environ.get("DEEPAGENTS_MANIFEST_ST_MODEL", "all-MiniLM-L6-v2")
        st = SentenceTransformer(model_name)
        return _SentenceTransformerShim(st)
    except Exception as exc:
        msg = (
            "no embedder configured (set DEEPAGENTS_MANIFEST_EMBEDDER or"
            " install sentence-transformers)"
        )
        raise _VectorUnavailableError(msg) from exc


class _Encoder(Protocol):
    """The slice of the ``SentenceTransformer`` API the shim relies on."""

    def encode(
        self, sentences: list[str], *, normalize_embeddings: bool
    ) -> Sequence[Sequence[float]]:
        """Encode sentences into embedding vectors."""
        ...


class _SentenceTransformerShim:
    """Adapt a ``SentenceTransformer`` to LangChain's embeddings API."""

    def __init__(self, model: _Encoder) -> None:
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents.

        Returns:
            One normalized float vector per input text.
        """
        return [
            list(map(float, v))
            for v in self._model.encode(texts, normalize_embeddings=True)
        ]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string.

        Returns:
            The normalized float vector for ``text``.
        """
        return list(
            map(float, self._model.encode([text], normalize_embeddings=True)[0])
        )


def _pack(vec: Sequence[float]) -> bytes:
    """Serialize a float vector to the little-endian f32 blob sqlite-vec expects.

    Returns:
        The packed little-endian float32 byte blob.
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def _cards(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return ``(comp_uid, card_text)`` for every component.

    Returns:
        One ``(comp_uid, card)`` pair per component row.
    """
    rows = con.execute(
        "SELECT comp_uid,"
        " COALESCE(name,'') || ' — ' || COALESCE(group_name,'')"
        " || ' (' || COALESCE(installed_on,'') || '). '"
        " || COALESCE(description,'')"
        " || CASE WHEN use_cases IS NOT NULL AND use_cases <> ''"
        " THEN ' Use cases: ' || use_cases ELSE '' END AS card"
        " FROM component"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def build_index(con: sqlite3.Connection) -> int:
    """Embed every component card into the ``vec_cards`` sqlite-vec table.

    Run once, offline, after ``build_manifest_db``. Requires an embedder (see the
    module docstring); the ``_load_sqlite_vec`` / ``_embedder`` helpers raise
    ``_VectorUnavailableError`` if the store or an embedder is missing.

    Returns:
        The number of component cards indexed.
    """
    _load_sqlite_vec(con)
    embedder = _embedder()
    cards = _cards(con)
    if not cards:
        return 0
    vectors = embedder.embed_documents([c for _, c in cards])
    dim = len(vectors[0])
    con.execute(f"DROP TABLE IF EXISTS {_VEC_TABLE}")
    con.execute(
        f"CREATE VIRTUAL TABLE {_VEC_TABLE}"
        f" USING vec0(comp_uid TEXT, embedding float[{dim}])"
    )
    # S608: table name is a fixed constant; the inserted values use ``?`` params.
    con.executemany(
        f"INSERT INTO {_VEC_TABLE}(comp_uid, embedding) VALUES (?, ?)",  # noqa: S608
        [(uid, _pack(vec)) for (uid, _), vec in zip(cards, vectors, strict=True)],
    )
    con.commit()
    logger.info("manifest_vector: indexed %d cards (dim=%d)", len(cards), dim)
    return len(cards)


def search(
    con: sqlite3.Connection,
    query: str,
    product: str | None = None,
    installed_on: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Nearest-neighbour component search for ``query``.

    Args mirror ``manifest_db.search_substring`` so ``search_components`` can swap
    them.

    Returns:
        Up to ``limit`` matching component rows, each with a ``distance`` score.

    Raises:
        _VectorUnavailableError: If the vector store, embedder or index is missing.
    """
    _load_sqlite_vec(con)
    has_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (_VEC_TABLE,),
    ).fetchone()
    if not has_table:
        msg = "vector index not built (call build_index first)"
        raise _VectorUnavailableError(msg)
    qvec = _pack(_embedder().embed_query(query))
    # S608: ``_VEC_TABLE`` is a fixed constant; all values use ``?`` params.
    knn = con.execute(
        f"SELECT comp_uid, distance FROM {_VEC_TABLE}"  # noqa: S608
        " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (qvec, max(limit * 3, limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for comp_uid, distance in knn:
        row = con.execute(
            "SELECT comp_uid, release_id, comp_id, name, group_name,"
            " installed_on, description"
            " FROM component WHERE comp_uid = ?",
            (comp_uid,),
        ).fetchone()
        if row is None:
            continue
        rec = dict(row)
        if product and not rec["release_id"].startswith(f"{product}:"):
            continue
        if installed_on and rec["installed_on"] != installed_on:
            continue
        rec["distance"] = round(float(distance), 4)
        out.append(rec)
        if len(out) >= limit:
            break
    return out
