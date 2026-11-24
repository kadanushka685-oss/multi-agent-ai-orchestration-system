"""Execution log: every event is stored in the database and mirrored to Python logging.

Secrets are redacted before anything is written, so keys never reach the log or the database.
"""
from __future__ import annotations

import logging

from orchestration.state import now_iso
from services.security import redact

_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_LOGGER = logging.getLogger("multi_agent")


class ExecutionLogger:
    def __init__(self, db, task_id: str, secrets=()):
        self.db = db
        self.task_id = task_id
        self.secrets = list(secrets)

    def log(self, level: str, event: str, message: str, agent: str | None = None) -> None:
        safe_message = redact(message, self.secrets)
        self.db.add_log(
            self.task_id,
            timestamp=now_iso(),
            level=level,
            event=event,
            message=safe_message,
            agent=agent,
        )
        _LOGGER.log(_LEVELS.get(level, logging.INFO), "[%s] %s", event, safe_message)
