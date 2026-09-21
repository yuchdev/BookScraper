"""Mock-tier tests for bookscraper.backends.storage - the mongo/json backend
adapters and resolve_store_backend()'s pre-flight-check-then-construct logic.

Every collaborator (database, deduplicate, store, check_local_write_permission) is
patched at its point of use inside the storage module, so no real MongoDB or
filesystem access occurs. The assertions pin both the delegated call args and that
each adapter returns exactly what its collaborator returned.
"""

from unittest.mock import Mock, patch

import pytest

from bookscraper.backends import storage
from bookscraper.backends.storage import JsonBackend, MongoBackend, resolve_store_backend


class TestMongoBackend:
    def test_save_books_delegates_to_database(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)
        books = [{"title": "A"}, {"title": "B"}]

        with patch.object(storage, "database") as mock_db:
            result = backend.save_books(books)

        mock_db.save_books_to_mongodb.assert_called_once_with(books, collection)
        # save_books returns None per the ABC contract.
        assert result is None

    def test_book_exists_by_hash_returns_delegated_value(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            mock_db.check_book_exists_in_db.return_value = True
            result = backend.book_exists_by_hash("deadbeef")

        mock_db.check_book_exists_in_db.assert_called_once_with("deadbeef", collection)
        assert result is True

    def test_book_exists_by_asin_returns_delegated_value(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            mock_db.check_amazon_asin_exists_in_db.return_value = False
            result = backend.book_exists_by_asin("B00XYZ")

        mock_db.check_amazon_asin_exists_in_db.assert_called_once_with("B00XYZ", collection)
        assert result is False

    def test_leanpub_book_exists_delegates_to_deduplicate(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "deduplicate") as mock_dedup:
            mock_dedup.leanpub_prescrape_deduplicate.return_value = True
            result = backend.leanpub_book_exists("id-1", "slug-1")

        mock_dedup.leanpub_prescrape_deduplicate.assert_called_once_with("id-1", "slug-1", collection)
        assert result is True

    def test_close_delegates_to_database(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            backend.close()

        mock_db.close_mongo_connection.assert_called_once_with()


class TestJsonBackend:
    def test_save_books_delegates_to_store(self) -> None:
        backend = JsonBackend()
        books = [{"title": "A"}]

        with patch.object(storage, "store") as mock_store:
            result = backend.save_books(books)

        mock_store.save_books.assert_called_once_with(books)
        assert result is None

    def test_book_exists_by_hash_returns_delegated_value(self) -> None:
        backend = JsonBackend()

        with patch.object(storage, "store") as mock_store:
            mock_store.check_book_exists.return_value = True
            result = backend.book_exists_by_hash("cafef00d")

        mock_store.check_book_exists.assert_called_once_with("cafef00d")
        assert result is True

    def test_book_exists_by_asin_returns_delegated_value(self) -> None:
        backend = JsonBackend()

        with patch.object(storage, "store") as mock_store:
            mock_store.check_amazon_asin_exists.return_value = False
            result = backend.book_exists_by_asin("B00ABC")

        mock_store.check_amazon_asin_exists.assert_called_once_with("B00ABC")
        assert result is False

    def test_leanpub_book_exists_delegates_to_store(self) -> None:
        backend = JsonBackend()

        with patch.object(storage, "store") as mock_store:
            mock_store.leanpub_book_exists.return_value = True
            result = backend.leanpub_book_exists("id-9", None)

        mock_store.leanpub_book_exists.assert_called_once_with("id-9", None)
        assert result is True

    def test_close_is_noop_default(self) -> None:
        """JsonBackend inherits the ABC's no-op close(); it must not raise."""
        backend = JsonBackend()
        assert backend.close() is None


class TestResolveStoreBackend:
    def test_mongo_with_live_collection_returns_mongo_backend(self) -> None:
        fake_collection = Mock(name="collection")

        with patch.object(storage, "database") as mock_db:
            mock_db.get_mongo_collection.return_value = fake_collection
            backend = resolve_store_backend("mongo")

        assert isinstance(backend, MongoBackend)
        assert backend._collection is fake_collection
        mock_db.get_mongo_collection.assert_called_once_with()

    def test_mongo_with_no_collection_exits(self) -> None:
        with patch.object(storage, "database") as mock_db:
            mock_db.get_mongo_collection.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("mongo")

        assert exc_info.value.code == 1

    def test_json_with_write_permission_returns_json_backend(self) -> None:
        with patch.object(storage, "check_local_write_permission", return_value=True) as mock_check:
            backend = resolve_store_backend("json")

        assert isinstance(backend, JsonBackend)
        mock_check.assert_called_once_with()

    def test_json_without_write_permission_exits(self) -> None:
        with patch.object(storage, "check_local_write_permission", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("json")

        assert exc_info.value.code == 1

    def test_unknown_backend_exits_without_running_checks(self) -> None:
        with (
            patch.object(storage, "database") as mock_db,
            patch.object(storage, "check_local_write_permission") as mock_check,
        ):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("csv")

        assert exc_info.value.code == 1
        mock_db.get_mongo_collection.assert_not_called()
        mock_check.assert_not_called()
