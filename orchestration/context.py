"""Shared task context: the blackboard that every agent reads from and writes to.

Agents never read each other's internal state. They receive this context from the orchestrator,
and the orchestrator decides what each agent may see and in what order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class TaskContext:
    task: str
    sources: list[dict] = field(default_factory=list)
    focus_terms: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    insights: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    top_terms: list[str] = field(default_factory=list)
    narrative: str = ""
    draft: str = ""
    feedback: list[str] = field(default_factory=list)
    verification: dict | None = None
    final_response: str = ""
    warnings: list[str] = field(default_factory=list)

    def add_source(self, document: str, position: int, text: str, focus: str) -> str:
        """Register a statement once and return its stable id (S1, S2, ...)."""
        for source in self.sources:
            if source["document"] == document and source["position"] == position:
                return source["id"]
        source_id = f"S{len(self.sources) + 1}"
        self.sources.append(
            {"id": source_id, "document": document, "position": position, "text": text, "focus": focus}
        )
        return source_id

    def source_map(self) -> dict[str, dict]:
        return {source["id"]: source for source in self.sources}

    def to_dict(self) -> dict:
        return asdict(self)
