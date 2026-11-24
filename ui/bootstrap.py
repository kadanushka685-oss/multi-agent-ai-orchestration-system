"""Creates the shared services once per server process. Settings are read at start-up.

Restart the Streamlit server after changing .env, because the settings are cached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import streamlit as st

from config.settings import ConfigError, load_settings
from database.repository import Database
from services.document_store import DocumentStore
from services.llm_client import LLMConfigError, build_llm_client


@dataclass
class Services:
    settings: Any
    db: Database
    store: DocumentStore
    llm: Any
    llm_error: str
    config_error: str = ""


@st.cache_resource(show_spinner=False)
def _database(path: str) -> Database:
    return Database(path)


@st.cache_resource(show_spinner=False)
def _store() -> DocumentStore:
    return DocumentStore()


def get_services() -> Services:
    config_error = ""
    try:
        settings = load_settings()
    except ConfigError as exc:
        config_error = str(exc)
        settings = load_settings(environ={}, env_file=None)

    llm = None
    llm_error = ""
    if not config_error:
        try:
            llm = build_llm_client(settings)
        except LLMConfigError as exc:
            llm_error = str(exc)
    return Services(
        settings=settings,
        db=_database(str(settings.db_path)),
        store=_store(),
        llm=llm,
        llm_error=llm_error,
        config_error=config_error,
    )
