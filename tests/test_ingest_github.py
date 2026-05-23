from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile

from ingest.scrape_github_samples import fetch_readme, save_readmes


def test_fetch_readme_decodes_base64_content():
    """GitHub REST API returns base64-encoded content."""
    import base64
    fake_content = "# Test README\n\nHello world."
    fake_resp = MagicMock(
        status_code=200,
        json=lambda: {
            "name": "README.md",
            "content": base64.b64encode(fake_content.encode()).decode(),
            "encoding": "base64",
            "sha": "abc123",
        },
    )
    with patch("ingest.scrape_github_samples.requests.get", return_value=fake_resp):
        result = fetch_readme("test/repo")
    assert result["repo"] == "test/repo"
    assert result["text"] == fake_content
    assert result["sha"] == "abc123"


def test_save_readmes_writes_jsonl():
    records = [{"repo": "x/y", "text": "hi"}, {"repo": "a/b", "text": "yo"}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "readmes.jsonl"
        save_readmes(records, out)
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["repo"] == "x/y"
