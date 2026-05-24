"""Fetch NVIDIA NGC catalog metadata for curated containers.

NGC public REST API (no auth required for metadata):
  https://api.ngc.nvidia.com/v2/repos/{org}/{name}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

_BASE = "https://api.ngc.nvidia.com/v2/repos"
_TIMEOUT = 20


def fetch_container_metadata(container_id: str) -> dict | None:
    """Fetch metadata for a single org/name. Returns None on 404 or network error."""
    url = f"{_BASE}/{container_id}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  ERR {container_id}: {e}", file=sys.stderr)
        return None

    return {
        "name": container_id,
        "display_name": data.get("displayName") or data.get("name", container_id),
        "description": data.get("description", ""),
        "labels": data.get("labels", {}),
        "image_size_bytes": data.get("imageInfo", {}).get("compressedSize", 0),
        "latest_tag": data.get("latestImageId") or data.get("latestTag", ""),
        "architectures": data.get("supportedArchitectures", []) or [],
        "raw_url": url,
    }


def save_catalog(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def main() -> None:
    seed = Path(__file__).resolve().parents[1] / "data" / "ngc_seed_list.txt"
    out = Path(__file__).resolve().parents[1] / "data" / "corpus" / "ngc" / "containers.jsonl"
    container_ids = [line.strip() for line in seed.read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.startswith("#")]

    print(f"Fetching {len(container_ids)} containers...")
    records = []
    for cid in container_ids:
        meta = fetch_container_metadata(cid)
        if meta:
            records.append(meta)
            print(f"  OK {cid}")
        else:
            print(f"  miss {cid}")
    save_catalog(records, out)
    print(f"\nWrote {len(records)} records to {out}")


if __name__ == "__main__":
    main()
