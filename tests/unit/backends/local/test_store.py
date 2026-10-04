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
    """Read and JSON-parse the store file at ``path``."""
    return json.loads(path.read_text(encoding="utf-8"))


def _temp_leftovers(store_path):
    """In-flight atomic-write temp files (``.books.json.<random>``) left in the dir."""
    prefix = f".{store_path.name}."
    return [p.name for p in store_path.parent.iterdir() if p.name.startswith(prefix)]


class TestSaveBooks:
    """Tests for store.save_books(): write, dedup, and SaveSummary contract."""

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
        [Unit] save books: verifies save_books returns a SaveSummary matching the mongo backend's contract.

        Scenario:
            Given an existing book with hash "h1" already saved
            When save_books is called with a mixed batch (new, duplicate, hash-less)
            Then the returned SaveSummary has inserted=1, duplicates=1, errors=1, total=3.

        Boundaries:
            - Mixed batch: one new insert, one hash-duplicate, one missing-hash error
            - Return type must be SaveSummary (not None), so a duplicate-only batch is distinguishable

        On failure, first check:
            - Whether save_books still returns SaveSummary rather than None
            - Whether hash-less entries are counted as errors, not silently dropped or inserted
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
    """Tests for store.check_book_exists(): hash-based duplicate lookup in the json store."""

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
    """Tests for store.check_amazon_asin_exists(): ASIN-based dedup lookup in the json store."""

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
    """Tests for store.leanpub_book_exists(): book_id/slug dedup lookup for the Leanpub scrape path."""

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
        [Unit] leanpub book exists: verifies a non-dict entry in the store is skipped, not raised on.

        Scenario:
            Given a store containing a bare string followed by a valid book document
            When leanpub_book_exists is called with the valid book's ID
            Then True is returned, proving the non-dict element was walked past safely.

        Boundaries:
            - Store contains a mix: one bare string (corrupted entry) and one valid dict
            - The valid match appears after the corrupted entry

        On failure, first check:
            - Whether leanpub_book_exists guards against calling .get on a non-dict entry
            - Whether an AttributeError is propagating instead of being caught
        """
        isolated_local_store.write_text(
            json.dumps(["not-a-dict", {"hash": "h1", "book_id": "42", "slug": "some-book"}]),
            encoding="utf-8",
        )
        assert store.leanpub_book_exists("42", None, isolated_local_store) is True


class TestAtomicWriteAndBackup:
    """Tests for the atomic write and .bak backup behaviour of save_books()."""

    def test_write_is_atomic_and_leaves_no_temp_files(self, isolated_local_store) -> None:
        """
        [Unit] atomic write: verifies a completed save leaves no in-flight temp files.

        Scenario:
            Given a fresh store with no prior books
            When save_books writes one book
            Then the store holds the book and no `.books.json.<random>` temp files remain.

        Boundaries:
            - Single insert; directory scan for temp file debris after the write
            - Real file I/O via isolated_local_store fixture

        On failure, first check:
            - Whether atomic_write_bytes cleans up the temp file after os.replace
            - Whether the write path is using os.replace vs a direct overwrite
        """
        store.save_books([{"hash": "h1", "title": "Book One"}], isolated_local_store)

        assert _read(isolated_local_store) == [{"hash": "h1", "title": "Book One"}]
        assert _temp_leftovers(isolated_local_store) == []

    def test_interrupted_write_preserves_previous_store(self, isolated_local_store, monkeypatch) -> None:
        """
        [Unit] atomic write: verifies an interrupted rename leaves the previous store intact.

        Scenario:
            Given a store with one existing book
            When os.replace is patched to raise OSError during a second save
            Then the original book survives and no temp files are left.

        Boundaries:
            - os.replace patched via monkeypatch; real file I/O otherwise
            - Guards against silent data loss when the process dies mid-write

        On failure, first check:
            - Whether the write creates the temp file before os.replace is called
            - Whether the temp file cleanup still runs in the error path
        """
        store.save_books([{"hash": "h1", "title": "Original"}], isolated_local_store)

        def boom(*_args, **_kwargs):
            """Simulate a crash during the final rename step."""
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
        [Unit] atomic write: verifies save_books raises LocalStoreCorruptError and leaves a corrupt file untouched.

        Scenario:
            Given an existing store file containing truncated/invalid JSON
            When save_books is called with a new valid book
            Then LocalStoreCorruptError is raised and the file is left byte-for-byte unchanged.

        Boundaries:
            - Real file I/O; the corrupt file must match the store path in the exception message
            - No successful write must occur; the content must be identical before and after

        On failure, first check:
            - Whether assert_store_readable is called before writing
            - Whether LocalStoreCorruptError is still raised for invalid JSON (not silently ignored)
        """
        isolated_local_store.write_text('[{"hash": "h1", "title": "trunc', encoding="utf-8")

        with pytest.raises(LocalStoreCorruptError, match=str(isolated_local_store)):
            store.save_books([{"hash": "h2", "title": "New"}], isolated_local_store)

        # File left byte-for-byte untouched.
        assert isolated_local_store.read_text(encoding="utf-8") == '[{"hash": "h1", "title": "trunc'

    def test_backup_file_holds_previous_generation(self, isolated_local_store) -> None:
        """
        [Unit] atomic write: verifies each save rotates the previous store into a .bak file.

        Scenario:
            Given a first save that creates the store with one book
            When a second save appends a second book
            Then the .bak file holds only the first-generation content and the live file holds both.

        Boundaries:
            - No .bak exists after the first (file-creating) write
            - After the second write: .bak = first generation, live = both books

        On failure, first check:
            - Whether save_books copies the current store to .bak before the atomic replace
            - Whether the .bak is written after the replace (wrong order, holds new content)
        """
        backup = isolated_local_store.with_name(f"{isolated_local_store.name}.bak")

        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        assert not backup.exists()  # nothing to back up on a fresh store

        store.save_books([{"hash": "h2", "title": "Second"}], isolated_local_store)

        assert _read(backup) == [{"hash": "h1", "title": "First"}]
        assert {b["hash"] for b in _read(isolated_local_store)} == {"h1", "h2"}


class TestAssertStoreReadable:
    """Tests for store.assert_store_readable(): pre-flight guard that raises on a corrupt store file."""

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
    """Tests for store._load_books() recovery: returns [] on malformed JSON, non-list JSON, missing file, or read error."""

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
        [Unit] load books recovery: verifies an OSError while reading the store degrades gracefully to an empty list.

        Scenario:
            Given the store path replaced by a directory (so read_text raises IsADirectoryError)
            When _load_books is called
            Then an empty list is returned instead of propagating the OSError.

        Boundaries:
            - Error is an OSError (IsADirectoryError), not a JSON parse error
            - Real filesystem via isolated_local_store; no mocking needed

        On failure, first check:
            - Whether _load_books catches OSError in addition to json.JSONDecodeError
            - Whether the except clause is too narrow (e.g. only ValueError/JSONDecodeError)
        """
        isolated_local_store.mkdir()  # path.exists() is True, but read_text raises OSError
        assert store._load_books(isolated_local_store) == []
