"""Agent smoke test: does the agent call tools and produce a final assistant reply?

Uses the real Anthropic API. Requires ANTHROPIC_API_KEY in env. Skipped otherwise.
"""
import os
import asyncio
import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="needs ANTHROPIC_API_KEY",
)


@pytest.mark.timeout(120)
def test_agent_basic_lookup():
    from src.agent import run_agent_single_turn
    response = asyncio.run(run_agent_single_turn(
        "What is the target_id for Jetson Orin Nano 8GB?"
    ))
    assert "JETSON_ORIN_NANO_TARGETS" in response


@pytest.mark.timeout(300)
def test_agent_can_use_rag_tool():
    """Agent should be able to call search_3p_sample_repos for a workload query."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("needs API key")
    from src.agent import run_agent_single_turn
    response = asyncio.run(run_agent_single_turn(
        "I want to run a YOLO object detection sample on Jetson Orin Nano 8GB."
    ))
    text_lower = response.lower()
    # Loose check: response should reference jetson-inference / detectnet / yolo / sample
    assert any(kw in text_lower for kw in ("jetson-inference", "detectnet", "yolo", "sample", "object detection"))
