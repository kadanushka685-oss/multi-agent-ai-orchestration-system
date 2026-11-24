
"""Each agent on its own: its inputs, its outputs and its rules."""
from agents import build_agents, self_test
from agents.analysis_agent import AnalysisAgent
from agents.research_agent import ResearchAgent
from agents.verification_agent import (
    VerificationAgent,
    annotate_report,
    check_claim,
    parse_sections,
)
from agents.writing_agent import REQUIRED_SECTIONS, WritingAgent
from orchestration.context import TaskContext
from orchestration.models import PlanStep


def step(agent, sid="S1", focus="", deps=None):
    return PlanStep(
        id=sid,
        agent=agent,
        title=f"{agent} step",
        instruction="do the work",
        depends_on=deps or [],
        focus=focus,
    )


def research_context(store, *focuses):
    context = TaskContext(task="AI and machine learning analysis test")
    researcher = ResearchAgent(store)
    for index, focus in enumerate(focuses, start=1):
        researcher.handle(
            step("research", sid=f"S{index}", focus=focus),
            context,
            [],
        )
    return context


def conflict_context():
    context = TaskContext(task="Compare regional AI adoption figures")
    context.sources = [
        {
            "id": "S1",
            "text": "European AI adoption reached 18% in healthcare systems.",
            "document": "regional_demo.md",
            "position": 1,
        },
        {
            "id": "S2",
            "text": "European AI adoption reached 22% in healthcare systems.",
            "document": "regional_demo.md",
            "position": 2,
        },
        {
            "id": "S3",
            "text": "Asian AI adoption reached 25% in healthcare systems.",
            "document": "regional_demo.md",
            "position": 3,
        },
    ]
    return context


def test_every_agent_declares_a_role_and_responsibilities(store):
    agents = build_agents(store)
    assert set(agents) == {
        "planning", "research", "analysis", "writing", "verification"
    }
    for name, agent in agents.items():
        assert agent.name == name
        assert agent.role
        assert len(agent.responsibilities) >= 3
        assert agent.display_name.endswith("Agent")


def test_research_agent_registers_statements_with_stable_ids(store):
    context = TaskContext(task="machine learning")
    output = ResearchAgent(store).handle(
        step("research", focus="machine"),
        context,
        [],
    )
    assert output.agent == "research"
    assert output.step_id == "S1"
    assert output.data["source_ids"] == [
        source["id"] for source in context.sources
    ]
    assert context.sources
    assert context.sources[0]["id"] == "S1"
    assert context.focus_terms == ["machine"]


def test_research_agent_does_not_duplicate_statements(store):
    context = TaskContext(task="machine learning")
    researcher = ResearchAgent(store)
    researcher.handle(
        step("research", sid="S1", focus="machine"),
        context,
        [],
    )
    researcher.handle(
        step("research", sid="S2", focus="machine"),
        context,
        [],
    )
    keys = [
        (source["document"], source["position"])
        for source in context.sources
    ]
    assert len(keys) == len(set(keys))


def test_research_agent_reports_coverage_gaps_instead_of_guessing(store):
    context = TaskContext(task="x")
    output = ResearchAgent(store).handle(
        step("research", focus="submarine"),
        context,
        [],
    )
    assert output.warnings == [
        "No sample statements matched 'submarine'."
    ]
    assert context.sources == []


def test_analysis_finds_regional_comparisons_and_the_conflicting_pair():
    context = conflict_context()
    output = AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    texts = [item["text"] for item in context.insights]

    assert any(
        text.startswith("Highest percentage among the European statements")
        for text in texts
    )
    assert any(
        text.startswith("Highest percentage among the Asian statements")
        for text in texts
    )

    pairs = {
        frozenset((item["a_value"], item["b_value"]))
        for item in context.contradictions
    }
    assert frozenset({18.0, 22.0}) in pairs
    assert output.summary.startswith("Produced")


def test_analysis_cites_only_statements_it_retrieved(store):
    context = research_context(store, "machine")
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    known = {source["id"] for source in context.sources}

    for item in context.insights:
        assert set(item["citations"]) <= known
        assert item["citations"]


def test_analysis_with_no_statements_says_so():
    context = TaskContext(task="empty")
    output = AnalysisAgent().handle(step("analysis"), context, [])
    assert output.warnings == [
        "Analysis had no statements to work with."
    ]
    assert context.insights == []


def test_writing_agent_produces_all_six_sections_with_citations(store):
    context = research_context(store, "machine")
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    WritingAgent().handle(step("writing", sid="S10"), context, [])

    headings, bullets = parse_sections(context.draft)
    for name in REQUIRED_SECTIONS:
        assert name.lower() in headings

    claims = [
        line
        for section, _, line in bullets
        if section in ("executive summary", "key findings", "analysis")
    ]
    assert claims
    assert all(
        "[S" in line or "[Process note]" in line
        for line in claims
    )


def test_recommendations_are_labelled_as_inferences(store):
    context = research_context(store, "artificial")
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    WritingAgent().handle(step("writing", sid="S10"), context, [])

    recommendations = [
        line
        for section, _, line in parse_sections(context.draft)[1]
        if section == "recommendations"
    ]
    assert recommendations
    assert all(
        "[Unverified inference]" in line or "[Process note]" in line
        for line in recommendations
    )


def test_writing_without_sources_says_so_instead_of_inventing():
    context = TaskContext(task="nothing relevant")
    WritingAgent().handle(step("writing"), context, [])
    assert "No matching statements were retrieved." in context.draft
    assert "No sources were used." in context.draft


def test_revision_removes_only_the_rejected_statement(store):
    context = research_context(store, "machine")
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    WritingAgent().handle(step("writing", sid="S10"), context, [])

    _, before = parse_sections(context.draft)
    findings = [
        line for section, _, line in before
        if section == "key findings"
    ]
    assert findings

    context.feedback = [findings[0]]
    WritingAgent().handle(step("writing", sid="S11"), context, [])

    _, after = parse_sections(context.draft)
    remaining = [
        line for section, _, line in after
        if section == "key findings"
    ]
    assert findings[0] not in remaining
    assert len(remaining) == len(findings) - 1
    assert context.feedback == []
    assert any(
        "removed during revision" in line
        for _, _, line in after
    )


def test_check_claim_classifies_each_failure_mode():
    sources = {
        "S1": {
            "id": "S1",
            "text": "Machine learning systems can identify patterns in historical data.",
        }
    }
    assert check_claim(
        "Machine learning systems can identify patterns in historical data [S1]",
        sources,
        3,
    )[0] == "verified"

    status, issue = check_claim(
        "Machine learning improves everything.",
        sources,
        4,
    )
    assert status == "unsupported"
    assert "no source citation" in issue.message

    status, issue = check_claim(
        "AI adoption reached 22% [S9]",
        sources,
        5,
    )
    assert status == "unsupported"
    assert "unknown source" in issue.message

    status, issue = check_claim(
        "AI adoption reached 77% [S1]",
        sources,
        6,
    )
    assert status == "unsupported"
    assert "77" in issue.message

    status, issue = check_claim(
        "Quantum teleportation of cargo ships reached the moon [S1]",
        sources,
        7,
    )
    assert status == "unverified"
    assert issue.kind == "weak_support"


def test_verification_passes_a_clean_report_and_labels_every_statement(store):
    context = research_context(store, "machine")
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    WritingAgent().handle(step("writing", sid="S10"), context, [])
    VerificationAgent().handle(
        step("verification", sid="S11"),
        context,
        [],
    )

    assert context.verification["verdict"] in (
        "passed",
        "passed_with_warnings",
    )
    assert context.verification["unsupported"] == 0
    assert "[VERIFIED]" in context.final_response
    assert context.final_response.startswith("> Verification:")


def test_verification_fails_a_report_with_missing_sections_and_bare_claims():
    context = TaskContext(task="t")
    context.draft = (
        "# Brief\n"
        "## Executive summary\n"
        "- Unsourced claim about prices.\n"
    )
    VerificationAgent().handle(step("verification"), context, [])

    result = context.verification
    assert result["verdict"] == "failed"
    kinds = {issue["kind"] for issue in result["issues"]}
    assert {"incomplete_report", "unsupported_claim"} <= kinds
    assert result["unsupported_lines"] == [
        "- Unsourced claim about prices."
    ]
    assert "FAILED" in context.final_response


def test_verification_reports_source_conflicts_as_warnings():
    context = conflict_context()
    AnalysisAgent().handle(step("analysis", sid="S9"), context, [])
    WritingAgent().handle(step("writing", sid="S10"), context, [])
    VerificationAgent().handle(
        step("verification", sid="S11"),
        context,
        [],
    )

    kinds = {
        issue["kind"]
        for issue in context.verification["issues"]
    }
    assert "inconsistency" in kinds
    assert context.verification["verdict"] == "passed_with_warnings"


def test_annotation_adds_labels_without_duplicating_inference_tags():
    draft = (
        "## Key findings\n"
        "- A fact [S1]\n"
        "- Idea [Unverified inference]\n"
    )
    verification = {
        "claims": [
            {"line": 2, "status": "verified"},
            {"line": 3, "status": "unverified_inference"},
        ]
    }
    lines = annotate_report(draft, verification).splitlines()
    assert lines[1].endswith("[VERIFIED]")
    assert lines[2] == "- Idea [Unverified inference]"


def test_self_test_runs_every_agent_on_real_inputs(store):
    for name in (
        "planning",
        "research",
        "analysis",
        "writing",
        "verification",
    ):
        output = self_test(name, store)
        assert output.agent == name
        assert output.summary
