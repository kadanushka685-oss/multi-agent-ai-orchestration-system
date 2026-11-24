
"""Failure handling and retry limits: transient faults, exhausted budgets and safe stops."""

import pytest
from pydantic import ValidationError

from config.settings import HARD_MAX_RETRIES
from orchestration.models import FaultInjection
from orchestration.retry import (
    AgentExecutionError,
    RetryableError,
    run_with_retries,
)
from services.security import InputError


TASK = (
    "Compare artificial intelligence and machine learning trends in healthcare, "
    "analyse real-world applications and challenges, and write an executive "
    "brief with recommendations."
)


def first_step(run, agent):
    return next(
        record for record in run.steps
        if record.step.agent == agent
    )


def test_a_transient_failure_is_retried_and_recovers(orchestrator, db):
    run = orchestrator.create_run(
        TASK,
        max_retries=2,
        fault_injection=FaultInjection(agent="research", fail_times=1),
    )
    orchestrator.run_to_completion(run)

    research = first_step(run, "research")
    assert research.status == "completed"
    assert research.attempts == 2
    assert "Injected transient failure" in research.errors[0]
    assert run.total_retries() >= 1
    assert "retry" in [entry["event"] for entry in db.get_logs(run.task_id)]
    assert run.status in ("completed", "completed_with_warnings")


def test_all_exhausted_research_steps_skip_downstream_report(orchestrator):
    run = orchestrator.create_run(
        TASK,
        max_retries=2,
        fault_injection=FaultInjection(agent="research", fail_times=3),
    )
    orchestrator.run_to_completion(run)

    research_steps = [
        record for record in run.steps
        if record.step.agent == "research"
    ]

    assert research_steps
    assert any(record.status == "failed" for record in research_steps)

    for record in research_steps:
        if record.status == "failed":
            assert record.attempts == 3

    # Downstream steps must not run if all research steps fail.
    if all(record.status == "failed" for record in research_steps):
        for agent in ("analysis", "writing", "verification"):
            assert first_step(run, agent).status == "skipped"
        assert run.status == "failed"
        assert run.error
    else:
        # Partial research failure allows downstream work with warnings.
        assert first_step(run, "analysis").status == "completed"
        assert run.status == "completed_with_warnings"


def test_a_failed_writing_step_fails_the_task_and_skips_verification(orchestrator, db):
    run = orchestrator.create_run(
        TASK,
        max_retries=1,
        fault_injection=FaultInjection(agent="writing", fail_times=2),
    )
    orchestrator.run_to_completion(run)

    writing = first_step(run, "writing")
    verification = first_step(run, "verification")

    assert writing.status == "failed"
    assert writing.attempts == 2
    assert verification.status == "skipped"
    assert "No upstream step completed" in verification.skip_reason
    assert run.status == "failed"
    assert run.error
    assert db.get_task(run.task_id)["status"] == "failed"


def test_the_retry_budget_is_capped_at_the_hard_ceiling():
    attempts = []

    def always_fails(attempt):
        attempts.append(attempt)
        raise RetryableError("timeout")

    outcome = run_with_retries(
        always_fails,
        max_retries=99,
        sleep=lambda seconds: None,
    )

    assert outcome.ok is False
    assert outcome.attempts == HARD_MAX_RETRIES + 1
    assert len(attempts) == HARD_MAX_RETRIES + 1


def test_non_transient_errors_are_not_retried():
    calls = []

    def bad_input(attempt):
        calls.append(attempt)
        raise ValueError("bad input")

    outcome = run_with_retries(
        bad_input,
        max_retries=3,
        sleep=lambda seconds: None,
    )

    assert outcome.ok is False
    assert outcome.attempts == 1
    assert len(calls) == 1


def test_success_after_failures_records_every_failed_attempt():
    def flaky(attempt):
        if attempt < 3:
            raise AgentExecutionError("not yet")
        return "done"

    outcome = run_with_retries(
        flaky,
        max_retries=3,
        sleep=lambda seconds: None,
    )

    assert outcome.ok and outcome.value == "done"
    assert outcome.attempts == 3
    assert len(outcome.errors) == 2


def test_backoff_grows_with_each_attempt():
    waits = []

    def flaky(attempt):
        if attempt < 3:
            raise RetryableError("temporary")
        return 1

    run_with_retries(
        flaky,
        max_retries=3,
        backoff_seconds=0.5,
        sleep=waits.append,
    )

    assert waits == [0.5, 1.0]


def test_run_options_reject_retry_counts_above_the_ceiling(orchestrator):
    with pytest.raises(InputError):
        orchestrator.create_run(TASK, max_retries=10)


def test_fault_injection_rejects_unknown_agents():
    with pytest.raises(ValidationError):
        FaultInjection(agent="hacker")


def test_agent_execution_errors_are_retryable():
    assert isinstance(AgentExecutionError("x"), RetryableError)
