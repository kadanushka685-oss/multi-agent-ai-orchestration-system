"""Helpers shared by the tests: a scripted language model and an orchestrator factory."""
from __future__ import annotations

import re

from orchestration.engine import Orchestrator
from services.llm_client import LLMClient, LLMError


class ScriptedLLM(LLMClient):
    """A fake provider for tests only. Replies are queued per agent role.

    A reply can be a string, an exception to raise, or a callable(system, user) -> str.
    The role is read from the ROLE= line that every live-mode prompt starts with.
    """

    provider = "scripted"
    model = "scripted-1"

    def __init__(self, replies: dict[str, list]):
        self.replies = {role: list(items) for role, items in replies.items()}
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        role = system.split("\n", 1)[0].replace("ROLE=", "").strip()
        self.calls.append(role)
        queue = self.replies.get(role, [])
        if not queue:
            raise LLMError(f"No scripted reply left for role '{role}'.", retryable=False)
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(system, user)
        return item


def first_statement(user: str) -> tuple[str, str]:
    """Return (id, text) of the first source statement listed in a writing prompt."""
    match = re.search(r"^\[(S\d+)\] (.+)$", user, flags=re.MULTILINE)
    assert match, "the prompt listed no sources"
    return match.group(1), match.group(2).strip()


def good_report(system: str, user: str) -> str:
    """A complete, correctly cited report built from the sources in the prompt."""
    source_id, text = first_statement(user)
    return (
        "# Brief: test\n\n"
        f"## Executive summary\n- {text} [{source_id}]\n\n"
        f"## Key findings\n- {text} [{source_id}]\n\n"
        f"## Analysis\n- {text} [{source_id}]\n\n"
        "## Recommendations\n- Collect more sources. [Unverified inference]\n\n"
        "## Limitations\n- Sample data only.\n\n"
        f"## Sources\n- {source_id}: sample document\n"
    )


def unsupported_report(system: str, user: str) -> str:
    """A report that contains one statement with no citation and one figure no source supports."""
    report = good_report(system, user)
    return report.replace(
        "## Limitations",
        "- Battery pack prices fell by 90 percent worldwide.\n- Demand rose 77% after 2030 [S1].\n\n## Limitations",
        1,
    )


def make_orchestrator(db, store, settings, llm=None) -> Orchestrator:
    return Orchestrator(db, store, settings, llm=llm, sleep=lambda seconds: None)
