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

import re
from dataclasses import dataclass
from typing import Literal, Optional, get_args

from . import isbn

#: The document schema version stamped onto every record this module produces. Version 1 is
#: everything written before this task (the per-site shapes described above); bumping this
#: constant is how the migration (subtask 06) tells a v1 document apart from a v2 one.
SCHEMA_VERSION = 2

#: The four supported sites, as bare keys (never a hostname such as ``"leanpub.com"``). Kept as
#: a ``Literal`` so a static type checker rejects any other value at a construction site.
Site = Literal["amazon", "packtpub", "leanpub", "oreilly"]

#: The runtime value domain of :data:`Site`, materialized once from the ``Literal`` for the
#: save-boundary check (:func:`validate_document`); the ``Literal`` itself only guards static
#: call sites, not a document that reached the store by another path (a migration, a raw insert).
_SITE_VALUES = frozenset(get_args(Site))

#: Every key a schema-2 document must carry, in the schema's documented order. Reused by
#: :func:`validate_document` so a dropped or renamed key is reported deterministically rather
#: than surfacing later as a confusing type error.
_REQUIRED_KEYS = (
    "schema_version",
    "site",
    "url",
    "source_id",
    "slug",
    "asin",
    "title",
    "authors",
    "isbn10",
    "isbn13",
    "publication_date",
    "publication_year",
    "description",
    "tags",
    "ai_extracted_fields",
    "scraped_at",
    "hash",
)

#: A 64-character lowercase-hexadecimal SHA-256 digest - the exact shape
#: :func:`bookscraper.book_utils.hash_book` produces and the unique key both backends dedupe on.
_HASH_RE = re.compile(r"[0-9a-f]{64}")

#: ISO date at the precision a site exposes: ``YYYY``, ``YYYY-MM``, or ``YYYY-MM-DD`` (the shape
#: :func:`bookscraper.book_utils.normalize_date` emits). Semantic validity (a real calendar day)
#: is the normalizer's job; the save boundary only pins the lexical shape.
_PUBLICATION_DATE_RE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")

# Maximum lengths for page-controlled stored text (03.0 security review, LOW). Scraped text is
# untrusted and was previously stored uncapped, so the save boundary rejects a document whose
# text exceeds these bounds (counted as a SaveSummary error, never truncated - a truncated
# record would silently corrupt the stored value and its hash input). Every bound is set far
# above any real title/author/tag/description/URL so no golden or real fixture is affected.
#: Maximum stored :attr:`BookRecord.title` length, in characters.
MAX_TITLE_LEN = 2000
#: Maximum stored length of a single :attr:`BookRecord.authors` entry, in characters.
MAX_AUTHOR_LEN = 500
#: Maximum stored length of a single :attr:`BookRecord.tags` entry, in characters.
MAX_TAG_LEN = 200
#: Maximum stored :attr:`BookRecord.description` length, in characters.
MAX_DESCRIPTION_LEN = 100_000
#: Maximum stored :attr:`BookRecord.url` length, in characters.
MAX_URL_LEN = 2000
#: Maximum number of :attr:`BookRecord.tags` entries (matches the scrapers' write-time cap).
MAX_TAGS = 50


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


def _validate_str_list(
    doc: dict,
    key: str,
    max_len: Optional[int] = None,
    max_items: Optional[int] = None,
) -> list[str]:
    """Validate a ``list[str]`` field, with optional per-entry length and item-count caps.

    Used by :func:`validate_document` for ``authors`` / ``tags`` / ``ai_extracted_fields``.
    Reports at most one length/type problem per field (the loop stops at the first offending
    entry) so a pathological page cannot flood the problem list.

    :param doc: The document under validation.
    :param key: The field name to check; a missing key is not this function's concern (the
        required-key pass in :func:`validate_document` reports it) and yields no problem here.
    :param max_len: The maximum length of any single entry, or ``None`` for no cap.
    :param max_items: The maximum number of entries, or ``None`` for no cap.
    :returns: The problems found (empty when the field is absent or valid).
    """
    problems: list[str] = []
    if key not in doc:
        return problems
    value = doc[key]
    if not isinstance(value, list):
        problems.append(f"{key} must be a list")
        return problems
    if max_items is not None and len(value) > max_items:
        problems.append(f"{key} exceeds {max_items} entries")
    for element in value:
        if not isinstance(element, str):
            problems.append(f"{key} entries must be strings")
            break
        if max_len is not None and len(element) > max_len:
            problems.append(f"{key} entry exceeds {max_len} characters")
            break
    return problems


def validate_document(doc: dict) -> list[str]:
    """Return the human-readable reasons ``doc`` is not a valid schema-2 record (empty = valid).

    The single definition of "valid" for the save boundary (subtask 05), reused by the migration
    (subtask 06) and the cross-backend parity tests. It never raises and never mutates ``doc``;
    it only reports, so a backend can count an invalid document as an error and log the problems
    without the whole document. Every schema-2 key must be present with the right type;
    :attr:`BookRecord.site` must be one of the four bare keys; ``hash`` must be 64 lowercase hex
    characters; each non-null ISBN must pass its :mod:`bookscraper.isbn` checksum; a non-null
    ``publication_date`` must be ISO ``YYYY[-MM[-DD]]``; ``schema_version`` must equal
    :data:`SCHEMA_VERSION`; and the page-controlled text fields must stay within the ``MAX_*``
    size caps (03.0 security review).

    :param doc: A candidate document, normally produced by :meth:`BookRecord.to_document`.
    :returns: A list of problem descriptions, one per violation; empty when ``doc`` is valid.
    """
    if not isinstance(doc, dict):
        return ["document is not a mapping"]

    problems: list[str] = []
    for key in _REQUIRED_KEYS:
        if key not in doc:
            problems.append(f"missing required key: {key}")

    if "schema_version" in doc:
        version = doc["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            problems.append("schema_version must be an integer")
        elif version != SCHEMA_VERSION:
            problems.append(f"schema_version must equal {SCHEMA_VERSION}")

    if "site" in doc:
        site = doc["site"]
        if not isinstance(site, str):
            problems.append("site must be a string")
        elif site not in _SITE_VALUES:
            problems.append(f"site must be one of {sorted(_SITE_VALUES)}")

    if "url" in doc:
        url = doc["url"]
        if not isinstance(url, str):
            problems.append("url must be a string")
        elif len(url) > MAX_URL_LEN:
            problems.append(f"url exceeds {MAX_URL_LEN} characters")

    if "title" in doc:
        title = doc["title"]
        if not isinstance(title, str):
            problems.append("title must be a string")
        elif len(title) > MAX_TITLE_LEN:
            problems.append(f"title exceeds {MAX_TITLE_LEN} characters")

    if "scraped_at" in doc and not isinstance(doc["scraped_at"], str):
        problems.append("scraped_at must be a string")

    if "hash" in doc:
        book_hash = doc["hash"]
        if not isinstance(book_hash, str):
            problems.append("hash must be a string")
        elif not _HASH_RE.fullmatch(book_hash):
            problems.append("hash must be 64 lowercase hexadecimal characters")

    for key in ("source_id", "slug", "asin", "isbn10", "isbn13", "publication_date"):
        if key in doc and doc[key] is not None and not isinstance(doc[key], str):
            problems.append(f"{key} must be a string or null")

    if isinstance(doc.get("isbn10"), str) and not isbn.is_valid_isbn10(doc["isbn10"]):
        problems.append("isbn10 is not a checksum-valid ISBN-10")
    if isinstance(doc.get("isbn13"), str) and not isbn.is_valid_isbn13(doc["isbn13"]):
        problems.append("isbn13 is not a checksum-valid ISBN-13")

    publication_date = doc.get("publication_date")
    if isinstance(publication_date, str) and not _PUBLICATION_DATE_RE.fullmatch(publication_date):
        problems.append("publication_date must be ISO YYYY, YYYY-MM, or YYYY-MM-DD")

    if "publication_year" in doc:
        year = doc["publication_year"]
        if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
            problems.append("publication_year must be an integer or null")

    if "description" in doc:
        description = doc["description"]
        if description is not None and not isinstance(description, str):
            problems.append("description must be a string or null")
        elif isinstance(description, str) and len(description) > MAX_DESCRIPTION_LEN:
            problems.append(f"description exceeds {MAX_DESCRIPTION_LEN} characters")

    problems.extend(_validate_str_list(doc, "authors", max_len=MAX_AUTHOR_LEN))
    problems.extend(_validate_str_list(doc, "tags", max_len=MAX_TAG_LEN, max_items=MAX_TAGS))
    problems.extend(_validate_str_list(doc, "ai_extracted_fields"))

    return problems
