"""Builds one report structure from stored run data. Both exporters read from this structure."""
from __future__ import annotations

from orchestration.state import now_iso
from services.security import redact


def build_report(task: dict, steps: list[dict], outputs: list[dict], secrets=()) -> dict:
    """Assemble every section the report needs. Each text field is redacted before export."""

    def clean(value):
        return redact(value, secrets) if isinstance(value, str) else value

    context = task.get("context_json") or {}
    verification = context.get("verification") or {}

    agents: dict[str, dict] = {}
    for step in steps:
        entry = agents.setdefault(
            step["agent"], {"agent": step["agent"], "steps": 0, "completed": 0, "failed": 0, "skipped": 0}
        )
        entry["steps"] += 1
        if step["status"] in ("completed", "failed", "skipped"):
            entry[step["status"]] += 1

    return {
        "title": "Multi-Agent Orchestration Report",
        "task_id": task["id"],
        "original_task": clean(task["task_text"]),
        "mode": task["mode"],
        "status": task["status"],
        "created_at": task.get("created_at"),
        "completed_at": task.get("completed_at"),
        "duration_seconds": task.get("duration_seconds"),
        "selected_agents": list(task.get("selected_agents") or []),
        "total_retries": task.get("total_retries", 0),
        "revisions": task.get("revisions", 0),
        "plan": [
            {
                "step_id": step["step_id"],
                "agent": step["agent"],
                "title": clean(step["title"]),
                "depends_on": list(step.get("depends_on") or []),
                "status": step["status"],
                "attempts": step["attempts"],
                "duration_seconds": step.get("duration_seconds"),
                "skip_reason": clean(step.get("skip_reason") or ""),
                "errors": [clean(error) for error in step.get("errors") or []],
            }
            for step in steps
        ],
        "agents_involved": list(agents.values()),
        "agent_outputs": [
            {
                "step_id": output["step_id"],
                "agent": output["agent"],
                "summary": clean(output["summary"]),
                "warnings": [clean(w) for w in output.get("warnings") or []],
            }
            for output in outputs
        ],
        "verification": {
            "verdict": verification.get("verdict", "not_run"),
            "verified": verification.get("verified", 0),
            "unverified": verification.get("unverified", 0),
            "inferences": verification.get("inferences", 0),
            "unsupported": verification.get("unsupported", 0),
            "issues": [
                {"severity": issue["severity"], "kind": issue["kind"], "message": clean(issue["message"])}
                for issue in verification.get("issues", [])
            ],
        },
        "final_response": clean(task.get("final_response") or ""),
        "generated_at": now_iso(),
    }
