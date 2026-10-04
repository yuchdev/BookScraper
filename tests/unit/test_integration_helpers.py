"""Unit tests for tests/integration/mongo_test_helpers.py's cert resolution.

The integration tier itself needs live Atlas credentials, but its *cert-resolution*
logic is pure filesystem work and is what decides whether that tier exercises the
current X.509 identity or silently skips. It regressed once already: the helper pinned
a single manually-downloaded filename (X509-cert-142838411852079927.pem), so once
`bookscraper rotate-cert` began writing fresh timestamped names, the tier could never
find the cert it was supposed to test. These tests pin the newest-by-mtime contract so
that cannot come back. No network, no real ~/.bookscrapper - tmp_path only.
"""

import pytest

from tests.integration import mongo_test_helpers


@pytest.fixture
def cert_dir(tmp_path, monkeypatch):
    """An empty stand-in for ~/.bookscrapper, with TLS_CERT_FILE cleared."""
    monkeypatch.delenv("TLS_CERT_FILE", raising=False)
    return tmp_path


def _make_cert(directory, name: str, mtime: float):
    """Create a stub PEM file in ``directory`` with the given name and mtime."""
    path = directory / name
    path.write_text("-----BEGIN CERTIFICATE-----\n")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_no_certificates_returns_none(cert_dir) -> None:
    """
    [Unit] integration helpers: verifies no certificates returns none.

    Scenario:
        Given test inputs and fixtures for no certificates returns none
        When test_no_certificates_returns_none executes the target flow
        Then the expected outcome for no certificates returns none is confirmed.

    Boundaries:
        - Focus: no certificates returns none
        - Fixtures/params: cert_dir
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to no certificates returns none
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by integration helpers
    """
    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) is None
    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) is None


def test_picks_newest_certificate_by_mtime(cert_dir) -> None:
    """
    [Unit] integration helpers: verifies picks newest certificate by mtime.

    Scenario:
        Given test inputs and fixtures for picks newest certificate by mtime
        When test_picks_newest_certificate_by_mtime executes the target flow
        Then the expected outcome for picks newest certificate by mtime is confirmed.

    Boundaries:
        - Focus: picks newest certificate by mtime
        - Fixtures/params: cert_dir
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to picks newest certificate by mtime
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by integration helpers
    """
    _make_cert(cert_dir, "X509-cert-20250101_000000.pem", 1_000)
    newest = _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    _make_cert(cert_dir, "X509-cert-20250601_000000.pem", 5_000)

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) == newest
    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(newest)


def test_newest_wins_even_when_name_sorts_lower(cert_dir) -> None:
    """
    [Unit] integration helpers: verifies mtime beats lexical name order when selecting the newest cert.

    Scenario:
        Given two cert files where the higher-mtime file has a lexically lower name
        When get_default_tls_cert_file is called
        Then the file with the highest mtime is returned, not the lexically last name.

    Boundaries:
        - No env-var override path; cert_dir fixture only
        - Sorting by mtime, not by filename alphanumeric order

    On failure, first check:
        - Whether get_default_tls_cert_file sorts candidates by mtime descending
        - Recent changes to the glob/sort logic in mongo_test_helpers
    """
    _make_cert(cert_dir, "X509-cert-99999999_999999.pem", 1_000)
    newest = _make_cert(cert_dir, "X509-cert-142838411852079927.pem", 9_000)

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) == newest


def test_a_freshly_rotated_cert_is_picked_up(cert_dir) -> None:
    """
    [Unit] integration helpers: verifies a freshly-rotated cert is picked up via mtime glob.

    Scenario:
        Given an older pinned cert and a newly-rotated cert with a higher mtime
        When get_tls_cert_file is called with no TLS_CERT_FILE env override
        Then the rotated cert's path is returned, not the older one's.

    Boundaries:
        - No env-var override; cert_dir fixture; file mtime ordering
        - Regression guard: the helper must not pin a hardcoded filename

    On failure, first check:
        - Whether get_tls_cert_file uses mtime not filename to pick the newest cert
        - Recent changes to TLS_CERT_GLOB or mtime-sort logic in mongo_test_helpers
    """
    _make_cert(cert_dir, "X509-cert-142838411852079927.pem", 1_000)
    rotated = _make_cert(cert_dir, "X509-cert-20260923_120000.pem", 9_000)

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(rotated)


def test_unrelated_files_are_ignored(cert_dir) -> None:
    """
    [Unit] integration helpers: verifies unrelated files are ignored.

    Scenario:
        Given test inputs and fixtures for unrelated files are ignored
        When test_unrelated_files_are_ignored executes the target flow
        Then the expected outcome for unrelated files are ignored is confirmed.

    Boundaries:
        - Focus: unrelated files are ignored
        - Fixtures/params: cert_dir
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unrelated files are ignored
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by integration helpers
    """
    (cert_dir / "settings.json").write_text("{}")
    (cert_dir / "notes.txt").write_text("x")
    (cert_dir / "X509-cert-old.pem.bak").write_text("x")

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) is None


def test_env_var_overrides_the_directory(cert_dir, tmp_path, monkeypatch) -> None:
    """
    [Local] integration helpers: verifies env var overrides the directory.

    Scenario:
        Given test inputs and fixtures for env var overrides the directory
        When test_env_var_overrides_the_directory executes the target flow
        Then the expected outcome for env var overrides the directory is confirmed.

    Boundaries:
        - Focus: env var overrides the directory
        - Fixtures/params: cert_dir, tmp_path, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to env var overrides the directory
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by integration helpers
    """
    _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    override = tmp_path / "elsewhere.pem"
    override.write_text("cert")
    monkeypatch.setenv("TLS_CERT_FILE", str(override))

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(override)


def test_nonexistent_env_var_path_falls_back_to_directory(cert_dir, monkeypatch) -> None:
    """
    [Local] integration helpers: verifies nonexistent env var path falls back to directory.

    Scenario:
        Given test inputs and fixtures for nonexistent env var path falls back to directory
        When test_nonexistent_env_var_path_falls_back_to_directory executes the target flow
        Then the expected outcome for nonexistent env var path falls back to directory is confirmed.

    Boundaries:
        - Focus: nonexistent env var path falls back to directory
        - Fixtures/params: cert_dir, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to nonexistent env var path falls back to directory
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by integration helpers
    """
    newest = _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    monkeypatch.setenv("TLS_CERT_FILE", str(cert_dir / "does-not-exist.pem"))

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(newest)


def test_helper_no_longer_pins_a_hardcoded_certificate_name() -> None:
    """
    [Unit] integration helpers: verifies the hardcoded cert constant is absent and the glob is present.

    Scenario:
        Given the mongo_test_helpers module
        When its attributes are inspected
        Then DEFAULT_TLS_CERT_FILE does not exist and TLS_CERT_GLOB equals "X509-cert-*.pem".

    Boundaries:
        - Attribute presence check only; no filesystem or network access
        - Guards against re-introduction of the pinned constant

    On failure, first check:
        - Whether DEFAULT_TLS_CERT_FILE was accidentally re-added to mongo_test_helpers
        - Whether TLS_CERT_GLOB was renamed or removed
    """
    assert not hasattr(mongo_test_helpers, "DEFAULT_TLS_CERT_FILE")
    assert mongo_test_helpers.TLS_CERT_GLOB == "X509-cert-*.pem"
