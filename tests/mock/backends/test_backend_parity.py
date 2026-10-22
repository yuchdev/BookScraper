"""Backend parity contract tests: one scenario list run against BOTH storage backends.

"Local storage matches database content one-to-one" is the central promise of the
``json`` backend (CLAUDE.md, *Storage backend*). Each backend already has its own
isolated tests, so nothing there fails when the two silently drift apart - e.g. if one
starts treating an empty ``slug`` as a match, or stores a book the other rejects. This
module runs the *same* assertions against both, so any such divergence surfaces as a
failure on exactly one parameter.

The ``mongo`` parameter is backed by an in-memory ``mongomock`` collection with the real
``database.ensure_indexes`` applied (so the unique ``hash`` index enforces duplicate
rejection with code 11000, exactly as Atlas would); the ``json`` parameter is a real
``JsonBackend`` writing to a ``tmp_path`` store via the ``isolated_local_store`` fixture,
so no test ever touches the real ``~/.bookscrapper`` or repo-root ``books.json``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Union

import mongomock
import pytest

from bookscraper.backends.local import store
from bookscraper.backends.mongo import database
from bookscraper.backends.storage import JsonBackend, MongoBackend, SaveSummary
from bookscraper.models import SCHEMA_VERSION

Backend = Union[JsonBackend, MongoBackend]


def _hash(seed: str) -> str:
    """A deterministic 64-char lowercase-hex digest so test documents pass the save boundary."""
    return hashlib.sha256(seed.encode()).hexdigest()


def _doc(**overrides) -> dict:
    """A minimal but save-boundary-valid schema-2 document, with per-test field overrides.

    The save boundary (subtask 05) now rejects anything that is not a full, well-typed schema-2
    document, so parity scenarios build from a valid baseline and override only the fields under
    test (``hash``, ``asin``, ``source_id``, ``slug``, ``title`` ...).

    :param overrides: Field values to replace on the baseline document.
    :returns: A valid schema-2 document carrying the overrides.
    """
    base = {
        "schema_version": SCHEMA_VERSION,
        "site": "leanpub",
        "url": "https://leanpub.com/book",
        "source_id": None,
        "slug": None,
        "asin": None,
        "title": "A Book",
        "authors": [],
        "isbn10": None,
        "isbn13": None,
        "publication_date": None,
        "publication_year": None,
        "description": None,
        "tags": [],
        "ai_extracted_fields": [],
        "scraped_at": "2026-10-05T12:00:00Z",
        "hash": _hash("default"),
    }
    base.update(overrides)
    return base


def _make_mongo_backend() -> MongoBackend:
    """A MongoBackend over a fresh in-memory collection with production indexes."""
    collection = mongomock.MongoClient().db.books
    database.ensure_indexes(collection)
    return MongoBackend(collection)


def _stored_docs(backend: Backend) -> list[dict]:
    """
    The documents a backend has actually persisted, with Mongo's synthetic ``_id``
    stripped so the two backends' stored content can be compared field-for-field.
    """
    if isinstance(backend, JsonBackend):
        return [b for b in store._load_books(store.LOCAL_STORE_FILE) if isinstance(b, dict)]
    docs = list(backend._collection.find({}))
    for doc in docs:
        doc.pop("_id", None)
    return docs


def _canonical_set(docs: list[dict]) -> set[str]:
    """Docs as a set of canonical (sorted-key) JSON strings, order-independent."""
    return {json.dumps(doc, sort_keys=True) for doc in docs}


@pytest.fixture(params=["json", "mongo"])
def backend(request: pytest.FixtureRequest, isolated_local_store) -> Backend:
    """A fresh, empty storage backend for each parameter.

    ``isolated_local_store`` is requested unconditionally so the ``json`` parameter's
    file writes land under ``tmp_path``; it is inert for the ``mongo`` parameter.
    """
    if request.param == "json":
        return JsonBackend()
    return _make_mongo_backend()


def test_saved_book_found_by_hash(backend: Backend):
    """
    [Local] storage backend parity: verifies saved book found by hash.

    Scenario:
        a saved book is found by its hash; an unknown hash and an empty hash are not.

    Boundaries:
        - exactly one stored book, queried by its own hash, a miss, and "".

    On failure, first check:
        - a genuine duplicate slips past ``book_exists_by_hash`` (re-scraped/re-saved forever), or an empty hash is treated as a match and blocks every new book.
    """
    only = _hash("only")
    summary = backend.save_books([_doc(hash=only, title="Only Book")])

    assert summary == SaveSummary(inserted=1, duplicates=0, errors=0)
    assert backend.book_exists_by_hash(only) is True
    assert backend.book_exists_by_hash(_hash("unknown")) is False
    assert backend.book_exists_by_hash("") is False


def test_duplicate_hash_counted_once(backend: Backend):
    """
    [Local] storage backend parity: verifies duplicate hash counted once.

    Scenario:
        the same hash saved twice - once within a single batch, once across two calls - is counted as exactly one duplicate each time and stored only once.

    Boundaries:
        - a two-doc batch sharing a hash, then a third call repeating that hash.

    On failure, first check:
        - a duplicate is silently inserted twice (corpus corruption) or miscounted, so the run's "N duplicates" figure no longer matches what the store actually rejected.
    """
    dup = _hash("dup")
    within_batch = backend.save_books([_doc(hash=dup, title="First"), _doc(hash=dup, title="Second")])
    assert within_batch == SaveSummary(inserted=1, duplicates=1, errors=0)

    across_calls = backend.save_books([_doc(hash=dup, title="Third")])
    assert across_calls == SaveSummary(inserted=0, duplicates=1, errors=0)

    assert len(_stored_docs(backend)) == 1


def test_book_without_hash_is_error(backend: Backend):
    """
    [Local] storage backend parity: verifies book without hash is error.

    Scenario:
        a book carrying no ``hash`` is counted as an error and never stored, on both backends - a hash-less document cannot be de-duplicated (Mongo's unique index would index it as null and silently accept it), so it must be rejected up front.

    Boundaries:
        - a single hash-less document with a live/empty store.

    On failure, first check:
        - one backend quietly stores an un-dedupable book, so the corpus accumulates duplicates that the next run's hash check can never catch.
    """
    summary = backend.save_books([{"title": "No Hash Here", "asin": "B999"}])

    assert summary == SaveSummary(inserted=0, duplicates=0, errors=1)
    assert _stored_docs(backend) == []
    assert backend.book_exists_by_asin("B999") is False


def test_asin_lookup(backend: Backend):
    """
    [Local] storage backend parity: verifies asin lookup.

    Scenario:
        ``book_exists_by_asin`` is true for a stored ASIN, false for an unknown one, and false for an empty string.

    Boundaries:
        - one stored book, queried by its ASIN, a miss, and "".

    On failure, first check:
        - Amazon dedup either re-scrapes known ASINs or treats "" as a universal match that blocks every new Amazon book.
    """
    backend.save_books(
        [
            _doc(
                hash=_hash("amz"),
                site="amazon",
                url="https://www.amazon.com/dp/B001",
                asin="B001",
                source_id="B001",
                title="Amazon Book",
            )
        ]
    )

    assert backend.book_exists_by_asin("B001") is True
    assert backend.book_exists_by_asin("B-nope") is False
    assert backend.book_exists_by_asin("") is False


def test_book_exists_by_url_parity(backend: Backend):
    """
    [Local] storage backend parity: verifies book_exists_by_url behaves identically on both backends.

    Scenario:
        ``book_exists_by_url`` is true for a stored book's detail URL, false for an unknown URL, and false for an empty string - the URL-keyed dedup a future provider needs for a site with no native id or ASIN (task 03.3 subtask 03).

    Boundaries:
        - one stored book queried by its own url, a miss, and ""; json goes through the in-memory url index, mongo through the sparse url_lookup_index.

    On failure, first check:
        - one backend's url lookup reads a field the other never indexes, so a URL-keyed provider re-scrapes known books on one backend but not the other, or "" is treated as a universal match that blocks every new book.
    """
    backend.save_books(
        [
            _doc(
                hash=_hash("url"),
                site="packtpub",
                url="https://www.packtpub.com/product/some-book/9781999999999",
                title="A URL-keyed Book",
            )
        ]
    )

    assert backend.book_exists_by_url("https://www.packtpub.com/product/some-book/9781999999999") is True
    assert backend.book_exists_by_url("https://www.packtpub.com/product/other/9780000000000") is False
    assert backend.book_exists_by_url("") is False


def test_leanpub_lookup_by_id_or_slug(backend: Backend):
    """
    [Local] storage backend parity: verifies leanpub lookup by id or slug.

    Scenario:
        ``leanpub_book_exists`` matches on book_id alone, on slug alone, and is false when neither identifier matches and when both are absent.

    Boundaries:
        - one stored Leanpub book; queried by id-only, slug-only, a double miss, and (None, None).

    On failure, first check:
        - half of the id-OR-slug match goes dead (the exact bug CLAUDE.md warns of) and Leanpub dedup silently degrades to a single field.
    """
    backend.save_books(
        [
            _doc(
                hash=_hash("lp"),
                source_id="42",
                slug="the-book",
                url="https://leanpub.com/the-book",
            )
        ]
    )

    assert backend.leanpub_book_exists("42", None) is True
    assert backend.leanpub_book_exists(None, "the-book") is True
    assert backend.leanpub_book_exists("99", "other-slug") is False
    assert backend.leanpub_book_exists(None, None) is False


def test_leanpub_lookup_ignores_non_string_identifiers(backend: Backend):
    """
    [Local] storage backend parity: verifies both backends ignore dict-valued Leanpub identifiers identically.

    Scenario:
        Given one stored Leanpub book in either backend
        When leanpub_book_exists is called with dicts such as {"$ne": None} as id and slug
        Then both backends return False without raising and never treat the dict as a query.

    Boundaries:
        - CWE-943 guard; json goes through the in-memory index and mongo through deduplicate

    On failure, first check:
        - The isinstance(str) guard in JsonBackend.leanpub_book_exists and in deduplicate.leanpub_prescrape_deduplicate
    """
    backend.save_books([_doc(hash=_hash("lp"), source_id="42", slug="the-book", url="https://leanpub.com/the-book")])

    assert backend.leanpub_book_exists({"$ne": None}, {"$gt": ""}) is False
    assert backend.leanpub_book_exists({"$ne": None}, "the-book") is True


def test_stored_documents_identical_across_backends(isolated_local_store):
    """
    [Local] storage backend parity: verifies stored documents identical across backends.

    Scenario:
        saving the same batch to a fresh json store and a fresh mongo collection yields byte-for-byte identical stored content (minus Mongo's synthetic ``_id``), compared as order-independent sets of canonical JSON - the "one-to-one" promise.

    Boundaries:
        - a mixed batch with a duplicate hash and a hash-less error, so inserted / duplicate / error handling must agree on *which* documents survive, not just the counts.

    On failure, first check:
        - the two backends persist different corpora from the same scrape, so a run's output silently depends on which ``--store-backend`` was chosen.
    """
    h1 = _hash("h1")
    h2 = _hash("h2")
    batch = [
        _doc(
            hash=h1,
            site="amazon",
            url="https://www.amazon.com/dp/B001",
            title="Alpha",
            asin="B001",
            source_id="B001",
            authors=["A", "B"],
            publication_year=2021,
        ),
        _doc(hash=h2, title="Beta", source_id="7", slug="beta", url="https://leanpub.com/beta"),
        _doc(hash=h1, title="Alpha duplicate"),  # duplicate hash -> dropped on both
        {"title": "Ghost", "asin": "B999"},  # invalid (no hash/keys) -> error on both, stored on neither
    ]

    json_backend = JsonBackend()
    mongo_backend = _make_mongo_backend()

    json_summary = json_backend.save_books(batch)
    mongo_summary = mongo_backend.save_books(batch)

    assert json_summary == mongo_summary == SaveSummary(inserted=2, duplicates=1, errors=1)
    assert _canonical_set(_stored_docs(json_backend)) == _canonical_set(_stored_docs(mongo_backend))


def test_invalid_document_not_saved_and_counted_as_error(backend: Backend):
    """
    [Local] storage backend parity: verifies an invalid document is rejected, counted, and never stored.

    Scenario:
        a batch pairs one valid canonical document with one that fails validate_document (an
        out-of-domain ``site`` plus a non-hex ``hash``); on both backends the valid one is
        inserted and the invalid one is counted as an error and never written.

    Boundaries:
        - a two-doc batch, one valid and one invalid, against a fresh store.

    On failure, first check:
        - a document failing validate_document reaches the store on one backend (the save-boundary gate is bypassed), or the rejection is miscounted as something other than an error.
    """
    good = _hash("good")
    batch = [
        _doc(hash=good, title="Valid Book"),
        _doc(site="not-a-site", hash="not-a-hex-hash"),  # two validation problems
    ]

    summary = backend.save_books(batch)

    assert summary == SaveSummary(inserted=1, duplicates=0, errors=1)
    stored = _stored_docs(backend)
    assert len(stored) == 1
    assert stored[0]["hash"] == good
