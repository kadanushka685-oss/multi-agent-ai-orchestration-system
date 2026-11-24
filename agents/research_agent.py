"""Research Agent: collects and organises the statements relevant to one focus topic."""
from __future__ import annotations

from agents.base import BaseAgent
from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, PlanStep
from services.text_utils import keywords


class ResearchAgent(BaseAgent):
    name = "research"
    display_name = "Research Agent"
    role = "Collects and organises the statements that are relevant to one focus topic."
    responsibilities = (
        "Search the local sample documents for statements that match the step's focus terms.",
        "Register each statement once and give it a stable source id (S1, S2, ...).",
        "Report coverage gaps instead of guessing when nothing relevant is found.",
    )

    def __init__(self, store, llm=None):
        super().__init__(llm)
        self.store = store

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        focus = step.focus.strip()
        label = focus or "the task"
        terms = keywords(focus) if focus else keywords(context.task)
        hits = self.store.search(terms, top_k=10)
        if focus and focus not in context.focus_terms:
            context.focus_terms.append(focus)
        source_ids = [context.add_source(h["document"], h["position"], h["text"], label) for h in hits]
        warnings = [] if hits else [f"No sample statements matched '{label}'."]
        data = {
            "focus": focus,
            "search_terms": terms,
            "source_ids": source_ids,
            "documents_searched": len(self.store.documents),
            "handoffs_received": len(inbox),
        }
        summary = (
            f"Retrieved {len(source_ids)} statement(s) for '{label}' "
            f"from {len(self.store.documents)} sample document(s)."
        )
        return self._output(step, summary, data, warnings)
