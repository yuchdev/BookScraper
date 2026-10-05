"""Unit tests for bookscraper.book_utils pure/local-I/O helpers.

hash_book is the book's identity: a change in its normalized input silently
re-identifies a book, so it's re-scraped forever or a genuine duplicate slips
through. extract_year_from_date feeds hash_book, so a missed date format has the
same effect. The MongoDB connection pre-flight now lives in backends/mongo/database.py
(see tests/mock/backends/mongo/test_connection_check.py).
"""

import bookscraper.book_utils as book_utils_module
from bookscraper.book_utils import (
    atomic_write_bytes,
    check_local_write_permission,
    extract_year_from_date,
    hash_book,
)


class TestHashBook:
    """Tests for book_utils.hash_book(): SHA-256 identity function for book deduplication."""

    def test_same_inputs_produce_same_hash(self) -> None:
        """
        [Unit] hash_book: identical title, authors, and year produce the same 64-character SHA-256 hexdigest.

        Scenario:
            Given hash_book called twice with "Fluent Python", ["Luciano Ramalho"], 2022
            When both results are compared
            Then h1 == h2 and len(h1) == 64.

        Boundaries:
            - Pure Python; no I/O, no mocking
            - Verifies determinism and output length but not the specific hash value

        On failure, first check:
            - Whether hash_book's normalization or hashing logic was changed in book_utils.py
        """
        h1 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        h2 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hexdigest

    def test_author_order_does_not_matter(self) -> None:
        """
        [Unit] hash_book: authors are sorted before hashing so order does not affect the result.

        Scenario:
            Given hash_book called with ["Alice", "Bob"] and then with ["Bob", "Alice"] for the same title and year
            When both results are compared
            Then h1 == h2.

        Boundaries:
            - Pure Python; tests a single normalization step (sort)

        On failure, first check:
            - Whether the author-sorting step was removed or changed in hash_book in book_utils.py
        """
        h1 = hash_book("T", ["Alice", "Bob"], 2020)
        h2 = hash_book("T", ["Bob", "Alice"], 2020)
        assert h1 == h2

    def test_author_whitespace_and_case_normalized(self) -> None:
        """
        [Unit] hash_book: author names are stripped and lowercased before hashing.

        Scenario:
            Given hash_book called with "Bob" and then with " bob " for the same title and year
            When both results are compared
            Then h1 == h2.

        Boundaries:
            - Pure Python; tests strip() + lower() on a single author name

        On failure, first check:
            - Whether author normalization (strip/lower) was changed or removed in book_utils.py
        """
        h1 = hash_book("T", ["Bob"], 2020)
        h2 = hash_book("T", [" bob "], 2020)
        assert h1 == h2

    def test_title_whitespace_and_case_normalized(self) -> None:
        """
        [Unit] hash_book: titles are stripped and lowercased before hashing.

        Scenario:
            Given hash_book called with "Fluent Python" and then with "  fluent python  " for the same author and year
            When both results are compared
            Then h1 == h2.

        Boundaries:
            - Pure Python; tests strip() + lower() on the title

        On failure, first check:
            - Whether title normalization (strip/lower) was changed or removed in book_utils.py
        """
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("  fluent python  ", ["A"], 2020)
        assert h1 == h2

    def test_different_title_produces_different_hash(self) -> None:
        """
        [Unit] hash_book: different titles produce different hashes.

        Scenario:
            Given hash_book called with "Fluent Python" and then with "Effective Python" for the same author and year
            When both results are compared
            Then h1 != h2.

        Boundaries:
            - Pure Python; tests that title is included in the hash input (not just authors and year)

        On failure, first check:
            - Whether the title is still included as a hash input in book_utils.py
        """
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("Effective Python", ["A"], 2020)
        assert h1 != h2

    def test_different_year_produces_different_hash(self) -> None:
        """
        [Unit] hash_book: different publication years produce different hashes.

        Scenario:
            Given hash_book called with year=2020 and then with year=2021 for the same title and author
            When both results are compared
            Then h1 != h2.

        Boundaries:
            - Pure Python; tests that year is included in the hash input

        On failure, first check:
            - Whether the year is still included as a hash input in book_utils.py
        """
        h1 = hash_book("T", ["A"], 2020)
        h2 = hash_book("T", ["A"], 2021)
        assert h1 != h2

    def test_empty_title_still_returns_hash(self) -> None:
        """
        [Unit] hash_book: an empty string title produces a valid 64-character hash, not an error sentinel.

        Scenario:
            Given hash_book called with title=""
            When the result is examined
            Then it is a string of length 64 (a real SHA-256 digest, not the "" error sentinel).

        Boundaries:
            - Distinguishes empty string from None: an empty title hashes fine; None does not
            - Pure Python; no I/O

        On failure, first check:
            - Whether the title normalization branch now treats "" the same as None in book_utils.py
        """
        h = hash_book("", ["A"], 2020)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_none_title_does_not_raise_but_returns_error_sentinel(self) -> None:
        # Documents a real quirk (candidate product bug): unlike an EMPTY-string
        # title (which hashes fine, see test_empty_title_still_returns_hash),
        # a None title makes `title.strip()` raise AttributeError, which the
        # function's bare `except` swallows and returns "" - the SAME sentinel it
        # returns on any hashing error. So every None-titled book collapses to
        # hash="" and collides with every other errored/None-titled book. The key
        # safety property asked for (must not raise) holds; the "returns a real
        # hash" part does not. Asserting actual behavior; flagged for python-expert.
        """
        [Unit] hash_book: a None title swallows AttributeError and returns "" rather than raising.

        Scenario:
            Given hash_book called with title=None
            When the result is examined
            Then it equals "" (the bare-except error sentinel), with no exception raised.

        Boundaries:
            - Known quirk: every None-titled book collapses to hash="" and collides with every other errored book
            - Asserting actual behavior; the safety property (must not raise) holds but "returns a real hash" does not

        On failure, first check:
            - Whether the bare except in hash_book was narrowed (e.g. to AttributeError) or removed in book_utils.py
            - If the return value changed from "" to something else, the dedup behavior changed too
        """
        h = hash_book(None, ["A"], 2020)
        assert h == ""

    def test_authors_none_treated_as_empty_list(self) -> None:
        """
        [Unit] hash_book: None for authors produces the same hash as an empty list.

        Scenario:
            Given hash_book called with authors=None and then with authors=[]
            When both results are compared
            Then h1 == h2.

        Boundaries:
            - Pure Python; verifies the None-coercion path before sorting

        On failure, first check:
            - Whether the None-guard for the authors argument was removed in book_utils.py
        """
        h1 = hash_book("T", None, 2020)
        h2 = hash_book("T", [], 2020)
        assert h1 == h2

    def test_none_year_differs_from_zero_year(self) -> None:
        # year=None -> "" ; year=0 -> "0" : must be distinct identities.
        """
        [Unit] hash_book: None year and year=0 produce different hashes.

        Scenario:
            Given hash_book called with year=None and then with year=0 for the same title and author
            When both results are compared
            Then the hashes differ (None serialises to "" and 0 serialises to "0").

        Boundaries:
            - Pure Python; guards against silent identity collision between "no year" and "year zero"

        On failure, first check:
            - Whether the year-to-string conversion in hash_book was changed in book_utils.py
        """
        assert hash_book("T", ["A"], None) != hash_book("T", ["A"], 0)

    def test_none_authors_entries_are_dropped(self) -> None:
        """
        [Unit] hash_book: None entries inside the authors list are silently dropped before hashing.

        Scenario:
            Given hash_book called with ["A", None] and then with ["A"] for the same title and year
            When both results are compared
            Then the hashes are equal.

        Boundaries:
            - Pure Python; verifies per-entry None filtering within the authors list

        On failure, first check:
            - Whether the per-entry None filter was removed from the author normalization loop in book_utils.py
        """
        assert hash_book("T", ["A", None], 2020) == hash_book("T", ["A"], 2020)


class TestExtractYearFromDate:
    """Tests for book_utils.extract_year_from_date(): normalises site-specific date strings to a year int."""

    def test_iso_date(self) -> None:
        """
        [Unit] extract_year_from_date: ISO "YYYY-MM-DD" format extracts the year correctly.

        Scenario:
            Given extract_year_from_date("2023-12-19")
            When the function parses the input
            Then it returns 2023.

        Boundaries:
            - Pure Python; no I/O
            - Covers the direct-format strptime path for "%Y-%m-%d"

        On failure, first check:
            - Whether "%Y-%m-%d" is still in the format list or the regex path now handles it first in book_utils.py
        """
        assert extract_year_from_date("2023-12-19") == 2023

    def test_long_month_name(self) -> None:
        """
        [Unit] extract_year_from_date: "Month DD, YYYY" format extracts the year correctly.

        Scenario:
            Given extract_year_from_date("December 17, 2019")
            When the function parses the input
            Then it returns 2019.

        Boundaries:
            - Pure Python; covers the "%B %d, %Y" strptime path (full month name)

        On failure, first check:
            - Whether "%B %d, %Y" is still in the format list in book_utils.py
        """
        assert extract_year_from_date("December 17, 2019") == 2019

    def test_bare_year(self) -> None:
        """
        [Unit] extract_year_from_date: a four-digit year string returns that year as an int.

        Scenario:
            Given extract_year_from_date("1995")
            When the function parses the input
            Then it returns 1995.

        Boundaries:
            - Pure Python; covers the bare-year "%Y" strptime path

        On failure, first check:
            - Whether "%Y" is still in the format list in book_utils.py
        """
        assert extract_year_from_date("1995") == 1995

    def test_short_month_name(self) -> None:
        """
        [Unit] extract_year_from_date: abbreviated-month "Mon DD, YYYY" format extracts the year correctly.

        Scenario:
            Given extract_year_from_date("Apr 23, 2021")
            When the function parses the input
            Then it returns 2021.

        Boundaries:
            - Pure Python; covers the "%b %d, %Y" strptime path (abbreviated month name)

        On failure, first check:
            - Whether "%b %d, %Y" is still in the format list in book_utils.py
        """
        assert extract_year_from_date("Apr 23, 2021") == 2021

    def test_month_and_year_only(self) -> None:
        """
        [Unit] extract_year_from_date: "Month YYYY" (no day) format extracts the year correctly.

        Scenario:
            Given extract_year_from_date("September 2016")
            When the function parses the input
            Then it returns 2016.

        Boundaries:
            - Pure Python; covers the "%B %Y" strptime path

        On failure, first check:
            - Whether "%B %Y" is still in the format list in book_utils.py
        """
        assert extract_year_from_date("September 2016") == 2016

    def test_embedded_iso_date_regex_path(self) -> None:
        """
        [Unit] extract_year_from_date: an ISO date embedded in surrounding text is extracted via the regex path.

        Scenario:
            Given extract_year_from_date("Last updated on 2016-11-29")
            When the function scans the string with the embedded-date regex
            Then it returns 2016.

        Boundaries:
            - Pure Python; exercises the regex branch, not the direct-format strptime loop

        On failure, first check:
            - Whether the embedded ISO-date regex or its handler was changed in book_utils.py
        """
        assert extract_year_from_date("Last updated on 2016-11-29") == 2016

    def test_na_returns_none(self) -> None:
        """
        [Unit] extract_year_from_date: "N/A" returns None.

        Scenario:
            Given extract_year_from_date("N/A")
            When the function attempts to parse the string
            Then it returns None (no year can be extracted).

        Boundaries:
            - Pure Python; verifies graceful handling of a common sentinel value

        On failure, first check:
            - Whether "N/A" now matches a new format or is handled specially in book_utils.py
        """
        assert extract_year_from_date("N/A") is None

    def test_none_returns_none(self) -> None:
        """
        [Unit] extract_year_from_date: None input returns None without raising.

        Scenario:
            Given extract_year_from_date(None)
            When the function checks the input type
            Then it returns None.

        Boundaries:
            - Pure Python; verifies the isinstance/type-guard at the start of the function

        On failure, first check:
            - Whether the None guard was removed or moved in book_utils.py
        """
        assert extract_year_from_date(None) is None

    def test_non_string_returns_none(self) -> None:
        """
        [Unit] extract_year_from_date: non-string input (e.g. int) returns None without raising.

        Scenario:
            Given extract_year_from_date(123)
            When the function checks the input type
            Then it returns None.

        Boundaries:
            - Pure Python; verifies the type-guard catches non-string inputs

        On failure, first check:
            - Whether the isinstance/type guard was removed or changed in book_utils.py
        """
        assert extract_year_from_date(123) is None

    def test_unparsable_string_returns_none(self) -> None:
        """
        [Unit] extract_year_from_date: an unrecognized date string returns None.

        Scenario:
            Given extract_year_from_date("not a date at all")
            When all parsing attempts fail
            Then the function returns None.

        Boundaries:
            - Pure Python; verifies the fall-through path when neither regex nor any strptime format matches

        On failure, first check:
            - Whether a new format was added that accidentally matches this string in book_utils.py
        """
        assert extract_year_from_date("not a date at all") is None

    def test_empty_string_returns_none(self) -> None:
        """
        [Unit] extract_year_from_date: an empty string returns None.

        Scenario:
            Given extract_year_from_date("")
            When all parsing attempts fail
            Then the function returns None.

        Boundaries:
            - Pure Python; verifies the empty-string edge case in the fall-through path

        On failure, first check:
            - Whether an early-exit or special-case for empty strings was added or removed in book_utils.py
        """
        assert extract_year_from_date("") is None

    def test_regex_matched_but_invalid_date_falls_through_to_none(self) -> None:
        # "9999-99-99" matches the \d{4}-\d{2}-\d{2} embedded-date regex shape but
        # isn't a real date (month/day out of range) - exercises the regex block's
        # ValueError branch, then falls through to the direct-format loop, which
        # also fails against this string, and returns None.
        """
        [Unit] extract_year_from_date: a string matching the ISO-date regex shape but with invalid month/day falls through to None.

        Scenario:
            Given extract_year_from_date("Report 9999-99-99 filed") whose substring matches \\d{4}-\\d{2}-\\d{2}
            When the embedded-date regex block raises ValueError (invalid month/day out of range), then the format loop also fails
            Then the function returns None.

        Boundaries:
            - Pure Python; exercises the ValueError branch inside the regex block and the final fall-through

        On failure, first check:
            - Whether the ValueError handler in the regex block now returns a value instead of falling through in book_utils.py
        """
        assert extract_year_from_date("Report 9999-99-99 filed") is None

    def test_non_valueerror_from_strptime_is_caught_by_outer_handler(self, monkeypatch) -> None:
        # Defensive outer `except Exception` around the direct-format loop; real
        # strptime never raises anything but ValueError for a str input, so exercise
        # it directly. "1995" has no embedded YYYY-MM-DD substring, so it skips the
        # regex block entirely and goes straight to the format loop. datetime.datetime
        # is an immutable C type - can't patch .strptime on it in place - so swap the
        # module-level `datetime` name itself for a stand-in exposing only strptime,
        # which is all this code path calls it for.
        """
        [Local] extract_year_from_date: a non-ValueError exception from strptime is caught by the outer handler and returns None.

        Scenario:
            Given the module-level datetime name replaced with a stub whose strptime raises RuntimeError
            And extract_year_from_date("1995") called (no embedded date, goes straight to the format loop)
            When the stub raises RuntimeError on the first strptime call
            Then the outer except-Exception handler catches it and the function returns None.

        Boundaries:
            - Monkeypatches bookscraper.book_utils.datetime (an immutable C type that cannot be patched in-place)
            - Covers only the outer handler, not the ValueError path

        On failure, first check:
            - Whether the outer except-Exception handler was narrowed to except-ValueError in book_utils.py
            - Whether "1995" now hits the regex path before the format loop, bypassing the patched datetime
        """

        class _RaisingDatetime:
            """Stub that replaces datetime to make strptime raise a non-ValueError."""

            @staticmethod
            def strptime(*args, **kwargs):
                """Raise RuntimeError unconditionally."""
                raise RuntimeError("boom")

        monkeypatch.setattr(book_utils_module, "datetime", _RaisingDatetime)
        assert extract_year_from_date("1995") is None


class TestCheckLocalWritePermission:
    """Tests for book_utils.check_local_write_permission(): pre-flight write check for the json backend."""

    def test_success_cleans_up_test_file(self, tmp_path) -> None:
        """
        [Unit] check_local_write_permission: returns True for a writable directory and removes the temp probe file.

        Scenario:
            Given a writable tmp_path directory
            When check_local_write_permission is called with that path
            Then it returns True and the "test_write_permission.tmp" probe file no longer exists.

        Boundaries:
            - Real file I/O via tmp_path; no mocking
            - Verifies both the return value and the cleanup behavior

        On failure, first check:
            - Whether the probe filename or the cleanup logic was changed in book_utils.py
        """
        result = check_local_write_permission(str(tmp_path))
        assert result is True
        assert not (tmp_path / "test_write_permission.tmp").exists()

    def test_oserror_returns_false(self, monkeypatch, tmp_path) -> None:
        """
        [Local] check_local_write_permission: an OSError from open() causes the function to return False.

        Scenario:
            Given builtins.open patched to raise OSError("disk full")
            When check_local_write_permission is called
            Then it returns False (the OSError is caught).

        Boundaries:
            - Monkeypatches builtins.open; tests only the OSError branch

        On failure, first check:
            - Whether the OSError handler was removed or narrowed in check_local_write_permission in book_utils.py
        """

        def raise_oserror(*args, **kwargs):
            """Simulate a disk-full OSError on any open() call."""
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", raise_oserror)
        assert check_local_write_permission(str(tmp_path)) is False

    def test_generic_exception_returns_false(self, monkeypatch, tmp_path) -> None:
        """
        [Local] check_local_write_permission: a non-OSError exception from open() also causes the function to return False.

        Scenario:
            Given builtins.open patched to raise RuntimeError("unexpected")
            When check_local_write_permission is called
            Then it returns False (the generic except catches it).

        Boundaries:
            - Monkeypatches builtins.open; tests the broad except-Exception fallback path

        On failure, first check:
            - Whether the broad exception handler was narrowed to OSError-only in check_local_write_permission in book_utils.py
        """

        def raise_runtime_error(*args, **kwargs):
            """Simulate an unexpected RuntimeError on any open() call."""
            raise RuntimeError("unexpected")

        monkeypatch.setattr("builtins.open", raise_runtime_error)
        assert check_local_write_permission(str(tmp_path)) is False


class TestAtomicWriteBytesPermissions:
    """Tests for atomic_write_bytes() permission handling: default 0644 and preserve-existing-mode."""

    def test_new_file_defaults_to_0644_and_existing_mode_is_preserved(self, tmp_path) -> None:
        """
        [Unit] book utils: verifies atomic_write_bytes defaults to 0644 for new files and preserves the existing mode on rewrites.

        Scenario:
            Given a new file written with no explicit mode, then re-written with mode 0640, then re-written with explicit 0600
            When atomic_write_bytes is called for each write
            Then the new file gets 0644, the 0640 file keeps 0640 after rewrite, and explicit 0600 is honoured.

        Boundaries:
            - Real file I/O via tmp_path; no mocking
            - Three scenarios in one test: new-file default, existing-mode preservation, explicit-mode override

        On failure, first check:
            - Whether atomic_write_bytes still reads the existing file's mode before the temp-file write
            - Whether mkstemp's 0600 default is being propagated instead of overridden
        """
        target = tmp_path / "store.json"

        atomic_write_bytes(target, b"[]")
        assert target.stat().st_mode & 0o777 == 0o644

        target.chmod(0o640)
        atomic_write_bytes(target, b"[1]")
        assert target.stat().st_mode & 0o777 == 0o640
        assert target.read_bytes() == b"[1]"

        atomic_write_bytes(target, b"[2]", mode=0o600)
        assert target.stat().st_mode & 0o777 == 0o600
