"""Keep unit tests isolated from the developer's configured PostgreSQL database."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_database_from_local_env(monkeypatch):
    """Tests opt into scratch SQLite files unless they explicitly set a DB URL."""
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.dispose_engine()
    yield
    db.dispose_engine()
