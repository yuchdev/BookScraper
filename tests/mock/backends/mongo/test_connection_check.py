"""Mock-tier tests for bookscraper.backends.mongo.database.check_mongodb_connection.

The connection pre-flight lives in ``backends/mongo/database.py`` (it moved out of
``book_utils.py``, where it needed function-level imports to dodge an import cycle). It builds
its client through the single factory re-exported by ``database``, so tests patch
``database.build_mongo_client`` / ``database.ping`` and the real resolver
``bookscraper.backends.mongo.config.resolve_mongo_connection``.
"""

import logging
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bookscraper.backends.mongo import database
from bookscraper.backends.mongo.client import MongoConnectionError
from bookscraper.backends.mongo.config import ConfigError


@pytest.fixture(autouse=True)
def _neutralize_dotenv(monkeypatch):
    """Stop load_dotenv from reading a repo-root .env into a test."""
    monkeypatch.setattr(database, "load_dotenv", lambda *a, **k: None)
    yield


def _patch_resolve(return_value=None, side_effect=None) -> AbstractContextManager[Any]:
    """Patch config.resolve_mongo_connection for check_mongodb_connection tests."""
    return patch(
        "bookscraper.backends.mongo.config.resolve_mongo_connection",
        return_value=return_value,
        side_effect=side_effect,
    )


def _patch_build(return_value=None, side_effect=None) -> AbstractContextManager[Any]:
    """Patch database.build_mongo_client for check_mongodb_connection tests."""
    return patch(
        "bookscraper.backends.mongo.database.build_mongo_client",
        return_value=return_value,
        side_effect=side_effect,
    )


def _patch_ping(side_effect=None) -> AbstractContextManager[Any]:
    """Patch database.ping for check_mongodb_connection tests."""
    return patch("bookscraper.backends.mongo.database.ping", side_effect=side_effect)


class TestCheckMongodbConnection:
    """Tests for database.check_mongodb_connection."""

    def test_config_error_returns_false(self):
        """
        [Local] check mongodb connection: verifies that a ConfigError from the resolver returns False.

        Scenario:
            Given config.resolve_mongo_connection raising ConfigError("bad settings.json")
            When check_mongodb_connection() is called
            Then False is returned without attempting to build a client.

        Boundaries:
            - Covers the resolver-ConfigError path only; missing URI is a separate test.

        On failure, first check:
            - The except ConfigError branch at the top of check_mongodb_connection.
        """
        with _patch_resolve(side_effect=ConfigError("bad settings.json")):
            assert database.check_mongodb_connection() is False

    def test_no_uri_returns_false(self):
        """
        [Local] check mongodb connection: verifies that a None URI from the resolver returns False.

        Scenario:
            Given config.resolve_mongo_connection returning (None, None)
            When check_mongodb_connection() is called
            Then False is returned without attempting to build a client.

        Boundaries:
            - Covers the no-URI guard; a valid URI with a missing cert is a separate test.

        On failure, first check:
            - The URI-is-None guard in check_mongodb_connection.
        """
        with _patch_resolve(return_value=(None, None)):
            assert database.check_mongodb_connection() is False

    def test_success_without_tls_cert(self):
        """
        [Local] check mongodb connection: verifies that password auth without a TLS cert returns True.

        Scenario:
            Given a SCRAM URI and cert=None from the resolver, with build and ping stubbed
            When check_mongodb_connection() is called
            Then True is returned, build_mongo_client is called with cert_file=None, and
            the client is closed.

        Boundaries:
            - No TLS cert; the cert-path forwarding is covered by the TLS cert test.

        On failure, first check:
            - The cert_file argument passed to build_mongo_client and the client.close() call.
        """
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(return_value=mock_client) as mock_build,
            _patch_ping(),
        ):
            assert database.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[1] is None  # cert_file
        mock_client.close.assert_called_once()

    def test_success_with_existing_tls_cert(self, tmp_path):
        """
        [Local] check mongodb connection: verifies that an existing TLS cert path is forwarded to the factory.

        Scenario:
            Given a URI and a cert path pointing to a real file, with build and ping stubbed
            When check_mongodb_connection() is called
            Then True is returned and build_mongo_client is called with the cert path as
            its second positional argument.

        Boundaries:
            - Existing file only; a missing cert path is a separate test.

        On failure, first check:
            - The cert_file argument forwarding to build_mongo_client.
        """
        cert_path = tmp_path / "cert.pem"
        cert_path.write_text("fake cert")
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", str(cert_path))),
            _patch_build(return_value=mock_client) as mock_build,
            _patch_ping(),
        ):
            assert database.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[1] == str(cert_path)  # cert_file forwarded to the factory
        mock_client.close.assert_called_once()

    def test_check_mongodb_connection_honors_tls_ca_file(self, monkeypatch):
        """
        [Local] book utils: verifies check mongodb connection honors tls ca file.

        Scenario:
            TLS_CA_FILE is forwarded to the factory on the pre-flight path, identically to the real connection - it is no longer ignored.

        Boundaries:
            - TLS_CA_FILE set in the environment; build + ping stubbed.

        On failure, first check:
            - the pre-flight check drops the CA bundle and loses server-CA pinning.
        """
        monkeypatch.setenv("TLS_CA_FILE", "/path/to/ca.pem")
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(return_value=mock_client) as mock_build,
            _patch_ping(),
        ):
            assert database.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[2] == "/path/to/ca.pem"  # ca_file

    def test_missing_cert_file_raises_and_returns_false(self, tmp_path):
        """
        [Local] book utils: verifies missing cert file raises and returns false.

        Scenario:
            a configured client cert that does not exist fails the check via the factory's MongoConnectionError rather than silently connecting certless (the old bug).

        Boundaries:
            - resolve returns a nonexistent cert path; the real factory runs; MongoClient must never be constructed.

        On failure, first check:
            - the missing cert is dropped and the check reports a false-positive success.
        """
        missing = str(tmp_path / "does_not_exist.pem")
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", missing)),
            patch("bookscraper.backends.mongo.client.MongoClient") as mock_ctor,
        ):
            assert database.check_mongodb_connection() is False
        mock_ctor.assert_not_called()

    def test_connection_failure_returns_false_and_closes(self):
        """
        [Local] check mongodb connection: verifies that a MongoConnectionError returns False and closes the client.

        Scenario:
            Given ping raising MongoConnectionError("ServerSelectionTimeoutError")
            When check_mongodb_connection() is called
            Then False is returned and client.close() is called once.

        Boundaries:
            - MongoConnectionError only; a raw RuntimeError from the factory is a separate test.

        On failure, first check:
            - The except MongoConnectionError handler and the finally: client.close() in
              check_mongodb_connection.
        """
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(return_value=mock_client),
            _patch_ping(side_effect=MongoConnectionError("ServerSelectionTimeoutError")),
        ):
            assert database.check_mongodb_connection() is False
        mock_client.close.assert_called_once()

    def test_generic_exception_returns_false_without_crashing(self):
        # If the factory raises something that isn't a MongoConnectionError, the defensive
        # branch returns False without leaking or crashing; client stays None so the finally
        # guard must not blow up.
        """
        [Local] check mongodb connection: verifies that a RuntimeError from the factory returns False without crashing.

        Scenario:
            Given build_mongo_client raising RuntimeError("boom") so client is never assigned
            When check_mongodb_connection() is called
            Then False is returned and the finally block does not raise (client is None).

        Boundaries:
            - Covers the broad except Exception fallback and the client-is-None finally guard.

        On failure, first check:
            - The generic except handler and the "if client:" guard in the finally block.
        """
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(side_effect=RuntimeError("boom")),
        ):
            assert database.check_mongodb_connection() is False

    def test_failure_does_not_leak_uri_or_host(self, caplog):
        """
        [Local] book utils: verifies failure does not leak uri or host.

        Scenario:
            regression - a redacted MongoConnectionError on the pre-flight path leaves no URI/host in the logs or print_log output (CWE-532).

        Boundaries:
            - ping raises a redacted error; a fake secret URI is resolved.

        On failure, first check:
            - the connection URI/host reaches the persistent log or console.
        """
        secret_uri = "mongodb+srv://user:pw@cluster0.example.net/db"
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=(secret_uri, None)),
            _patch_build(return_value=mock_client),
            _patch_ping(side_effect=MongoConnectionError("OperationFailure 8000 AtlasError")),
            patch.object(database, "print_log") as mock_print,
            caplog.at_level(logging.DEBUG),
        ):
            assert database.check_mongodb_connection() is False

        printed = " ".join(str(a) for c in mock_print.call_args_list for a in c.args)
        for needle in ("cluster0.example.net", "user:pw", secret_uri):
            assert needle not in printed
            assert needle not in caplog.text
