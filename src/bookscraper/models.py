"""The canonical cross-site book record (subtask 03.1/01).

Before this module each site wrote a different document shape to the same store: Playwright
sites used ``description`` / ``publication_date`` / ``"N/A"`` ISBN sentinels and a ``"amazon"``
site key, while Leanpub wrote ``about_the_book`` / ``categories`` / ``last_published_at`` / a
``"leanpub.com"`` site key and no ``url``. :class:`BookRecord` replaces every one of those
shapes with a single frozen, schema-versioned record that both backends store verbatim via
:meth:`BookRecord.to_document`, and that :meth:`BookRecord.from_document` reads back strictly.

This subtask defines the model and its (de)serialization only. Per-site normalizers (subtask
04) build a fully formed record from raw scrape output, and save-boundary validation (subtask
05) enforces the invariants in the field table; :class:`BookRecord` itself computes and
validates nothing in ``__post_init__`` by design, so it can represent whatever a normalizer or
migration produces without second-guessing it.

The field schema, a worked example per site, and the v1-to-v2 mapping live in
``docs/scraping/book-record-schema.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

#: The document schema version stamped onto every record this module produces. Version 1 is
#: everything written before this task (the per-site shapes described above); bumping this
#: constant is how the migration (subtask 06) tells a v1 document apart from a v2 one.
SCHEMA_VERSION = 2

#: The four supported sites, as bare keys (never a hostname such as ``"leanpub.com"``). Kept as
#: a ``Literal`` so a static type checker rejects any other value at a construction site.
Site = Literal["amazon", "packtpub", "leanpub", "oreilly"]


@dataclass(frozen=True, slots=True)
class BookRecord:
    """One book's canonical, cross-site metadata record.

    Every field is supplied at construction time: a normalizer (subtask 04) or the migration
    (subtask 06) always builds a fully formed record, so there are no defaults to guess. All
    four sites populate the same fields, with site-specific concepts (Leanpub's ``book_id`` /
    ``slug``, Amazon's ``asin``) folded into the shared :attr:`source_id` plus their own
    dedicated fields where a lookup still needs them.

    :ivar schema_version: The document schema version; :data:`SCHEMA_VERSION` for new records.
    :ivar site: The bare site key (:data:`Site`), never a hostname.
    :ivar url: The canonical human-facing detail URL (for Leanpub, ``https://leanpub.com/<slug>``).
    :ivar source_id: The site's own identifier - an Amazon ASIN or a Leanpub ``book_id`` -
        or ``None`` where the site has none.
    :ivar slug: The Leanpub slug, retained for pre-scrape deduplication; ``None`` elsewhere.
    :ivar asin: The Amazon ASIN, retained as its own field for ``book_exists_by_asin``;
        ``None`` elsewhere.
    :ivar title: The stripped, non-empty book title.
    :ivar authors: The author names - stripped, de-duplicated, order preserved; may be empty.
    :ivar isbn10: A checksum-valid ISBN-10 (10 chars, digits plus an optional ``X``), or ``None``.
    :ivar isbn13: A checksum-valid ISBN-13 (13 digits), or ``None``.
    :ivar publication_date: An ISO date at the precision the site exposes - ``YYYY-MM-DD``,
        ``YYYY-MM``, or ``YYYY`` - or ``None``.
    :ivar publication_year: The year derived from :attr:`publication_date`, or ``None``.
    :ivar description: The plain-text description (HTML converted via
        :func:`scraping.text.html_to_text`), or ``None``.
    :ivar tags: The site's category/tag strings; may be empty.
    :ivar ai_extracted_fields: The sorted names of fields filled by the 04.1 LLM fallback;
        always ``[]`` until that task lands.
    :ivar scraped_at: The scrape time as a UTC ISO-8601 string ending in ``Z``.
    :ivar hash: The deduplication key, ``book_utils.hash_book(title, authors, publication_year)``.
    """

    schema_version: int
    site: Site
    url: str
    source_id: Optional[str]
    slug: Optional[str]
    asin: Optional[str]
    title: str
    authors: list[str]
    isbn10: Optional[str]
    isbn13: Optional[str]
    publication_date: Optional[str]
    publication_year: Optional[int]
    description: Optional[str]
    tags: list[str]
    ai_extracted_fields: list[str]
    scraped_at: str
    hash: str

    def to_document(self) -> dict:
        """Return the exact JSON-serializable document both backends store.

        Every field is emitted, including absent optional data as an explicit ``null`` rather
        than a dropped key, so every schema-2 document has a uniform shape and there are no
        ``"N/A"`` sentinels. The two list fields are copied so a caller cannot mutate the frozen
        record through the returned document.

        :returns: A dict carrying all seventeen fields in the schema's documented order.
        """
        return {
            "schema_version": self.schema_version,
            "site": self.site,
            "url": self.url,
            "source_id": self.source_id,
            "slug": self.slug,
            "asin": self.asin,
            "title": self.title,
            "authors": list(self.authors),
            "isbn10": self.isbn10,
            "isbn13": self.isbn13,
            "publication_date": self.publication_date,
            "publication_year": self.publication_year,
            "description": self.description,
            "tags": list(self.tags),
            "ai_extracted_fields": list(self.ai_extracted_fields),
            "scraped_at": self.scraped_at,
            "hash": self.hash,
        }

    @classmethod
    def from_document(cls, doc: dict) -> BookRecord:
        """Rebuild a :class:`BookRecord` from a document produced by :meth:`to_document`.

        The strict inverse used by the tests and the migration: every schema-2 key must be
        present (a dropped or renamed key raises :class:`KeyError` rather than being silently
        defaulted), matching the uniform-document guarantee of :meth:`to_document`. The two list
        fields are copied so the new record does not alias the source document.

        :param doc: A document previously produced by :meth:`to_document`.
        :returns: The reconstructed record.
        :raises KeyError: if any schema-2 field is missing from ``doc``.
        """
        return cls(
            schema_version=doc["schema_version"],
            site=doc["site"],
            url=doc["url"],
            source_id=doc["source_id"],
            slug=doc["slug"],
            asin=doc["asin"],
            title=doc["title"],
            authors=list(doc["authors"]),
            isbn10=doc["isbn10"],
            isbn13=doc["isbn13"],
            publication_date=doc["publication_date"],
            publication_year=doc["publication_year"],
            description=doc["description"],
            tags=list(doc["tags"]),
            ai_extracted_fields=list(doc["ai_extracted_fields"]),
            scraped_at=doc["scraped_at"],
            hash=doc["hash"],
        )
