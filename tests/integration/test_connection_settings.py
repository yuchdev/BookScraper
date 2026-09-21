"""
Integration test for the deterministic ~/.bookscrapper/settings.json config resolver
(bookscraper.backends.mongo.config.resolve_mongo_connection).

Pytest-based (marked `integration`, excluded from the default run; execute explicitly
with `uv run pytest -m integration`). Unlike the password/tls connection tests, this one
DOES import from src/bookscraper - the whole point is to exercise the real production
resolver, not to stay independent of it.

Exercises all 6 valid settings.json shapes (one parametrized case each):
  pass  / literal uri
  pass  / env uri
  x509  / literal uri, literal cert
  x509  / literal uri, env cert
  x509  / env uri, literal cert
  x509  / env uri, env cert

For each shape: writes a temp settings.json (via tmp_path - never touches the real
~/.bookscrapper/settings.json), applies any 'env' sources via monkeypatch.setenv,
resolves via resolve_mongo_connection(path=...), asserts the resolved URI/cert are
exactly what was declared, then attempts a real connection via run_connection_test -
reusing the same test credentials as the password/tls tests (TEST_MONGODB_URI_PASS /
TEST_MONGODB_URI_TLS, set in .env) and the production X.509 cert (shared, for the same
reason: one real X.509 identity, not a separate test one).

Expected outcome:
  - Both "pass" shapes resolve AND connect successfully (real, unmarked assertions).
  - All 4 "x509" shapes resolve to identical, correct values regardless of which
    source combination declared them, but the live connection currently fails with
    Atlas's cert-validation error - a known, pre-existing Atlas-side X.509 issue
    unrelated to this resolver (see CLAUDE.md). Those 4 cases are xfail(strict=False):
    the resolution + connection assertions stay real, and the marker absorbs the
    currently-expected connection failure (XFAIL today, XPASS the day Atlas fixes it).

A ConfigError from the resolver is a REAL failure (a resolver regression), never
expected - it is surfaced via pytest.fail with the message. "No cert available on this
machine" is a distinct environment issue from the Atlas xfail and skips the x509 cases.
"""

import json
from pathlib import Path

import pytest

from bookscraper.backends.mongo import config
from tests.integration.conftest import require_env
from tests.integration.mongo_test_helpers import run_connection_test

PASS_URI_ENV_VAR = "TEST_MONGODB_URI_PASS"
TLS_URI_ENV_VAR = "TEST_MONGODB_URI_TLS"

X509_XFAIL_REASON = (
    "Known Atlas-side X.509 certificate validation issue (error code 8000) - see "
    "CLAUDE.md's Authentication configuration / TLS certificate rotation pipeline section"
)

# Names of the env vars the "env"-sourced scenarios point their settings.json fields at.
_SETTINGS_PASS_URI = "TEST_SETTINGS_PASS_URI"
_SETTINGS_TLS_URI = "TEST_SETTINGS_TLS_URI"
_SETTINGS_TLS_CERT = "TEST_SETTINGS_TLS_CERT"


def _write_settings(tmp_path: Path, data: dict) -> Path:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(data))
    return settings_path


def _build_scenario(scenario_name: str):
    """Returns (settings_data, env_overrides, expected_uri, expected_cert) for the given
    shape, pulling live credentials via require_env (skips if absent) and the production
    X.509 cert via config.get_default_tls_cert_file (skips the x509 cases if absent)."""
    if scenario_name.startswith("x509/"):
        tls_uri = require_env(TLS_URI_ENV_VAR)
        tls_cert_path = config.get_default_tls_cert_file()
        if not tls_cert_path:
            pytest.skip(
                "No production X.509 cert found under ~/.bookscrapper/ - cannot run this x509 integration test."
            )
        # Bare filename exercises the resolver's CONFIG_DIR-relative literal resolution;
        # the resulting absolute path must match the env-sourced absolute path exactly.
        tls_cert_name = Path(tls_cert_path).name
        expected_literal_cert = str(config.CONFIG_DIR / tls_cert_name)

        builders = {
            "x509/literal-literal": (
                {
                    "auth_type": "x509",
                    "x509": {
                        "uri": {"source": "literal", "value": tls_uri},
                        "cert": {"source": "literal", "value": tls_cert_name},
                    },
                },
                {},
                tls_uri,
                expected_literal_cert,
            ),
            "x509/literal-env": (
                {
                    "auth_type": "x509",
                    "x509": {
                        "uri": {"source": "literal", "value": tls_uri},
                        "cert": {"source": "env", "value": _SETTINGS_TLS_CERT},
                    },
                },
                {_SETTINGS_TLS_CERT: tls_cert_path},
                tls_uri,
                tls_cert_path,
            ),
            "x509/env-literal": (
                {
                    "auth_type": "x509",
                    "x509": {
                        "uri": {"source": "env", "value": _SETTINGS_TLS_URI},
                        "cert": {"source": "literal", "value": tls_cert_name},
                    },
                },
                {_SETTINGS_TLS_URI: tls_uri},
                tls_uri,
                expected_literal_cert,
            ),
            "x509/env-env": (
                {
                    "auth_type": "x509",
                    "x509": {
                        "uri": {"source": "env", "value": _SETTINGS_TLS_URI},
                        "cert": {"source": "env", "value": _SETTINGS_TLS_CERT},
                    },
                },
                {_SETTINGS_TLS_URI: tls_uri, _SETTINGS_TLS_CERT: tls_cert_path},
                tls_uri,
                tls_cert_path,
            ),
        }
        return builders[scenario_name]

    pass_uri = require_env(PASS_URI_ENV_VAR)
    builders = {
        "pass/literal": (
            {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": pass_uri}}},
            {},
            pass_uri,
            None,
        ),
        "pass/env": (
            {"auth_type": "pass", "pass": {"uri": {"source": "env", "value": _SETTINGS_PASS_URI}}},
            {_SETTINGS_PASS_URI: pass_uri},
            pass_uri,
            None,
        ),
    }
    return builders[scenario_name]


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_name",
    [
        "pass/literal",
        "pass/env",
        pytest.param(
            "x509/literal-literal",
            marks=pytest.mark.xfail(reason=X509_XFAIL_REASON, strict=False),
        ),
        pytest.param(
            "x509/literal-env",
            marks=pytest.mark.xfail(reason=X509_XFAIL_REASON, strict=False),
        ),
        pytest.param(
            "x509/env-literal",
            marks=pytest.mark.xfail(reason=X509_XFAIL_REASON, strict=False),
        ),
        pytest.param(
            "x509/env-env",
            marks=pytest.mark.xfail(reason=X509_XFAIL_REASON, strict=False),
        ),
    ],
    ids=[
        "pass/literal",
        "pass/env",
        "x509/literal-literal",
        "x509/literal-env",
        "x509/env-literal",
        "x509/env-env",
    ],
)
def test_settings_resolution_and_connect(scenario_name, tmp_path, monkeypatch):
    """resolve_mongo_connection() reads the declared settings.json shape, returns
    exactly the declared URI/cert, and the resolved pair connects to live Atlas.

    'pass' shapes must resolve AND connect (unmarked). 'x509' shapes must resolve
    correctly; the connection is expected to XFAIL today (Atlas-side cert issue).
    """
    settings_data, env_overrides, expected_uri, expected_cert = _build_scenario(scenario_name)

    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)

    settings_path = _write_settings(tmp_path, settings_data)

    try:
        uri, cert = config.resolve_mongo_connection(path=settings_path)
    except config.ConfigError as exc:
        pytest.fail(f"resolve_mongo_connection raised ConfigError for {scenario_name}: {exc}")

    assert uri == expected_uri
    assert cert == expected_cert
    if cert is not None:
        assert Path(cert).exists(), f"resolved cert path does not exist: {cert}"

    assert run_connection_test(uri, tls_cert_file=cert) is True
