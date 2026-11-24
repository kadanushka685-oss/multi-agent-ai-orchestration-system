
"""End-to-end orchestration in demo mode: ordering, outputs, labels, selection and limits."""
import json

import pytest

from config.settings import SAMPLE_TASKS_PATH, Settings
from orchestration.engine import NOT_RUN_BANNER
from orchestration.state import TERMINAL_STATUSES
from services.document_store import DocumentStore
from services.security import InputError
from tests.helpers import make_orchestrator

SAMPLES = json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8"))

TASK = (
    "Compare artificial intelligence and machine learning trends in healthcare, "
    "analyse real-world applications and challenges, and write an executive brief."
)

INJECTION = next(item["task"] for item in SAMPLES if item["id"] == "injection")


def test_automatic_run_completes_every_step_in_dependency_order(orchestrator):
    run = orchestrator.create_run(TASK)
    assert run.status == "running"
    orchestrator.run_to_completion(run)
    assert run.status in ("completed", "completed_with_warnings")
    order = [record.id for record in run.steps]
    for record in run.steps:
        assert record.status == "completed"
        for dependency in record.step.depends_on:
            assert order.index(dependency) < order.index(record.id)
    assert run.executions == len(run.steps)


def test_all_five_agents_produce_stored_outputs(orchestrator, db):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    stored = {row["agent"] for row in db.get_outputs(run.task_id)}
    assert stored == {"planning", "research", "analysis", "writing", "verification"}


def test_final_response_is_verified_and_cited(orchestrator):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    text = run.context.final_response
    assert text.startswith("> Verification:")
    assert "[VERIFIED]" in text
    assert "[UNSUPPORTED]" not in text
    assert "## Sources" in text


def test_conflicting_survey_figures_are_surfaced_as_a_warning(
    db, settings, tmp_path
):
    survey_dir = tmp_path / "survey_documents"
    survey_dir.mkdir()

    (survey_dir / "survey_results.md").write_text(
        """
        # European Hospital AI Adoption Survey

        European hospitals reported that artificial intelligence adoption reached 18% in clinical care.

        European hospitals reported that artificial intelligence adoption reached 22% in clinical care.
        """,
        encoding="utf-8",
    )

    survey_store = DocumentStore(directory=survey_dir)
    survey_orchestrator = make_orchestrator(db, survey_store, settings)

    survey_task = (
        "Compare artificial intelligence adoption figures reported by "
        "European hospitals and explain any conflicting survey results."
    )

    run = survey_orchestrator.create_run(survey_task)
    survey_orchestrator.run_to_completion(run)

    pairs = {
        frozenset((c["a_value"], c["b_value"]))
        for c in run.context.contradictions
    }

    assert frozenset({18.0, 22.0}) in pairs

    kinds = {
        issue["kind"]
        for issue in run.context.verification["issues"]
    }
    assert "inconsistency" in kinds
    assert run.status == "completed_with_warnings"


def test_prompt_injection_is_flagged_and_treated_as_data(orchestrator, db):
    run = orchestrator.create_run(INJECTION)
    orchestrator.run_to_completion(run)
    assert "instruction override" in run.injection_flags
    assert run.status == "completed_with_warnings"
    events = [entry["event"] for entry in db.get_logs(run.task_id)]
    assert "injection_detected" in events
    assert run.context.sources == []
    assert run.error == ""


def test_every_sample_task_reaches_a_terminal_state_without_unsupported_claims(orchestrator):
    for sample in SAMPLES:
        run = orchestrator.create_run(sample["task"])
        orchestrator.run_to_completion(run)
        assert run.status in TERMINAL_STATUSES, sample["id"]
        assert run.error == "", sample["id"]
        verification = run.context.verification or {}
        assert verification.get("unsupported", 0) == 0, sample["id"]
        assert verification.get("verdict") in ("passed", "passed_with_warnings"), sample["id"]


def test_selection_without_the_writing_agent_is_rejected(orchestrator):
    with pytest.raises(InputError):
        orchestrator.create_run(TASK, selected_agents=["research", "analysis"])


def test_unknown_agent_names_are_rejected(orchestrator):
    with pytest.raises(InputError):
        orchestrator.create_run(TASK, selected_agents=["research", "writing", "wizard"])


def test_a_run_without_verification_is_labelled_unverified(orchestrator):
    run = orchestrator.create_run(TASK, selected_agents=["research", "writing"])
    orchestrator.run_to_completion(run)
    assert {record.step.agent for record in run.steps} == {"research", "writing"}
    assert run.context.verification is None
    assert run.context.final_response.startswith(NOT_RUN_BANNER)
    assert run.status == "completed_with_warnings"


def test_a_disabled_agent_is_left_out_of_new_plans(orchestrator, db):
    db.set_agent_enabled("analysis", False)
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    assert "analysis" not in {record.step.agent for record in run.steps}
    assert run.status in ("completed", "completed_with_warnings")


def test_empty_and_oversized_tasks_are_rejected(orchestrator):
    with pytest.raises(InputError):
        orchestrator.create_run("too short")
    with pytest.raises(InputError):
        orchestrator.create_run("A" * 2500)


def test_planning_must_fit_the_step_limit(db, store, settings):
    orchestrator = make_orchestrator(db, store, settings)
    orchestrator.max_steps = 3
    run = orchestrator.create_run(TASK)
    assert run.status == "failed"
    assert "limit is 3" in run.error
    assert run.steps == []


def test_time_limit_stops_a_run_safely(db, store, tmp_path):
    counter = {"now": 0.0}

    def clock():
        counter["now"] += 100.0
        return counter["now"]

    tight = Settings(
        retry_backoff_seconds=0.0,
        max_run_seconds=150.0,
        db_path=tmp_path / "t.db",
    )
    from orchestration.engine import Orchestrator

    orchestrator = Orchestrator(
        db, store, tight, sleep=lambda seconds: None, clock=clock
    )
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    assert run.status == "failed"
    assert "time limit" in run.error
    assert any(
        record.status == "skipped" and "Stopped" in record.skip_reason
        for record in run.steps
    )
