"""Input validation, prompt-injection screening and secret redaction.

Anything that comes from a user or from a document is treated as data. These helpers reject
malformed input, flag instruction-like phrases, neutralise text that tries to break out of the
data wrapper, and remove secrets before anything is stored, logged, displayed or exported.
"""
from __future__ import annotations

import re
from typing import Iterable

from config.settings import MAX_SOURCE_CHARS, MAX_TASK_CHARS, MIN_TASK_CHARS


class InputError(ValueError):
    """Raised when user input fails validation."""


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNTRUSTED_TAG = re.compile(r"</?\s*untrusted[^>]*>", re.IGNORECASE)
_ROLE_PREFIX = re.compile(r"^\s*(system|assistant|user|developer)\s*:", re.IGNORECASE | re.MULTILINE)
_SECRET_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")

INJECTION_PATTERNS = {
    "instruction override": (
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b"
        r".{0,40}\b(instructions?|prompts?|rules?)\b"
    ),
    "system prompt probe": r"\b(system|hidden|initial)\s+prompt\b",
    "secret probe": r"\b(api[\s_-]?keys?|secret keys?|passwords?|access tokens?)\b",
    "role override": r"\byou are now\b|\bpretend (to be|you are)\b",
    "code execution request": r"\b(run|execute)\b.{0,30}\b(shell|bash|terminal|command|script|code)\b",
}
_COMPILED_PATTERNS = {
    label: re.compile(pattern, re.IGNORECASE | re.DOTALL) for label, pattern in INJECTION_PATTERNS.items()
}


def validate_task_text(text, min_chars: int = MIN_TASK_CHARS, max_chars: int = MAX_TASK_CHARS) -> str:
    """Return the cleaned task text, or raise InputError with a user-facing message."""
    if not isinstance(text, str):
        raise InputError("The task must be text.")
    if _CONTROL_CHARS.search(text):
        raise InputError("The task contains control characters that cannot be processed.")
    cleaned = text.strip()
    if len(cleaned) < min_chars:
        raise InputError(f"The task must be at least {min_chars} characters long.")
    if len(cleaned) > max_chars:
        raise InputError(f"The task must be at most {max_chars} characters long.")
    return cleaned


def detect_injection(text: str) -> list[str]:
    """Return the labels of instruction-like patterns found in the text (empty when clean)."""
    return [label for label, pattern in _COMPILED_PATTERNS.items() if pattern.search(text or "")]


def sanitize_untrusted(text: str, max_chars: int = MAX_SOURCE_CHARS) -> str:
    """Neutralise wrapper tags and chat-role prefixes, strip control characters, and truncate."""
    cleaned = _UNTRUSTED_TAG.sub("[tag removed]", text or "")
    cleaned = _ROLE_PREFIX.sub(lambda match: f"{match.group(1)} (quoted):", cleaned)
    cleaned = _CONTROL_CHARS.sub("", cleaned)
    return cleaned[:max_chars]


def wrap_untrusted(label: str, text: str) -> str:
    """Wrap data so a language model is told to read it as data, never as instructions."""
    return f'<untrusted source="{label}">\n{sanitize_untrusted(text)}\n</untrusted>'


def redact(text, secrets: Iterable[str] = ()) -> str:
    """Replace known secret values and anything that looks like an API key."""
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return _SECRET_TOKEN.sub("[REDACTED]", result)
