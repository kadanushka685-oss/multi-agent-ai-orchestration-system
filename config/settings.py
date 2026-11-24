"""Configuration: paths, execution limits and environment-driven LLM settings.

Values are read from environment variables. A local .env file (copy .env.example) is read when
present, and real environment variables always take precedence over the file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"
DOCUMENTS_DIR = SAMPLE_DATA_DIR / "documents"
SAMPLE_TASKS_PATH = SAMPLE_DATA_DIR / "sample_tasks.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "orchestration.db"
ENV_FILE = PROJECT_ROOT / ".env"

AGENT_NAMES = ("planning", "research", "analysis", "writing", "verification")
PROVIDERS = ("mock", "openai", "anthropic")
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-haiku-latest"}
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

MIN_TASK_CHARS = 10
MAX_TASK_CHARS = 2000
HARD_MAX_RETRIES = 3
DEFAULT_MAX_RETRIES = 2
MAX_STEPS = 15
MAX_RUN_SECONDS = 120.0
MAX_REVISIONS = 1
MAX_SOURCE_CHARS = 4000


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


def parse_env_file(path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Comments, blank lines and surrounding quotes are handled."""
    values: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return values
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.strip():
            values[key.strip()] = value
    return values


@dataclass
class Settings:
    provider: str = "mock"
    model: str = ""
    openai_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 30.0
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_seconds: float = 0.5
    max_steps: int = MAX_STEPS
    max_run_seconds: float = MAX_RUN_SECONDS
    db_path: Path = DEFAULT_DB_PATH
    log_level: str = "INFO"

    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS.get(self.provider, "")

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    @property
    def api_key(self) -> str:
        if self.provider == "openai":
            return self.openai_api_key
        if self.provider == "anthropic":
            return self.anthropic_api_key
        return ""

    @property
    def secrets(self) -> list[str]:
        """Every configured secret value, used for redaction. Never shown in the UI."""
        return [key for key in (self.openai_api_key, self.anthropic_api_key) if key]

    def describe(self) -> dict:
        """Settings that are safe to display. Key values are never included."""
        if self.is_mock:
            key_status = "not required (demo mode)"
        else:
            key_status = "configured" if self.api_key else "missing"
        return {
            "provider": self.provider,
            "model": self.resolved_model() or "not applicable (demo mode)",
            "api_key_status": key_status,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "max_steps": self.max_steps,
            "max_run_seconds": self.max_run_seconds,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "db_path": str(self.db_path),
            "log_level": self.log_level,
        }


def _read_int(values: dict, key: str, default: int, low: int, high: int) -> int:
    raw = values.get(key, "").strip() or str(default)
    try:
        number = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a whole number.") from exc
    if not low <= number <= high:
        raise ConfigError(f"{key} must be between {low} and {high}.")
    return number


def _read_float(values: dict, key: str, default: float, low: float, high: float) -> float:
    raw = values.get(key, "").strip() or str(default)
    try:
        number = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number.") from exc
    if not low <= number <= high:
        raise ConfigError(f"{key} must be between {low} and {high}.")
    return number


def load_settings(environ=None, env_file=ENV_FILE) -> Settings:
    """Build Settings from the .env file and the environment.

    Pass environ={} and env_file=None in tests to ignore the real environment.
    """
    values: dict[str, str] = dict(parse_env_file(env_file)) if env_file else {}
    values.update(os.environ if environ is None else environ)

    provider = values.get("LLM_PROVIDER", "mock").strip().lower() or "mock"
    if provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of: {', '.join(PROVIDERS)}.")
    log_level = values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}.")

    db_raw = values.get("DB_PATH", "").strip()
    db_path = Path(db_raw) if db_raw else DEFAULT_DB_PATH
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        provider=provider,
        model=values.get("LLM_MODEL", "").strip(),
        openai_api_key=values.get("OPENAI_API_KEY", "").strip(),
        anthropic_api_key=values.get("ANTHROPIC_API_KEY", "").strip(),
        openai_base_url=values.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1",
        llm_timeout_seconds=_read_float(values, "LLM_TIMEOUT_SECONDS", 30.0, 1, 120),
        max_retries=_read_int(values, "MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, HARD_MAX_RETRIES),
        retry_backoff_seconds=_read_float(values, "RETRY_BACKOFF_SECONDS", 0.5, 0, 10),
        db_path=db_path,
        log_level=log_level,
    )
