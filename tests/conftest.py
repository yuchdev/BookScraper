"""
Shared fixtures for the unit/ and mock/ tiers.

CRITICAL: the real ~/.bookscrapper/settings.json holds real production MongoDB Atlas
credentials (see CLAUDE.md's Authentication configuration section), and the repo-root
books.json is the real local store. No unit/mock test may ever read or write either -
use isolated_config / isolated_local_store below instead of touching bookscraper.backends.mongo.config
or bookscraper.backends.local.store's real paths directly.
"""

import os
from collections.abc import MutableMapping
from pathlib import Path

import pytest

from bookscraper.backends.local import store
from bookscraper.backends.mongo import config

# Enables the ``pytester`` fixture so test_detail_goldens.py can prove the --update-goldens
# contract (rewrite-then-fail) in an isolated inline run that never touches the real goldens.
pytest_plugins = ["pytester"]


def pytest_addoption(parser: pytest.Parser):
    """Register ``--update-goldens`` for the detail-page golden tier (task 06.0, subtask 04).

    When set, each golden test rewrites its ``<name>.expected.json`` from the *current* scraper
    output instead of asserting against it, then deliberately fails the run with
    ``"goldens updated - review the diff"`` so a regeneration is never silently green - the diff
    must be reviewed and committed by hand.
    """
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help=(
            "Rewrite the detail-page *.expected.json golden files from current scraper output, "
            "then fail the run so the diff is reviewed (never silently green)."
        ),
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    """Whether ``--update-goldens`` was passed (see ``pytest_addoption``)."""
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def isolated_config(monkeypatch, tmp_path) -> Path:
    """Points bookscraper.backends.mongo.config's CONFIG_DIR/SETTINGS_FILE at a tmp_path
    instead of the real ~/.bookscrapper, so get_default_tls_cert_file()'s glob and any
    relative cert-path resolution never touch real files."""

    config_dir = tmp_path / ".bookscrapper"
    config_dir.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "SETTINGS_FILE", config_dir / "settings.json")
    return config_dir


@pytest.fixture
def isolated_local_store(monkeypatch, tmp_path) -> Path:
    """Points bookscraper.backends.local.store's LOCAL_STORE_FILE at a tmp_path instead
    of the repo-root books.json."""

    store_path = tmp_path / "books.json"
    monkeypatch.setattr(store, "LOCAL_STORE_FILE", store_path)
    return store_path


@pytest.fixture
def no_real_env(monkeypatch) -> MutableMapping[str, str]:
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
