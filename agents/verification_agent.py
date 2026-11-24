"""Verification Agent: checks the report against the evidence, without using a language model.

Verification is deliberately deterministic. A model that grades its own output, or a model that
can be steered by injected text, cannot be trusted to decide what is supported. Each statement is
classified as verified, unverified, a labelled inference, or unsupported.
"""
from __future__ import annotations

import re

from agents.base import BaseAgent
from agents.writing_agent import INFERENCE_TAG, PROCESS_TAG, REQUIRED_SECTIONS
from orchestration.context import TaskContext
from orchestration.models import AgentMessage, AgentOutput, PlanStep, VerificationIssue, VerificationResult
from services.text_utils import extract_numbers, keyword_set

CITATION = re.compile(r"\[S(\d+)\]")
CLAIM_SECTIONS = {"executive summary", "key findings", "analysis", "recommendations"}
SUPPORT_THRESHOLD = 0.3

LABELS = {
    "verified": " [VERIFIED]",
    "unverified": " [UNVERIFIED]",
    "unverified_inference": " [UNVERIFIED INFERENCE]",
    "unsupported": " [UNSUPPORTED]",
}


def parse_sections(markdown: str) -> tuple[set[str], list[tuple[str, int, str]]]:
    """Return the section names present and every bullet with its section and line number."""
    headings: set[str] = set()
    bullets: list[tuple[str, int, str]] = []
    section = ""
    for number, raw in enumerate((markdown or "").splitlines(), start=1):
        line = raw.strip()
        if line.lower().startswith("## "):
            section = line[3:].strip().lower()
            headings.add(section)
            continue
        if line.startswith("- "):
            bullets.append((section, number, line))
    return headings, bullets


def check_claim(text: str, sources: dict, line_no: int) -> tuple[str, VerificationIssue | None]:
    """Classify one bullet against the registered sources.

    The checks, in order: a citation is present, every cited id exists, every figure in the
    statement appears in the cited text, and most of the statement's content words appear there.
    """
    cited = [f"S{number}" for number in CITATION.findall(text)]
    if not cited:
        return "unsupported", VerificationIssue(
            severity="error", kind="unsupported_claim", message="Statement has no source citation.", line=line_no
        )
    unknown = [source_id for source_id in cited if source_id not in sources]
    if unknown:
        return "unsupported", VerificationIssue(
            severity="error", kind="unsupported_claim",
            message=f"Cites unknown source(s): {', '.join(unknown)}.", line=line_no,
        )

    body = CITATION.sub(" ", text)
    cited_text = " ".join(sources[source_id]["text"] for source_id in cited)
    cited_numbers = extract_numbers(cited_text)
    missing = [
        value for value in extract_numbers(body)
        if not any(abs(value - known) < 1e-9 for known in cited_numbers)
    ]
    if missing:
        listed = ", ".join(f"{value:g}" for value in missing)
        return "unsupported", VerificationIssue(
            severity="error", kind="unsupported_claim",
            message=f"Figure(s) {listed} do not appear in the cited source(s).", line=line_no,
        )

    words = keyword_set(body)
    support = len(words & keyword_set(cited_text)) / len(words) if words else 1.0
    if support < SUPPORT_THRESHOLD:
        return "unverified", VerificationIssue(
            severity="warning", kind="weak_support",
            message="Only part of this statement's wording appears in the cited source(s).", line=line_no,
        )
    return "verified", None


def annotate_report(draft: str, verification: dict) -> str:
    """Append a status label to every checked statement so readers can see what is supported."""
    status_by_line = {claim["line"]: claim["status"] for claim in verification.get("claims", [])}
    annotated = []
    for number, line in enumerate(draft.splitlines(), start=1):
        status = status_by_line.get(number, "")
        # Inference bullets already carry the [Unverified inference] tag written by the Writing Agent.
        suffix = "" if status == "unverified_inference" else LABELS.get(status, "")
        annotated.append(line + suffix)
    return "\n".join(annotated)


def verification_banner(verification: dict) -> str:
    verdict = verification.get("verdict")
    if verdict == "passed":
        return "> Verification: PASSED. No unsupported statements were found. Check the label on each statement."
    if verdict == "passed_with_warnings":
        count = sum(1 for issue in verification.get("issues", []) if issue.get("severity") == "warning")
        return f"> Verification: PASSED WITH WARNINGS ({count} warning(s)). Read the labelled statements."
    return "> Verification: FAILED. Review every [UNSUPPORTED] statement before using this report."


class VerificationAgent(BaseAgent):
    name = "verification"
    display_name = "Verification Agent"
    role = "Checks completeness, citations, figures and conflicts, then labels every statement."
    responsibilities = (
        "Confirm that all six required sections are present.",
        "Confirm that every factual statement cites a source that exists.",
        "Confirm that every figure in a statement appears in the cited source.",
        "Report unsupported statements so the Writing Agent can revise, and report conflicts and gaps.",
    )

    def handle(self, step: PlanStep, context: TaskContext, inbox: list[AgentMessage]) -> AgentOutput:
        headings, bullets = parse_sections(context.draft)
        sources = context.source_map()
        issues: list[VerificationIssue] = []
        claims: list[dict] = []
        unsupported_lines: list[str] = []
        counts = {"verified": 0, "unverified": 0, "unverified_inference": 0, "unsupported": 0}

        missing = [name for name in REQUIRED_SECTIONS if name.lower() not in headings]
        if missing:
            issues.append(VerificationIssue(
                severity="error", kind="incomplete_report",
                message=f"Missing section(s): {', '.join(missing)}.", line=0,
            ))

        for section, number, line in bullets:
            if section not in CLAIM_SECTIONS:
                continue
            text = line[2:].strip()
            if PROCESS_TAG in text:
                continue
            if INFERENCE_TAG in text:
                counts["unverified_inference"] += 1
                claims.append({"line": number, "section": section, "status": "unverified_inference", "text": text})
                continue
            status, issue = check_claim(text, sources, number)
            counts[status] += 1
            claims.append({"line": number, "section": section, "status": status, "text": text})
            if issue is not None:
                issues.append(issue)
            if status == "unsupported":
                unsupported_lines.append(line)

        for conflict in context.contradictions:
            issues.append(VerificationIssue(
                severity="warning", kind="inconsistency",
                message=(
                    f"Sources disagree: {conflict['a_id']} reports {conflict['a_value']:g} and "
                    f"{conflict['b_id']} reports {conflict['b_value']:g}."
                ),
            ))
        if not context.sources:
            issues.append(VerificationIssue(
                severity="warning", kind="missing_information",
                message="No sources were retrieved, so no statement could be checked against evidence.",
            ))
        for warning in context.warnings:
            if warning.startswith("No sample statements matched"):
                issues.append(VerificationIssue(severity="warning", kind="missing_information", message=warning))

        errors = [issue for issue in issues if issue.severity == "error"]
        verdict = "failed" if errors else ("passed_with_warnings" if issues else "passed")
        result = VerificationResult(
            verdict=verdict,
            verified=counts["verified"],
            unverified=counts["unverified"],
            inferences=counts["unverified_inference"],
            unsupported=counts["unsupported"],
            issues=issues,
            unsupported_lines=unsupported_lines,
            claims=claims,
        )
        context.verification = result.model_dump()
        context.final_response = (
            verification_banner(context.verification) + "\n\n" + annotate_report(context.draft, context.verification)
        )
        summary = (
            f"Verdict {verdict}: {counts['verified']} verified, {counts['unverified']} unverified, "
            f"{counts['unverified_inference']} labelled inference(s), {counts['unsupported']} unsupported."
        )
        return self._output(step, summary, {"verdict": verdict, "counts": counts}, [])
