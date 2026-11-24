"""Message routing. Agents never call each other directly.

The orchestrator creates task and revision messages and handoff messages between agents, and
delivers them through this bus. Every message is kept in the run history for the execution log.
"""
from __future__ import annotations

from config.settings import AGENT_NAMES
from orchestration.models import AgentMessage

VALID_PARTIES = frozenset(AGENT_NAMES) | {"orchestrator"}


class MessageBus:
    def __init__(self) -> None:
        self._pending: list[AgentMessage] = []
        self._history: list[AgentMessage] = []

    def send(self, message: AgentMessage) -> AgentMessage:
        if message.sender not in VALID_PARTIES or message.recipient not in VALID_PARTIES:
            raise ValueError(f"Unknown party in message: {message.sender} -> {message.recipient}")
        self._pending.append(message)
        self._history.append(message)
        return message

    def take_for(self, recipient: str, step_id: str) -> list[AgentMessage]:
        """Remove and return the pending messages addressed to this agent for this step."""
        taken = [m for m in self._pending if m.recipient == recipient and m.step_id == step_id]
        self._pending = [m for m in self._pending if not (m.recipient == recipient and m.step_id == step_id)]
        return taken

    @property
    def history(self) -> list[AgentMessage]:
        return list(self._history)

    @property
    def pending_count(self) -> int:
        return len(self._pending)
