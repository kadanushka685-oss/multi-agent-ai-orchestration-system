"""Report generation: the report structure, CSV, PDF and redaction of secrets in exports."""
import csv
import io
import shutil
import subprocess

import pytest

from reports.csv_report import build_csv
from reports.pdf_report import build_pdf
from reports.report_builder import build_report
from config.settings import SAMPLE_TASKS_PATH
import json

TASK = next(item["task"] for item in json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8")) if item["id"] == "ev-brief")


def finished_report(orchestrator, db, settings):
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)
    return build_report(db.get_task(run.task_id), db.get_plan_steps(run.task_id),
                        db.get_outputs(run.task_id), settings.secrets), run


def pdf_text(pdf_bytes: bytes, workdir) -> str:
    """Extract text with the poppler pdftotext tool, reading a real file."""
    pdf_path = workdir / "report.pdf"
    pdf_path.write_bytes(pdf_bytes)
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], capture_output=True, check=False
    )
    return result.stdout.decode("utf-8", errors="replace")


def test_report_contains_every_required_section(orchestrator, db, settings):
    report, run = finished_report(orchestrator, db, settings)
    for key in ("original_task", "plan", "agents_involved", "agent_outputs", "verification",
                "final_response", "duration_seconds", "total_retries"):
        assert key in report
    assert report["original_task"] == TASK
    assert {agent["agent"] for agent in report["agents_involved"]} == {"research", "analysis", "writing", "verification"}
    assert report["verification"]["verdict"] in ("passed", "passed_with_warnings")
    assert report["final_response"].startswith("> Verification:")


def test_csv_is_excel_ready_and_covers_every_section(orchestrator, db, settings):
    report, _ = finished_report(orchestrator, db, settings)
    data = build_csv(report)
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0] == ["section", "item", "field", "value"]
    sections = {row[0] for row in rows[1:]}
    assert {"summary", "plan", "agent", "agent_output", "verification", "final_response"} <= sections
    assert ["verification", "", "verdict", report["verification"]["verdict"]] in rows


def test_pdf_is_a_well_formed_document(orchestrator, db, settings):
    report, _ = finished_report(orchestrator, db, settings)
    data = build_pdf(report)
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")


def test_pdf_text_contains_the_report_sections(orchestrator, db, settings, tmp_path):
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext is not installed, so the PDF text could not be checked")
    report, _ = finished_report(orchestrator, db, settings)
    text = pdf_text(build_pdf(report), tmp_path)
    for heading in ("Original task", "Execution plan", "Agents involved", "Agent outputs",
                    "Verification results", "Final response"):
        assert heading in text
    assert "Verification:" in text or "verification" in text.lower()


def test_secrets_are_removed_from_both_exports(db, settings):
    secret = "sk-EXPORTCHECK-555555"
    db.create_task(task_id="leak", created_at="2026-01-01T00:00:00+00:00",
                   task_text=f"Summarise the battery market, token {secret}", mode="automatic",
                   status="completed", selected_agents=["writing"])
    report = build_report(db.get_task("leak"), [], [], [secret])
    csv_bytes = build_csv(report)
    pdf_bytes = build_pdf(report)
    assert secret.encode() not in csv_bytes
    assert secret.encode() not in pdf_bytes
    assert b"[REDACTED]" in csv_bytes


def test_reports_for_failed_runs_still_export(orchestrator, db, settings):
    orchestrator.max_steps = 1
    run = orchestrator.create_run(TASK)
    assert run.status == "failed"
    report = build_report(db.get_task(run.task_id), db.get_plan_steps(run.task_id),
                          db.get_outputs(run.task_id), settings.secrets)
    assert build_csv(report).startswith(b"\xef\xbb\xbf")
    assert build_pdf(report).startswith(b"%PDF-")
    assert report["verification"]["verdict"] == "not_run"
