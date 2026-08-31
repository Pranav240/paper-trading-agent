"""
Shared pytest fixtures.

Two mechanisms working together here, doing two different jobs:

1. `app.dependency_overrides[get_store]` — swaps every route's injected
   store for a fresh `InMemoryStore()`, so each test starts from known,
   clean state instead of a shared, mutating instance. This is the
   Repository seam (app/repository/base.py) at work: any test can hand
   the app a completely different implementation without touching
   route code.

2. `os.environ["SKIP_DB_STARTUP"] = "1"` — tells app.main's lifespan not
   to open a real Postgres connection pool at all. This is necessary
   because #1 alone doesn't stop the app from trying to connect to a
   database: `lifespan` runs on every `TestClient()` regardless of what
   dependencies are overridden, since it's wired to the app object
   itself, not to any particular route's dependencies.

Together, these mean this test suite runs with zero Postgres
involvement — no server needs to be up for `pytest` to pass.
"""

import os

os.environ["SKIP_DB_STARTUP"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repository.memory import InMemoryStore
from app.store import get_store


@pytest.fixture
def client():
    test_store = InMemoryStore()
    app.dependency_overrides[get_store] = lambda: test_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
