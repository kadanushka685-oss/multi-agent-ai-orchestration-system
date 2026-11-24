"""Shared fixtures. Every test gets its own temporary database, so tests never touch real data."""
import pytest

from config.settings import Settings
from database.repository import Database
from services.document_store import DocumentStore
from tests.helpers import make_orchestrator


@pytest.fixture(scope="session")
def store():
    return DocumentStore()


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def settings(tmp_path):
    """Demo mode settings with no real environment and no backoff delay."""
    return Settings(retry_backoff_seconds=0.0, db_path=tmp_path / "test.db")


@pytest.fixture
def orchestrator(db, store, settings):
    return make_orchestrator(db, store, settings)
