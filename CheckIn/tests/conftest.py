"""Keep unit tests isolated from the developer's configured PostgreSQL database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Unit tests replace all network calls. Keeping a dummy value here makes test
# collection independent of a developer's private .env file.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-a-secret")


@pytest.fixture(autouse=True)
def isolate_database_from_local_env(monkeypatch):
    """Tests opt into scratch SQLite files unless they explicitly set a DB URL."""
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.dispose_engine()
    yield
    db.dispose_engine()
