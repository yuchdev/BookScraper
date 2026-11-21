"""Mock-tier tests for bookscraper.book_utils.check_mongodb_connection.

The pure/local-I/O helpers in book_utils.py are covered in tests/unit/test_book_utils.py;
this function needs mocking (MongoClient, the resolver) so it lives here instead. It
resolves connection details via a LOCAL import - `from .backends.mongo import config`
inside the function body (see the function's own comment on why) - so tests patch the
real submodule attribute `bookscraper.backends.mongo.config.resolve_mongo_connection`
rather than anything on `bookscraper.book_utils` itself.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from bookscraper import book_utils
from bookscraper.backends.mongo.config import ConfigError

from .scraping.playwright_fakes import build_async_client_factory


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


class _CoverResponse:
    """Minimal httpx.Response stand-in for download_cover_image (content + headers)."""

    def __init__(self, content=b"\xff\xd8\xff", content_type="image/jpeg", raise_exc=None):
        self.content = content
        self.headers = {"content-type": content_type} if content_type is not None else {}
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


def _patch_cover_httpx(responses=None, get_side_effect=None):
    factory, client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    return patch.object(book_utils.httpx, "AsyncClient", factory), client


class TestDownloadCoverImage:
    async def test_success_writes_cover_and_returns_path(self, tmp_path) -> None:
        patcher, client = _patch_cover_httpx(responses=[_CoverResponse(content=b"IMAGEBYTES")])
        with patcher:
            result = await book_utils.download_cover_image(
                "https://cdn.example/x.jpg", "A Tour of C++", covers_dir=tmp_path
            )

        expected = tmp_path / "A Tour of C++" / "cover.jpg"
        assert result == str(expected)
        assert expected.read_bytes() == b"IMAGEBYTES"
        client.get.assert_awaited_once()
        # Identifying, non-spoofed User-Agent (not a rotated browser string).
        _, kwargs = client.get.call_args
        assert "bookscraper" in kwargs["headers"]["User-Agent"].lower()

    async def test_extension_follows_content_type(self, tmp_path) -> None:
        patcher, _ = _patch_cover_httpx(responses=[_CoverResponse(content=b"PNG", content_type="image/png")])
        with patcher:
            result = await book_utils.download_cover_image("https://cdn.example/x", "Book", covers_dir=tmp_path)
        assert result.endswith("cover.png")
        assert (tmp_path / "Book" / "cover.png").exists()

    async def test_title_sanitized_into_folder_name(self, tmp_path) -> None:
        patcher, _ = _patch_cover_httpx(responses=[_CoverResponse()])
        with patcher:
            result = await book_utils.download_cover_image("https://cdn.example/x.jpg", "a/b:c", covers_dir=tmp_path)
        assert result == str(tmp_path / "a b c" / "cover.jpg")

    @pytest.mark.parametrize("bad_url", [None, "", "   ", 123])
    async def test_missing_url_returns_none_without_fetching(self, tmp_path, bad_url) -> None:
        patcher, client = _patch_cover_httpx(responses=[_CoverResponse()])
        with patcher:
            result = await book_utils.download_cover_image(bad_url, "Book", covers_dir=tmp_path)
        assert result is None
        client.get.assert_not_awaited()

    async def test_empty_body_returns_none(self, tmp_path) -> None:
        patcher, _ = _patch_cover_httpx(responses=[_CoverResponse(content=b"")])
        with patcher:
            result = await book_utils.download_cover_image("https://cdn.example/x.jpg", "Book", covers_dir=tmp_path)
        assert result is None
        assert not (tmp_path / "Book").exists()

    async def test_http_status_error_returns_none(self, tmp_path) -> None:
        request = httpx.Request("GET", "https://cdn.example/x.jpg")
        response = httpx.Response(404, request=request)
        status_exc = httpx.HTTPStatusError("boom", request=request, response=response)
        patcher, _ = _patch_cover_httpx(responses=[_CoverResponse(raise_exc=status_exc)])
        with patcher:
            result = await book_utils.download_cover_image("https://cdn.example/x.jpg", "Book", covers_dir=tmp_path)
        assert result is None

    async def test_network_error_returns_none(self, tmp_path) -> None:
        exc = httpx.RequestError("network down", request=httpx.Request("GET", "https://cdn.example/x.jpg"))
        patcher, _ = _patch_cover_httpx(get_side_effect=[exc])
        with patcher:
            result = await book_utils.download_cover_image("https://cdn.example/x.jpg", "Book", covers_dir=tmp_path)
        assert result is None
