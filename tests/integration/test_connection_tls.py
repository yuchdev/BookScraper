"""
MongoDB Connection Test - X.509 (TLS Client Certificate) Authentication.

Pytest-based integration test (marked `integration`, excluded from the default run;
execute explicitly with `uv run pytest -m integration`). Verifies live connectivity to
a real MongoDB Atlas cluster using X.509 client-certificate authentication.

Credential source:
  URI:  TEST_MONGODB_URI_TLS, set in .env (repo root, git-ignored) or the environment
        directly - no fallback file (see mongo_test_helpers.py). Skips cleanly if unset.
  Cert: TLS_CERT_FILE, or ~/.bookscrapper/X509-cert-*.pem (shared with production -
        there is one X.509 cert, not a separate test one). Skips cleanly if none found.

This test is xfail(strict=False): Atlas currently rejects this cluster's X.509 database
user certificate (error code 8000 - a known server-side issue, not a client/resolver
bug), so the live connection is expected to fail today and reports as XFAIL. The
assertion itself stays real; the marker absorbs the currently-expected failure and would
surface as XPASS (not a hard failure) the day Atlas fixes this server-side.
"""

import pytest

from tests.integration import mongo_test_helpers
from tests.integration.conftest import require_env
from tests.integration.mongo_test_helpers import run_connection_test

ENV_VAR_NAME = "TEST_MONGODB_URI_TLS"

X509_XFAIL_REASON = (
    "Known Atlas-side X.509 certificate validation issue (error code 8000) - see "
    "CLAUDE.md's Authentication configuration / TLS certificate rotation pipeline section"
)


@pytest.mark.integration
@pytest.mark.xfail(reason=X509_XFAIL_REASON, strict=False)
def test_x509_auth_connects():
    """X.509 client-cert auth resolves and pings the live Atlas cluster.

    Expected XFAIL today due to the Atlas-side cert-validation issue above.
    """
    uri = require_env(ENV_VAR_NAME)
    cert = mongo_test_helpers.get_tls_cert_file()
    if cert is None:
        pytest.skip(
            "No X.509 client certificate available (TLS_CERT_FILE or "
            "~/.bookscrapper/X509-cert-*.pem) - cannot run this integration test."
        )
    assert run_connection_test(uri, tls_cert_file=cert) is True
