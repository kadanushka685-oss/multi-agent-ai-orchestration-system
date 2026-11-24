"""Agent communication: every message goes through the orchestrator's bus."""
import pytest

from config.settings import AGENT_NAMES, SAMPLE_TASKS_PATH
from orchestration.messages import MessageBus
from orchestration.models import AgentMessage

import json

TASK = next(item["task"] for item in json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8")) if item["id"] == "ev-brief")
AGENTS = set(AGENT_NAMES)


def message(recipient, step_id="S1", kind="handoff", sender="research"):
    return AgentMessage(sender=sender, recipient=recipient, message_type=kind, step_id=step_id, payload={}, created_at="t")


def test_bus_delivers_only_to_the_addressed_agent_and_step():
    bus = MessageBus()
    bus.send(message("writing", "S3"))
    bus.send(message("analysis", "S2"))
    assert bus.pending_count == 2
    taken = bus.take_for("writing", "S3")
    assert len(taken) == 1 and taken[0].recipient == "writing"
    assert bus.take_for("writing", "S3") == []
    assert bus.pending_count == 1
    assert len(bus.history) == 2


def test_bus_rejects_unknown_parties():
    with pytest.raises(ValueError):
        MessageBus().send(message("ghost"))


def test_orchestrator_routes_task_and_handoff_messages(orchestrator):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    history = run.bus.history
    tasks = [item for item in history if item.message_type == "task"]
    handoffs = [item for item in history if item.message_type == "handoff"]
    assert len(tasks) == len(run.steps)
    assert all(item.sender == "orchestrator" for item in tasks)
    assert handoffs, "dependent steps must receive handoff messages"
    for item in handoffs:
        assert item.sender in AGENTS and item.recipient in AGENTS
        assert item.sender != item.recipient
        target = run.step(item.step_id)
        assert target.step.agent == item.recipient
        assert item.payload["from_step"] in target.step.depends_on


def test_every_message_is_delivered_by_the_end_of_the_run(orchestrator):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    assert run.bus.pending_count == 0


def test_writing_agent_receives_inputs_from_upstream_agents(orchestrator):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    writing = next(record for record in run.steps if record.step.agent == "writing")
    assert writing.output.data["handoffs_received"] >= 2


def test_handoffs_and_step_events_are_logged_in_order(orchestrator, db):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    events = [entry["event"] for entry in db.get_logs(run.task_id)]
    assert "handoff" in events
    assert events.index("step_started") < events.index("step_completed")
