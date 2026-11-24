"""Pure helper functions for labels shown in the dashboard. No Streamlit import, so they are testable."""
from __future__ import annotations


def short_time(value) -> str:
    """'2026-01-10T09:00:00+00:00' -> '2026-01-10 09:00:00'."""
    return (value or "-")[:19].replace("T", " ")


def task_labels(tasks: list[dict]) -> dict[str, str]:
    """One unique label per run, mapped to the run id.

    The run id is part of the label, so two runs created in the same second, with the same status
    and the same task text, still get distinct labels.
    """
    return {
        f"{short_time(task['created_at'])} · {task['status']} · {task['id']} · {task['task_text'][:60]}": task["id"]
        for task in tasks
    }
