"""Eval engine schemas.

CaseSpec defines an eval case (input + expected + metadata). One JSONL line per case.

RunRecord is one (case, arm, sample) result. Append to eval/runs/<run_id>.jsonl,
one row per record. Scoring fields are filled by the scorers in eval/scorers/.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


CaseLayer = Literal["L1", "L2", "L3"]
CaseTrack = Literal["smoke", "reasoning", "troubleshoot", "adversarial"]


class CaseSpec(BaseModel):
    case_id: str
    layer: CaseLayer
    track: CaseTrack
    input: str
    expected: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output_text: str = ""
    latency_s: float = 0.0


class RunRecord(BaseModel):
    run_id: str
    git_sha: Optional[str] = None
    prompt_version: Optional[str] = None
    model: str
    arm: str = "main"
    sample_index: int = 0

    case_id: str
    case_layer: CaseLayer
    case_track: CaseTrack
    case_input: str

    started_at: str
    ended_at: str
    latency_s: float
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    turns: Optional[int] = None
    tool_sequence: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)

    output_text: str = ""
    output_files: list[str] = Field(default_factory=list)

    scores: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
