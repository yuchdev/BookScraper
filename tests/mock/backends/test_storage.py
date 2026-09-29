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
from bookscraper.backends.local import store
from bookscraper.backends.storage import JsonBackend, MongoBackend, SaveSummary, resolve_store_backend


class TestSaveSummary:
    def test_total_sums_the_three_fields(self) -> None:
        """
        Scenario: total is the aggregate of inserted + duplicates + errors.
        Boundaries: mixed non-zero counts.
        On failure: a caller cannot trust total to equal the batch size.
        """
        summary = SaveSummary(inserted=3, duplicates=2, errors=1)
        assert summary.total == 6

    def test_is_frozen(self) -> None:
        """
        Scenario: SaveSummary is an immutable value object.
        Boundaries: attribute assignment after construction.
        On failure: a summary could be mutated after the fact, hiding the real outcome.
        """
        summary = SaveSummary(inserted=1, duplicates=0, errors=0)
        with pytest.raises(AttributeError):
            summary.inserted = 5  # type: ignore[misc]


class TestMongoBackend:
    def test_save_books_delegates_to_database(self) -> None:
        collection = Mock(name="collection")
        backend = MongoBackend(collection)
        books = [{"title": "A"}, {"title": "B"}]
        expected = SaveSummary(inserted=2, duplicates=0, errors=0)

        with patch.object(storage, "database") as mock_db:
            mock_db.save_books_to_mongodb.return_value = expected
            result = backend.save_books(books)

        mock_db.save_books_to_mongodb.assert_called_once_with(books, collection)
        # save_books now returns the SaveSummary its collaborator produced.
        assert result is expected

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
        expected = SaveSummary(inserted=1, duplicates=0, errors=0)

        with patch.object(storage, "store") as mock_store:
            mock_store.save_books.return_value = expected
            result = backend.save_books(books)

        # JsonBackend passes the (call-time) store path explicitly, so isolation via
        # monkeypatching store.LOCAL_STORE_FILE holds.
        mock_store.save_books.assert_called_once_with(books, mock_store.LOCAL_STORE_FILE)
        assert result is expected

    def test_lookups_read_from_the_cached_index(self, isolated_local_store) -> None:
        """
        Scenario: hash/asin/book_id/slug lookups resolve against the in-memory index built
        from the file, returning the same answers the old per-call file reads did.
        Boundaries: one stored book queried by each identifier, plus a miss and an empty key.
        On failure: a lookup reads a field the index never captured, so genuine duplicates
        slip through or new books are wrongly rejected.
        """
        store.save_books(
            [{"hash": "h1", "asin": "B001", "book_id": "42", "slug": "some-book"}],
            isolated_local_store,
        )
        backend = JsonBackend()

        assert backend.book_exists_by_hash("h1") is True
        assert backend.book_exists_by_hash("nope") is False
        assert backend.book_exists_by_hash("") is False
        assert backend.book_exists_by_asin("B001") is True
        assert backend.book_exists_by_asin("B999") is False
        assert backend.book_exists_by_asin("") is False
        assert backend.leanpub_book_exists("42", None) is True
        assert backend.leanpub_book_exists(None, "some-book") is True
        assert backend.leanpub_book_exists("99", "other") is False
        assert backend.leanpub_book_exists(None, None) is False

    def test_json_backend_reads_file_once_for_many_lookups(self, isolated_local_store) -> None:
        """
        Scenario: the index is built once and reused, so N dedup lookups parse the file once
        (the success criterion: 1,000 lookups against a 5,000-book store parse once).
        Boundaries: many mixed lookups after a single populated store, spying on _load_books.
        On failure: each lookup re-reads and re-parses the whole file - O(candidates x size),
        the exact cost this cache exists to remove.
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        backend = JsonBackend()

        with patch.object(store, "_load_books", wraps=store._load_books) as spy:
            for _ in range(50):
                backend.book_exists_by_hash("h1")
                backend.book_exists_by_asin("B001")
                backend.leanpub_book_exists("x", "y")

        spy.assert_called_once()

    def test_json_backend_cache_sees_books_saved_in_same_process(self, isolated_local_store) -> None:
        """
        Scenario: save_books keeps the in-memory index current, so a book saved after the
        index was built is immediately visible to lookups without re-reading the file.
        Boundaries: build the index (a lookup) before saving, then save and re-query.
        On failure: the cache goes stale within a run and a just-saved book is re-scraped or
        re-inserted as if new.
        """
        backend = JsonBackend()
        assert backend.book_exists_by_hash("h1") is False  # builds an empty index

        # A non-dict element and a hash-less book are ignored by the cache update, exactly
        # as store.save_books ignores them on the write side.
        backend.save_books(
            ["not-a-dict", {"title": "no hash"}, {"hash": "h1", "asin": "B001", "book_id": "42", "slug": "s"}]
        )

        assert backend.book_exists_by_hash("h1") is True
        assert backend.book_exists_by_asin("B001") is True
        assert backend.leanpub_book_exists("42", None) is True

    def test_close_is_noop_default(self) -> None:
        """JsonBackend inherits the ABC's no-op close(); it must not raise."""
        backend = JsonBackend()
        assert backend.close() is None


def test_mongo_and_json_backends_return_save_summary() -> None:
    """
    Scenario: both adapters propagate a SaveSummary from their collaborator, so a
    caller can treat the two backends interchangeably on the write path.
    Boundaries: distinct summaries per backend; the adapters must not coerce or drop them.
    On failure: one backend returns None (or a different type) and the exit-code contract
    that reads summary.errors breaks for that backend only.
    """
    mongo_summary = SaveSummary(inserted=5, duplicates=1, errors=2)
    json_summary = SaveSummary(inserted=1, duplicates=0, errors=0)

    with patch.object(storage, "database") as mock_db:
        mock_db.save_books_to_mongodb.return_value = mongo_summary
        mongo_result = MongoBackend(Mock(name="collection")).save_books([{"hash": "h"}])

    with patch.object(storage, "store") as mock_store:
        mock_store.save_books.return_value = json_summary
        json_result = JsonBackend().save_books([{"hash": "h"}])

    assert mongo_result is mongo_summary
    assert json_result is json_summary
    assert isinstance(mongo_result, SaveSummary)
    assert isinstance(json_result, SaveSummary)


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

    def test_json_with_write_permission_returns_json_backend(self, isolated_local_store) -> None:
        with patch.object(storage, "check_local_write_permission", return_value=True) as mock_check:
            backend = resolve_store_backend("json")

        assert isinstance(backend, JsonBackend)
        mock_check.assert_called_once_with()

    def test_preflight_exits_on_corrupt_store(self, isolated_local_store) -> None:
        """
        Scenario: a corrupt store is caught at pre-flight, before any scraping starts, so a
        run aborts loudly instead of failing at the first save (or clobbering the store).
        Boundaries: write permission granted, but the existing store file is invalid JSON.
        On failure: the process proceeds to scrape and only trips at save time, or worse,
        overwrites the corrupt store.
        """
        isolated_local_store.write_text("{not valid json", encoding="utf-8")

        with patch.object(storage, "check_local_write_permission", return_value=True):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("json")

        assert exc_info.value.code == 1

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
