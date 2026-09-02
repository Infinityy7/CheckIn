"""Keep unit tests isolated from the developer's .env and configured database.

Everything below the imports runs before any application module is imported.
``config.py`` and ``db`` call ``load_dotenv()``, which never overrides values
already in the environment, so plain assignment here is what stops a private
.env (gateway on, PostgreSQL URL, live inventory) from leaking into tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SCRATCH_DIR = Path(tempfile.mkdtemp(prefix="checkin-tests-"))
SCRATCH_DATABASE_URL = f"sqlite+pysqlite:///{SCRATCH_DIR / 'conftest.db'}"

os.environ["ANTHROPIC_API_KEY"] = "test-key-not-a-secret"
os.environ["LLM_GATEWAY_ENABLED"] = "false"
os.environ["LLM_GATEWAY_API_KEY"] = ""
os.environ["INVENTORY_PROVIDER"] = "unavailable"
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = SCRATCH_DATABASE_URL


@pytest.fixture(autouse=True)
def no_feasibility_network(monkeypatch):
    """The trip-create feasibility check must never reach a model in unit tests."""
    import feasibility

    async def _no_network(*_args, **_kwargs):
        raise RuntimeError("unit tests must stub LLM calls")

    monkeypatch.setattr(feasibility, "generate_text", _no_network)


@pytest.fixture(autouse=True)
def isolate_database_from_local_env(monkeypatch):
    """Tests opt into scratch SQLite files unless they explicitly set a DB URL."""
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db.dispose_engine()
    yield
    db.dispose_engine()
