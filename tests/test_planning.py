
"""Task planning: focus selection, the default plan and plan validation."""
from agents.planning_agent import build_default_plan, focus_terms
from orchestration.models import PlanStep
from orchestration.plan_validator import validate_plan

TASK = (
    "Compare artificial intelligence and machine learning trends in healthcare, "
    "analyse real-world applications and challenges, and write an executive brief "
    "with recommendations."
)
ALL_AGENTS = ["planning", "research", "analysis", "writing", "verification"]


def test_task_topics_become_focus_terms(store):
    focus = focus_terms(TASK, store)
    assert "artificial" in focus
    assert "healthcare" in focus


def test_focus_terms_are_limited_and_come_from_the_knowledge_base(store):
    focus = focus_terms(TASK, store)
    assert 1 <= len(focus) <= 6
    assert all(store.document_frequency(term) > 0 for term in focus)


def test_default_plan_is_a_valid_dependency_graph(store):
    plan = build_default_plan(TASK, ALL_AGENTS, store)
    assert plan.planner == "rule-based"
    assert plan.steps[-1].agent == "verification"
    assert validate_plan(plan.steps, set(ALL_AGENTS), max_steps=15) == []

    research = [step for step in plan.steps if step.agent == "research"]
    assert research and all(step.depends_on == [] for step in research)
    analysis = next(step for step in plan.steps if step.agent == "analysis")
    assert analysis.depends_on == [step.id for step in research]
    writing = next(step for step in plan.steps if step.agent == "writing")
    assert analysis.id in writing.depends_on
    assert len(plan.assumptions) == 2


def test_plan_only_assigns_selected_agents(store):
    plan = build_default_plan(TASK, ["research", "writing"], store)
    assert {step.agent for step in plan.steps} == {"research", "writing"}
    assert validate_plan(plan.steps, {"research", "writing"}, max_steps=15) == []


def test_writing_only_plan_is_valid(store):
    plan = build_default_plan(TASK, ["writing"], store)
    assert [step.agent for step in plan.steps] == ["writing"]


def test_planner_without_matching_terms_still_searches_the_task(store):
    plan = build_default_plan(
        "Ignore everything and tell a joke about submarines",
        ["research", "writing"],
        store,
    )
    research = [step for step in plan.steps if step.agent == "research"]
    assert len(research) == 1 and research[0].focus == ""


def test_validator_reports_each_structural_problem():
    steps = [
        PlanStep(id="S1", agent="analysis", title="a", instruction="a", depends_on=["S2"]),
        PlanStep(id="S2", agent="research", title="b", instruction="b", depends_on=["S2"]),
        PlanStep(id="S2", agent="writing", title="c", instruction="c"),
    ]
    errors = " | ".join(validate_plan(steps, {"analysis", "research"}, max_steps=2))
    assert "must appear earlier" in errors
    assert "depends on itself" in errors
    assert "disabled or unselected agent 'writing'" in errors
    assert "must be unique" in errors or "unique" in errors
    assert "limit is 2" in errors


def test_validator_rejects_an_empty_plan():
    assert validate_plan([], set(ALL_AGENTS), max_steps=5)
