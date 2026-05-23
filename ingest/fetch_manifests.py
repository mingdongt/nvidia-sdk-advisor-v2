"""Fetch NVIDIA SDK Manager configuration manifests from public CDN.

Run once during project setup. Outputs to data/manifests/. Re-runnable to refresh.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

MASTER_PRODUCT_URL = "https://developer.download.nvidia.com/sdkmanager/sdkm-config/main/sdkml1_repo.json"
MASTER_HW_URL = "https://developer.download.nvidia.com/sdkmanager/sdkm-config-hw/sdkml1_repo_hw.json"
_TIMEOUT = 30


def _fetch_json(url: str) -> dict:
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _write_relative(out_dir: Path, base_url: str, rel_url: str, payload: dict) -> Path:
    """Resolve rel_url against base_url, mirror the path layout into out_dir."""
    abs_url = urljoin(base_url, rel_url)
    for marker in ("/sdkm-config/", "/sdkm-config-hw/"):
        if marker in abs_url:
            rel_path = abs_url.split(marker, 1)[1]
            break
    else:
        rel_path = abs_url.rsplit("/", 1)[-1]
    target = out_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def fetch_manifest_tree(out_dir: Path) -> None:
    """Fetch both master indexes and all referenced child manifests."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Products
    product_index = _fetch_json(MASTER_PRODUCT_URL)
    (out_dir / "sdkml1_repo.json").write_text(json.dumps(product_index, indent=2), encoding="utf-8")

    # Handle both old (sdkml1_release) and new (productCategories) formats
    releases = product_index.get("sdkml1_release", [])
    if not releases and "productCategories" in product_index:
        # Flatten productCategories -> productLines
        for cat in product_index.get("productCategories", []):
            releases.extend(cat.get("productLines", []))

    for entry in releases:
        rel = entry.get("releasesIndexURL")
        if not rel:
            continue
        try:
            child = _fetch_json(urljoin(MASTER_PRODUCT_URL, rel))
            _write_relative(out_dir, MASTER_PRODUCT_URL, rel, child)
            display_name = entry.get('productDisplayName') or entry.get('targetOS', '?')
            print(f"  OK {display_name} -> {rel}")
        except requests.HTTPError as e:
            display_name = entry.get('productDisplayName') or entry.get('targetOS', '?')
            print(f"  ERR {display_name}: {e}", file=sys.stderr)

    # Hardware
    hw_index = _fetch_json(MASTER_HW_URL)
    (out_dir / "sdkml1_repo_hw.json").write_text(json.dumps(hw_index, indent=2), encoding="utf-8")

    # Handle both old (sdkml1_release_hw) and new (families) formats
    hw_entries = hw_index.get("sdkml1_release_hw", [])
    if not hw_entries and "families" in hw_index:
        hw_entries = hw_index.get("families", [])

    for entry in hw_entries:
        # Handle both key names: "uri" (new) or "hwIndexURL"/"releasesIndexURL" (old)
        rel = entry.get("uri") or entry.get("hwIndexURL") or entry.get("releasesIndexURL")
        if not rel:
            continue
        try:
            child = _fetch_json(urljoin(MASTER_HW_URL, rel))
            _write_relative(out_dir, MASTER_HW_URL, rel, child)
            display_name = entry.get('productDisplayName') or entry.get('name', '?')
            print(f"  OK HW {display_name} -> {rel}")
        except requests.HTTPError as e:
            display_name = entry.get('productDisplayName') or entry.get('name', '?')
            print(f"  ERR HW {display_name}: {e}", file=sys.stderr)


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "data" / "manifests"
    print(f"Fetching to {target}")
    fetch_manifest_tree(target)
    print("Done.")
