"""CSV export: one long table with section, item, field and value columns.

The file is UTF-8 with a byte-order mark so Excel on Windows opens accented text correctly.
"""
from __future__ import annotations

import csv
import io


def build_csv(report: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "item", "field", "value"])

    for key in ("task_id", "original_task", "mode", "status", "created_at", "completed_at",
                "duration_seconds", "total_retries", "revisions", "generated_at"):
        writer.writerow(["summary", "", key, report.get(key, "")])
    writer.writerow(["summary", "", "selected_agents", ", ".join(report["selected_agents"])])

    for step in report["plan"]:
        item = step["step_id"]
        writer.writerow(["plan", item, "agent", step["agent"]])
        writer.writerow(["plan", item, "title", step["title"]])
        writer.writerow(["plan", item, "depends_on", ", ".join(step["depends_on"])])
        writer.writerow(["plan", item, "status", step["status"]])
        writer.writerow(["plan", item, "attempts", step["attempts"]])
        writer.writerow(["plan", item, "duration_seconds", step["duration_seconds"]])
        writer.writerow(["plan", item, "skip_reason", step["skip_reason"]])
        writer.writerow(["plan", item, "errors", " | ".join(step["errors"])])

    for agent in report["agents_involved"]:
        writer.writerow(["agent", agent["agent"], "steps", agent["steps"]])
        writer.writerow(["agent", agent["agent"], "completed", agent["completed"]])
        writer.writerow(["agent", agent["agent"], "failed", agent["failed"]])
        writer.writerow(["agent", agent["agent"], "skipped", agent["skipped"]])

    for output in report["agent_outputs"]:
        writer.writerow(["agent_output", output["step_id"], output["agent"], output["summary"]])
        for warning in output["warnings"]:
            writer.writerow(["agent_output", output["step_id"], "warning", warning])

    verification = report["verification"]
    writer.writerow(["verification", "", "verdict", verification["verdict"]])
    for key in ("verified", "unverified", "inferences", "unsupported"):
        writer.writerow(["verification", "", key, verification[key]])
    for issue in verification["issues"]:
        writer.writerow(["verification", issue["kind"], issue["severity"], issue["message"]])

    writer.writerow(["final_response", "", "text", report["final_response"]])
    return buffer.getvalue().encode("utf-8-sig")
