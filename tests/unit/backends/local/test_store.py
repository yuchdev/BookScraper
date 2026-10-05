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
        [Unit] save books: verifies save_books creates the store file and writes the book on first use.

        Scenario:
            Given no store file exists yet
            When save_books([{"hash": "h1", "title": "Book One"}], path) is called
            Then the file exists and contains exactly one entry with hash "h1" and title "Book One"

        Boundaries:
            - No pre-existing file; file creation is part of the write path
            - Single-item batch; no dedup check runs for the first write

        On failure, first check:
            - Whether save_books creates the parent directory if it does not exist
            - Recent changes to the _write_books / file-creation path in store.py
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
        [Unit] save books: verifies a second book with the same hash is rejected without overwriting the first.

        Scenario:
            Given a store already containing a book with hash "h1" and title "First"
            When save_books is called again with the same hash and title "Second"
            Then the store still contains exactly one entry with title "First"

        Boundaries:
            - Hash uniqueness is the sole dedup key; title differences are irrelevant
            - The original record must not be mutated or replaced

        On failure, first check:
            - Whether the in-memory hash index is populated from the existing store before the write
            - Recent changes to the duplicate-detection path in save_books
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h1", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert len(data) == 1
        assert data[0]["title"] == "First"  # original kept, not overwritten

    def test_new_book_appends_alongside_existing(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies a book with a new hash is appended without removing existing entries.

        Scenario:
            Given a store containing a book with hash "h1"
            When save_books is called with a book with hash "h2"
            Then the store contains exactly two books with hashes {"h1", "h2"}

        Boundaries:
            - Different hash from the existing entry; no dedup conflict
            - The existing entry must not be removed or overwritten

        On failure, first check:
            - Whether the existing store is read and merged before writing
            - Recent changes to the append logic in save_books
        """
        store.save_books([{"hash": "h1", "title": "First"}], isolated_local_store)
        store.save_books([{"hash": "h2", "title": "Second"}], isolated_local_store)
        data = _read(isolated_local_store)
        assert {b["hash"] for b in data} == {"h1", "h2"}

    def test_book_missing_hash_is_skipped_not_written(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies a book dict without a "hash" key is silently skipped.

        Scenario:
            Given a book dict with only a "title" key and no "hash"
            When save_books([{"title": "No Hash"}], path) is called
            Then no store file is created

        Boundaries:
            - The store file must not exist after the call (nothing was inserted)
            - No exception is raised; the entry is silently discarded

        On failure, first check:
            - Whether the hash-presence check runs before the dedup lookup
            - Whether save_books creates an empty file even when nothing was inserted
        """
        store.save_books([{"title": "No Hash"}], isolated_local_store)
        # _write_books only runs if something was inserted; nothing was.
        assert not isolated_local_store.exists()

    def test_mixed_batch_writes_only_valid_book(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies that in a mixed batch only the hash-bearing book is written.

        Scenario:
            Given a batch containing one book without a "hash" and one valid book with hash "h1"
            When save_books is called
            Then the store contains exactly one entry with hash "h1"

        Boundaries:
            - Hash-less entry is skipped; valid entry is written; order in the batch does not matter
            - Confirms per-item filtering, not whole-batch rejection

        On failure, first check:
            - Whether a hash-less entry causes the whole batch to be rejected
            - Recent changes to the per-item filtering loop in save_books
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
        [Unit] save books: verifies an empty book list is a no-op and does not create the store file.

        Scenario:
            Given no store file and an empty list
            When save_books([], path) is called
            Then the store file still does not exist

        Boundaries:
            - Empty batch with no pre-existing file; no write path is reached
            - No exception is raised; calling with [] is always safe

        On failure, first check:
            - Whether save_books creates an empty JSON array file on an empty input
            - Recent changes to the early-exit guard in save_books
        """
        store.save_books([], isolated_local_store)
        assert not isolated_local_store.exists()

    def test_all_duplicates_does_not_rewrite_missing_file(self, isolated_local_store) -> None:
        """
        [Unit] save books: verifies a batch of all-duplicates does not trigger a file rewrite.

        Scenario:
            Given a store with one book and the same book saved again (all duplicates, inserted=0)
            When save_books is called a second time with the same hash
            Then the store content is byte-for-byte unchanged

        Boundaries:
            - The "skip write if nothing was inserted" guard is exercised
            - Exact dict equality confirms no rewrite happened

        On failure, first check:
            - Whether save_books skips _write_books when inserted==0
            - Whether the file is unnecessarily rewritten even when nothing changed
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
        [Unit] check book exists: verifies True is returned when the hash is present in the store.

        Scenario:
            Given a store containing a book with hash "h1"
            When check_book_exists("h1", path) is called
            Then True is returned

        Boundaries:
            - Exact hash match; store populated via save_books in the same test
            - Lookup reads from the file, not only from an in-memory index

        On failure, first check:
            - Whether the hash index is built from the stored file on each call
            - Recent changes to check_book_exists in store.py
        """
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("h1", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies False is returned when the hash is absent from the store.

        Scenario:
            Given a store containing a book with hash "h1"
            When check_book_exists("nope", path) is called
            Then False is returned

        Boundaries:
            - Hash "nope" is not in the store; a different hash is present
            - No exception; missing hash is always a clean False

        On failure, first check:
            - Whether a KeyError or IndexError escapes instead of returning False
            - Recent changes to the not-found path in check_book_exists
        """
        store.save_books([{"hash": "h1", "title": "T"}], isolated_local_store)
        assert store.check_book_exists("nope", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies False is returned when the store file does not exist.

        Scenario:
            Given no store file
            When check_book_exists("h1", path) is called
            Then False is returned

        Boundaries:
            - Non-existent store; function must not raise FileNotFoundError
            - Contrast with corrupt store, which raises LocalStoreCorruptError

        On failure, first check:
            - Whether _load_books handles a missing file by returning []
            - Recent changes to the empty-store path in check_book_exists
        """
        assert store.check_book_exists("h1", isolated_local_store) is False

    def test_empty_hash_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check book exists: verifies False is returned when an empty string hash is queried.

        Scenario:
            Given no store file
            When check_book_exists("", path) is called
            Then False is returned

        Boundaries:
            - Empty string is a valid query; the store may not contain it
            - Guards against a truthy/falsy check that would treat "" as "no hash"

        On failure, first check:
            - Whether an empty hash string triggers an early-return branch with wrong result
            - Recent changes to the hash lookup in check_book_exists
        """
        assert store.check_book_exists("", isolated_local_store) is False


class TestCheckAmazonAsinExists:
    """Tests for store.check_amazon_asin_exists(): ASIN-based dedup lookup in the json store."""

    def test_found(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies True is returned when the ASIN is present.

        Scenario:
            Given a store containing a book with asin "B001"
            When check_amazon_asin_exists("B001", path) is called
            Then True is returned

        Boundaries:
            - Exact ASIN match; store populated via save_books in the same test
            - "asin" field lookup, not "hash"

        On failure, first check:
            - Whether the ASIN index is built from the stored file correctly
            - Recent changes to check_amazon_asin_exists in store.py
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is True

    def test_not_found(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies False is returned when the ASIN is absent.

        Scenario:
            Given a store containing a book with asin "B001"
            When check_amazon_asin_exists("B999", path) is called
            Then False is returned

        Boundaries:
            - "B999" is not in the store; "B001" is present to confirm the lookup is not trivially empty
            - No exception; absent ASIN is always a clean False

        On failure, first check:
            - Whether the ASIN lookup accidentally matches a prefix or substring
            - Recent changes to the not-found path in check_amazon_asin_exists
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        assert store.check_amazon_asin_exists("B999", isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies False is returned when the store file does not exist.

        Scenario:
            Given no store file
            When check_amazon_asin_exists("B001", path) is called
            Then False is returned

        Boundaries:
            - Non-existent store; function must not raise FileNotFoundError
            - Mirrors the same behavior in check_book_exists

        On failure, first check:
            - Whether _load_books handles a missing file by returning []
            - Recent changes to the empty-store path in check_amazon_asin_exists
        """
        assert store.check_amazon_asin_exists("B001", isolated_local_store) is False

    def test_empty_asin_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] check amazon asin exists: verifies False is returned when an empty string ASIN is queried.

        Scenario:
            Given no store file
            When check_amazon_asin_exists("", path) is called
            Then False is returned

        Boundaries:
            - Empty string; guards against a falsy check treating "" as "no ASIN to look up"
            - Result must be False, not raise

        On failure, first check:
            - Whether an empty ASIN triggers an early-return branch with wrong result
            - Recent changes to the ASIN lookup in check_amazon_asin_exists
        """
        assert store.check_amazon_asin_exists("", isolated_local_store) is False


class TestLeanpubBookExists:
    """Tests for store.leanpub_book_exists(): book_id/slug dedup lookup for the Leanpub scrape path."""

    def test_match_by_book_id_alone(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies True is returned when only book_id matches (slug is None).

        Scenario:
            Given a store with a book having book_id "42" and slug "some-book"
            When leanpub_book_exists("42", None, path) is called
            Then True is returned

        Boundaries:
            - slug argument is None; match is solely on book_id
            - Confirms book_id is checked independently of slug

        On failure, first check:
            - Whether leanpub_book_exists requires both identifiers to match
            - Recent changes to the book_id lookup in leanpub_book_exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", None, isolated_local_store) is True

    def test_match_by_slug_alone(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies True is returned when only slug matches (book_id is None).

        Scenario:
            Given a store with a book having book_id "42" and slug "some-book"
            When leanpub_book_exists(None, "some-book", path) is called
            Then True is returned

        Boundaries:
            - book_id argument is None; match is solely on slug
            - Confirms slug is checked independently of book_id

        On failure, first check:
            - Whether leanpub_book_exists requires both identifiers to match
            - Recent changes to the slug lookup in leanpub_book_exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists(None, "some-book", isolated_local_store) is True

    def test_match_by_both(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies True is returned when both book_id and slug are provided and match.

        Scenario:
            Given a store with book_id "42" and slug "some-book"
            When leanpub_book_exists("42", "some-book", path) is called
            Then True is returned

        Boundaries:
            - Both identifiers provided; the function should return True on a match by either one
            - Confirms the function does not require both to be absent

        On failure, first check:
            - Whether providing both identifiers accidentally triggers an AND check that fails
            - Recent changes to the lookup logic in leanpub_book_exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("42", "some-book", isolated_local_store) is True

    def test_no_match(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies False when neither book_id nor slug matches.

        Scenario:
            Given a store with book_id "42" and slug "some-book"
            When leanpub_book_exists("99", "other-book", path) is called
            Then False is returned

        Boundaries:
            - Both identifiers differ from the stored values
            - A book exists in the store to confirm the lookup is not trivially empty

        On failure, first check:
            - Whether partial-match logic accidentally returns True for one mismatched field
            - Recent changes to the no-match path in leanpub_book_exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        assert store.leanpub_book_exists("99", "other-book", isolated_local_store) is False

    def test_neither_identifier_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies False and no exception when both identifiers are None.

        Scenario:
            Given a store with a valid book and both book_id and slug arguments set to None
            When leanpub_book_exists(None, None, path) is called
            Then False is returned without raising

        Boundaries:
            - Both falsy arguments exercise the "no identifier" warning branch
            - Must not raise; None/None is a valid caller state (search result with no IDs)

        On failure, first check:
            - Whether the "no identifier" branch returns False or raises TypeError
            - Recent changes to the early-exit guard in leanpub_book_exists
        """
        store.save_books([{"hash": "h1", "book_id": "42", "slug": "some-book"}], isolated_local_store)
        # Both falsy -> the "no identifier" warning branch, must not raise.
        assert store.leanpub_book_exists(None, None, isolated_local_store) is False

    def test_empty_store_returns_false(self, isolated_local_store) -> None:
        """
        [Unit] leanpub book exists: verifies False is returned when the store file does not exist.

        Scenario:
            Given no store file
            When leanpub_book_exists("42", "some-book", path) is called
            Then False is returned

        Boundaries:
            - Non-existent store; function must not raise FileNotFoundError
            - Mirrors the same behavior in check_book_exists and check_amazon_asin_exists

        On failure, first check:
            - Whether _load_books handles a missing file by returning []
            - Recent changes to the empty-store path in leanpub_book_exists
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
        [Unit] assert store readable: verifies a well-formed store passes the pre-flight check without error.

        Scenario:
            Given a store containing one valid book document
            When assert_store_readable(path) is called
            Then no exception is raised

        Boundaries:
            - "Readable" means parseable as a JSON list; content validity not checked here
            - Successful return (no assert, no exception) is the expected outcome

        On failure, first check:
            - Whether assert_store_readable is raising on valid JSON unexpectedly
            - Recent changes to the JSON parse or type check in assert_store_readable
        """
        store.save_books([{"hash": "h1"}], isolated_local_store)
        store.assert_store_readable(isolated_local_store)  # must not raise

    def test_missing_store_does_not_raise(self, isolated_local_store) -> None:
        """
        [Unit] assert store readable: verifies a missing store file passes without error.

        Scenario:
            Given no store file at the path
            When assert_store_readable(path) is called
            Then no exception is raised

        Boundaries:
            - Absent file is a valid state (first run); only a corrupt file is an error
            - FileNotFoundError must not propagate

        On failure, first check:
            - Whether assert_store_readable raises on a missing file instead of returning silently
            - Recent changes to the existence check in assert_store_readable
        """
        store.assert_store_readable(isolated_local_store)  # must not raise

    def test_corrupt_store_raises(self, isolated_local_store) -> None:
        """
        [Unit] assert store readable: verifies a corrupt store file raises LocalStoreCorruptError.

        Scenario:
            Given a store file containing invalid JSON ("{not json")
            When assert_store_readable(path) is called
            Then LocalStoreCorruptError is raised

        Boundaries:
            - Corrupt means unparseable JSON; a valid-JSON non-list also raises (tested in save_books)
            - The exception type must be LocalStoreCorruptError, not JSONDecodeError

        On failure, first check:
            - Whether the JSONDecodeError is wrapped in LocalStoreCorruptError
            - Recent changes to the error-type wrapping in assert_store_readable
        """
        isolated_local_store.write_text("{not json", encoding="utf-8")
        with pytest.raises(LocalStoreCorruptError):
            store.assert_store_readable(isolated_local_store)


class TestLoadBooksRecovery:
    """Tests for store._load_books() recovery: returns [] on malformed JSON, non-list JSON, missing file, or read error."""

    def test_malformed_json_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies _load_books returns [] when the file contains invalid JSON.

        Scenario:
            Given a store file with content "{not valid json"
            When _load_books(path) is called
            Then an empty list is returned

        Boundaries:
            - _load_books is an internal read helper; it degrades gracefully
            - Contrast with assert_store_readable which raises on the same input

        On failure, first check:
            - Whether _load_books propagates JSONDecodeError instead of returning []
            - Recent changes to the except clause in _load_books
        """
        isolated_local_store.write_text("{not valid json", encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_valid_json_non_list_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies _load_books returns [] when the file contains valid JSON but not a list.

        Scenario:
            Given a store file containing '{"hash": "h1"}' (a dict, not a list)
            When _load_books(path) is called
            Then an empty list is returned

        Boundaries:
            - Valid JSON parse succeeds; type check (must be a list) fails gracefully
            - The store contract requires a JSON array at the top level

        On failure, first check:
            - Whether _load_books checks isinstance(data, list) after parsing
            - Recent changes to the type-guard in _load_books
        """
        isolated_local_store.write_text('{"hash": "h1"}', encoding="utf-8")
        assert store._load_books(isolated_local_store) == []

    def test_nonexistent_file_returns_empty_list(self, isolated_local_store) -> None:
        """
        [Unit] load books recovery: verifies _load_books returns [] when the store file does not exist.

        Scenario:
            Given no store file at the path
            When _load_books(path) is called
            Then an empty list is returned

        Boundaries:
            - Non-existent file is the "first run" state; no error should propagate
            - FileNotFoundError must be caught and converted to []

        On failure, first check:
            - Whether FileNotFoundError is in the except clause of _load_books
            - Recent changes to the missing-file path in _load_books
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
