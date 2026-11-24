"""Runtime state of one orchestration run. The database keeps the durable copy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from orchestration.context import TaskContext
from orchestration.messages import MessageBus
from orchestration.models import AgentOutput, Plan, PlanStep, RunOptions

TERMINAL_STATUSES = frozenset({"completed", "completed_with_warnings", "failed"})
RESOLVED_STATUSES = frozenset({"completed", "failed", "skipped"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepRecord:
    step: PlanStep
    status: str = "pending"
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    skip_reason: str = ""
    output: Optional[AgentOutput] = None

    @property
    def id(self) -> str:
        return self.step.id


@dataclass
class RunState:
    task_id: str
    task_text: str
    options: RunOptions
    selected_agents: list[str]
    context: TaskContext
    status: str = "planning"
    plan: Optional[Plan] = None
    steps: list[StepRecord] = field(default_factory=list)
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    revisions: int = 0
    executions: int = 0
    error: str = ""
    injection_flags: list[str] = field(default_factory=list)
    fault_remaining: int = 0
    clock_start: float = 0.0
    bus: MessageBus = field(default_factory=MessageBus)

    @property
    def mode(self) -> str:
        return self.options.mode

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def step(self, step_id: str) -> StepRecord:
        for record in self.steps:
            if record.id == step_id:
                return record
        raise KeyError(step_id)

    def total_retries(self) -> int:
        return sum(max(0, record.attempts - 1) for record in self.steps)
