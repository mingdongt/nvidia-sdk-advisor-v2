"""Brave Search API client (https://api.search.brave.com/).

Free tier: 2000 queries/month, 1 req/sec. No paid plan needed for portfolio demo.
"""
import os
import requests

_BASE_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = 15


class BraveSearchError(RuntimeError):
    pass


def brave_search(query: str, k: int = 5, site: str = "") -> list[dict]:
    """Run a Brave web search; return up to k normalized hits.

    site: optional domain filter. e.g. 'forums.developer.nvidia.com'.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        raise BraveSearchError("BRAVE_API_KEY not set; see .env.example")

    q = query
    if site:
        q = f"{q} site:{site}"

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": q,
        "count": min(k, 20),
        "result_filter": "web",
    }
    try:
        resp = requests.get(_BASE_URL, headers=headers, params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise BraveSearchError(f"network error: {e}") from e

    if resp.status_code == 429:
        raise BraveSearchError("rate limit (Brave free tier = 2000/mo, 1/s)")
    if resp.status_code != 200:
        raise BraveSearchError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    raw = data.get("web", {}).get("results", [])
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in raw[:k]
    ]
