import os
import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


def test_format_diagnosis_renders_key_fields():
    from src.troubleshoot import _format_diagnosis
    diag = {
        "failed_stage": "apt", "error_signature": "E: Unable to locate package nvidia-jetpack=6.1*",
        "error_class": "apt-missing-package", "target": "JETSON_ORIN_NX_TARGETS",
        "host_os": "ubuntu22.04", "jetpack_version": "6.1", "timestamp": "2026-05-22 14:33:21",
        "last_successful_step": "apt-get update completed", "raw_excerpt": "...",
        "search_terms": ["nvidia-jetpack apt unable to locate"],
    }
    text = _format_diagnosis(diag)
    assert "apt-missing-package" in text
    assert "JETSON_ORIN_NX_TARGETS" in text
    assert "6.1" in text


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


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
@pytest.mark.timeout(180)
def test_run_troubleshoot_end_to_end_apt_case():
    """End-to-end on the apt fixture: log parsed, fix synthesized.

    Brave Search may or may not be available; the test only requires that
    a fix recommendation is produced (forum context optional).
    """
    from src.troubleshoot import run_troubleshoot
    log = str(_FIXTURES / "apt_missing_package.log")
    result = asyncio.run(run_troubleshoot(log, auto_confirm=False, write_fix=False))
    assert result["diagnosis"]["error_class"] == "apt-missing-package"
    assert "fix_recommendation" in result
    assert len(result["fix_recommendation"]) > 0
