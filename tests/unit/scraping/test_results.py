"""Unit tests for bookscraper.scraping.results (subtask 03.0/02).

Pure logic, no mocks: these exercise the ``ScrapeResult`` invariant and the ``ScrapeStatus``
enum contract that the scraper and both CLI commands depend on.
"""

from __future__ import annotations

import pytest

from bookscraper.scraping.results import ScrapeResult, ScrapeStatus


def test_scrape_result_success_requires_book() -> None:
    """Scenario: a SUCCESS result is constructed with a book document.

    Boundaries: the happy path of ``__post_init__`` - SUCCESS carrying a dict is accepted and
    the fields round-trip unchanged.
    On failure: the ``book is not None`` ⇔ SUCCESS invariant rejected a legitimate success,
    which would make every successful scrape raise.
    """
    book = {"title": "A Book", "hash": "abc"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://x/dp/1", site="amazon", book=book)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book is book
    assert result.error is None
    assert result.attempts == 1


def test_scrape_result_success_without_book_is_rejected() -> None:
    """Scenario: a SUCCESS result is constructed with no book document.

    Boundaries: the invalid-input half of the invariant - SUCCESS must carry a book.
    On failure: a success result with no payload would slip through and be appended to the
    save batch as ``None``, corrupting the store.
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
    """Scenario: every non-success status is constructed while carrying a book document.

    Boundaries: the invalid-input half of the invariant across all non-success statuses; each
    must raise because a non-success result may never carry a book.
    On failure: a failed/duplicate result could smuggle a book document into the diagnostics,
    reintroducing the ambiguity the typed result was meant to remove.
    """
    with pytest.raises(ValueError, match="must not carry a book"):
        ScrapeResult(status, url="https://x", site="amazon", book={"title": "Nope"})


def test_scrape_result_non_success_allows_error_and_attempts() -> None:
    """Scenario: a non-success result records its error reason and attempt count.

    Boundaries: a FAILED result with no book but an ``error`` and ``attempts`` is accepted and
    the values round-trip.
    On failure: the diagnostic metadata (reason, retry count) that ``failed_books.csv`` reports
    would be dropped or rejected.
    """
    result = ScrapeResult(
        ScrapeStatus.TIMEOUT, url="https://x", site="amazon", error="timed out after 3 attempts", attempts=3
    )

    assert result.book is None
    assert result.error == "timed out after 3 attempts"
    assert result.attempts == 3


def test_scrape_status_values_serialize_to_their_names() -> None:
    """Scenario: a ScrapeStatus is used where its string value is expected (CSV/summary).

    Boundaries: the ``str``-Enum contract - ``.value`` equals the member name - which the CSV
    writer and the status-breakdown summary rely on.
    On failure: a status would serialize as ``ScrapeStatus.FAILED`` instead of ``FAILED``,
    breaking the diagnostic CSV and any downstream drift job.
    """
    assert ScrapeStatus.SUCCESS.value == "SUCCESS"
    assert ScrapeStatus.NOT_FOUND.value == "NOT_FOUND"
    assert ScrapeStatus.FAILED.value == "FAILED"
