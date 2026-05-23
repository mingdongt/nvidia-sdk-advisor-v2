import os
from unittest.mock import patch, MagicMock

import pytest

from src.brave_search import brave_search, BraveSearchError


def test_brave_search_returns_normalized_hits():
    fake_resp = MagicMock(
        status_code=200,
        json=lambda: {
            "web": {
                "results": [
                    {"title": "Result A", "url": "https://example/a", "description": "snippet A"},
                    {"title": "Result B", "url": "https://example/b", "description": "snippet B"},
                ],
            },
        },
    )
    with patch("src.brave_search.requests.get", return_value=fake_resp), \
         patch.dict(os.environ, {"BRAVE_API_KEY": "BSA-test"}):
        hits = brave_search("test query", k=5)
    assert len(hits) == 2
    assert hits[0]["url"] == "https://example/a"
    assert hits[0]["title"] == "Result A"
    assert "snippet" in hits[0]


def test_brave_search_site_filter_appends():
    """site:domain filter should be appended to query."""
    fake_resp = MagicMock(status_code=200, json=lambda: {"web": {"results": []}})
    with patch("src.brave_search.requests.get", return_value=fake_resp) as mock_get, \
         patch.dict(os.environ, {"BRAVE_API_KEY": "BSA-test"}):
        brave_search("orin nano", k=3, site="forums.developer.nvidia.com")
    call_args = mock_get.call_args
    assert "site:forums.developer.nvidia.com" in call_args[1]["params"]["q"]


def test_brave_search_missing_key_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(BraveSearchError):
            brave_search("anything")


def test_brave_search_429_raises_friendly():
    fake_resp = MagicMock(status_code=429, text="rate limit hit")
    with patch("src.brave_search.requests.get", return_value=fake_resp), \
         patch.dict(os.environ, {"BRAVE_API_KEY": "BSA-test"}):
        with pytest.raises(BraveSearchError) as excinfo:
            brave_search("anything")
        assert "rate" in str(excinfo.value).lower()
