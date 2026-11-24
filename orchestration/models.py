"""Typed data contracts (Pydantic v2) shared by the orchestrator, agents and reports.

Pydantic validates every plan produced by the Planning Agent (including plans written by a
language model), every message routed between agents, and every verification result.
"""
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

AgentName = Literal["planning", "research", "analysis", "writing", "verification"]


class PlanStep(BaseModel):
    """One unit of work. `depends_on` lists the ids of steps that must finish first."""

    id: str = Field(min_length=2, max_length=6)
    agent: AgentName
    title: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=600)
    depends_on: list[str] = Field(default_factory=list, max_length=10)
    focus: str = Field(default="", max_length=120)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not re.fullmatch(r"S\d{1,2}", value):
            raise ValueError("step ids must look like S1, S2, ...")
        return value


class Plan(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    planner: str = Field(default="rule-based", max_length=60)


class FaultInjection(BaseModel):
    """Demo-only switch that makes one agent fail a set number of times to exercise retries."""

    agent: AgentName
    fail_times: int = Field(default=1, ge=1, le=5)


class RunOptions(BaseModel):
    mode: Literal["automatic", "manual"] = "automatic"
    max_retries: int = Field(default=2, ge=0, le=3)
    fault_injection: Optional[FaultInjection] = None


class AgentOutput(BaseModel):
    """What an agent returns to the orchestrator after one step."""

    agent: AgentName
    step_id: str = Field(min_length=2, max_length=6)
    summary: str = Field(min_length=1, max_length=2000)
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=50)


class AgentMessage(BaseModel):
    """A message routed by the orchestrator between the orchestrator and an agent."""

    sender: str = Field(min_length=1, max_length=40)
    recipient: str = Field(min_length=1, max_length=40)
    message_type: Literal["task", "handoff", "revision"]
    step_id: str = Field(min_length=2, max_length=6)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default="", max_length=40)


class VerificationIssue(BaseModel):
    severity: Literal["error", "warning"]
    kind: str = Field(min_length=1, max_length=40)
    message: str = Field(min_length=1, max_length=400)
    line: int = Field(default=0, ge=0)


class VerificationResult(BaseModel):
    verdict: Literal["passed", "passed_with_warnings", "failed"]
    verified: int = Field(default=0, ge=0)
    unverified: int = Field(default=0, ge=0)
    inferences: int = Field(default=0, ge=0)
    unsupported: int = Field(default=0, ge=0)
    issues: list[VerificationIssue] = Field(default_factory=list)
    unsupported_lines: list[str] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
