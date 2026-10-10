"""Per-site normalizers: raw scrape output to a canonical :class:`BookRecord` (subtask 03.1/04).

Before this module each site's scraper assembled its own ad hoc dict - Playwright sites used
``publication_date`` (raw site text) with ``"N/A"`` ISBN sentinels and a ``"amazon"`` site key,
while Leanpub used ``about_the_book`` / ``categories`` / ``last_published_at`` and a
``"leanpub.com"`` site key with no ``url`` - and those dicts were stored verbatim. This module
is now the single place that turns raw scrape output into a fully formed
:class:`bookscraper.models.BookRecord`, so ``scrape_book`` and ``get_leanpub_book_details`` each
return ``ScrapeResult(book=record.to_document())`` and nothing else in ``scraping/`` builds a
book document by hand.

The hash input is deliberately unchanged from the pre-03.1 code:
:func:`bookscraper.book_utils.hash_book` is still fed ``(title, authors, publication_year)``,
and ``publication_year`` is still the year derived from the same raw date (Leanpub's
``last_published_at``), so every historical ``hash`` is preserved. Authors de-duplication
(:func:`_clean_authors`) only ever removes an exact case-insensitive repeat, which the sorted,
lower-cased hash input would otherwise have counted twice.

ISBNs are normalized/validated via :mod:`bookscraper.isbn` (``"N/A"`` and any invalid value
become ``None``), dates via :func:`bookscraper.book_utils.normalize_date` (ISO precision
preserved), and Leanpub's HTML ``about_the_book`` via :func:`bookscraper.scraping.text.html_to_text`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from .. import isbn
from ..book_utils import hash_book, normalize_date
from ..models import MAX_AUTHORS, SCHEMA_VERSION, BookRecord
from .parameters import site_constants
from .text import html_to_text

#: Upper bound on the number of tags kept per record. A page-controlled tag/category list is
#: untrusted input, so the collection is capped (after de-duplication) rather than stored
#: unbounded, matching :data:`bookscraper.scraping.scrape_details.TAGS_LIMIT`.
TAGS_LIMIT = 50

#: ``scraped_at`` for a migrated v1 document (subtask 06). A v1 document never recorded a scrape
#: time, so a fixed UTC epoch sentinel marks "unknown" uniformly rather than inventing a
#: plausible-but-false timestamp. Used only by :func:`from_v1_document`.
SENTINEL_SCRAPED_AT = "1970-01-01T00:00:00Z"


def _now_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``.

    The single clock used by both normalizers for :attr:`BookRecord.scraped_at`; a test injects
    a fixed timestamp through each normalizer's ``now`` parameter instead of patching the clock.

    :returns: The current UTC time formatted as ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_authors(authors: Optional[list]) -> list[str]:
    """Strip, drop empty, and de-duplicate author names case-insensitively, order preserved.

    The first spelling of a case-insensitively repeated name wins (so ``"Bjarne Stroustrup"``
    beats a later ``"bjarne stroustrup"``). Because :func:`bookscraper.book_utils.hash_book`
    lower-cases and sorts its author input, dropping an exact case-insensitive repeat here never
    changes the hash of any record that did not already carry a duplicate.

    :param authors: The raw author list (any element may be ``None`` or surrounding-whitespace),
        or ``None``.
    :returns: The cleaned, de-duplicated author names, order preserved, at most
        :data:`bookscraper.models.MAX_AUTHORS` (a page controls the count, so it is capped after
        de-duplication to match the save-boundary cap rather than stored unbounded).
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for author in authors or []:
        if author is None:
            continue
        name = str(author).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
        if len(cleaned) >= MAX_AUTHORS:
            break
    return cleaned


def _clean_tags(tags: Optional[list]) -> list[str]:
    """Strip, drop empty, de-duplicate case-insensitively, and cap at :data:`TAGS_LIMIT`.

    The same cleaning as :func:`_clean_authors`, plus an upper bound: a page controls the tag
    count, so the list is capped after de-duplication rather than stored unbounded.

    :param tags: The raw tag/category list (any element may be ``None``), or ``None``.
    :returns: The cleaned, de-duplicated tags, order preserved, at most :data:`TAGS_LIMIT`.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags or []:
        if tag is None:
            continue
        text = str(tag).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= TAGS_LIMIT:
            break
    return cleaned


def from_playwright(raw: dict, site: str, url: str, *, now: Optional[str] = None) -> BookRecord:
    """Build a canonical :class:`BookRecord` from ``scrape_book``'s raw Playwright dict.

    Maps the raw per-field scrape output (``title`` / ``authors`` / ``publication_date`` /
    ``isbn10`` / ``isbn13`` / ``description`` / ``tags`` and, for Amazon, ``asin``) onto the
    canonical record: authors are cleaned, ISBNs normalized/validated (the ``"N/A"`` sentinel and
    any invalid value become ``None``), and the raw date normalized to ISO precision with its
    year re-derived. The Amazon ASIN populates both :attr:`BookRecord.asin` (its dedicated lookup
    field) and :attr:`BookRecord.source_id`; ``slug`` is always ``None`` for a Playwright site.

    :param raw: ``scrape_book``'s in-progress book dict.
    :param site: The bare site key (``"amazon"`` / ``"packtpub"`` / ``"oreilly"``).
    :param url: The absolute detail URL that was scraped.
    :param now: A fixed UTC ISO-8601 timestamp for :attr:`BookRecord.scraped_at` (tests only);
        the current time via :func:`_now_utc_iso` when omitted.
    :returns: The fully formed, hash-stamped record.
    """
    title = (raw.get("title") or "").strip()
    authors = _clean_authors(raw.get("authors"))
    isbn10, isbn13 = isbn.normalize(raw.get("isbn10"), raw.get("isbn13"))
    publication_date, publication_year = normalize_date(raw.get("publication_date"))
    asin = raw.get("asin")
    return BookRecord(
        schema_version=SCHEMA_VERSION,
        site=site,
        url=url,
        source_id=asin,
        slug=None,
        asin=asin,
        title=title,
        authors=authors,
        isbn10=isbn10,
        isbn13=isbn13,
        publication_date=publication_date,
        publication_year=publication_year,
        description=raw.get("description"),
        tags=_clean_tags(raw.get("tags")),
        ai_extracted_fields=[],
        scraped_at=now or _now_utc_iso(),
        hash=hash_book(title, authors, publication_year),
    )


def from_leanpub_api(
    attributes: dict,
    book_id: Optional[str],
    authors: list[str],
    categories: list[str],
    *,
    now: Optional[str] = None,
) -> BookRecord:
    """Build a canonical :class:`BookRecord` from a Leanpub JSON:API book.

    The Leanpub book id becomes :attr:`BookRecord.source_id` (``asin`` is always ``None`` here),
    the ``slug`` is carried through for pre-scrape deduplication, and the human-facing
    :attr:`BookRecord.url` is derived as ``<BASE_URL>/<slug>`` (the API endpoint is no longer
    stored). ``last_published_at`` is normalized to ISO precision with its year re-derived -
    exactly the pre-03.1 hash input, so the Leanpub ``hash`` is unchanged - and the HTML
    ``about_the_book`` is converted to plain text.

    :param attributes: The JSON:API resource ``attributes`` object (``title`` / ``slug`` /
        ``last_published_at`` / ``about_the_book``).
    :param book_id: The Leanpub book id (the JSON:API resource ``id``), or ``None``.
    :param authors: The already-resolved author names.
    :param categories: The already-resolved category names.
    :param now: A fixed UTC ISO-8601 timestamp for :attr:`BookRecord.scraped_at` (tests only);
        the current time via :func:`_now_utc_iso` when omitted.
    :returns: The fully formed, hash-stamped record.
    """
    title = (attributes.get("title") or "").strip()
    slug = attributes.get("slug")
    cleaned_authors = _clean_authors(authors)
    publication_date, publication_year = normalize_date(attributes.get("last_published_at"))
    base_url = site_constants["leanpub"].get("BASE_URL") or ""
    url = f"{base_url}/{slug}" if slug else base_url
    return BookRecord(
        schema_version=SCHEMA_VERSION,
        site="leanpub",
        url=url,
        source_id=book_id,
        slug=slug,
        asin=None,
        title=title,
        authors=cleaned_authors,
        isbn10=None,
        isbn13=None,
        publication_date=publication_date,
        publication_year=publication_year,
        description=html_to_text(attributes.get("about_the_book")),
        tags=_clean_tags(categories),
        ai_extracted_fields=[],
        scraped_at=now or _now_utc_iso(),
        hash=hash_book(title, cleaned_authors, publication_year),
    )


def _from_v2_passthrough(content: dict) -> dict:
    """Re-emit an already-schema-2 document, recomputing its hash for idempotency.

    A v2 document is already clean, so :meth:`bookscraper.models.BookRecord.from_document`
    round-trips it unchanged; the hash is recomputed from the same ``(title, authors,
    publication_year)`` the live scrapers use, which equals the stored hash for a genuine v2
    record (so a second migration pass is a no-op) and surfaces any drift for a would-be v2
    document that is not.

    :param content: A document claiming ``schema_version == 2`` (without any Mongo ``_id``).
    :returns: The canonical v2 document.
    :raises KeyError: if ``content`` is missing a schema-2 key.
    """
    record = BookRecord.from_document(content)
    document = record.to_document()
    document["hash"] = hash_book(record.title, record.authors, record.publication_year)
    return document


def from_v1_document(doc: dict) -> dict:
    """Normalize a stored v1 (or already-v2) document into a canonical schema-2 document.

    A stored v1 document is just another raw input to normalize: this routes it to the same
    per-site normalizer a fresh scrape uses - :func:`from_leanpub_api` for a ``"leanpub"`` /
    ``"leanpub.com"`` record, otherwise :func:`from_playwright` - so the v1-to-v2 mapping
    (``about_the_book`` to ``description``, ``categories`` to ``tags``, ``last_published_at`` to a
    normalized ``publication_date``, ``"N/A"`` ISBNs to ``None`` or a validated value, the Leanpub
    hostname to the bare key, a derived ``https://leanpub.com/<slug>`` URL, ``schema_version: 2``,
    the :data:`SENTINEL_SCRAPED_AT` scrape-time sentinel, and dropped unknown keys) is applied
    exactly as a fresh scrape would, with the hash recomputed from the migrated fields. An
    already-v2 document round-trips unchanged (idempotent), preserving its real ``scraped_at``.

    The migration engine (:mod:`bookscraper.backends.migration`) takes this function as an injected
    ``transform`` callable, so ``backends`` never imports ``scraping``.

    :param doc: A stored v1 or v2 document (a Mongo ``_id``, if present, is ignored).
    :returns: The fully-formed, hash-stamped schema-2 document.
    :raises KeyError: if an already-v2 document is missing a schema-2 key.
    """
    content = {key: value for key, value in doc.items() if key != "_id"}
    if content.get("schema_version") == SCHEMA_VERSION:
        return _from_v2_passthrough(content)

    site = content.get("site")
    if site in ("leanpub", "leanpub.com"):
        record = from_leanpub_api(
            content,
            content.get("book_id"),
            content.get("authors") or [],
            content.get("categories") or [],
            now=SENTINEL_SCRAPED_AT,
        )
    else:
        record = from_playwright(
            content,
            site,
            content.get("url") or "",
            now=SENTINEL_SCRAPED_AT,
        )
    return record.to_document()
