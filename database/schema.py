"""SQL schema. Foreign keys cascade, so deleting a task removes its plan, outputs and logs."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    task_text TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    selected_agents TEXT NOT NULL DEFAULT '[]',
    fault_injection TEXT,
    plan_json TEXT,
    context_json TEXT,
    final_response TEXT,
    verification_verdict TEXT,
    duration_seconds REAL,
    total_retries INTEGER NOT NULL DEFAULT 0,
    revisions INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS plan_steps (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    agent TEXT NOT NULL,
    title TEXT NOT NULL,
    instruction TEXT NOT NULL,
    depends_on TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    skip_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, step_id)
);

CREATE TABLE IF NOT EXISTS agent_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    agent TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_config (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_task ON execution_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_outputs_task ON agent_outputs(task_id);
"""
