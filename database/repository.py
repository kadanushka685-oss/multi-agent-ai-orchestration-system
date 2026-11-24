"""Database access. Every query uses placeholders; column names come from fixed allow-lists."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config.settings import AGENT_NAMES, DEFAULT_DB_PATH
from database.schema import SCHEMA

TASK_FIELDS = frozenset({
    "status", "started_at", "completed_at", "duration_seconds", "final_response",
    "verification_verdict", "total_retries", "revisions", "error", "context_json", "plan_json",
})
STEP_FIELDS = frozenset({
    "status", "attempts", "errors", "started_at", "completed_at", "duration_seconds", "skip_reason",
})


def _encode(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _decode(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _task_from_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["selected_agents"] = _decode(data.get("selected_agents"), [])
    data["fault_injection"] = _decode(data.get("fault_injection"), None)
    data["plan_json"] = _decode(data.get("plan_json"), None)
    data["context_json"] = _decode(data.get("context_json"), {})
    data["total_retries"] = int(data.get("total_retries") or 0)
    data["revisions"] = int(data.get("revisions") or 0)
    return data


class Database:
    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            for name in AGENT_NAMES:
                conn.execute("INSERT OR IGNORE INTO agent_config (name, enabled) VALUES (?, 1)", (name,))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ tasks

    def create_task(self, *, task_id, created_at, task_text, mode, status, selected_agents, fault_injection=None):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id, created_at, task_text, mode, status, selected_agents, fault_injection) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, created_at, task_text, mode, status, _encode(selected_agents), _encode(fault_injection)),
            )

    def update_task(self, task_id: str, **fields) -> None:
        unknown = set(fields) - TASK_FIELDS
        if unknown:
            raise ValueError(f"Unknown task field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_encode(value) for value in fields.values()]
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*values, task_id))

    def get_task(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def list_tasks(self, search=None, statuses=None, agent=None, start=None, end=None, limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if search:
            clauses.append("task_text LIKE ?")
            params.append(f"%{search}%")
        if statuses:
            clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if agent:
            clauses.append("id IN (SELECT task_id FROM plan_steps WHERE agent = ?)")
            params.append(agent)
        if start:
            clauses.append("created_at >= ?")
            params.append(start)
        if end:
            clauses.append("created_at < ?")
            params.append(end)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_task_from_row(row) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def delete_all_tasks(self) -> int:
        with self.connect() as conn:
            return conn.execute("DELETE FROM tasks").rowcount

    # ------------------------------------------------------------ plan steps

    def replace_plan_steps(self, task_id: str, rows: list[dict]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM plan_steps WHERE task_id = ?", (task_id,))
            for row in rows:
                conn.execute(
                    "INSERT INTO plan_steps (task_id, step_id, position, agent, title, instruction, depends_on, "
                    "status, attempts, errors, started_at, completed_at, duration_seconds, skip_reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id, row["step_id"], row["position"], row["agent"], row["title"], row["instruction"],
                        _encode(row.get("depends_on", [])), row.get("status", "pending"), row.get("attempts", 0),
                        _encode(row.get("errors", [])), row.get("started_at"), row.get("completed_at"),
                        row.get("duration_seconds"), row.get("skip_reason", ""),
                    ),
                )

    def update_plan_step(self, task_id: str, step_id: str, **fields) -> None:
        unknown = set(fields) - STEP_FIELDS
        if unknown:
            raise ValueError(f"Unknown step field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_encode(value) for value in fields.values()]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE plan_steps SET {assignments} WHERE task_id = ? AND step_id = ?",
                (*values, task_id, step_id),
            )

    def get_plan_steps(self, task_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_steps WHERE task_id = ? ORDER BY position", (task_id,)
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["depends_on"] = _decode(data.get("depends_on"), [])
            data["errors"] = _decode(data.get("errors"), [])
            result.append(data)
        return result

    # ------------------------------------------------------------ outputs and logs

    def add_output(self, task_id: str, *, step_id: str, agent: str, created_at: str, summary: str,
                   data: dict, warnings: list) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO agent_outputs (task_id, step_id, agent, created_at, summary, data_json, warnings_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, step_id, agent, created_at, summary, _encode(data or {}), _encode(warnings or [])),
            )

    def get_outputs(self, task_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_outputs WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["data"] = _decode(data.pop("data_json"), {})
            data["warnings"] = _decode(data.pop("warnings_json"), [])
            result.append(data)
        return result

    def add_log(self, task_id: str, *, timestamp: str, level: str, event: str, message: str, agent=None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO execution_logs (task_id, timestamp, level, event, agent, message) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, timestamp, level, event, agent, message),
            )

    def get_logs(self, task_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ agent configuration

    def get_agent_enabled(self) -> dict[str, bool]:
        with self.connect() as conn:
            rows = conn.execute("SELECT name, enabled FROM agent_config").fetchall()
        return {row["name"]: bool(row["enabled"]) for row in rows}

    def set_agent_enabled(self, name: str, enabled: bool) -> None:
        if name not in AGENT_NAMES:
            raise ValueError(f"Unknown agent: {name}")
        with self.connect() as conn:
            conn.execute("UPDATE agent_config SET enabled = ? WHERE name = ?", (1 if enabled else 0, name))

    # ------------------------------------------------------------ dashboard queries

    def dashboard_metrics(self) -> dict:
        with self.connect() as conn:
            total, completed, failed, avg_duration = conn.execute(
                "SELECT COUNT(*), "
                "COALESCE(SUM(CASE WHEN status IN ('completed', 'completed_with_warnings') THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0), "
                "AVG(duration_seconds) FROM tasks"
            ).fetchone()
            active_agents = conn.execute("SELECT COUNT(*) FROM agent_config WHERE enabled = 1").fetchone()[0]
            succeeded, attempted = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0), "
                "COALESCE(SUM(CASE WHEN status IN ('completed', 'failed') THEN 1 ELSE 0 END), 0) FROM plan_steps"
            ).fetchone()
        return {
            "total": int(total),
            "completed": int(completed),
            "failed": int(failed),
            "active_agents": int(active_agents),
            "avg_duration": round(avg_duration, 3) if avg_duration is not None else None,
            "success_rate": (succeeded / attempted) if attempted else None,
        }

    def status_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def agent_success_rates(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT agent, "
                "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS succeeded, "
                "SUM(CASE WHEN status IN ('completed', 'failed') THEN 1 ELSE 0 END) AS attempted "
                "FROM plan_steps GROUP BY agent"
            ).fetchall()
        result = []
        for row in rows:
            attempted = int(row["attempted"] or 0)
            succeeded = int(row["succeeded"] or 0)
            result.append({
                "agent": row["agent"],
                "attempted": attempted,
                "succeeded": succeeded,
                "rate": (succeeded / attempted) if attempted else None,
            })
        return sorted(result, key=lambda item: AGENT_NAMES.index(item["agent"]) if item["agent"] in AGENT_NAMES else 99)

    def daily_task_counts(self) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n FROM tasks GROUP BY day ORDER BY day"
            ).fetchall()
        return [(row["day"], int(row["n"])) for row in rows]
