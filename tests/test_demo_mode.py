"""Demo mode: the whole system works with no API key, no network and no paid service."""
import json

from config.settings import DOCUMENTS_DIR, SAMPLE_TASKS_PATH, load_settings
from reports.report_builder import build_report
from services.llm_client import build_llm_client

MIN_TASK_CHARS = 10
MAX_TASK_CHARS = 2000


def test_demo_mode_needs_no_credentials_or_configuration():
    settings = load_settings(environ={}, env_file=None)
    assert settings.is_mock
    assert settings.secrets == []
    assert build_llm_client(settings) is None


def test_sample_documents_and_tasks_are_present_and_valid():
    assert len(list(DOCUMENTS_DIR.glob("*.md"))) == 6
    tasks = json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8"))
    assert len(tasks) >= 5
    for item in tasks:
        assert {"id", "title", "task", "notes"} <= set(item)
        assert MIN_TASK_CHARS <= len(item["task"]) <= MAX_TASK_CHARS


def test_a_full_demo_run_completes_without_any_network(orchestrator, db):
    task = json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8"))[0]["task"]
    run = orchestrator.create_run(task)
    orchestrator.run_to_completion(run)
    assert run.status in ("completed", "completed_with_warnings")
    assert run.plan.planner == "rule-based"
    stored = db.get_task(run.task_id)
    assert stored["final_response"].startswith("> Verification:")
    assert stored["verification_verdict"] in ("passed", "passed_with_warnings")


def test_demo_run_report_is_complete(orchestrator, db, settings):
    task = json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8"))[1]["task"]
    run = orchestrator.create_run(task)
    orchestrator.run_to_completion(run)
    report = build_report(db.get_task(run.task_id), db.get_plan_steps(run.task_id),
                          db.get_outputs(run.task_id), settings.secrets)
    assert report["plan"]
    assert report["agent_outputs"]
    assert report["verification"]["unsupported"] == 0
