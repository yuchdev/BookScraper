"""
Shared scaffolding for the integration tier.

Every test here is marked `@pytest.mark.integration` and hits a REAL MongoDB Atlas
cluster over the network using test-only credentials from .env (repo root,
git-ignored): TEST_MONGODB_URI_PASS / TEST_MONGODB_URI_TLS. The tier is excluded from
the default `pytest` run via `addopts = -m "not integration"`; run it explicitly with
`uv run pytest -m integration` on a machine where those credentials are present.

The neither-original-scripts-nor-mongo_test_helpers call load_dotenv() at import time,
so this module owns loading .env exactly once per session (autouse fixture below).
"""

import os

import pytest
from dotenv import load_dotenv


@pytest.fixture(scope="session", autouse=True)
def _load_env_once():
    """Loads .env once per test session so TEST_MONGODB_URI_* (and any settings.json
    'env' sources) are visible to every integration test. mongo_test_helpers.py and the
    resolver under test both read os.environ directly and never call load_dotenv
    themselves, so this is the single load point - no double-loading."""
    load_dotenv()
    yield


def require_env(name: str) -> str:
    """Returns the value of environment variable `name`, or skips the calling test
    cleanly if it is unset/empty. A missing test credential is an environment issue, not
    a real failure, so these tests skip rather than fail when run without .env."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set (check .env) - cannot run this integration test.")
    return value
