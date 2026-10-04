"""Unit tests for bookscraper.book_utils pure/local-I/O helpers.

hash_book is the book's identity: a change in its normalized input silently
re-identifies a book, so it's re-scraped forever or a genuine duplicate slips
through. extract_year_from_date feeds hash_book, so a missed date format has the
same effect. check_mongodb_connection is intentionally NOT covered here (it
needs mocking; it belongs to the mock tier).
"""

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
        [Unit] hash book: verifies same inputs produce same hash.

        Scenario:
            Given test inputs and fixtures for same inputs produce same hash
            When test_same_inputs_produce_same_hash executes the target flow
            Then the expected outcome for same inputs produce same hash is confirmed.

        Boundaries:
            - Focus: same inputs produce same hash
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to same inputs produce same hash
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        h2 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hexdigest

    def test_author_order_does_not_matter(self) -> None:
        """
        [Unit] hash book: verifies author order does not matter.

        Scenario:
            Given test inputs and fixtures for author order does not matter
            When test_author_order_does_not_matter executes the target flow
            Then the expected outcome for author order does not matter is confirmed.

        Boundaries:
            - Focus: author order does not matter
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to author order does not matter
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("T", ["Alice", "Bob"], 2020)
        h2 = hash_book("T", ["Bob", "Alice"], 2020)
        assert h1 == h2

    def test_author_whitespace_and_case_normalized(self) -> None:
        """
        [Unit] hash book: verifies author whitespace and case normalized.

        Scenario:
            Given test inputs and fixtures for author whitespace and case normalized
            When test_author_whitespace_and_case_normalized executes the target flow
            Then the expected outcome for author whitespace and case normalized is confirmed.

        Boundaries:
            - Focus: author whitespace and case normalized
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to author whitespace and case normalized
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("T", ["Bob"], 2020)
        h2 = hash_book("T", [" bob "], 2020)
        assert h1 == h2

    def test_title_whitespace_and_case_normalized(self) -> None:
        """
        [Unit] hash book: verifies title whitespace and case normalized.

        Scenario:
            Given test inputs and fixtures for title whitespace and case normalized
            When test_title_whitespace_and_case_normalized executes the target flow
            Then the expected outcome for title whitespace and case normalized is confirmed.

        Boundaries:
            - Focus: title whitespace and case normalized
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to title whitespace and case normalized
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("  fluent python  ", ["A"], 2020)
        assert h1 == h2

    def test_different_title_produces_different_hash(self) -> None:
        """
        [Unit] hash book: verifies different title produces different hash.

        Scenario:
            Given test inputs and fixtures for different title produces different hash
            When test_different_title_produces_different_hash executes the target flow
            Then the expected outcome for different title produces different hash is confirmed.

        Boundaries:
            - Focus: different title produces different hash
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to different title produces different hash
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("Effective Python", ["A"], 2020)
        assert h1 != h2

    def test_different_year_produces_different_hash(self) -> None:
        """
        [Unit] hash book: verifies different year produces different hash.

        Scenario:
            Given test inputs and fixtures for different year produces different hash
            When test_different_year_produces_different_hash executes the target flow
            Then the expected outcome for different year produces different hash is confirmed.

        Boundaries:
            - Focus: different year produces different hash
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to different year produces different hash
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("T", ["A"], 2020)
        h2 = hash_book("T", ["A"], 2021)
        assert h1 != h2

    def test_empty_title_still_returns_hash(self) -> None:
        """
        [Unit] hash book: verifies empty title still returns hash.

        Scenario:
            Given test inputs and fixtures for empty title still returns hash
            When test_empty_title_still_returns_hash executes the target flow
            Then the expected outcome for empty title still returns hash is confirmed.

        Boundaries:
            - Focus: empty title still returns hash
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty title still returns hash
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
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
        [Unit] hash book: verifies none title does not raise but returns error sentinel.

        Scenario:
            Given test inputs and fixtures for none title does not raise but returns error sentinel
            When test_none_title_does_not_raise_but_returns_error_sentinel executes the target flow
            Then the expected outcome for none title does not raise but returns error sentinel is confirmed.

        Boundaries:
            - Focus: none title does not raise but returns error sentinel
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none title does not raise but returns error sentinel
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h = hash_book(None, ["A"], 2020)
        assert h == ""

    def test_authors_none_treated_as_empty_list(self) -> None:
        """
        [Unit] hash book: verifies authors none treated as empty list.

        Scenario:
            Given test inputs and fixtures for authors none treated as empty list
            When test_authors_none_treated_as_empty_list executes the target flow
            Then the expected outcome for authors none treated as empty list is confirmed.

        Boundaries:
            - Focus: authors none treated as empty list
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to authors none treated as empty list
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        h1 = hash_book("T", None, 2020)
        h2 = hash_book("T", [], 2020)
        assert h1 == h2

    def test_none_year_differs_from_zero_year(self) -> None:
        # year=None -> "" ; year=0 -> "0" : must be distinct identities.
        """
        [Unit] hash book: verifies none year differs from zero year.

        Scenario:
            Given test inputs and fixtures for none year differs from zero year
            When test_none_year_differs_from_zero_year executes the target flow
            Then the expected outcome for none year differs from zero year is confirmed.

        Boundaries:
            - Focus: none year differs from zero year
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none year differs from zero year
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        assert hash_book("T", ["A"], None) != hash_book("T", ["A"], 0)

    def test_none_authors_entries_are_dropped(self) -> None:
        """
        [Unit] hash book: verifies none authors entries are dropped.

        Scenario:
            Given test inputs and fixtures for none authors entries are dropped
            When test_none_authors_entries_are_dropped executes the target flow
            Then the expected outcome for none authors entries are dropped is confirmed.

        Boundaries:
            - Focus: none authors entries are dropped
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none authors entries are dropped
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by hash book
        """
        assert hash_book("T", ["A", None], 2020) == hash_book("T", ["A"], 2020)


class TestExtractYearFromDate:
    """Tests for book_utils.extract_year_from_date(): normalises site-specific date strings to a year int."""

    def test_iso_date(self) -> None:
        """
        [Unit] extract year from date: verifies iso date.

        Scenario:
            Given test inputs and fixtures for iso date
            When test_iso_date executes the target flow
            Then the expected outcome for iso date is confirmed.

        Boundaries:
            - Focus: iso date
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to iso date
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("2023-12-19") == 2023

    def test_long_month_name(self) -> None:
        """
        [Unit] extract year from date: verifies long month name.

        Scenario:
            Given test inputs and fixtures for long month name
            When test_long_month_name executes the target flow
            Then the expected outcome for long month name is confirmed.

        Boundaries:
            - Focus: long month name
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to long month name
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("December 17, 2019") == 2019

    def test_bare_year(self) -> None:
        """
        [Unit] extract year from date: verifies bare year.

        Scenario:
            Given test inputs and fixtures for bare year
            When test_bare_year executes the target flow
            Then the expected outcome for bare year is confirmed.

        Boundaries:
            - Focus: bare year
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to bare year
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("1995") == 1995

    def test_short_month_name(self) -> None:
        """
        [Unit] extract year from date: verifies short month name.

        Scenario:
            Given test inputs and fixtures for short month name
            When test_short_month_name executes the target flow
            Then the expected outcome for short month name is confirmed.

        Boundaries:
            - Focus: short month name
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to short month name
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("Apr 23, 2021") == 2021

    def test_month_and_year_only(self) -> None:
        """
        [Unit] extract year from date: verifies month and year only.

        Scenario:
            Given test inputs and fixtures for month and year only
            When test_month_and_year_only executes the target flow
            Then the expected outcome for month and year only is confirmed.

        Boundaries:
            - Focus: month and year only
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to month and year only
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("September 2016") == 2016

    def test_embedded_iso_date_regex_path(self) -> None:
        """
        [Unit] extract year from date: verifies embedded iso date regex path.

        Scenario:
            Given test inputs and fixtures for embedded iso date regex path
            When test_embedded_iso_date_regex_path executes the target flow
            Then the expected outcome for embedded iso date regex path is confirmed.

        Boundaries:
            - Focus: embedded iso date regex path
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to embedded iso date regex path
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("Last updated on 2016-11-29") == 2016

    def test_na_returns_none(self) -> None:
        """
        [Unit] extract year from date: verifies na returns none.

        Scenario:
            Given test inputs and fixtures for na returns none
            When test_na_returns_none executes the target flow
            Then the expected outcome for na returns none is confirmed.

        Boundaries:
            - Focus: na returns none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to na returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("N/A") is None

    def test_none_returns_none(self) -> None:
        """
        [Unit] extract year from date: verifies none returns none.

        Scenario:
            Given test inputs and fixtures for none returns none
            When test_none_returns_none executes the target flow
            Then the expected outcome for none returns none is confirmed.

        Boundaries:
            - Focus: none returns none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date(None) is None

    def test_non_string_returns_none(self) -> None:
        """
        [Unit] extract year from date: verifies non string returns none.

        Scenario:
            Given test inputs and fixtures for non string returns none
            When test_non_string_returns_none executes the target flow
            Then the expected outcome for non string returns none is confirmed.

        Boundaries:
            - Focus: non string returns none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to non string returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date(123) is None

    def test_unparsable_string_returns_none(self) -> None:
        """
        [Unit] extract year from date: verifies unparsable string returns none.

        Scenario:
            Given test inputs and fixtures for unparsable string returns none
            When test_unparsable_string_returns_none executes the target flow
            Then the expected outcome for unparsable string returns none is confirmed.

        Boundaries:
            - Focus: unparsable string returns none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to unparsable string returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("not a date at all") is None

    def test_empty_string_returns_none(self) -> None:
        """
        [Unit] extract year from date: verifies empty string returns none.

        Scenario:
            Given test inputs and fixtures for empty string returns none
            When test_empty_string_returns_none executes the target flow
            Then the expected outcome for empty string returns none is confirmed.

        Boundaries:
            - Focus: empty string returns none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to empty string returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        assert extract_year_from_date("") is None

    def test_regex_matched_but_invalid_date_falls_through_to_none(self) -> None:
        # "9999-99-99" matches the \d{4}-\d{2}-\d{2} embedded-date regex shape but
        # isn't a real date (month/day out of range) - exercises the regex block's
        # ValueError branch, then falls through to the direct-format loop, which
        # also fails against this string, and returns None.
        """
        [Unit] extract year from date: verifies regex matched but invalid date falls through to none.

        Scenario:
            Given test inputs and fixtures for regex matched but invalid date falls through to none
            When test_regex_matched_but_invalid_date_falls_through_to_none executes the target flow
            Then the expected outcome for regex matched but invalid date falls through to none is confirmed.

        Boundaries:
            - Focus: regex matched but invalid date falls through to none
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to regex matched but invalid date falls through to none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
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
        [Local] extract year from date: verifies non valueerror from strptime is caught by outer handler.

        Scenario:
            Given test inputs and fixtures for non valueerror from strptime is caught by outer handler
            When test_non_valueerror_from_strptime_is_caught_by_outer_handler executes the target flow
            Then the expected outcome for non valueerror from strptime is caught by outer handler is confirmed.

        Boundaries:
            - Focus: non valueerror from strptime is caught by outer handler
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to non valueerror from strptime is caught by outer handler
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by extract year from date
        """
        import bookscraper.book_utils as book_utils_module

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
        [Unit] check local write permission: verifies success cleans up test file.

        Scenario:
            Given test inputs and fixtures for success cleans up test file
            When test_success_cleans_up_test_file executes the target flow
            Then the expected outcome for success cleans up test file is confirmed.

        Boundaries:
            - Focus: success cleans up test file
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to success cleans up test file
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check local write permission
        """
        result = check_local_write_permission(str(tmp_path))
        assert result is True
        assert not (tmp_path / "test_write_permission.tmp").exists()

    def test_oserror_returns_false(self, monkeypatch, tmp_path) -> None:
        """
        [Local] check local write permission: verifies oserror returns false.

        Scenario:
            Given test inputs and fixtures for oserror returns false
            When test_oserror_returns_false executes the target flow
            Then the expected outcome for oserror returns false is confirmed.

        Boundaries:
            - Focus: oserror returns false
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to oserror returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check local write permission
        """

        def raise_oserror(*args, **kwargs):
            """Simulate a disk-full OSError on any open() call."""
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", raise_oserror)
        assert check_local_write_permission(str(tmp_path)) is False

    def test_generic_exception_returns_false(self, monkeypatch, tmp_path) -> None:
        """
        [Local] check local write permission: verifies generic exception returns false.

        Scenario:
            Given test inputs and fixtures for generic exception returns false
            When test_generic_exception_returns_false executes the target flow
            Then the expected outcome for generic exception returns false is confirmed.

        Boundaries:
            - Focus: generic exception returns false
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to generic exception returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check local write permission
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
