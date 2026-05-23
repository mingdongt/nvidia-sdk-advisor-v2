"""Fetch README.md from a curated list of GitHub repos via REST API.

No GitHub token required; rate limit 60 req/hour without auth.
Set GH_TOKEN env var to raise to 5000/hour.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

_API = "https://api.github.com/repos"
_TIMEOUT = 20


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_readme(repo: str, branch: str = "main") -> dict | None:
    """Try main, then master. Returns dict or None on miss."""
    for br in (branch, "master"):
        url = f"{_API}/{repo}/contents/README.md?ref={br}"
        try:
            resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
        except requests.RequestException as e:
            print(f"  ERR {repo}: {e}", file=sys.stderr)
            return None
        if resp.status_code == 200:
            data = resp.json()
            content_b64 = data.get("content", "")
            try:
                text = base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception as e:
                print(f"  decode err {repo}: {e}", file=sys.stderr)
                return None
            return {
                "repo": repo,
                "branch": br,
                "url": data.get("html_url", f"https://github.com/{repo}"),
                "sha": data.get("sha", ""),
                "text": text,
            }
        elif resp.status_code == 404:
            continue
        elif resp.status_code == 403:
            print(f"  RATELIMIT — set GH_TOKEN to raise to 5000/hr", file=sys.stderr)
            return None
    return None


def save_readmes(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def main() -> None:
    seed = Path(__file__).resolve().parents[1] / "data" / "github_seed_list.txt"
    out = Path(__file__).resolve().parents[1] / "data" / "corpus" / "github" / "readmes.jsonl"
    repos = [line.strip() for line in seed.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]

    print(f"Fetching {len(repos)} READMEs...")
    records = []
    for repo in repos:
        record = fetch_readme(repo)
        if record:
            records.append(record)
            print(f"  OK {repo} ({len(record['text'])} chars)")
        else:
            print(f"  miss {repo}")
        time.sleep(0.5)  # gentle rate limit even with token
    save_readmes(records, out)
    print(f"\nWrote {len(records)} READMEs to {out}")


if __name__ == "__main__":
    main()
