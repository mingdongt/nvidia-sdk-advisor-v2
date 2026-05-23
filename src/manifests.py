"""KnowledgeBase: in-memory facade over the fetched NVIDIA CDN manifests.

Schema reference (verified from real CDN data):
- sdkml1_repo.json:    {productCategories: [{categoryName, productLines: [{targetOS, releasesIndexURL}]}]}
- sdkml1_repo_hw.json: {families: [{name, uri}]}
- per-product:         {releases: [{title, releaseVersion, supportedHardware, ...}]}
- per-hw-family:       {series: [{id, uri}]}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional


class KnowledgeBase:
    def __init__(self, manifests_dir: Path):
        self._dir = Path(manifests_dir)
        if not (self._dir / "sdkml1_repo.json").exists():
            raise FileNotFoundError(
                f"Master product index not found at {self._dir / 'sdkml1_repo.json'}. "
                f"Run `python -m ingest.fetch_manifests` first."
            )

    @lru_cache(maxsize=1)
    def _master_products_raw(self) -> dict:
        return json.loads((self._dir / "sdkml1_repo.json").read_text(encoding="utf-8"))

    @lru_cache(maxsize=1)
    def _master_hw_raw(self) -> dict:
        return json.loads((self._dir / "sdkml1_repo_hw.json").read_text(encoding="utf-8"))

    def list_products(self) -> list[dict]:
        """Flatten productCategories[].productLines[] into a list of entries.

        Each entry: {name: categoryName, targetOS: ..., releasesIndexURL: ...}
        """
        out = []
        for cat in self._master_products_raw().get("productCategories", []):
            name = cat.get("categoryName")
            for line in cat.get("productLines", []):
                out.append({
                    "name": name,
                    "targetOS": line.get("targetOS"),
                    "releasesIndexURL": line.get("releasesIndexURL"),
                })
        return out

    def _product_manifest_paths(self, product: str) -> list[Path]:
        """Return all per-product manifest file paths for a product name (across targetOS)."""
        paths = []
        for entry in self.list_products():
            if entry["name"] == product:
                rel = (entry.get("releasesIndexURL") or "").lstrip("./")
                while rel.startswith("../"):
                    rel = rel[3:]
                candidate = self._dir / rel
                if candidate.exists():
                    paths.append(candidate)
        return paths

    def list_releases(self, product: str) -> list[dict]:
        """All releases across all productLines for a product."""
        releases = []
        for path in self._product_manifest_paths(product):
            data = json.loads(path.read_text(encoding="utf-8"))
            releases.extend(data.get("releases", []))
        return releases

    def get_release(self, product: str, version: str) -> Optional[dict]:
        for r in self.list_releases(product):
            if r.get("releaseVersion") == version:
                return r
        return None

    def list_hardware(self, family: str) -> list[dict]:
        """Return series[] from the hardware family manifest."""
        for fam in self._master_hw_raw().get("families", []):
            if fam.get("name", "").lower() == family.lower():
                rel = (fam.get("uri") or "").lstrip("./")
                while rel.startswith("../"):
                    rel = rel[3:]
                path = self._dir / rel
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return data.get("series", [])
        return []

    _ALIASES = {
        "JETSON_AGX_THOR_TARGETS": ["agx thor", "thor", "jetson thor"],
        "JETSON_AGX_ORIN_TARGETS": ["agx orin", "jetson agx orin", "agx orin 32gb", "agx orin 64gb"],
        "JETSON_ORIN_NX_TARGETS": ["orin nx", "orin nx 8gb", "orin nx 16gb", "jetson orin nx"],
        "JETSON_ORIN_NANO_TARGETS": ["orin nano", "orin nano 4gb", "orin nano 8gb", "jetson orin nano"],
        "JETSON_AGX_XAVIER_TARGETS": ["agx xavier", "xavier agx", "xavier"],
        "JETSON_XAVIER_NX_TARGETS": ["xavier nx", "jetson xavier nx"],
        "JETSON_NANO_TARGETS": ["nano", "jetson nano", "jetson nano 4gb"],
        "JETSON_TX2_TARGETS": ["tx2", "jetson tx2"],
        "JETSON_TX1_TARGETS": ["tx1", "jetson tx1"],
    }

    def lookup_target_id(self, board_name: str) -> Optional[dict]:
        """Resolve free-text board name to a canonical target_id.

        Tries three matching strategies in order:
        1. Canonical: exact lowercase match against target_id itself
        2. Alias: exact lowercase match against known aliases
        3. Fuzzy: word overlap (requires at least 2 matching words)

        Returns dict with keys:
        - target_id: canonical ID string (e.g., "JETSON_ORIN_NANO_TARGETS")
        - matched_on: strategy used ("canonical", "alias", or "fuzzy")

        Returns None if no match found.
        """
        q = board_name.lower().strip()

        # Strategy 1: canonical (exact match against target_id)
        for target_id, aliases in self._ALIASES.items():
            if q == target_id.lower():
                return {"target_id": target_id, "matched_on": "canonical"}

        # Strategy 2: alias (exact match against known aliases)
        for target_id, aliases in self._ALIASES.items():
            for alias in aliases:
                if q == alias:
                    return {"target_id": target_id, "matched_on": "alias"}

        # Strategy 3: fuzzy (word overlap)
        q_words = set(q.split())
        best_id, best_overlap = None, 0
        for target_id, aliases in self._ALIASES.items():
            for alias in aliases:
                a_words = set(alias.split())
                overlap = len(q_words & a_words)
                if overlap > best_overlap and overlap >= 2:
                    best_overlap, best_id = overlap, target_id

        if best_id:
            return {"target_id": best_id, "matched_on": "fuzzy"}

        return None
