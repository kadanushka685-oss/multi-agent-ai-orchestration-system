"""Planning Agent: turns a task into a dependency-ordered plan and assigns each step to an agent.

Demo mode builds the plan with explicit rules: focus topics come from the task words that appear
in the knowledge base, and the stages follow research, analysis, writing, verification. Live mode
asks the configured language model for a JSON plan, and Pydantic checks that plan before use.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from agents.base import BaseAgent, parse_json_object
from agents.prompts import PLANNING_INSTRUCTIONS, system_prompt
from config.settings import AGENT_NAMES
from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, Plan, PlanStep
from orchestration.retry import AgentExecutionError
from services.security import wrap_untrusted
from services.text_utils import STOPWORDS, keywords, normalise_word

MAX_FOCUS_TERMS = 4
MAX_ENTITY_TERMS = 2


def entity_terms(task: str, store, limit: int = MAX_ENTITY_TERMS) -> list[str]:
    """Capitalised words after the first word, such as regions named for a comparison.

    Named entities are research targets in their own right, so each one gets its own research step
    even when it is common across the knowledge base.
    """
    found: list[str] = []
    words = re.findall(r"[A-Za-z][A-Za-z\-]*", task)
    for index, word in enumerate(words):
        if index == 0 or not word[0].isupper():
            continue
        term = normalise_word(word.lower())
        if len(term) >= 3 and term not in STOPWORDS and term not in found and store.document_frequency(term) > 0:
            found.append(term)
    return found[:limit]


def focus_terms(task: str, store, limit: int = MAX_FOCUS_TERMS) -> list[str]:
    """Task keywords that appear in the knowledge base, most specific first.

    Terms found in fewer documents are more specific, so they rank higher. A word such as
    "market" that appears everywhere says little about where to look.
    """
    candidates = keywords(task)
    ranked = sorted(
        ((store.document_frequency(term), index, term) for index, term in enumerate(candidates)),
        key=lambda item: (item[0], item[1]),
    )
    subject = [term for frequency, _, term in ranked if frequency > 0][:limit]
    return subject + [term for term in entity_terms(task, store) if term not in subject]


def build_default_plan(task: str, enabled, store) -> Plan:
    """Rule-based plan: one research step per focus topic, then analysis, writing and verification."""
    enabled_set = set(enabled)
    terms = focus_terms(task, store)
    steps: list[PlanStep] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"S{counter}"

    research_ids: list[str] = []
    if "research" in enabled_set:
        for term in terms or [""]:
            step_id = next_id()
            research_ids.append(step_id)
            label = term or "the task"
            steps.append(
                PlanStep(
                    id=step_id,
                    agent="research",
                    title=f"Gather sources on '{label}'",
                    instruction=f"Search the sample documents for statements about {label} and assign source ids.",
                    depends_on=[],
                    focus=term,
                )
            )

    analysis_id = None
    if "analysis" in enabled_set:
        analysis_id = next_id()
        steps.append(
            PlanStep(
                id=analysis_id,
                agent="analysis",
                title="Analyse the evidence",
                instruction="Find recurring topics, compare the statements and flag conflicting figures.",
                depends_on=list(research_ids),
            )
        )

    writing_dependencies = ([analysis_id] if analysis_id else []) + research_ids
    writing_id = next_id()
    steps.append(
        PlanStep(
            id=writing_id,
            agent="writing",
            title="Write the report",
            instruction="Draft a structured report in which every finding cites its source.",
            depends_on=writing_dependencies,
        )
    )

    if "verification" in enabled_set:
        steps.append(
            PlanStep(
                id=next_id(),
                agent="verification",
                title="Verify the report",
                instruction="Check citations, figures and completeness, and label each statement.",
                depends_on=[writing_id],
            )
        )

    assumptions = [
        "Sources come only from the local sample knowledge base. No live web search is performed.",
        "Focus topics are the task words that appear most often across the sample documents: "
        + (", ".join(terms) if terms else "none matched, so the whole task is searched."),
    ]
    return Plan(task=task, steps=steps, assumptions=assumptions, planner="rule-based")


class PlanningAgent(BaseAgent):
    name = "planning"
    display_name = "Planning Agent"
    role = "Breaks the task into a dependency-ordered plan and assigns each step to an agent."
    responsibilities = (
        "Find the focus topics in the task that the knowledge base can answer.",
        "Create one research step per focus topic, then analysis, writing and verification steps.",
        "Record which steps depend on which, so the orchestrator runs them in a valid order.",
        "State the assumptions behind the plan so the user can review them.",
    )

    def __init__(self, store, llm=None):
        super().__init__(llm)
        self.store = store

    def build_plan(self, task: str, enabled) -> Plan:
        """Produce a plan. The orchestrator validates it before any step runs."""
        if self.uses_llm:
            return self._llm_plan(task, enabled)
        return build_default_plan(task, enabled, self.store)

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        plan = self.build_plan(context.task, AGENT_NAMES)
        return self._output(
            step,
            f"Planned {len(plan.steps)} step(s) using the {plan.planner} planner.",
            {"steps": [item.model_dump() for item in plan.steps], "assumptions": plan.assumptions},
        )

    def _llm_plan(self, task: str, enabled) -> Plan:
        user = (
            f"Task:\n{wrap_untrusted('user_task', task)}\n\n"
            f"Enabled agents: {', '.join(sorted(enabled))}\n"
            f"Sample documents: {', '.join(self.store.documents)}"
        )
        reply = self._ask_llm(system_prompt("planning", PLANNING_INSTRUCTIONS), user)
        data = parse_json_object(reply)
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise AgentExecutionError("The model plan contained no steps.")
        try:
            steps = [PlanStep(**item) for item in raw_steps]
            assumptions = [str(item)[:300] for item in data.get("assumptions", []) if str(item).strip()][:10]
            return Plan(task=task, steps=steps, assumptions=assumptions, planner=f"llm:{self.llm.provider}")
        except (ValidationError, TypeError, ValueError) as exc:
            raise AgentExecutionError(f"The model plan failed validation: {exc}") from exc
