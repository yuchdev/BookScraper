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
    path = directory / name
    path.write_text("-----BEGIN CERTIFICATE-----\n")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_no_certificates_returns_none(cert_dir) -> None:
    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) is None
    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) is None


def test_picks_newest_certificate_by_mtime(cert_dir) -> None:
    _make_cert(cert_dir, "X509-cert-20250101_000000.pem", 1_000)
    newest = _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    _make_cert(cert_dir, "X509-cert-20250601_000000.pem", 5_000)

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) == newest
    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(newest)


def test_newest_wins_even_when_name_sorts_lower(cert_dir) -> None:
    """mtime, not lexical name order, decides - a manually placed cert can outrank a
    later-named one."""
    _make_cert(cert_dir, "X509-cert-99999999_999999.pem", 1_000)
    newest = _make_cert(cert_dir, "X509-cert-142838411852079927.pem", 9_000)

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) == newest


def test_a_freshly_rotated_cert_is_picked_up(cert_dir) -> None:
    """The regression that motivated this module: after rotation writes a new
    timestamped cert, resolution must follow it rather than a pinned filename."""
    _make_cert(cert_dir, "X509-cert-142838411852079927.pem", 1_000)
    rotated = _make_cert(cert_dir, "X509-cert-20260923_120000.pem", 9_000)

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(rotated)


def test_unrelated_files_are_ignored(cert_dir) -> None:
    (cert_dir / "settings.json").write_text("{}")
    (cert_dir / "notes.txt").write_text("x")
    (cert_dir / "X509-cert-old.pem.bak").write_text("x")

    assert mongo_test_helpers.get_default_tls_cert_file(cert_dir) is None


def test_env_var_overrides_the_directory(cert_dir, tmp_path, monkeypatch) -> None:
    _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    override = tmp_path / "elsewhere.pem"
    override.write_text("cert")
    monkeypatch.setenv("TLS_CERT_FILE", str(override))

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(override)


def test_nonexistent_env_var_path_falls_back_to_directory(cert_dir, monkeypatch) -> None:
    newest = _make_cert(cert_dir, "X509-cert-20260101_000000.pem", 9_000)
    monkeypatch.setenv("TLS_CERT_FILE", str(cert_dir / "does-not-exist.pem"))

    assert mongo_test_helpers.get_tls_cert_file(config_dir=cert_dir) == str(newest)


def test_helper_no_longer_pins_a_hardcoded_certificate_name() -> None:
    """The stale constant must stay gone - its removal is the fix."""
    assert not hasattr(mongo_test_helpers, "DEFAULT_TLS_CERT_FILE")
    assert mongo_test_helpers.TLS_CERT_GLOB == "X509-cert-*.pem"
