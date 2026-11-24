"""Pluggable language-model clients for live mode.

Demo mode does not use this module at all. Live mode uses the standard library's urllib, so no
extra HTTP dependency is needed. Every error message is redacted before it leaves this module.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from config.settings import Settings
from services.security import redact


class LLMError(RuntimeError):
    """A failed call to the language-model provider. `retryable` tells the orchestrator whether to retry."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMConfigError(ValueError):
    """The provider is selected but not usable, for example because its API key is missing."""


def http_post_json(url: str, headers: dict, payload: dict, timeout: float, secrets=()) -> dict:
    """POST JSON and return the decoded JSON reply."""
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace")
        retryable = exc.code in (408, 409, 429) or exc.code >= 500
        raise LLMError(redact(f"HTTP {exc.code} from the LLM provider: {detail}", secrets), retryable) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise LLMError(redact(f"Could not reach the LLM provider: {exc}", secrets), retryable=True) from None
    except json.JSONDecodeError:
        raise LLMError("The LLM provider returned a reply that is not valid JSON.", retryable=True) from None


class LLMClient:
    """Interface used by agents: one system prompt and one user message in, text out."""

    provider = "base"
    model = ""

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAIChatClient(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float, transport=None):
        if not api_key:
            raise LLMConfigError("OPENAI_API_KEY is not set. Add it to .env or switch LLM_PROVIDER to mock.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        data = self._post(url, headers, payload)
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise LLMError("The OpenAI-compatible reply had an unexpected shape.", retryable=True) from None

    def _post(self, url, headers, payload):
        if self._transport is not None:
            return self._transport(url, headers, payload, self.timeout)
        return http_post_json(url, headers, payload, self.timeout, secrets=[self.api_key])


class AnthropicMessagesClient(LLMClient):
    provider = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout: float, transport=None):
        if not api_key:
            raise LLMConfigError("ANTHROPIC_API_KEY is not set. Add it to .env or switch LLM_PROVIDER to mock.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 1500,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        if self._transport is not None:
            data = self._transport(self.URL, headers, payload, self.timeout)
        else:
            data = http_post_json(self.URL, headers, payload, self.timeout, secrets=[self.api_key])
        blocks = data.get("content") or []
        text = "".join(
            str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise LLMError("The Anthropic reply contained no text.", retryable=True)
        return text


def build_llm_client(settings: Settings, transport=None) -> LLMClient | None:
    """Return the live client for the configured provider, or None in demo mode."""
    if settings.is_mock:
        return None
    model = settings.resolved_model()
    if settings.provider == "openai":
        return OpenAIChatClient(
            settings.openai_api_key, model, settings.openai_base_url, settings.llm_timeout_seconds, transport
        )
    if settings.provider == "anthropic":
        return AnthropicMessagesClient(settings.anthropic_api_key, model, settings.llm_timeout_seconds, transport)
    raise LLMConfigError(f"Unsupported provider: {settings.provider}")


def check_connection(client: LLMClient) -> tuple[bool, str]:
    """Send one tiny request and report whether the provider answered."""
    try:
        reply = client.complete("Reply with the single word OK.", "Connection check.")
    except (LLMError, LLMConfigError) as exc:
        return False, str(exc)
    return True, f"Connected to {client.provider} ({client.model}). Reply: {reply.strip()[:40]!r}"
