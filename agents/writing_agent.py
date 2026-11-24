"""Writing Agent: converts analysed evidence into a structured, citation-backed report.

On a revision round the Writing Agent receives the statements that verification rejected and
removes them. It never adds unsupported facts to fix a rejection.
"""
from __future__ import annotations

import json

from agents.base import BaseAgent
from agents.prompts import WRITING_INSTRUCTIONS, system_prompt
from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, PlanStep
from services.security import wrap_untrusted
from services.text_utils import keyword_set

REQUIRED_SECTIONS = ("Executive summary", "Key findings", "Analysis", "Recommendations", "Limitations", "Sources")
MAX_KEY_FINDINGS = 8
INFERENCE_TAG = "[Unverified inference]"
PROCESS_TAG = "[Process note]"


def _bullet(text: str) -> str:
    return f"- {text}"


def _note(text: str) -> str:
    return f"- {PROCESS_TAG} {text}"


def _cites(ids: list[str]) -> str:
    return " ".join(f"[{source_id}]" for source_id in ids)


def _one_line(text: str) -> str:
    return " ".join(text.split())


class WritingAgent(BaseAgent):
    name = "writing"
    display_name = "Writing Agent"
    role = "Converts the analysed evidence into a structured report in which every finding cites its source."
    responsibilities = (
        "Write the six required report sections in a fixed order.",
        "Cite every factual statement with a source id from the Research Agent.",
        "Label recommendations as unverified inferences, never as facts.",
        "On revision, remove only the statements that verification rejected.",
    )

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        revision = bool(context.feedback)
        draft = self._llm_draft(context, revision) if self.uses_llm else self._template_draft(context)
        context.draft = draft
        rejected = len(context.feedback)
        context.feedback = []
        data = {
            "revision": revision,
            "rejected_statements_received": rejected,
            "sections": list(REQUIRED_SECTIONS),
            "source_count": len(context.sources),
            "handoffs_received": len(inbox),
        }
        action = "Revised" if revision else "Drafted"
        return self._output(step, f"{action} the report with {len(context.sources)} source(s).", data, [])

    def _template_draft(self, context: TaskContext) -> str:
        rejected = set(context.feedback)
        removed = 0

        def keep(line: str) -> bool:
            nonlocal removed
            if line in rejected:
                removed += 1
                return False
            return True

        summary_lines = [
            _bullet(f"{item['text']} {_cites(item['citations'])}")
            for item in context.insights[:2]
            if item.get("citations")
        ]
        finding_lines = [_bullet(f"{s['text']} [{s['id']}]") for s in context.sources[:MAX_KEY_FINDINGS]]
        analysis_lines = [
            _bullet(f"{item['text']} {_cites(item['citations'])}") for item in context.insights if item.get("citations")
        ]
        recommendation_lines = self._recommendations(context)
        limitation_lines = [
            _bullet("The knowledge base is a small local sample and contains no live market data."),
            _bullet("Figures come from synthetic sample documents and must be checked against primary sources."),
        ] + [_bullet(warning) for warning in context.warnings]
        source_lines = [
            _bullet(f"{s['id']}: {s['document']} (statement {s['position'] + 1})") for s in context.sources
        ]

        sections = {
            "Executive summary": [l for l in summary_lines if keep(l)]
            or [_note("No statement has enough evidence for a summary.")],
            "Key findings": [l for l in finding_lines if keep(l)]
            or [_note("No matching statements were retrieved.")],
            "Analysis": [l for l in analysis_lines if keep(l)]
            or [_note("The analysis produced no cited insights.")],
            "Recommendations": [l for l in recommendation_lines if keep(l)]
            or [_note("No recommendations were generated from the available evidence.")],
            "Limitations": limitation_lines,
            "Sources": source_lines or [_note("No sources were used.")],
        }
        if removed:
            sections["Limitations"].append(
                _note(f"{removed} statement(s) were removed during revision because they lacked supporting sources.")
            )

        lines = [f"# Brief: {_one_line(context.task)[:90]}", ""]
        for name in REQUIRED_SECTIONS:
            lines.append(f"## {name}")
            lines.extend(sections[name])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _recommendations(self, context: TaskContext) -> list[str]:
        lines: list[str] = []
        for conflict in context.contradictions[:2]:
            lines.append(_bullet(
                f"Resolve the conflicting figures before publishing: {conflict['a_value']:g} in "
                f"{conflict['a_id']} and {conflict['b_value']:g} in {conflict['b_id']}. {INFERENCE_TAG}"
            ))
        if context.top_terms:
            lines.append(_bullet(
                f"Monitor '{context.top_terms[0]}' in future updates, because it recurs across the sources. {INFERENCE_TAG}"
            ))
        missing = [
            term for term in context.focus_terms
            if not any(term in keyword_set(s["text"]) for s in context.sources)
        ]
        if missing:
            lines.append(_bullet(f"Collect additional sources on: {', '.join(missing)}. {INFERENCE_TAG}"))
        return lines

    def _llm_draft(self, context: TaskContext, revision: bool) -> str:
        sources_block = wrap_untrusted(
            "sources", "\n".join(f"[{s['id']}] {s['text']}" for s in context.sources)
        )
        user = (
            f"Task:\n{wrap_untrusted('user_task', context.task)}\n\n"
            f"Sources:\n{sources_block}\n\n"
            f"Analysis insights (JSON):\n{json.dumps(context.insights, ensure_ascii=False)}\n\n"
            f"Analysis notes:\n{context.narrative or 'none'}\n"
        )
        if revision:
            user += "\nRevision required. Remove these statements or support them with sources:\n"
            user += "\n".join(context.feedback) + "\n"
        return self._ask_llm(system_prompt("writing", WRITING_INSTRUCTIONS), user)
