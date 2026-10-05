"""Unit tests for bookscraper.scraping.results (subtask 03.0/02).

Pure logic, no mocks: these exercise the ``ScrapeResult`` invariant and the ``ScrapeStatus``
enum contract that the scraper and both CLI commands depend on.
"""

from __future__ import annotations

import pytest

from bookscraper.scraping.results import ScrapeResult, ScrapeStatus


def test_scrape_result_success_requires_book() -> None:
    """
    [Unit] scrape results: verifies a SUCCESS ScrapeResult with a book document is accepted.

    Scenario:
        Given a SUCCESS status and a non-None book dict
        When ScrapeResult is constructed
        Then status, book, error, and attempts all round-trip as expected

    Boundaries:
        - Happy path of __post_init__ only; no invalid-input branch exercised
        - In-memory dataclass; no I/O or external calls

    On failure, first check:
        - The book-is-not-None <=> SUCCESS invariant in ScrapeResult.__post_init__ was changed
    """
    book = {"title": "A Book", "hash": "abc"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://x/dp/1", site="amazon", book=book)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book is book
    assert result.error is None
    assert result.attempts == 1


def test_scrape_result_success_without_book_is_rejected() -> None:
    """
    [Unit] scrape results: verifies constructing a SUCCESS ScrapeResult without a book raises.

    Scenario:
        Given a SUCCESS status and no book argument
        When ScrapeResult is constructed
        Then ValueError is raised with "SUCCESS must carry a book"

    Boundaries:
        - Invalid-input branch of __post_init__ only; the valid path is a separate test
        - Does not test any non-SUCCESS status

    On failure, first check:
        - The invariant guard in ScrapeResult.__post_init__ was removed or weakened
        - A None book could slip into the save batch and corrupt the store
    """
    with pytest.raises(ValueError, match="SUCCESS must carry a book"):
        ScrapeResult(ScrapeStatus.SUCCESS, url="https://x", site="amazon")


@pytest.mark.parametrize(
    "status",
    [
        ScrapeStatus.DUPLICATE,
        ScrapeStatus.NOT_FOUND,
        ScrapeStatus.TIMEOUT,
        ScrapeStatus.BLOCKED,
        ScrapeStatus.PARSE_ERROR,
        ScrapeStatus.FAILED,
    ],
)
def test_scrape_result_non_success_forbids_book(status: ScrapeStatus) -> None:
    """
    [Unit] scrape results: verifies every non-success status rejects a book document.

    Scenario:
        Given a non-success status (parametrized over all six) and a non-None book dict
        When ScrapeResult is constructed
        Then ValueError is raised with "must not carry a book"

    Boundaries:
        - Covers all non-SUCCESS statuses via parametrize; the valid non-success path is separate
        - Does not test SUCCESS (tested by test_scrape_result_success_requires_book)

    On failure, first check:
        - A non-success status was added without adding an invariant guard for the book field
        - The invariant guard in ScrapeResult.__post_init__ was weakened for a specific status
    """
    with pytest.raises(ValueError, match="must not carry a book"):
        ScrapeResult(status, url="https://x", site="amazon", book={"title": "Nope"})


def test_scrape_result_non_success_allows_error_and_attempts() -> None:
    """
    [Unit] scrape results: verifies a non-success result accepts error and attempts metadata.

    Scenario:
        Given a TIMEOUT status, an error string, and an attempts count of 3
        When ScrapeResult is constructed with no book
        Then book is None and error/attempts round-trip unchanged

    Boundaries:
        - Uses TIMEOUT as a representative non-success status; others share the same path
        - Does not test that the fields are consumed by any downstream code

    On failure, first check:
        - ScrapeResult dropped or rejected the error or attempts field in __post_init__
        - failed_books.csv diagnostics would lose the reason and retry count
    """
    result = ScrapeResult(
        ScrapeStatus.TIMEOUT, url="https://x", site="amazon", error="timed out after 3 attempts", attempts=3
    )

    assert result.book is None
    assert result.error == "timed out after 3 attempts"
    assert result.attempts == 3


def test_scrape_status_values_serialize_to_their_names() -> None:
    """
    [Unit] scrape results: verifies ScrapeStatus.value equals the member name for CSV serialization.

    Scenario:
        Given the ScrapeStatus str-Enum definition
        When .value is read for SUCCESS, NOT_FOUND, and FAILED
        Then each value equals its member name as a plain string

    Boundaries:
        - Three representative members checked; the contract applies to all via the str-Enum base
        - Does not test the CSV writer itself, only the string contract it relies on

    On failure, first check:
        - ScrapeStatus base class was changed away from str (or StrEnum)
        - The diagnostic CSV and status-breakdown summary would emit "ScrapeStatus.X" instead of "X"
    """
    assert ScrapeStatus.SUCCESS.value == "SUCCESS"
    assert ScrapeStatus.NOT_FOUND.value == "NOT_FOUND"
    assert ScrapeStatus.FAILED.value == "FAILED"
