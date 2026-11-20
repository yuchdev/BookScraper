"""
Shared fixtures for the unit/ and mock/ tiers.

CRITICAL: the real ~/.bookscrapper/settings.json holds real production MongoDB Atlas
credentials (see CLAUDE.md's Authentication configuration section), and the repo's staging/
directory holds a reviewer's real work. No unit/mock test may ever read or write either -
use isolated_config / isolated_staging_file below instead of touching
bookscraper.backends.mongo.config or the default staging path directly.
"""

import os

import pytest


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Points bookscraper.backends.mongo.config's CONFIG_DIR/SETTINGS_FILE at a tmp_path
    instead of the real ~/.bookscrapper, so get_default_tls_cert_file()'s glob and any
    relative cert-path resolution never touch real files."""
    from bookscraper.backends.mongo import config

    config_dir = tmp_path / ".bookscrapper"
    config_dir.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "SETTINGS_FILE", config_dir / "settings.json")
    return config_dir


@pytest.fixture
def isolated_staging_file(monkeypatch, tmp_path):
    """Runs the test from tmp_path and returns tmp_path/staging/books.json, so the default
    staging path, the legacy repo-root books.json check, and any report written to a relative
    path all resolve under tmp_path - never in the repository."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / "staging" / "books.json"


@pytest.fixture
def no_real_env(monkeypatch):
    """Strips real MongoDB credential env vars for the test's duration, so a prior
    load_dotenv() call (e.g. from importing book_utils) can't leak real values into a
    test that expects a clean environment."""
    for var in (
        "MONGODB_URI",
        "TLS_CERT_FILE",
        "TLS_CA_FILE",
        "TEST_MONGODB_URI_PASS",
        "TEST_MONGODB_URI_TLS",
    ):
        monkeypatch.delenv(var, raising=False)
    return os.environ
