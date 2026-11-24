
"""Live mode: the same agents with a language model, scripted here so no network is used."""
import json

import pytest

from config.settings import Settings
from services.llm_client import (
    AnthropicMessagesClient,
    LLMConfigError,
    LLMError,
    OpenAIChatClient,
    build_llm_client,
    check_connection,
)
from tests.helpers import (
    ScriptedLLM,
    first_statement,
    good_report,
    make_orchestrator,
    unsupported_report,
)


TASK = (
    "Compare artificial intelligence and machine learning trends in healthcare, "
    "analyse real-world applications and challenges, and write an executive "
    "brief with recommendations."
)

NO_PLANNER = ["research", "analysis", "writing", "verification"]

VALID_PLAN = json.dumps({
    "steps": [
        {
            "id": "S1",
            "agent": "research",
            "title": "Research artificial intelligence",
            "instruction": "Find relevant artificial intelligence statements",
            "depends_on": [],
            "focus": "artificial",
        },
        {
            "id": "S2",
            "agent": "analysis",
            "title": "Analyse",
            "instruction": "Analyse the retrieved evidence",
            "depends_on": ["S1"],
            "focus": "",
        },
        {
            "id": "S3",
            "agent": "writing",
            "title": "Write",
            "instruction": "Draft the executive brief",
            "depends_on": ["S2", "S1"],
            "focus": "",
        },
        {
            "id": "S4",
            "agent": "verification",
            "title": "Verify",
            "instruction": "Check the report against sources",
            "depends_on": ["S3"],
            "focus": "",
        },
    ],
    "assumptions": ["Plan written by the scripted model."],
})

BAD_PLAN = json.dumps({
    "steps": [
        {
            "id": "S1",
            "agent": "research",
            "title": "Research",
            "instruction": "Find AI statements",
            "depends_on": [],
            "focus": "artificial",
        },
        {
            "id": "S2",
            "agent": "writing",
            "title": "Write",
            "instruction": "Draft",
            "depends_on": ["S9"],
            "focus": "",
        },
    ],
})


def analysis_reply(system, user):
    source_id, _ = first_statement(user)
    return f"- The retrieved statements describe the same market [{source_id}]"


def test_live_mode_uses_the_model_for_planning_analysis_and_writing(store, db, settings):
    llm = ScriptedLLM({
        "planning": [VALID_PLAN],
        "analysis": [analysis_reply],
        "writing": [good_report],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)

    assert run.plan.planner == "llm:scripted"
    assert llm.calls == ["planning", "analysis", "writing"]
    assert run.status in ("completed", "completed_with_warnings")
    assert run.context.narrative.startswith("- The retrieved")
    assert run.context.verification["unsupported"] == 0


def test_an_unparseable_plan_is_retried_then_accepted(store, db, settings):
    llm = ScriptedLLM({
        "planning": [
            "this is not json",
            '{"steps": [{"id": "X9", "agent": "research"}]}',
            VALID_PLAN,
        ],
        "analysis": [analysis_reply],
        "writing": [good_report],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK)

    assert run.plan.planner == "llm:scripted"
    assert llm.calls.count("planning") == 3
    assert [
        entry["event"] for entry in db.get_logs(run.task_id)
    ].count("retry") == 2


def test_a_plan_that_breaks_the_rules_is_rejected_after_retries(store, db, settings):
    llm = ScriptedLLM({
        "planning": [BAD_PLAN, BAD_PLAN, BAD_PLAN],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK)

    assert run.status == "failed"
    assert "Plan rejected" in run.error
    assert llm.calls.count("planning") == 3
    assert "analysis" not in llm.calls
    assert "writing" not in llm.calls


def test_one_unsupported_round_triggers_one_revision_then_passes(store, db, settings):
    llm = ScriptedLLM({
        "analysis": [analysis_reply],
        "writing": [unsupported_report, good_report],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK, selected_agents=NO_PLANNER)
    orchestrator.run_to_completion(run)

    assert run.revisions == 1
    assert llm.calls.count("writing") == 2
    assert run.context.verification["unsupported"] == 0
    assert "90 percent" not in run.context.draft
    assert "revision" in [
        entry["event"] for entry in db.get_logs(run.task_id)
    ]


def test_persistent_unsupported_claims_fail_after_a_single_revision(store, db, settings):
    llm = ScriptedLLM({
        "analysis": [analysis_reply],
        "writing": [unsupported_report, unsupported_report],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK, selected_agents=NO_PLANNER)
    orchestrator.run_to_completion(run)

    assert run.revisions == 1
    assert llm.calls.count("writing") == 2
    assert run.context.verification["verdict"] == "failed"
    assert run.status == "completed_with_warnings"
    assert "FAILED" in run.context.final_response
    assert "[UNSUPPORTED]" in run.context.final_response


def test_a_provider_outage_is_retried_and_then_fails_safely(store, db, settings):
    outage = LLMError("temporary outage at the provider", retryable=True)
    llm = ScriptedLLM({
        "planning": [outage, outage, outage],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK)

    assert run.status == "failed"
    assert "temporary outage" in run.error
    assert llm.calls.count("planning") == 3


def test_an_empty_model_reply_is_treated_as_a_retryable_failure(store, db, settings):
    llm = ScriptedLLM({
        "analysis": [analysis_reply],
        "writing": ["   ", good_report],
    })

    orchestrator = make_orchestrator(db, store, settings, llm=llm)
    run = orchestrator.create_run(TASK, selected_agents=NO_PLANNER)
    orchestrator.run_to_completion(run)

    writing = next(
        record for record in run.steps
        if record.step.agent == "writing"
    )

    assert writing.attempts == 2
    assert writing.status == "completed"


def test_api_keys_never_reach_the_database_or_the_log(store, db, tmp_path):
    key = "sk-LEAKCHECK-987654321"

    leaky = Settings(
        provider="openai",
        openai_api_key=key,
        retry_backoff_seconds=0.0,
        db_path=tmp_path / "k.db",
    )

    llm = ScriptedLLM({
        "planning": [
            LLMError(f"401 Unauthorised for key {key}", retryable=False)
        ],
    })

    orchestrator = make_orchestrator(db, store, leaky, llm=llm)
    run = orchestrator.create_run(TASK)

    assert run.status == "failed"

    stored = json.dumps(db.get_task(run.task_id), default=str)
    stored += json.dumps(db.get_logs(run.task_id), default=str)
    stored += run.error

    assert key not in stored
    assert "LEAKCHECK" not in stored
    assert "[REDACTED]" in stored


def test_openai_client_builds_a_chat_request_and_reads_the_reply():
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        return {
            "choices": [
                {"message": {"content": "hello from the model"}}
            ]
        }

    client = OpenAIChatClient(
        "sk-TEST-KEY-1234567",
        "gpt-test",
        "https://example.test/v1/",
        12.0,
        transport=transport,
    )

    assert client.complete("ROLE=planning\nrules", "question") == "hello from the model"

    url, headers, payload, timeout = calls[0]

    assert url == "https://example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-TEST-KEY-1234567"
    assert payload["messages"][0]["role"] == "system"
    assert payload["model"] == "gpt-test"
    assert timeout == 12.0


def test_anthropic_client_joins_text_blocks_and_ignores_other_blocks():
    def transport(url, headers, payload, timeout):
        assert headers["x-api-key"] == "sk-ant-TEST-123456789"
        assert payload["system"].startswith("ROLE=")

        return {
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "tool_use", "id": "x"},
                {"type": "text", "text": " part two"},
            ]
        }

    client = AnthropicMessagesClient(
        "sk-ant-TEST-123456789",
        "claude-test",
        10.0,
        transport=transport,
    )

    assert client.complete("ROLE=planning\nx", "y") == "part one part two"


def test_live_clients_refuse_to_start_without_a_key():
    with pytest.raises(LLMConfigError):
        build_llm_client(Settings(provider="openai", openai_api_key=""))

    with pytest.raises(LLMConfigError):
        build_llm_client(Settings(provider="anthropic", anthropic_api_key=""))

    assert build_llm_client(Settings(provider="mock")) is None


def test_connection_check_reports_failure_instead_of_raising():
    ok, message = check_connection(ScriptedLLM({}))

    assert ok is False
    assert message
