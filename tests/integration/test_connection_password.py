"""
MongoDB Connection Test - Password (SCRAM) Authentication.

Pytest-based integration test (marked `integration`, excluded from the default run;
execute explicitly with `uv run pytest -m integration`). Verifies live connectivity to
a real MongoDB Atlas cluster using standard username/password (SCRAM) authentication.

Credential source: TEST_MONGODB_URI_PASS, set in .env (repo root, git-ignored) or the
environment directly - no fallback file (see mongo_test_helpers.py). The test skips
cleanly, rather than failing, when that credential is absent.
"""

import pytest

from tests.integration.conftest import require_env
from tests.integration.mongo_test_helpers import run_connection_test

ENV_VAR_NAME = "TEST_MONGODB_URI_PASS"


@pytest.mark.integration
def test_password_auth_connects():
    """Password/SCRAM auth resolves and pings the live Atlas cluster successfully."""
    uri = require_env(ENV_VAR_NAME)
    assert run_connection_test(uri, tls_cert_file=None) is True
