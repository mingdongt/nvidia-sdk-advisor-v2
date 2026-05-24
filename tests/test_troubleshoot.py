import os
import asyncio
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def test_format_excerpt_renders_key_fields():
    from src.troubleshoot import _format_excerpt_for_terminal
    excerpt = {
        "target": "JETSON_ORIN_NX_TARGETS",
        "host_os": "linux",
        "jetpack_version": "6.1",
        "timestamp": "2026-05-22 14:33:21",
        "tail_text": "E: Unable to locate package nvidia-jetpack=6.1*\n",
        "file_count": 1,
        "total_size_bytes": 1024,
        "source_path": "/tmp/log.zip",
    }
    text = _format_excerpt_for_terminal(excerpt)
    assert "JETSON_ORIN_NX_TARGETS" in text
    assert "6.1" in text
    assert "linux" in text


def test_format_excerpt_handles_missing_metadata():
    """When filename didn't encode metadata, formatter should indicate so."""
    from src.troubleshoot import _format_excerpt_for_terminal
    excerpt = {
        "target": None, "host_os": None, "jetpack_version": None, "timestamp": None,
        "tail_text": "some log content", "file_count": 1, "total_size_bytes": 100,
        "source_path": "/tmp/log.log",
    }
    text = _format_excerpt_for_terminal(excerpt)
    assert "filename did not encode" in text


def test_extract_fix_script_pulls_bash_block():
    from src.troubleshoot import _extract_fix_script
    md = "Some prose.\n\n```bash\nsudo apt update\nsudo apt install x\n```\n\nMore prose."
    script = _extract_fix_script(md)
    assert script is not None
    assert "sudo apt update" in script
    assert "sudo apt install x" in script


def test_extract_fix_script_returns_none_if_no_block():
    from src.troubleshoot import _extract_fix_script
    assert _extract_fix_script("Just prose, no bash block.") is None


def test_label_from_excerpt_combines_target_and_jetpack():
    from src.troubleshoot import _label_from_excerpt
    label = _label_from_excerpt({
        "target": "JETSON_ORIN_NX_TARGETS",
        "jetpack_version": "6.1",
    })
    assert "orin_nx" in label
    assert "jp6" in label


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
@pytest.mark.timeout(180)
def test_run_troubleshoot_end_to_end_apt_case():
    """End-to-end on the apt fixture: log parsed, agent reads tail, fix synthesized.

    Web search may or may not be available — the test only requires that
    a fix recommendation is produced.
    """
    from src.troubleshoot import run_troubleshoot
    log = str(_FIXTURES / "apt_missing_package.log")
    result = asyncio.run(run_troubleshoot(log, auto_confirm=False, write_fix=False))
    assert "excerpt" in result
    assert result["excerpt"]["tail_text"]  # log was read
    assert "fix_recommendation" in result
    assert len(result["fix_recommendation"]) > 0
