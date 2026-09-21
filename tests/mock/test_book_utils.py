"""Mock-tier tests for bookscraper.book_utils.check_mongodb_connection.

The pure/local-I/O helpers in book_utils.py are covered in tests/unit/test_book_utils.py;
this function needs mocking (MongoClient, the resolver) so it lives here instead. It
resolves connection details via a LOCAL import - `from .backends.mongo import config`
inside the function body (see the function's own comment on why) - so tests patch the
real submodule attribute `bookscraper.backends.mongo.config.resolve_mongo_connection`
rather than anything on `bookscraper.book_utils` itself.
"""

from unittest.mock import MagicMock, patch

from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from bookscraper import book_utils
from bookscraper.backends.mongo.config import ConfigError


def _patch_resolve(return_value=None, side_effect=None):
    return patch(
        "bookscraper.backends.mongo.config.resolve_mongo_connection",
        return_value=return_value,
        side_effect=side_effect,
    )


class TestCheckMongodbConnection:
    def test_config_error_returns_false(self) -> None:
        with _patch_resolve(side_effect=ConfigError("bad settings.json")):
            assert book_utils.check_mongodb_connection() is False

    def test_no_uri_returns_false(self) -> None:
        with _patch_resolve(return_value=(None, None)):
            assert book_utils.check_mongodb_connection() is False

    def test_success_without_tls_cert(self) -> None:
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            patch.object(book_utils, "MongoClient", return_value=mock_client) as mock_ctor,
        ):
            assert book_utils.check_mongodb_connection() is True

        _, kwargs = mock_ctor.call_args
        assert "tlsCertificateKeyFile" not in kwargs
        mock_client.admin.command.assert_called_once_with("ping")
        mock_client.close.assert_called_once()

    def test_success_with_existing_tls_cert(self, tmp_path) -> None:
        cert_path = tmp_path / "cert.pem"
        cert_path.write_text("fake cert")
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", str(cert_path))),
            patch.object(book_utils, "MongoClient", return_value=mock_client) as mock_ctor,
        ):
            assert book_utils.check_mongodb_connection() is True

        _, kwargs = mock_ctor.call_args
        assert kwargs["tls"] is True
        assert kwargs["tlsCertificateKeyFile"] == str(cert_path)
        mock_client.close.assert_called_once()

    def test_tls_cert_path_that_does_not_exist_is_ignored(self, tmp_path) -> None:
        missing_cert = str(tmp_path / "does_not_exist.pem")
        mock_client = MagicMock()
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", missing_cert)),
            patch.object(book_utils, "MongoClient", return_value=mock_client) as mock_ctor,
        ):
            assert book_utils.check_mongodb_connection() is True

        _, kwargs = mock_ctor.call_args
        assert "tlsCertificateKeyFile" not in kwargs

    def test_connection_failure_returns_false(self) -> None:
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = ConnectionFailure("no route to host")
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            patch.object(book_utils, "MongoClient", return_value=mock_client),
        ):
            assert book_utils.check_mongodb_connection() is False
        mock_client.close.assert_called_once()

    def test_server_selection_timeout_returns_false(self) -> None:
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = ServerSelectionTimeoutError("timed out")
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            patch.object(book_utils, "MongoClient", return_value=mock_client),
        ):
            assert book_utils.check_mongodb_connection() is False

    def test_generic_exception_during_ping_returns_false(self) -> None:
        mock_client = MagicMock()
        mock_client.admin.command.side_effect = RuntimeError("boom")
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            patch.object(book_utils, "MongoClient", return_value=mock_client),
        ):
            assert book_utils.check_mongodb_connection() is False
        mock_client.close.assert_called_once()

    def test_generic_exception_constructing_client_returns_false_without_crashing(self) -> None:
        # client stays None if the constructor itself raises - the `finally: if client:
        # client.close()` guard must not blow up on a None client.
        with (
            _patch_resolve(return_value=("mongodb+srv://u:p@cluster/db", None)),
            patch.object(book_utils, "MongoClient", side_effect=RuntimeError("boom")),
        ):
            assert book_utils.check_mongodb_connection() is False
