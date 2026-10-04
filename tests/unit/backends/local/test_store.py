"""Unit tests for bookscraper.backends.local.store, the `json` backend.

The json store must behave identically to MongoDB from the caller's perspective:
hash-uniqueness on write, and asin/book_id/slug lookups on read. A dedup lookup
reading a field the writer never stores, or an off-by-one in the "skip if hash
exists" path, either writes the same book repeatedly or rejects genuinely new
books. Every test uses the isolated_local_store fixture so no test ever touches
the real repo-root books.json.
"""

import json
import os

import pytest

from bookscraper.backends.local import store
from bookscraper.backends.local.store import LocalStoreCorruptError
from bookscraper.backends.storage import SaveSummary


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _temp_leftovers(store_path):
    """In-flight atomic-write temp files (``.books.json.<random>``) left in the dir."""
    prefix = f".{store_path.name}."
    return [p.name for p in store_path.parent.iterdir() if p.name.startswith(prefix)]


class TestSaveBooks:
    def test_creates_file_with_content_on_fresh_store(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies creates file with content on fresh store.

        Scenario:
            Given test inputs and fixtures for creates file with content on fresh store
            When test_creates_file_with_content_on_fresh_store executes the target flow
            Then the expected outcome for creates file with content on fresh store is confirmed.

        Boundaries:
            - Focus: creates file with content on fresh store
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to creates file with content on fresh store
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        assert not isolated_local_store.exists()
        store.save_books([{"hash": "h1", "title": "Book One"}], isolated_local_store)
        assert isolated_local_store.exists()
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["hash"] == "h1"
        assert data[0]["title"] == "Book One"

    def test_duplicate_hash_is_skipped(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies duplicate hash is skipped.

        Scenario:
            Given test inputs and fixtures for duplicate hash is skipped
            When test_duplicate_hash_is_skipped executes the target flow
            Then the expected outcome for duplicate hash is skipped is confirmed.

        Boundaries:
            - Focus: duplicate hash is skipped
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to duplicate hash is skipped
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h1", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["title"] == "First"  # original kept, not overwritten

    def test_new_book_appends_alongside_existing(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies new book appends alongside existing.

        Scenario:
            Given test inputs and fixtures for new book appends alongside existing
            When test_new_book_appends_alongside_existing executes the target flow
            Then the expected outcome for new book appends alongside existing is confirmed.

        Boundaries:
            - Focus: new book appends alongside existing
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to new book appends alongside existing
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h2", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert {b["hash"] for b in data} == {"h1", "h2"}

    def test_book_missing_hash_is_skipped_not_written(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies book missing hash is skipped not written.

        Scenario:
            Given test inputs and fixtures for book missing hash is skipped not written
            When test_book_missing_hash_is_skipped_not_written executes the target flow
            Then the expected outcome for book missing hash is skipped not written is confirmed.

        Boundaries:
            - Focus: book missing hash is skipped not written
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to book missing hash is skipped not written
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books([{"title": "No Hash"}], isolated_local_store)
        # _write_books only runs if something was inserted; nothing was.
        assert not isolated_local_store.exists()

    def test_mixed_batch_writes_only_valid_book(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies mixed batch writes only valid book.

        Scenario:
            Given test inputs and fixtures for mixed batch writes only valid book
            When test_mixed_batch_writes_only_valid_book executes the target flow
            Then the expected outcome for mixed batch writes only valid book is confirmed.

        Boundaries:
            - Focus: mixed batch writes only valid book
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to mixed batch writes only valid book
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books(
            [{"title": "No Hash"}, {"hash": "h1", "title": "Valid"}],
            isolated_local_store,
        )
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["hash"] == "h1"

    def test_empty_list_is_noop_no_file_created(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies empty list is noop no file created.

        Scenario:
            Given test inputs and fixtures for empty list is noop no file created
            When test_empty_list_is_noop_no_file_created executes the target flow
            Then the expected outcome for empty list is noop no file created is confirmed.

        Boundaries:
            - Focus: empty list is noop no file created
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty list is noop no file created
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books([], isolated_local_store)
        assert not isolated_local_store.exists()

    def test_all_duplicates_does_not_rewrite_missing_file(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies all duplicates does not rewrite missing file.

        Scenario:
            Given test inputs and fixtures for all duplicates does not rewrite missing file
            When test_all_duplicates_does_not_rewrite_missing_file executes the target flow
            Then the expected outcome for all duplicates does not rewrite missing file is confirmed.

        Boundaries:
            - Focus: all duplicates does not rewrite missing file
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to all duplicates does not rewrite missing file
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by save books
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        # Save the same hash again: num_inserted == 0, so _write_books is not called;
        # file content stays exactly as it was.
        before = _read(isolated_local_store)
        store.save_books([{"hash": "h1", "title": "Dup"}], isolated_local_store)
        assert _read(isolated_local_store) == before

    def test_json_save_books_returns_summary(self, isolated_local_store) -> None:
        """
        Scenario: save_books reports its outcome as a SaveSummary, matching the mongo
        backend's return contract so the two are interchangeable to the caller.
        Boundaries: a mixed batch - one insert, one hash-duplicate, one hash-less error.
        On failure: the counts drift from what was actually written, or the caller gets
        None and cannot tell a duplicate-only batch from a failure.
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)

        summary = store.save_books(
            [
                {"hash": "h2", "title": "New"},
                {"hash": "h1", "title": "Dup"},
                {"title": "No Hash"},
            ],
            isolated_local_store,
        )

        assert isinstance(summary, SaveSummary)
        assert (summary.inserted, summary.duplicates, summary.errors) == (1, 1, 1)
        assert summary.total == 3


class TestCheckBookExists:
    def test_found(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies found.

        Scenario:
            Given test inputs and fixtures for found
            When test_found executes the target flow
            Then the expected outcome for found is confirmed.

        Boundaries:
            - Focus: found
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("h1", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies not found.

        Scenario:
            Given test inputs and fixtures for not found
            When test_not_found executes the target flow
            Then the expected outcome for not found is confirmed.

        Boundaries:
            - Focus: not found
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("nope", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies empty store returns false.

        Scenario:
            Given test inputs and fixtures for empty store returns false
            When test_empty_store_returns_false executes the target flow
            Then the expected outcome for empty store returns false is confirmed.

        Boundaries:
            - Focus: empty store returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty store returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        assert store.check_book_exists("h1", isolated_local_store) is False

    def test_empty_hash_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies empty hash returns false.

        Scenario:
            Given test inputs and fixtures for empty hash returns false
            When test_empty_hash_returns_false executes the target flow
            Then the expected outcome for empty hash returns false is confirmed.

        Boundaries:
            - Focus: empty hash returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty hash returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        assert store.check_book_exists("", isolated_local_store) is False


class TestCheckAmazonAsinExists:
    def test_found(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies found.

        Scenario:
            Given test inputs and fixtures for found
            When test_found executes the target flow
            Then the expected outcome for found is confirmed.

        Boundaries:
            - Focus: found
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies not found.

        Scenario:
            Given test inputs and fixtures for not found
            When test_not_found executes the target flow
            Then the expected outcome for not found is confirmed.

        Boundaries:
            - Focus: not found
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B999", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies empty store returns false.

        Scenario:
            Given test inputs and fixtures for empty store returns false
            When test_empty_store_returns_false executes the target flow
            Then the expected outcome for empty store returns false is confirmed.

        Boundaries:
            - Focus: empty store returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty store returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is False

    def test_empty_asin_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies empty asin returns false.

        Scenario:
            Given test inputs and fixtures for empty asin returns false
            When test_empty_asin_returns_false executes the target flow
            Then the expected outcome for empty asin returns false is confirmed.

        Boundaries:
            - Focus: empty asin returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty asin returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        assert store.check_amazon_asin_exists("", isolated_local_store) is False


class TestLeanpubBookExists:
    def test_match_by_book_id_alone(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies match by book id alone.

        Scenario:
            Given test inputs and fixtures for match by book id alone
            When test_match_by_book_id_alone executes the target flow
            Then the expected outcome for match by book id alone is confirmed.

        Boundaries:
            - Focus: match by book id alone
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to match by book id alone
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", None, isolated_local_store) is True

    def test_match_by_slug_alone(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies match by slug alone.

        Scenario:
            Given test inputs and fixtures for match by slug alone
            When test_match_by_slug_alone executes the target flow
            Then the expected outcome for match by slug alone is confirmed.

        Boundaries:
            - Focus: match by slug alone
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to match by slug alone
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists(None, "some-book", isolated_local_store) is True

    def test_match_by_both(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies match by both.

        Scenario:
            Given test inputs and fixtures for match by both
            When test_match_by_both executes the target flow
            Then the expected outcome for match by both is confirmed.

        Boundaries:
            - Focus: match by both
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to match by both
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", "some-book", isolated_local_store) is True

    def test_no_match(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies no match.

        Scenario:
            Given test inputs and fixtures for no match
            When test_no_match executes the target flow
            Then the expected outcome for no match is confirmed.

        Boundaries:
            - Focus: no match
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to no match
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("99", "other-book", isolated_local_store) is False

    def test_neither_identifier_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies neither identifier returns false.

        Scenario:
            Given test inputs and fixtures for neither identifier returns false
            When test_neither_identifier_returns_false executes the target flow
            Then the expected outcome for neither identifier returns false is confirmed.

        Boundaries:
            - Focus: neither identifier returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to neither identifier returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        # Both falsy -> the "no identifier" warning branch, must not raise.
        assert store.leanpub_book_exists(None, None, isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies empty store returns false.

        Scenario:
            Given test inputs and fixtures for empty store returns false
            When test_empty_store_returns_false executes the target flow
            Then the expected outcome for empty store returns false is confirmed.

        Boundaries:
            - Focus: empty store returns false
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty store returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub book exists
        """
        assert store.leanpub_book_exists("42", "some-book", isolated_local_store) is False

    def test_non_dict_entry_is_skipped(self, isolated_local_store) -> None:
        """
        Scenario: a corrupted store holding a non-dict element is walked past to reach a
        genuine match further down the list.
        Boundaries: a list mixing a bare string with a valid book document.
        On failure: the loop calls .get on a str and raises AttributeError instead of
        skipping the malformed entry.
        """
        isolated_local_store.write_text(
            json.dumps(["not-a-dict", {"hash": "h1", "book_id": "42", "slug": "some-book"}]),
            encoding="utf-8",
        )
        assert store.leanpub_book_exists("42", None, isolated_local_store) is True


class TestAtomicWriteAndBackup:
    def test_write_is_atomic_and_leaves_no_temp_files(self, isolated_local_store) -> None:
        """
        Scenario: a completed save leaves the final store and no debris.
        Boundaries: one insert into a fresh store, then a directory scan for temp files.
        On failure: a stray ``.books.json.<random>`` temp file lingers (the replace was
        skipped or the temp was never cleaned), signalling a non-atomic write path.
        """
        store.save_books([{"hash": "h1", "title": "Book One"}], isolated_local_store)

        assert _read(isolated_local_store) == [{"hash": "h1", "title": "Book One"}]
        assert _temp_leftovers(isolated_local_store) == []

    def test_interrupted_write_preserves_previous_store(self, isolated_local_store, monkeypatch) -> None:
        """
        Scenario: the process dies during the final rename of a save; the accumulated
        store must survive intact so the next run doesn't overwrite it with a truncated
        (then empty-parsed) file.
        Boundaries: os.replace raises mid-write on the second save; the first book stays.
        On failure: books.json is truncated or reverts to only the new book - silent loss
        of every previously accumulated book.
        """
        store.save_books([{"hash": "h1", "title": "Original"}], isolated_local_store)

        def boom(*_args, **_kwargs):
            raise OSError("simulated crash during rename")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError, match="simulated crash"):
            store.save_books([{"hash": "h2", "title": "New"}], isolated_local_store)

        # The live store is exactly what it was before the interrupted save.
        assert _read(isolated_local_store) == [{"hash": "h1", "title": "Original"}]
        # ...and the failed attempt cleaned up its temp file.
        assert list(isolated_local_store.parent.glob(f".{isolated_local_store.name}.*")) == []

    def test_save_refuses_to_overwrite_corrupt_store(self, isolated_local_store) -> None:
        """
        Scenario: an existing store file is not valid JSON; a save must refuse rather than
        clobber it and discard whatever it might hold.
        Boundaries: a truncated/garbage file on disk, then a save of a valid book.
        On failure: save_books silently overwrites the corrupt file, losing its contents
        and returning a misleading success summary.
        """
        isolated_local_store.write_text('[{"hash": "h1", "title": "trunc', encoding="utf-8")

        with pytest.raises(LocalStoreCorruptError, match=str(isolated_local_store)):
            store.save_books([{"hash": "h2", "title": "New"}], isolated_local_store)

        # File left byte-for-byte untouched.
        assert isolated_local_store.read_text(encoding="utf-8") == '[{"hash": "h1", "title": "trunc'

    def test_backup_file_holds_previous_generation(self, isolated_local_store) -> None:
        """
        Scenario: each replace is preceded by copying the current store to <name>.bak, so
        one generation back is always recoverable.
        Boundaries: no .bak on the first (file-creating) write; after the second write the
        .bak holds the first generation while the live file holds both books.
        On failure: the backup is missing or already holds the new content, defeating the
        one-generation recovery the corrupt-store refusal points users to.
        """
        backup = isolated_local_store.with_name(f"{isolated_local_store.name}.bak")

        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        assert not backup.exists()  # nothing to back up on a fresh store

        store.save_books([{"hash": "h2", "title": "Second"}], isolated_local_store)

        assert _read(backup) == [{"hash": "h1", "title": "First"}]
        assert {b["hash"] for b in _read(isolated_local_store)} == {"h1", "h2"}


class TestAssertStoreReadable:
    def test_valid_store_does_not_raise(self, isolated_local_store) -> None:
        """
        [Unit] assert store readable: verifies valid store does not raise.

        Scenario:
            Given test inputs and fixtures for valid store does not raise
            When test_valid_store_does_not_raise executes the target flow
            Then the expected outcome for valid store does not raise is confirmed.

        Boundaries:
            - Focus: valid store does not raise
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to valid store does not raise
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by assert store readable
        """
        store.save_books([{"hash": "h1"}], isolated_local_store)
        store.assert_store_readable(isolated_local_store)  # must not raise

    def test_missing_store_does_not_raise(self, isolated_local_store) -> None:
        """
        [Unit] assert store readable: verifies missing store does not raise.

        Scenario:
            Given test inputs and fixtures for missing store does not raise
            When test_missing_store_does_not_raise executes the target flow
            Then the expected outcome for missing store does not raise is confirmed.

        Boundaries:
            - Focus: missing store does not raise
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing store does not raise
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by assert store readable
        """
        store.assert_store_readable(isolated_local_store)  # must not raise

    def test_corrupt_store_raises(self, isolated_local_store) -> None:
        """
        [Unit] assert store readable: verifies corrupt store raises.

        Scenario:
            Given test inputs and fixtures for corrupt store raises
            When test_corrupt_store_raises executes the target flow
            Then the expected outcome for corrupt store raises is confirmed.

        Boundaries:
            - Focus: corrupt store raises
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to corrupt store raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by assert store readable
        """
        isolated_local_store.write_text("{not json", encoding="utf-8")
        with pytest.raises(LocalStoreCorruptError):
            store.assert_store_readable(isolated_local_store)


class TestLoadBooksRecovery:
    def test_malformed_json_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies malformed json returns empty list.

        Scenario:
            Given test inputs and fixtures for malformed json returns empty list
            When test_malformed_json_returns_empty_list executes the target flow
            Then the expected outcome for malformed json returns empty list is confirmed.

        Boundaries:
            - Focus: malformed json returns empty list
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to malformed json returns empty list
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load books recovery
        """
        isolated_local_store.write_text("{not valid json", encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_valid_json_non_list_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies valid json non list returns empty list.

        Scenario:
            Given test inputs and fixtures for valid json non list returns empty list
            When test_valid_json_non_list_returns_empty_list executes the target flow
            Then the expected outcome for valid json non list returns empty list is confirmed.

        Boundaries:
            - Focus: valid json non list returns empty list
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to valid json non list returns empty list
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load books recovery
        """
        isolated_local_store.write_text('{"hash": "h1"}', encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_nonexistent_file_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies nonexistent file returns empty list.

        Scenario:
            Given test inputs and fixtures for nonexistent file returns empty list
            When test_nonexistent_file_returns_empty_list executes the target flow
            Then the expected outcome for nonexistent file returns empty list is confirmed.

        Boundaries:
            - Focus: nonexistent file returns empty list
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to nonexistent file returns empty list
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load books recovery
        """
        assert store._load_books(isolated_local_store) == []

    def test_unreadable_file_returns_empty_list(self, isolated_local_store) -> None:
        """
        Scenario: an OSError while reading the store (not a JSON error) degrades to an empty
        result for read-only callers rather than crashing a lookup.
        Boundaries: the store path is a directory, so read_text raises IsADirectoryError.
        On failure: a transient read error propagates out of a lookup instead of being
        contained.
        """
        isolated_local_store.mkdir()  # path.exists() is True, but read_text raises OSError
        assert store._load_books(isolated_local_store) == []
