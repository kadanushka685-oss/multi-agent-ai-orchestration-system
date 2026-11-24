"""Retry policy with a hard ceiling. Only transient failures are retried."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from config.settings import HARD_MAX_RETRIES


class RetryableError(RuntimeError):
    """A transient failure (timeout, rate limit, injected demo fault) that may succeed later."""


class AgentExecutionError(RetryableError):
    """An agent produced an unusable result, for example an invalid plan or an empty LLM reply."""


@dataclass
class RetryOutcome:
    ok: bool
    value: Any = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableError) or bool(getattr(exc, "retryable", False))


def run_with_retries(
    operation: Callable[[int], Any],
    max_retries: int,
    backoff_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> RetryOutcome:
    """Call operation(attempt) until it succeeds or the retry budget is spent.

    The budget is capped at HARD_MAX_RETRIES, so a caller cannot request unbounded retries.
    Non-transient errors fail immediately.
    """
    limit = max(0, min(int(max_retries), HARD_MAX_RETRIES))
    errors: list[str] = []
    attempt = 0
    while True:
        attempt += 1
        try:
            value = operation(attempt)
        except Exception as exc:  # the orchestrator records every failure and decides what happens next
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if not is_retryable(exc) or attempt > limit:
                return RetryOutcome(False, None, attempt, errors)
            if on_retry is not None:
                on_retry(attempt, exc)
            if backoff_seconds > 0:
                sleep(backoff_seconds * attempt)
            continue
        return RetryOutcome(True, value, attempt, errors)
