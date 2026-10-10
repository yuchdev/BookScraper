"""Unit tests for bookscraper.scraping.normalize (pure raw-dict to BookRecord mapping).

No mocks, no I/O: the two normalizers and their helpers are pure functions, so these tests feed
them raw dicts and assert on the canonical :class:`BookRecord` they build - the v2 field renames
(Leanpub ``book_id`` -> ``source_id``, ``categories`` -> ``tags``, ``about_the_book`` ->
``description``, ``"leanpub.com"`` -> ``"leanpub"``, derived human-facing ``url``), the
``"N/A"``-to-``None`` ISBN normalization, author de-duplication, and - most load-bearing - the
hash stability contract the whole milestone rests on (three pinned historical records).

The ``scraped_at`` clock is injected through each normalizer's ``now`` parameter so the records
are fully deterministic.
"""

from bookscraper.models import MAX_AUTHORS, SCHEMA_VERSION, validate_document
from bookscraper.scraping import normalize

# A fixed scraped_at so records are deterministic; the real clock is _now_utc_iso().
_NOW = "2026-01-01T00:00:00Z"


def _amazon_v1() -> dict:
    """A realistic v1 Amazon document (raw date, hyphenated ISBN-13, bare site key)."""
    return {
        "asin": "0136816487",
        "authors": ["Bjarne Stroustrup"],
        "description": "A concise introduction to C++.",
        "hash": "1b93bf19f83335db49b6af3fc2700565f80d42e1dabb44834b2c55a9ff4ad648",
        "isbn10": "0136816487",
        "isbn13": "978-0136816485",
        "publication_date": "September 24, 2022",
        "publication_year": 2022,
        "site": "amazon",
        "tags": ["C++ Programming Language", "Object-Oriented Design"],
        "title": "Tour of C++, A (C++ In-Depth Series)",
        "url": "https://www.amazon.com/dp/0136816487",
    }


def _leanpub_v1() -> dict:
    """A realistic v1 Leanpub document (``leanpub.com`` host, API-derived keys, no url)."""
    return {
        "about_the_book": "<p>Everything about C++ lambdas.</p>",
        "authors": ["Bartlomiej Filipek"],
        "book_id": "102",
        "categories": ["Computer Science", "C and C++"],
        "hash": "3b1beb4b6d58060b587c648b566e9af155241f7c6405a9bfa8c3249f2cff26c5",
        "last_published_at": "2020-09-01",
        "site": "leanpub.com",
        "slug": "cpplambda",
        "title": "C++ Lambda Story",
    }


def test_from_playwright_maps_all_fields():
    """
    [Unit] normalize: verifies from_playwright maps every raw field onto the canonical record.

    Scenario:
        Given a fully populated amazon raw scrape dict (title, authors, hyphenated ISBN-13,
        valid ISBN-10, a month-name publication_date, description, tags and an asin)
        When normalize.from_playwright builds the record
        Then every canonical field is populated - schema_version is SCHEMA_VERSION, the ISBNs are
        cleaned/validated, the date is ISO with its year derived, the asin fills both asin and
        source_id, slug is None, and the injected scraped_at is carried through.

    Boundaries:
        - Exercises the happy mapping only; "N/A" ISBNs and dedup are separate tests.
        - scraped_at is injected so the record is deterministic.

    On failure, first check:
        - normalize.from_playwright field assignment, or isbn.normalize / book_utils.normalize_date.
    """
    raw = {
        "url": "https://www.amazon.com/dp/0136816487",
        "site": "amazon",
        "title": "  A Tour of C++  ",
        "authors": ["Bjarne Stroustrup"],
        "publication_date": "September 24, 2022",
        "isbn10": "0136816487",
        "isbn13": "978-0136816485",
        "description": "A concise introduction.",
        "tags": ["C++", "C++"],
        "asin": "0136816487",
    }

    record = normalize.from_playwright(raw, "amazon", raw["url"], now=_NOW)

    assert record.schema_version == SCHEMA_VERSION
    assert record.site == "amazon"
    assert record.url == "https://www.amazon.com/dp/0136816487"
    assert record.title == "A Tour of C++"
    assert record.authors == ["Bjarne Stroustrup"]
    assert record.isbn10 == "0136816487"
    assert record.isbn13 == "9780136816485"
    assert record.publication_date == "2022-09-24"
    assert record.publication_year == 2022
    assert record.description == "A concise introduction."
    assert record.tags == ["C++"]
    assert record.asin == "0136816487"
    assert record.source_id == "0136816487"
    assert record.slug is None
    assert record.ai_extracted_fields == []
    assert record.scraped_at == _NOW


def test_from_playwright_na_isbn_becomes_none():
    """
    [Unit] normalize: verifies the "N/A" ISBN sentinel and invalid ISBNs normalize to None.

    Scenario:
        Given a raw dict whose isbn10 is the legacy "N/A" sentinel and whose isbn13 is a
        checksum-invalid string
        When normalize.from_playwright builds the record
        Then both isbn10 and isbn13 are None (no "N/A" ever reaches a schema-2 document).

    Boundaries:
        - Only the ISBN path is asserted; a missing asin leaves source_id/asin None too.

    On failure, first check:
        - isbn.normalize (clean() strips "N/A" to "" which fails validation), or the raw->record wiring.
    """
    raw = {
        "title": "No ISBN Book",
        "authors": [],
        "isbn10": "N/A",
        "isbn13": "978-0000000000",
        "publication_date": None,
        "description": None,
        "tags": [],
    }

    record = normalize.from_playwright(raw, "packtpub", "https://packtpub.com/x", now=_NOW)

    assert record.isbn10 is None
    assert record.isbn13 is None
    assert record.asin is None
    assert record.source_id is None


def test_from_leanpub_maps_about_the_book_to_description_and_categories_to_tags():
    """
    [Unit] normalize: verifies from_leanpub_api renames about_the_book and categories.

    Scenario:
        Given Leanpub attributes whose about_the_book is HTML and a categories list
        When normalize.from_leanpub_api builds the record
        Then description is the plain-text rendering (tags stripped) and tags holds the category
        names; the book id fills source_id (asin stays None), the slug is carried, and
        last_published_at becomes the ISO publication_date with its year derived.

    Boundaries:
        - The HTML-to-text conversion detail itself is covered by test_text; here only the
          field rename and routing are asserted.

    On failure, first check:
        - normalize.from_leanpub_api's description=html_to_text(...) / tags=_clean_tags(...) wiring.
    """
    attributes = {
        "title": "C++ Lambda Story",
        "slug": "cpplambda",
        "about_the_book": "<p>Everything about <b>lambdas</b>.</p>",
        "last_published_at": "2020-09-01T00:00:00Z",
    }

    record = normalize.from_leanpub_api(
        attributes, "102", ["Bartlomiej Filipek"], ["Computer Science", "C and C++"], now=_NOW
    )

    assert record.description == "Everything about lambdas."
    assert record.tags == ["Computer Science", "C and C++"]
    assert record.source_id == "102"
    assert record.asin is None
    assert record.slug == "cpplambda"
    assert record.publication_date == "2020-09-01"
    assert record.publication_year == 2020


def test_leanpub_site_is_bare_key_and_url_is_human_facing():
    """
    [Unit] normalize: verifies the Leanpub site key is bare and the url is human-facing.

    Scenario:
        Given Leanpub attributes carrying a slug
        When normalize.from_leanpub_api builds the record
        Then site is the bare "leanpub" (never the "leanpub.com" hostname) and url is
        https://leanpub.com/<slug> (the API endpoint is no longer stored).

    Boundaries:
        - Pins the v2 site-key and URL-derivation rules; field renames are a separate test.

    On failure, first check:
        - normalize.from_leanpub_api's site="leanpub" literal and the f"{BASE_URL}/{slug}" url.
    """
    attributes = {"title": "C++ Lambda Story", "slug": "cpplambda", "last_published_at": "2020-09-01"}

    record = normalize.from_leanpub_api(attributes, "102", [], [], now=_NOW)

    assert record.site == "leanpub"
    assert record.url == "https://leanpub.com/cpplambda"


def test_authors_deduplicated_case_insensitively():
    """
    [Unit] normalize: verifies authors are stripped, de-duplicated case-insensitively, first-spelling kept.

    Scenario:
        Given a raw author list with surrounding whitespace, an empty entry, a None, and a
        case-variant duplicate ("Ada Lovelace" then "ada lovelace")
        When normalize.from_playwright cleans it
        Then the result keeps the first spelling once, drops the empty/None entries, and preserves
        the original order.

    Boundaries:
        - Covers the author-cleaning rule (the one that also keeps duplicate-free hashes stable);
          tag cleaning shares the helper and is exercised via the tags assertions elsewhere.

    On failure, first check:
        - normalize._clean_authors (the seen-set keyed on the lower-cased name, order preserved).
    """
    raw = {
        "title": "T",
        "authors": ["  Ada Lovelace  ", "", "Grace Hopper", None, "ada lovelace"],
        "isbn10": "N/A",
        "isbn13": "N/A",
        "publication_date": None,
        "description": None,
        "tags": [],
    }

    record = normalize.from_playwright(raw, "amazon", "https://www.amazon.com/dp/X", now=_NOW)

    assert record.authors == ["Ada Lovelace", "Grace Hopper"]


def test_clean_authors_caps_at_max_authors():
    """
    [Unit] normalize: verifies _clean_authors caps the list at MAX_AUTHORS, mirroring the save boundary.

    Scenario:
        Given an author list with more than MAX_AUTHORS distinct names
        When normalize._clean_authors cleans it
        Then at most MAX_AUTHORS names are returned, keeping the first ones in order, so a
        page-controlled author list can never be stored unbounded (or rejected by the save boundary).

    Boundaries:
        - Edge case: the cap branch the field-level tests (max 6 authors) never reach
        - The cap must equal models.MAX_AUTHORS so normalizer output always passes validate_document

    On failure, first check:
        - The ``len(cleaned) >= MAX_AUTHORS`` break in normalize._clean_authors
        - Whether MAX_AUTHORS drifted between models and normalize
    """
    overflow = [f"Author {i}" for i in range(MAX_AUTHORS + 10)]
    capped = normalize._clean_authors(overflow)
    assert len(capped) == MAX_AUTHORS
    assert capped == overflow[:MAX_AUTHORS]


def test_clean_tags_strips_dedups_and_caps():
    """
    [Unit] normalize: verifies _clean_tags strips, drops empty/None, de-duplicates, and caps.

    Scenario:
        Given a tag list with surrounding whitespace, an empty string, a None, a case-variant
        duplicate, and (separately) more than TAGS_LIMIT distinct tags
        When normalize._clean_tags cleans each
        Then empty/None entries are dropped, the case-insensitive duplicate is removed keeping the
        first spelling, order is preserved, and no more than TAGS_LIMIT tags are returned.

    Boundaries:
        - Exercises the tag-cleaning helper directly, including the TAGS_LIMIT cap branch that the
          field-level tests do not reach.

    On failure, first check:
        - normalize._clean_tags (the None/empty guards, the seen-set, or the len>=TAGS_LIMIT break).
    """
    assert normalize._clean_tags(["  Python  ", "", None, "python", "Data"]) == ["Python", "Data"]
    assert normalize._clean_tags(None) == []

    overflow = [f"tag-{i}" for i in range(normalize.TAGS_LIMIT + 10)]
    capped = normalize._clean_tags(overflow)
    assert len(capped) == normalize.TAGS_LIMIT
    assert capped == overflow[: normalize.TAGS_LIMIT]


def test_hash_unchanged_for_known_historical_records():
    """
    [Unit] normalize: verifies three pinned historical records keep their exact v1 hash.

    Scenario:
        Given three (title, authors, year) records captured before task 03.1 (an Amazon, a
        Packtpub, and a Leanpub golden)
        When the matching normalizer rebuilds each canonical record
        Then record.hash equals the exact SHA-256 the pre-03.1 code stored - proving the
        canonical model feeds hash_book the same inputs and no stored dedup key drifts.

    Boundaries:
        - The non-negotiable milestone constraint (hash stability); any change to the hash input
          (title/authors/year) would break both backends' unique dedup key and must fail here.

    On failure, first check:
        - normalize's hash_book inputs (title, cleaned authors, publication_year derived from the
          same raw date) drifted from the pre-03.1 scraper.
    """
    amazon = normalize.from_playwright(
        {
            "title": "Tour of C++, A (C++ In-Depth Series)",
            "authors": ["Bjarne Stroustrup"],
            "publication_date": "September 24, 2022",
            "isbn10": "N/A",
            "isbn13": "N/A",
            "description": None,
            "tags": [],
        },
        "amazon",
        "https://www.amazon.com/dp/0136816487",
        now=_NOW,
    )
    assert amazon.hash == "1b93bf19f83335db49b6af3fc2700565f80d42e1dabb44834b2c55a9ff4ad648"

    packtpub = normalize.from_playwright(
        {
            "title": "C++ Windows Programming",
            "authors": ["Stefan Bjornander"],
            "publication_date": "September 2016",
            "isbn10": "N/A",
            "isbn13": "9781786464224",
            "description": None,
            "tags": [],
        },
        "packtpub",
        "https://www.packtpub.com/product/c-windows-programming/9781786464224",
        now=_NOW,
    )
    assert packtpub.hash == "bfc97faa0f5cdaee735dae7f1109efc10eea27df1f2a0587b12a0d84cd606743"

    leanpub = normalize.from_leanpub_api(
        {"title": "C++ Lambda Story", "slug": "cpplambda", "last_published_at": "2020-09-01"},
        "102",
        ["Bartlomiej Filipek"],
        ["Computer Science", "C and C++"],
        now=_NOW,
    )
    assert leanpub.hash == "3b1beb4b6d58060b587c648b566e9af155241f7c6405a9bfa8c3249f2cff26c5"


def test_from_v1_document_maps_every_legacy_field():
    """
    [Unit] normalize from_v1_document: verifies every v1 legacy field maps onto its v2 counterpart.

    Scenario:
        Given a v1 Leanpub document (``leanpub.com`` host, ``about_the_book`` / ``categories`` /
            ``last_published_at`` keys, no ``url`` or ``schema_version``)
        When normalize.from_v1_document is called
        Then the result is a valid schema-2 document: bare ``leanpub`` site, derived
            ``https://leanpub.com/<slug>`` url, ``book_id`` folded into ``source_id``,
            ``about_the_book`` to plain-text ``description``, ``categories`` to ``tags``,
            ``last_published_at`` to an ISO ``publication_date`` plus derived year,
            ``schema_version`` 2, and the SENTINEL_SCRAPED_AT scrape-time sentinel

    Boundaries:
        - Leanpub-specific mapping (the shape that differs most from v2), not the Playwright one
        - HTML description is converted to plain text (tags stripped)

    On failure, first check:
        - from_v1_document's leanpub.com routing to from_leanpub_api
        - The v1 to v2 mapping table in docs/scraping/book-record-schema.md
    """
    result = normalize.from_v1_document(_leanpub_v1())

    assert validate_document(result) == []
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["site"] == "leanpub"
    assert result["url"] == "https://leanpub.com/cpplambda"
    assert result["source_id"] == "102"
    assert result["slug"] == "cpplambda"
    assert result["asin"] is None
    assert result["description"] == "Everything about C++ lambdas."
    assert result["tags"] == ["Computer Science", "C and C++"]
    assert result["publication_date"] == "2020-09-01"
    assert result["publication_year"] == 2020
    assert result["ai_extracted_fields"] == []
    assert result["scraped_at"] == normalize.SENTINEL_SCRAPED_AT
    assert "about_the_book" not in result
    assert "categories" not in result


def test_from_v1_document_normalizes_playwright_na_isbn_and_raw_date():
    """
    [Unit] normalize from_v1_document: verifies a Playwright v1 record is normalized without hash drift.

    Scenario:
        Given a v1 Amazon document with a hyphenated ISBN-13 and a raw "September 24, 2022" date
        When normalize.from_v1_document is called
        Then the ISBN-13 is cleaned, the date becomes ISO "2022-09-24", the ASIN also populates
            ``source_id``, and the recomputed hash equals the stored v1 hash (stable)

    Boundaries:
        - Hash stability: title/authors/year unchanged so the hash must not drift
        - ASIN folded into both ``asin`` and ``source_id``

    On failure, first check:
        - Whether normalize_date or isbn.normalize changed output for these inputs
        - Whether hash_book's (title, authors, year) inputs drifted
    """
    v1 = _amazon_v1()
    result = normalize.from_v1_document(v1)

    assert validate_document(result) == []
    assert result["isbn13"] == "9780136816485"
    assert result["isbn10"] == "0136816487"
    assert result["publication_date"] == "2022-09-24"
    assert result["source_id"] == "0136816487"
    assert result["asin"] == "0136816487"
    assert result["hash"] == v1["hash"]


def test_from_v1_document_is_idempotent_on_v2_documents():
    """
    [Unit] normalize from_v1_document: verifies transforming an already-v2 document is a no-op.

    Scenario:
        Given a document already migrated to v2 (the output of a first from_v1_document call)
        When normalize.from_v1_document is called on it again
        Then the result is identical to the input (same fields, same recomputed hash)

    Boundaries:
        - Idempotency gate: a second migration pass must change nothing
        - scraped_at is preserved (NOT reset to the sentinel) for an already-v2 record

    On failure, first check:
        - The v2 passthrough branch (_from_v2_passthrough)
        - Whether a normalizer step is non-idempotent on already-clean input
    """
    once = normalize.from_v1_document(_amazon_v1())
    twice = normalize.from_v1_document(once)
    assert twice == once
