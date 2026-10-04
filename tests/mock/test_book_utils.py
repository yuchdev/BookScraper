"""Mock-tier tests for bookscraper.book_utils.check_mongodb_connection.

The pure/local-I/O helpers in book_utils.py are covered in tests/unit/test_book_utils.py;
this function needs mocking (the client factory, the resolver) so it lives here instead. It
now builds its client through the single factory in ``backends.mongo.client`` via LOCAL
imports inside the function body (see the function's own comment on why), so tests patch the
real submodule attributes ``bookscraper.backends.mongo.client.build_mongo_client`` / ``.ping``
and ``bookscraper.backends.mongo.config.resolve_mongo_connection`` rather than anything on
``bookscraper.book_utils`` itself.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bookscraper import book_utils
from bookscraper.backends.mongo.client import MongoConnectionError
from bookscraper.backends.mongo.config import ConfigError


@pytest.fixture(autouse=True)
def _neutralize_dotenv(monkeypatch):
    """Stop load_dotenv from reading a repo-root .env into a test."""
    monkeypatch.setattr(book_utils, "load_dotenv", lambda *a, **k: None)
    yield


def _patch_resolve(return_value=None, side_effect=None):
    """Patch config.resolve_mongo_connection for check_mongodb_connection tests."""
    return patch(
        "bookscraper.backends.mongo.config.resolve_mongo_connection",
        return_value=return_value,
        side_effect=side_effect,
    )


def _patch_build(return_value=None, side_effect=None):
    """Patch client.build_mongo_client for check_mongodb_connection tests."""
    return patch(
        "bookscraper.backends.mongo.client.build_mongo_client",
        return_value=return_value,
        side_effect=side_effect,
    )


def _patch_ping(side_effect=None):
    """Patch client.ping for check_mongodb_connection tests."""
    return patch("bookscraper.backends.mongo.client.ping", side_effect=side_effect)


class TestCheckMongodbConnection:
    """Tests for book_utils.check_mongodb_connection."""
    def test_config_error_returns_false(self) -> None:
        """
        [Local] check mongodb connection: verifies config error returns false.

        Scenario:
            Given test inputs and fixtures for config error returns false
            When test_config_error_returns_false executes the target flow
            Then the expected outcome for config error returns false is confirmed.

        Boundaries:
            - Focus: config error returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to config error returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        with _patch_resolve(side_effect=ConfigError("bad settings.json")):
            assert book_utils.check_mongodb_connection() is False

    def test_no_uri_returns_false(self) -> None:
        """
        [Local] check mongodb connection: verifies no uri returns false.

        Scenario:
            Given test inputs and fixtures for no uri returns false
            When test_no_uri_returns_false executes the target flow
            Then the expected outcome for no uri returns false is confirmed.

        Boundaries:
            - Focus: no uri returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to no uri returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        with _patch_resolve(return_value=(None, None)):
            assert book_utils.check_mongodb_connection() is False

    def test_success_without_tls_cert(self) -> None:
        """
        [Local] check mongodb connection: verifies success without tls cert.

        Scenario:
            Given test inputs and fixtures for success without tls cert
            When test_success_without_tls_cert executes the target flow
            Then the expected outcome for success without tls cert is confirmed.

        Boundaries:
            - Focus: success without tls cert
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to success without tls cert
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(return_value=mock_client) as mock_build,
            _patch_ping(),
        ):
            assert book_utils.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[1] is None  # cert_file
        mock_client.close.assert_called_once()

    def test_success_with_existing_tls_cert(self, tmp_path) -> None:
        """
        [Local] check mongodb connection: verifies success with existing tls cert.

        Scenario:
            Given test inputs and fixtures for success with existing tls cert
            When test_success_with_existing_tls_cert executes the target flow
            Then the expected outcome for success with existing tls cert is confirmed.

        Boundaries:
            - Focus: success with existing tls cert
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to success with existing tls cert
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        cert_path = tmp_path / "cert.pem"
        cert_path.write_text("fake cert")
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", str(cert_path))),
            _patch_build(return_value=mock_client) as mock_build,
            _patch_ping(),
        ):
            assert book_utils.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[1] == str(cert_path)  # cert_file forwarded to the factory
        mock_client.close.assert_called_once()

    def test_check_mongodb_connection_honors_tls_ca_file(self, monkeypatch) -> None:
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
            assert book_utils.check_mongodb_connection() is True

        args, _ = mock_build.call_args
        assert args[2] == "/path/to/ca.pem"  # ca_file

    def test_missing_cert_file_raises_and_returns_false(self, tmp_path) -> None:
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
            assert book_utils.check_mongodb_connection() is False
        mock_ctor.assert_not_called()

    def test_connection_failure_returns_false_and_closes(self) -> None:
        """
        [Local] check mongodb connection: verifies connection failure returns false and closes.

        Scenario:
            Given test inputs and fixtures for connection failure returns false and closes
            When test_connection_failure_returns_false_and_closes executes the target flow
            Then the expected outcome for connection failure returns false and closes is confirmed.

        Boundaries:
            - Focus: connection failure returns false and closes
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to connection failure returns false and closes
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(return_value=mock_client),
            _patch_ping(side_effect=MongoConnectionError("ServerSelectionTimeoutError")),
        ):
            assert book_utils.check_mongodb_connection() is False
        mock_client.close.assert_called_once()

    def test_generic_exception_returns_false_without_crashing(self) -> None:
        # If the factory raises something that isn't a MongoConnectionError, the defensive
        # branch returns False without leaking or crashing; client stays None so the finally
        # guard must not blow up.
        """
        [Local] check mongodb connection: verifies generic exception returns false without crashing.

        Scenario:
            Given test inputs and fixtures for generic exception returns false without crashing
            When test_generic_exception_returns_false_without_crashing executes the target flow
            Then the expected outcome for generic exception returns false without crashing is confirmed.

        Boundaries:
            - Focus: generic exception returns false without crashing
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to generic exception returns false without crashing
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check mongodb connection
        """
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            _patch_build(side_effect=RuntimeError("boom")),
        ):
            assert book_utils.check_mongodb_connection() is False

    def test_failure_does_not_leak_uri_or_host(self, caplog) -> None:
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
            patch.object(book_utils, "print_log") as mock_print,
            caplog.at_level(logging.DEBUG),
        ):
            assert book_utils.check_mongodb_connection() is False

        printed = " ".join(str(a) for c in mock_print.call_args_list for a in c.args)
        for needle in ("cluster0.example.net", "user:pw", secret_uri):
            assert needle not in printed
            assert needle not in caplog.text
