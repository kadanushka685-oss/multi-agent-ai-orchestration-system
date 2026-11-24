"""Database operations: round trips, filters, cascades, configuration and dashboard queries."""
import pytest

from database.repository import Database

START = "2026-01-10T09:00:00+00:00"


def make_task(db, task_id, status="completed", text="battery market brief", agents=("research", "writing"),
              created="2026-01-10T09:00:00+00:00", duration=None):
    db.create_task(
        task_id=task_id,
        created_at=created,
        task_text=text,
        mode="automatic",
        status=status,
        selected_agents=list(agents),
    )
    if duration is not None:
        db.update_task(task_id, duration_seconds=duration)


def make_steps(db, task_id, rows):
    db.replace_plan_steps(task_id, [
        {"step_id": sid, "position": index, "agent": agent, "title": f"{agent} work",
         "instruction": "do it", "depends_on": [], "status": status, "attempts": 1}
        for index, (sid, agent, status) in enumerate(rows)
    ])


def test_schema_is_created_and_every_agent_starts_enabled(db):
    flags = db.get_agent_enabled()
    assert set(flags) == {"planning", "research", "analysis", "writing", "verification"}
    assert all(flags.values())


def test_task_round_trip_decodes_json_fields(db):
    db.create_task(
        task_id="t1", created_at=START, task_text="a task", mode="manual", status="planning",
        selected_agents=["research", "writing"], fault_injection={"agent": "research", "fail_times": 1},
    )
    task = db.get_task("t1")
    assert task["selected_agents"] == ["research", "writing"]
    assert task["fault_injection"] == {"agent": "research", "fail_times": 1}
    assert task["context_json"] == {}
    assert task["total_retries"] == 0
    assert db.get_task("missing") is None


def test_update_task_stores_context_and_rejects_unknown_fields(db):
    make_task(db, "t2")
    db.update_task("t2", context_json={"sources": [{"id": "S1"}]}, total_retries=2)
    task = db.get_task("t2")
    assert task["context_json"] == {"sources": [{"id": "S1"}]}
    assert task["total_retries"] == 2
    with pytest.raises(ValueError):
        db.update_task("t2", not_a_column="x")


def test_plan_steps_are_replaced_and_updated_with_errors(db):
    make_task(db, "t3")
    make_steps(db, "t3", [("S1", "research", "pending"), ("S2", "writing", "pending")])
    db.update_plan_step("t3", "S1", status="failed", attempts=3, errors=["boom 1", "boom 2"])
    steps = db.get_plan_steps("t3")
    assert [s["step_id"] for s in steps] == ["S1", "S2"]
    assert steps[0]["status"] == "failed"
    assert steps[0]["errors"] == ["boom 1", "boom 2"]
    with pytest.raises(ValueError):
        db.update_plan_step("t3", "S1", bogus=1)
    make_steps(db, "t3", [("S9", "analysis", "pending")])
    assert [s["step_id"] for s in db.get_plan_steps("t3")] == ["S9"]


def test_outputs_and_logs_keep_their_order(db):
    make_task(db, "t4")
    db.add_output("t4", step_id="S1", agent="research", created_at=START, summary="first",
                  data={"n": 1}, warnings=["w"])
    db.add_output("t4", step_id="S2", agent="analysis", created_at=START, summary="second", data={}, warnings=[])
    outputs = db.get_outputs("t4")
    assert [o["summary"] for o in outputs] == ["first", "second"]
    assert outputs[0]["data"] == {"n": 1}
    assert outputs[0]["warnings"] == ["w"]
    db.add_log("t4", timestamp=START, level="INFO", event="a", message="one", agent="research")
    db.add_log("t4", timestamp=START, level="ERROR", event="b", message="two")
    assert [entry["event"] for entry in db.get_logs("t4")] == ["a", "b"]


def test_list_tasks_filters_by_text_status_agent_and_date(db):
    make_task(db, "a", status="completed", text="battery supply risks", agents=("research",),
              created="2026-01-05T10:00:00+00:00")
    make_task(db, "b", status="failed", text="charging gaps review", agents=("writing",),
              created="2026-01-20T10:00:00+00:00")
    make_task(db, "c", status="completed", text="Battery pricing trends", agents=("analysis",),
              created="2026-02-02T10:00:00+00:00")
    make_steps(db, "a", [("S1", "research", "completed")])
    make_steps(db, "b", [("S1", "writing", "failed")])
    make_steps(db, "c", [("S1", "analysis", "completed")])

    assert {t["id"] for t in db.list_tasks(search="battery")} == {"a", "c"}
    assert {t["id"] for t in db.list_tasks(statuses=["failed"])} == {"b"}
    assert {t["id"] for t in db.list_tasks(agent="writing")} == {"b"}
    in_january = db.list_tasks(start="2026-01-01T00:00:00", end="2026-02-01T00:00:00")
    assert {t["id"] for t in in_january} == {"a", "b"}
    newest_first = db.list_tasks()
    assert newest_first[0]["id"] == "c"


def test_deleting_a_task_cascades_to_its_plan_outputs_and_logs(db):
    make_task(db, "gone")
    make_steps(db, "gone", [("S1", "research", "completed")])
    db.add_output("gone", step_id="S1", agent="research", created_at=START, summary="x", data={}, warnings=[])
    db.add_log("gone", timestamp=START, level="INFO", event="e", message="m")
    assert db.delete_task("gone") is True
    assert db.delete_task("gone") is False
    assert db.get_plan_steps("gone") == []
    assert db.get_outputs("gone") == []
    assert db.get_logs("gone") == []


def test_delete_all_removes_every_task(db):
    make_task(db, "x1")
    make_task(db, "x2")
    assert db.delete_all_tasks() == 2
    assert db.list_tasks() == []


def test_agent_configuration_can_be_switched_and_validated(db):
    db.set_agent_enabled("analysis", False)
    assert db.get_agent_enabled()["analysis"] is False
    db.set_agent_enabled("analysis", True)
    assert db.get_agent_enabled()["analysis"] is True
    with pytest.raises(ValueError):
        db.set_agent_enabled("wizard", True)


def test_dashboard_metrics_compute_totals_durations_and_success_rate(db):
    make_task(db, "d1", status="completed", duration=2.0)
    make_task(db, "d2", status="completed_with_warnings", duration=4.0)
    make_task(db, "d3", status="failed", duration=None)
    make_steps(db, "d1", [("S1", "research", "completed"), ("S2", "writing", "completed")])
    make_steps(db, "d2", [("S1", "research", "failed"), ("S2", "writing", "skipped")])
    metrics = db.dashboard_metrics()
    assert metrics["total"] == 3
    assert metrics["completed"] == 2
    assert metrics["failed"] == 1
    assert metrics["avg_duration"] == pytest.approx(3.0)
    assert metrics["success_rate"] == pytest.approx(2 / 3)
    assert metrics["active_agents"] == 5


def test_agent_success_rates_ignore_skipped_steps(db):
    make_task(db, "r1")
    make_steps(db, "r1", [("S1", "research", "completed"), ("S2", "research", "failed"), ("S3", "writing", "skipped")])
    rows = {row["agent"]: row for row in db.agent_success_rates()}
    assert rows["research"]["attempted"] == 2
    assert rows["research"]["succeeded"] == 1
    assert rows["research"]["rate"] == pytest.approx(0.5)
    assert rows["writing"]["attempted"] == 0
    assert rows["writing"]["rate"] is None


def test_status_and_daily_counts(db):
    make_task(db, "s1", status="completed", created="2026-03-01T08:00:00+00:00")
    make_task(db, "s2", status="completed", created="2026-03-01T09:00:00+00:00")
    make_task(db, "s3", status="failed", created="2026-03-02T09:00:00+00:00")
    assert db.status_counts() == {"completed": 2, "failed": 1}
    assert db.daily_task_counts() == [("2026-03-01", 2), ("2026-03-02", 1)]


def test_empty_database_metrics_are_safe(tmp_path):
    empty = Database(tmp_path / "empty.db")
    metrics = empty.dashboard_metrics()
    assert metrics["total"] == 0
    assert metrics["avg_duration"] is None
    assert metrics["success_rate"] is None
