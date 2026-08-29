"""
Shared pytest fixtures.

The key pattern here is `app.dependency_overrides`. Our routes ask for a
store via `Depends(get_store)`, which normally returns the single
process-lifetime `_store` instance in app/store.py. In tests we don't
want that shared, mutating-across-tests instance — each test should
start from a known, clean state. FastAPI lets you override any
dependency for the lifetime of the app object, so we swap
`get_store` for a function that returns a brand new `InMemoryStore()`
per test.

This is the same seam Phase 02 will use for a different purpose (real DB
vs stub) — here we're using it for test isolation instead.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import InMemoryStore, get_store


@pytest.fixture
def client():
    test_store = InMemoryStore()
    app.dependency_overrides[get_store] = lambda: test_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
