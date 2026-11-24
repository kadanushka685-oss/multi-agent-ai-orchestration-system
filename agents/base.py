"""Base class for agents and helpers shared by them."""
from __future__ import annotations

import json
import re

from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, PlanStep
from orchestration.retry import AgentExecutionError


class BaseAgent:
    """An agent receives one step and the shared context, and returns one AgentOutput.

    Agents never call other agents and never write to the database. The orchestrator delivers
    their inputs, records their outputs, and routes any handoff messages between them.
    """

    name = ""
    display_name = ""
    role = ""
    responsibilities: tuple[str, ...] = ()

    def __init__(self, llm=None):
        self.llm = llm

    @property
    def uses_llm(self) -> bool:
        return self.llm is not None

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        raise NotImplementedError

    def _output(self, step: PlanStep, summary: str, data: dict | None = None, warnings=None) -> AgentOutput:
        return AgentOutput(
            agent=self.name,
            step_id=step.id,
            summary=(summary or "Step finished.")[:2000],
            data=data or {},
            warnings=list(warnings or []),
        )

    def _ask_llm(self, system: str, user: str) -> str:
        if self.llm is None:
            raise RuntimeError("_ask_llm called without a language model client.")
        text = self.llm.complete(system, user)
        if not text or not text.strip():
            raise AgentExecutionError(f"{self.display_name} received an empty reply from the language model.")
        return text.strip()


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from a model reply, tolerating code fences."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise AgentExecutionError("The model reply did not contain a JSON object.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentExecutionError(f"The model reply was not valid JSON: {exc.msg}.") from exc
    if not isinstance(data, dict):
        raise AgentExecutionError("The model reply JSON was not an object.")
    return data
