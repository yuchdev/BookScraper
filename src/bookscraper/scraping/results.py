"""Typed result for a single book scrape (subtask 03.0/02).

Before this module, :func:`scrape_details.scrape_book` returned a union of four shapes - a
``dict`` on success, ``(None, "DUPLICATE")``, ``(None, "FAILED")``, or a bare ``None`` on the
Leanpub path - so a caller had to pattern-match on ``isinstance``/tuple length and a 404, a
timeout, and a parse error all collapsed into the one opaque ``"FAILED"`` string.

:class:`ScrapeResult` replaces every one of those shapes with a single frozen record whose
:class:`ScrapeStatus` distinguishes *why* a scrape did not yield a book, and whose ``error``
carries a short, secret-free reason for the diagnostic CSV.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ScrapeStatus(str, Enum):
    """The outcome of a single :func:`scrape_details.scrape_book` call.

    ``str``-valued so a status serializes to its own name in CSV/JSON without extra mapping.
    """

    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    NOT_FOUND = "NOT_FOUND"  # 404 / page-not-found title
    TIMEOUT = "TIMEOUT"  # all attempts timed out
    BLOCKED = "BLOCKED"  # bot wall detected (populated by task 03.2 subtask 04)
    SKIPPED_ROBOTS = "SKIPPED_ROBOTS"  # disallowed by robots.txt (task 03.2 subtask 02)
    PARSE_ERROR = "PARSE_ERROR"  # page loaded, required field missing
    FAILED = "FAILED"  # anything else


#: The scrape statuses the exit-code contract (task 05.0 subtask 05) counts as a failed item, so a
#: run that saw any of them returns :data:`bookscraper.exitcodes.PARTIAL`. ``DUPLICATE``,
#: ``SKIPPED_ROBOTS`` and ``NOT_FOUND`` are deliberately excluded - a duplicate or a filtered-out
#: item means the run did what it should, not that an item failed.
FAILURE_STATUSES = frozenset(
    {
        ScrapeStatus.FAILED,
        ScrapeStatus.TIMEOUT,
        ScrapeStatus.PARSE_ERROR,
        ScrapeStatus.BLOCKED,
    }
)


@dataclass(frozen=True)
class ScrapeResult:
    """The typed outcome of scraping one book URL.

    :ivar status: Why the scrape ended; see :class:`ScrapeStatus`.
    :ivar url: The URL that was scraped (the real book URL, never a placeholder).
    :ivar site: The site identifier the URL was scraped as (e.g. ``"amazon"``).
    :ivar book: The scraped book document; set **iff** ``status`` is
        :attr:`ScrapeStatus.SUCCESS`, ``None`` otherwise.
    :ivar error: A short, secret-free failure reason; ``None`` on
        :attr:`ScrapeStatus.SUCCESS` and :attr:`ScrapeStatus.DUPLICATE`.
    :ivar attempts: How many attempts were made before this result was produced.
    """

    status: ScrapeStatus
    url: str
    site: str
    book: Optional[dict] = None
    error: Optional[str] = None
    attempts: int = 1

    def __post_init__(self):
        """Enforce the invariant ``book is not None`` ⇔ ``status is SUCCESS``.

        :raises ValueError: if a book document is present on a non-success status, or a
            success result carries no book document.
        """
        has_book = self.book is not None
        is_success = self.status is ScrapeStatus.SUCCESS
        if has_book and not is_success:
            raise ValueError(f"ScrapeResult with status {self.status.value} must not carry a book document")
        if is_success and not has_book:
            raise ValueError("ScrapeResult with status SUCCESS must carry a book document")
