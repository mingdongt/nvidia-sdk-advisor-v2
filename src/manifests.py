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
