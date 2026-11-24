"""Analysis Agent: finds recurring topics, compares same-kind figures and flags conflicting evidence.

Every comparison is traceable to specific statements. A figure is compared only with another
figure of the same kind. Two statements are reported as conflicting only when they describe the
same region and subject with different figures.
"""
from __future__ import annotations

import json
from collections import Counter

from agents.base import BaseAgent
from agents.prompts import ANALYSIS_INSTRUCTIONS, system_prompt
from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, PlanStep
from services.security import wrap_untrusted
from services.text_utils import jaccard, keyword_set, primary_figure

GEO_TERMS = frozenset({"european", "asian", "east", "southeast", "nordic", "southern", "western", "chinese", "china"})
REGION_LABELS = {"european": "European", "asian": "Asian"}
MAX_CONTRADICTIONS = 5
CONFLICT_SIMILARITY = 0.5


class AnalysisAgent(BaseAgent):
    name = "analysis"
    display_name = "Analysis Agent"
    role = "Finds recurring topics, compares same-kind figures and flags conflicting evidence."
    responsibilities = (
        "Count which subject terms recur across the retrieved statements.",
        "Compare the percentage figures between regions and cite the statement behind each comparison.",
        "Detect statements that describe the same region and subject with different figures.",
        "Attach to every insight the source ids that support it.",
    )

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        sources = context.sources
        if not sources:
            context.insights, context.contradictions, context.top_terms = [], [], []
            return self._output(
                step,
                "No statements were available to analyse.",
                {"insights": [], "contradictions": [], "top_terms": [], "handoffs_received": len(inbox)},
                ["Analysis had no statements to work with."],
            )

        frequency: Counter = Counter()
        for source in sources:
            frequency.update(keyword_set(source["text"]))
        top_terms = [term for term, count in frequency.most_common(5) if count >= 2]
        top_terms = top_terms or [term for term, _ in frequency.most_common(3)]

        insights: list[dict] = []
        if top_terms:
            citations = [s["id"] for s in sources if top_terms[0] in keyword_set(s["text"])][:3]
            insights.append({
                "text": f"Most frequently discussed topics: {', '.join(top_terms[:3])}.",
                "citations": citations,
            })

        insights.extend(self._region_comparisons(sources))

        contradictions = self._find_conflicts(sources)
        for conflict in contradictions:
            insights.append({
                "text": f"Sources disagree on a related figure: {conflict['a_value']:g} versus {conflict['b_value']:g}.",
                "citations": sorted({conflict["a_id"], conflict["b_id"]}),
            })

        narrative = ""
        if self.uses_llm:
            statements = "\n".join(f"[{s['id']}] {s['text']}" for s in sources)
            user = (
                f"Statements:\n{wrap_untrusted('sources', statements)}\n\n"
                f"Insights (JSON):\n{json.dumps(insights, ensure_ascii=False)}\n\n"
                f"Conflicts (JSON):\n{json.dumps(contradictions, ensure_ascii=False)}"
            )
            narrative = self._ask_llm(system_prompt("analysis", ANALYSIS_INSTRUCTIONS), user)

        context.insights = insights
        context.contradictions = contradictions
        context.top_terms = top_terms
        context.narrative = narrative
        data = {
            "insights": insights,
            "contradictions": contradictions,
            "top_terms": top_terms,
            "region_comparisons": len(insights) - len(contradictions) - (1 if top_terms else 0),
            "handoffs_received": len(inbox),
        }
        summary = f"Produced {len(insights)} insight(s) and found {len(contradictions)} conflicting figure pair(s)."
        return self._output(step, summary, data, [])

    @staticmethod
    def _region_comparisons(sources: list[dict]) -> list[dict]:
        """For each region, the highest percentage among its statements, with the statement cited."""
        comparisons = []
        for region, label in REGION_LABELS.items():
            candidates = []
            for source in sources:
                if region not in keyword_set(source["text"]):
                    continue
                figure = primary_figure(source["text"])
                if figure is not None and figure[1] == "percent":
                    candidates.append((figure[0], source))
            if candidates:
                value, source = max(candidates, key=lambda item: item[0])
                comparisons.append({
                    "text": f"Highest percentage among the {label} statements: {value:g}%.",
                    "citations": [source["id"]],
                })
        return comparisons

    @staticmethod
    def _find_conflicts(sources: list[dict]) -> list[dict]:
        conflicts: list[dict] = []
        for index, first in enumerate(sources):
            first_figure = primary_figure(first["text"])
            if first_figure is None:
                continue
            first_terms = keyword_set(first["text"])
            for second in sources[index + 1:]:
                second_figure = primary_figure(second["text"])
                if second_figure is None or second_figure[1] != first_figure[1]:
                    continue
                if second_figure[0] == first_figure[0]:
                    continue
                second_terms = keyword_set(second["text"])
                if first_terms & GEO_TERMS != second_terms & GEO_TERMS:
                    continue
                if jaccard(first_terms, second_terms) < CONFLICT_SIMILARITY:
                    continue
                conflicts.append({
                    "a_id": first["id"],
                    "b_id": second["id"],
                    "a_value": first_figure[0],
                    "b_value": second_figure[0],
                    "a_text": first["text"],
                    "b_text": second["text"],
                })
                if len(conflicts) >= MAX_CONTRADICTIONS:
                    return conflicts
        return conflicts
