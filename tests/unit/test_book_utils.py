"""Unit tests for bookscraper.book_utils pure/local-I/O helpers.

hash_book is the book's identity: a change in its normalized input silently
re-identifies a book, so it's re-scraped forever or a genuine duplicate slips
through. extract_year_from_date feeds hash_book, so a missed date format has the
same effect. check_mongodb_connection is intentionally NOT covered here (it
needs mocking; it belongs to the mock tier).
"""

import json
from datetime import datetime

from bookscraper.book_utils import (
    _cover_extension,
    check_local_write_permission,
    dumps_documents,
    extract_year_from_date,
    hash_book,
    sanitize_title_for_path,
    save_json_report,
)


class TestHashBook:
    def test_same_inputs_produce_same_hash(self) -> None:
        h1 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        h2 = hash_book("Fluent Python", ["Luciano Ramalho"], 2022)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hexdigest

    def test_author_order_does_not_matter(self) -> None:
        h1 = hash_book("T", ["Alice", "Bob"], 2020)
        h2 = hash_book("T", ["Bob", "Alice"], 2020)
        assert h1 == h2

    def test_author_whitespace_and_case_normalized(self) -> None:
        h1 = hash_book("T", ["Bob"], 2020)
        h2 = hash_book("T", [" bob "], 2020)
        assert h1 == h2

    def test_title_whitespace_and_case_normalized(self) -> None:
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("  fluent python  ", ["A"], 2020)
        assert h1 == h2

    def test_different_title_produces_different_hash(self) -> None:
        h1 = hash_book("Fluent Python", ["A"], 2020)
        h2 = hash_book("Effective Python", ["A"], 2020)
        assert h1 != h2

    def test_different_year_produces_different_hash(self) -> None:
        h1 = hash_book("T", ["A"], 2020)
        h2 = hash_book("T", ["A"], 2021)
        assert h1 != h2

    def test_empty_title_still_returns_hash(self) -> None:
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
        h = hash_book(None, ["A"], 2020)
        assert h == ""

    def test_authors_none_treated_as_empty_list(self) -> None:
        h1 = hash_book("T", None, 2020)
        h2 = hash_book("T", [], 2020)
        assert h1 == h2

    def test_none_year_differs_from_zero_year(self) -> None:
        # year=None -> "" ; year=0 -> "0" : must be distinct identities.
        assert hash_book("T", ["A"], None) != hash_book("T", ["A"], 0)

    def test_none_authors_entries_are_dropped(self) -> None:
        assert hash_book("T", ["A", None], 2020) == hash_book("T", ["A"], 2020)


class TestExtractYearFromDate:
    def test_iso_date(self) -> None:
        assert extract_year_from_date("2023-12-19") == 2023

    def test_long_month_name(self) -> None:
        assert extract_year_from_date("December 17, 2019") == 2019

    def test_bare_year(self) -> None:
        assert extract_year_from_date("1995") == 1995

    def test_short_month_name(self) -> None:
        assert extract_year_from_date("Apr 23, 2021") == 2021

    def test_month_and_year_only(self) -> None:
        assert extract_year_from_date("September 2016") == 2016

    def test_embedded_iso_date_regex_path(self) -> None:
        assert extract_year_from_date("Last updated on 2016-11-29") == 2016

    def test_na_returns_none(self) -> None:
        assert extract_year_from_date("N/A") is None

    def test_none_returns_none(self) -> None:
        assert extract_year_from_date(None) is None

    def test_non_string_returns_none(self) -> None:
        assert extract_year_from_date(123) is None

    def test_unparsable_string_returns_none(self) -> None:
        assert extract_year_from_date("not a date at all") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_year_from_date("") is None

    def test_regex_matched_but_invalid_date_falls_through_to_none(self) -> None:
        # "9999-99-99" matches the \d{4}-\d{2}-\d{2} embedded-date regex shape but
        # isn't a real date (month/day out of range) - exercises the regex block's
        # ValueError branch, then falls through to the direct-format loop, which
        # also fails against this string, and returns None.
        assert extract_year_from_date("Report 9999-99-99 filed") is None

    def test_non_valueerror_from_strptime_is_caught_by_outer_handler(self, monkeypatch) -> None:
        # Defensive outer `except Exception` around the direct-format loop; real
        # strptime never raises anything but ValueError for a str input, so exercise
        # it directly. "1995" has no embedded YYYY-MM-DD substring, so it skips the
        # regex block entirely and goes straight to the format loop. datetime.datetime
        # is an immutable C type - can't patch .strptime on it in place - so swap the
        # module-level `datetime` name itself for a stand-in exposing only strptime,
        # which is all this code path calls it for.
        import bookscraper.book_utils as book_utils_module

        class _RaisingDatetime:
            @staticmethod
            def strptime(*args, **kwargs):
                raise RuntimeError("boom")

        monkeypatch.setattr(book_utils_module, "datetime", _RaisingDatetime)
        assert extract_year_from_date("1995") is None


class TestCheckLocalWritePermission:
    def test_success_cleans_up_test_file(self, tmp_path) -> None:
        result = check_local_write_permission(str(tmp_path))
        assert result is True
        assert not (tmp_path / "test_write_permission.tmp").exists()

    def test_oserror_returns_false(self, monkeypatch, tmp_path) -> None:
        def raise_oserror(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", raise_oserror)
        assert check_local_write_permission(str(tmp_path)) is False

    def test_generic_exception_returns_false(self, monkeypatch, tmp_path) -> None:
        def raise_runtime_error(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("builtins.open", raise_runtime_error)
        assert check_local_write_permission(str(tmp_path)) is False


class TestDumpsDocuments:
    """The single serializer behind books.json and every diagnostic report."""

    def test_round_trips_as_top_level_array(self) -> None:
        docs = [{"title": "T", "authors": ["A"], "publication_year": 2020, "isbn": None}]
        assert json.loads(dumps_documents(docs)) == docs

    def test_non_ascii_written_verbatim(self) -> None:
        text = dumps_documents([{"title": "Programmieren in C++ für Einsteiger"}])
        assert "für" in text

    def test_non_json_native_values_are_stringified(self) -> None:
        text = dumps_documents([{"scraped_at": datetime(2026, 1, 2, 3, 4, 5)}])
        assert json.loads(text) == [{"scraped_at": "2026-01-02 03:04:05"}]


class TestSanitizeTitleForPath:
    def test_plain_title_unchanged(self) -> None:
        assert sanitize_title_for_path("A Tour of C++ 3rd Edition") == "A Tour of C++ 3rd Edition"

    def test_path_separators_and_unsafe_chars_replaced(self) -> None:
        # Every unsafe char becomes a space, then whitespace collapses.
        assert sanitize_title_for_path('C:/Foo\\Bar*?"<>|Baz') == "C Foo Bar Baz"

    def test_no_path_separator_survives(self) -> None:
        result = sanitize_title_for_path("deep/nested/title")
        assert "/" not in result and "\\" not in result

    def test_control_characters_removed(self) -> None:
        assert sanitize_title_for_path("Title\twith\nnewlines") == "Title with newlines"

    def test_whitespace_collapsed_and_trimmed(self) -> None:
        assert sanitize_title_for_path("   many    spaces   ") == "many spaces"

    def test_trailing_dots_and_spaces_stripped(self) -> None:
        assert sanitize_title_for_path("Windows name...  ") == "Windows name"

    def test_empty_or_dotted_becomes_untitled(self) -> None:
        assert sanitize_title_for_path("") == "untitled"
        assert sanitize_title_for_path("...") == "untitled"
        assert sanitize_title_for_path("   ") == "untitled"

    def test_non_string_input_is_tolerated(self) -> None:
        assert sanitize_title_for_path(None) == "untitled"
        assert sanitize_title_for_path(1234) == "1234"


class TestCoverExtension:
    def test_content_type_wins(self) -> None:
        assert _cover_extension("image/png", "https://x/cover") == ".png"
        assert _cover_extension("image/jpeg; charset=binary", "https://x/cover") == ".jpg"
        assert _cover_extension("image/webp", "https://x/cover.jpg") == ".webp"

    def test_falls_back_to_url_suffix_when_type_generic(self) -> None:
        assert _cover_extension("application/octet-stream", "https://x/cover.png") == ".png"
        assert _cover_extension(None, "https://x/img/cover.gif?v=2") == ".gif"

    def test_defaults_to_jpg_when_nothing_usable(self) -> None:
        assert _cover_extension(None, "https://x/no-extension-here") == ".jpg"
        assert _cover_extension("application/octet-stream", "https://x/weird.thisisnotanext") == ".jpg"


class TestSaveJsonReport:
    def test_writes_records(self, tmp_path) -> None:
        target = tmp_path / "report.json"
        save_json_report([{"url": "u"}], str(target), "links")
        assert json.loads(target.read_text(encoding="utf-8")) == [{"url": "u"}]

    def test_empty_records_write_no_file(self, tmp_path) -> None:
        target = tmp_path / "report.json"
        save_json_report([], str(target), "links")
        assert not target.exists()

    def test_overwrites_previous_run(self, tmp_path) -> None:
        target = tmp_path / "report.json"
        save_json_report([{"url": "old"}], str(target), "links")
        save_json_report([{"url": "new"}], str(target), "links")
        assert json.loads(target.read_text(encoding="utf-8")) == [{"url": "new"}]
