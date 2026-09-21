"""Unit tests for bookscraper.backends.local.store, the `json` backend.

The json store must behave identically to MongoDB from the caller's perspective:
hash-uniqueness on write, and asin/book_id/slug lookups on read. A dedup lookup
reading a field the writer never stores, or an off-by-one in the "skip if hash
exists" path, either writes the same book repeatedly or rejects genuinely new
books. Every test uses the isolated_local_store fixture so no test ever touches
the real repo-root books.json.
"""

import json

from bookscraper.backends.local import store


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSaveBooks:
    def test_creates_file_with_content_on_fresh_store(self, isolated_local_store) -> None:
        assert not isolated_local_store.exists()
        store.save_books([{"hash": "h1", "title": "Book One"}], isolated_local_store)
        assert isolated_local_store.exists()
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["hash"] == "h1"
        assert data[0]["title"] == "Book One"

    def test_duplicate_hash_is_skipped(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h1", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["title"] == "First"  # original kept, not overwritten

    def test_new_book_appends_alongside_existing(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h2", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert {b["hash"] for b in data} == {"h1", "h2"}

    def test_book_missing_hash_is_skipped_not_written(self, isolated_local_store) -> None:
        store.save_books([{"title": "No Hash"}], isolated_local_store)
        # _write_books only runs if something was inserted; nothing was.
        assert not isolated_local_store.exists()

    def test_mixed_batch_writes_only_valid_book(self, isolated_local_store) -> None:
        store.save_books(
            [{"title": "No Hash"}, {"hash": "h1", "title": "Valid"}],
            isolated_local_store,
        )
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["hash"] == "h1"

    def test_empty_list_is_noop_no_file_created(self, isolated_local_store) -> None:
        store.save_books([], isolated_local_store)
        assert not isolated_local_store.exists()

    def test_all_duplicates_does_not_rewrite_missing_file(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        # Save the same hash again: num_inserted == 0, so _write_books is not called;
        # file content stays exactly as it was.
        before = _read(isolated_local_store)
        store.save_books([{"hash": "h1", "title": "Dup"}], isolated_local_store)
        assert _read(isolated_local_store) == before


class TestCheckBookExists:
    def test_found(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("h1", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("nope", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        assert store.check_book_exists("h1", isolated_local_store) is False

    def test_empty_hash_returns_false(self, isolated_local_store) -> None:
        assert store.check_book_exists("", isolated_local_store) is False


class TestCheckAmazonAsinExists:
    def test_found(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B999", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is False

    def test_empty_asin_returns_false(self, isolated_local_store) -> None:
        assert store.check_amazon_asin_exists("", isolated_local_store) is False


class TestLeanpubBookExists:
    def test_match_by_book_id_alone(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", None, isolated_local_store) is True

    def test_match_by_slug_alone(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists(None, "some-book", isolated_local_store) is True

    def test_match_by_both(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", "some-book", isolated_local_store) is True

    def test_no_match(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("99", "other-book", isolated_local_store) is False

    def test_neither_identifier_returns_false(self, isolated_local_store) -> None:
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        # Both falsy -> the "no identifier" warning branch, must not raise.
        assert store.leanpub_book_exists(None, None, isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        assert store.leanpub_book_exists("42", "some-book", isolated_local_store) is False


class TestLoadBooksRecovery:
    def test_malformed_json_returns_empty_list(self, isolated_local_store) -> None:
        isolated_local_store.write_text("{not valid json", encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_valid_json_non_list_returns_empty_list(self, isolated_local_store) -> None:
        isolated_local_store.write_text('{"hash": "h1"}', encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_nonexistent_file_returns_empty_list(self, isolated_local_store) -> None:
        assert store._load_books(isolated_local_store) == []
