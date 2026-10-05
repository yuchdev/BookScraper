"""Unit tests for bookscraper.models.BookRecord (subtask 03.1/01).

BookRecord is the single document shape both backends store, so its
to_document / from_document pair is the contract every normalizer (subtask 04),
the save boundary (subtask 05), and the migration (subtask 06) build on. These
tests pin that contract: a lossless round trip, JSON-serializability, explicit
nulls for absent optionals (never a dropped key or an "N/A" sentinel), and the
site field's bare-key literal domain.
"""

from __future__ import annotations

import json
from typing import get_args

from bookscraper.models import SCHEMA_VERSION, BookRecord, Site


def _full_record() -> BookRecord:
    """Build a fully populated BookRecord for the round-trip and serialization tests.

    :returns: A BookRecord with every optional field set to a non-null value.
    """
    return BookRecord(
        schema_version=SCHEMA_VERSION,
        site="amazon",
        url="https://www.amazon.com/dp/0136816487",
        source_id="0136816487",
        slug=None,
        asin="0136816487",
        title="A Tour of C++",
        authors=["Bjarne Stroustrup"],
        isbn10="0136816487",
        isbn13="9780136816485",
        publication_date="2022-09-24",
        publication_year=2022,
        description="A concise introduction to C++.",
        tags=["C++", "Programming"],
        ai_extracted_fields=[],
        scraped_at="2026-10-05T12:00:00Z",
        hash="1b93bf19f83335db49b6af3fc2700565f80d42e1dabb44834b2c55a9ff4ad648",
    )


def _sparse_record() -> BookRecord:
    """Build a BookRecord with every optional field left as None/empty.

    :returns: A BookRecord exercising the absent-data path of to_document.
    """
    return BookRecord(
        schema_version=SCHEMA_VERSION,
        site="leanpub",
        url="https://leanpub.com/cpplambda",
        source_id="102",
        slug="cpplambda",
        asin=None,
        title="C++ Lambda Story",
        authors=[],
        isbn10=None,
        isbn13=None,
        publication_date=None,
        publication_year=None,
        description=None,
        tags=[],
        ai_extracted_fields=[],
        scraped_at="2026-10-05T12:00:00Z",
        hash="3b1beb4b6d58060b587c648b566e9af155241f7c6405a9bfa8c3249f2cff26c5",
    )


def test_to_document_round_trips_through_from_document():
    """
    [Unit] BookRecord: from_document(to_document(r)) reconstructs an equal record.

    Scenario:
        Given a fully populated BookRecord r
        When its document is produced and fed back through from_document
        Then the rebuilt record equals r field-for-field.

    Boundaries:
        - Pure Python; no I/O, no mocking
        - Equality covers every field via the frozen dataclass __eq__

    On failure, first check:
        - Whether a field was added/renamed in only one of to_document / from_document
    """
    record = _full_record()
    rebuilt = BookRecord.from_document(record.to_document())
    assert rebuilt == record


def test_to_document_is_json_serializable():
    """
    [Unit] BookRecord: to_document() output survives a json.dumps/loads round trip unchanged.

    Scenario:
        Given a fully populated BookRecord
        When its document is serialized with json.dumps and parsed back
        Then the parsed dict equals the original document.

    Boundaries:
        - Pure Python; exercises json only, not from_document
        - Proves every value is a JSON-native type (str, int, None, list[str])

    On failure, first check:
        - Whether a non-JSON-native value (e.g. a datetime) leaked into a field
    """
    document = _full_record().to_document()
    assert json.loads(json.dumps(document)) == document


def test_optional_fields_serialize_as_explicit_null():
    """
    [Unit] BookRecord: absent optional data is an explicit null key, never a dropped key.

    Scenario:
        Given a BookRecord with every optional field None/empty
        When to_document() is produced
        Then each optional key is present with value None (and lists are []), and a
            round trip through from_document reconstructs the record.

    Boundaries:
        - Pure Python; verifies key presence, not just equality
        - Guards the "uniform document, no N/A sentinel" invariant

    On failure, first check:
        - Whether to_document started dropping None-valued keys or emitting a sentinel
    """
    record = _sparse_record()
    document = record.to_document()
    for key in ("asin", "isbn10", "isbn13", "publication_date", "publication_year", "description"):
        assert key in document
        assert document[key] is None
    assert document["authors"] == []
    assert document["tags"] == []
    assert document["ai_extracted_fields"] == []
    assert "N/A" not in document.values()
    assert BookRecord.from_document(document) == record


def test_site_literal_rejects_hostname_values():
    """
    [Unit] BookRecord: the Site literal admits only the four bare site keys, not a hostname.

    Scenario:
        Given the Site literal's declared members
        When they are inspected via typing.get_args
        Then they are exactly the four bare keys and no hostname (e.g. "leanpub.com") is admitted.

    Boundaries:
        - A type-check-level test; the runtime value domain is also enforced in subtask 05
        - Inspects the Literal's args rather than attempting a rejected construction

    On failure, first check:
        - Whether a hostname-style value was added to the Site literal in models.py
    """
    members = set(get_args(Site))
    assert members == {"amazon", "packtpub", "leanpub", "oreilly"}
    assert "leanpub.com" not in members
