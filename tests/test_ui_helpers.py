"""Dashboard helpers that do not need Streamlit: labels for runs and timestamps."""
from ui.labels import short_time, task_labels


def test_short_time_formats_iso_timestamps():
    assert short_time("2026-01-10T09:05:07+00:00") == "2026-01-10 09:05:07"
    assert short_time(None) == "-"


def test_run_labels_stay_unique_for_runs_created_in_the_same_second():
    tasks = [
        {"id": "aaa111", "created_at": "2026-01-10T09:00:00+00:00", "status": "completed", "task_text": "Same task text"},
        {"id": "bbb222", "created_at": "2026-01-10T09:00:00+00:00", "status": "completed", "task_text": "Same task text"},
    ]
    labels = task_labels(tasks)
    assert len(labels) == 2
    assert labels[next(k for k in labels if "aaa111" in k)] == "aaa111"
    assert labels[next(k for k in labels if "bbb222" in k)] == "bbb222"


def test_labels_map_back_to_the_correct_run_id():
    tasks = [{"id": "ccc333", "created_at": "2026-02-02T10:00:00+00:00", "status": "failed", "task_text": "x" * 200}]
    labels = task_labels(tasks)
    (label, run_id), = labels.items()
    assert run_id == "ccc333"
    assert len(label) < 150
