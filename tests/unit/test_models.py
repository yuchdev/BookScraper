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

import pytest

from bookscraper.models import (
    MAX_DESCRIPTION_LEN,
    MAX_TAG_LEN,
    MAX_TAGS,
    MAX_TITLE_LEN,
    SCHEMA_VERSION,
    BookRecord,
    Site,
    validate_document,
)


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


def test_validate_document_accepts_canonical_record():
    """
    [Unit] validate_document: a canonical to_document() output has no problems.

    Scenario:
        Given the documents of a fully populated and a sparse (all-optional-null) BookRecord
        When each is passed to validate_document
        Then both return an empty problem list.

    Boundaries:
        - Pure Python; the two records span every optional field present and absent
        - Proves the validator accepts exactly what the normalizers emit (no false positives)

    On failure, first check:
        - Whether a required-key, type, or regex rule is stricter than to_document's output
    """
    assert validate_document(_full_record().to_document()) == []
    assert validate_document(_sparse_record().to_document()) == []


def test_validate_document_reports_each_missing_required_key():
    """
    [Unit] validate_document: every absent schema-2 key is reported individually.

    Scenario:
        Given an empty dict (no schema-2 keys at all)
        When validate_document is called
        Then there is a distinct "missing required key: <k>" problem for all seventeen keys.

    Boundaries:
        - Pure Python; the empty dict is the maximal missing-key case
        - Confirms the validator does not stop at the first missing key

    On failure, first check:
        - Whether _REQUIRED_KEYS drifted from BookRecord's field set, or an early return truncates
    """
    problems = validate_document({})
    missing = {p.removeprefix("missing required key: ") for p in problems if p.startswith("missing required key: ")}
    assert missing == {
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
    }


def test_validate_document_rejects_bad_hash_and_bad_isbn():
    """
    [Unit] validate_document: a non-hex hash and checksum-invalid ISBNs are each reported.

    Scenario:
        Given a canonical document mutated with an uppercase/short hash and invalid ISBN-10/13
        When validate_document is called
        Then it reports the bad hash and both bad ISBNs (and still has no missing-key problems).

    Boundaries:
        - Pure Python; the ISBNs are well-typed strings that merely fail the checksum
        - Uppercase hex is rejected because the store key is lowercase-only

    On failure, first check:
        - Whether the hash regex allows uppercase/short digests, or the ISBN checksum call is skipped
    """
    document = _full_record().to_document()
    document["hash"] = "DEADBEEF"  # uppercase and far too short
    document["isbn10"] = "1234567890"  # well-formed length, invalid checksum
    document["isbn13"] = "9780000000000"  # well-formed length/prefix, invalid checksum

    problems = validate_document(document)

    assert "hash must be 64 lowercase hexadecimal characters" in problems
    assert "isbn10 is not a checksum-valid ISBN-10" in problems
    assert "isbn13 is not a checksum-valid ISBN-13" in problems
    assert not any(p.startswith("missing required key") for p in problems)


def test_validate_document_rejects_malformed_publication_date():
    """
    [Unit] validate_document: a non-ISO publication_date string is reported.

    Scenario:
        Given a canonical document whose publication_date is "24-09-2022" (wrong order, not ISO)
        When validate_document is called
        Then the ISO-shape problem is reported.

    Boundaries:
        - Pure Python; the value is a well-typed string that fails only the lexical shape
        - null and valid ISO precisions (YYYY / YYYY-MM / YYYY-MM-DD) are accepted elsewhere

    On failure, first check:
        - Whether _PUBLICATION_DATE_RE was loosened or the fullmatch became a search
    """
    document = _full_record().to_document()
    document["publication_date"] = "24-09-2022"
    assert "publication_date must be ISO YYYY, YYYY-MM, or YYYY-MM-DD" in validate_document(document)


def test_validate_document_rejects_wrong_schema_version_and_hostname_site():
    """
    [Unit] validate_document: a wrong schema_version and a hostname-style site are reported.

    Scenario:
        Given a canonical document with schema_version bumped and site set to "leanpub.com"
        When validate_document is called
        Then it reports both the version mismatch and the out-of-domain site.

    Boundaries:
        - Pure Python; site is a string, so the failure is the value domain, not the type
        - Guards the runtime site-domain check the Literal cannot enforce on a raw document

    On failure, first check:
        - Whether _SITE_VALUES drifted from the Site literal, or the version equality check was dropped
    """
    document = _full_record().to_document()
    document["schema_version"] = SCHEMA_VERSION + 1
    document["site"] = "leanpub.com"

    problems = validate_document(document)

    assert f"schema_version must equal {SCHEMA_VERSION}" in problems
    assert any(p.startswith("site must be one of") for p in problems)


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("title", "x" * (MAX_TITLE_LEN + 1), f"title exceeds {MAX_TITLE_LEN} characters"),
        ("description", "y" * (MAX_DESCRIPTION_LEN + 1), f"description exceeds {MAX_DESCRIPTION_LEN} characters"),
        ("authors", ["ok", "a" * 501], "authors entry exceeds 500 characters"),
        ("tags", ["t" * (MAX_TAG_LEN + 1)], f"tags entry exceeds {MAX_TAG_LEN} characters"),
        ("tags", [f"tag-{i}" for i in range(MAX_TAGS + 1)], f"tags exceeds {MAX_TAGS} entries"),
        ("url", "https://example.com/" + "p" * 2000, "url exceeds 2000 characters"),
    ],
)
def test_validate_document_rejects_oversized_text(field: str, value: object, fragment: str):
    """
    [Unit] validate_document: page-controlled text past its MAX_* cap is rejected, not stored.

    Scenario:
        Given a canonical document whose one text field (title/description/author/tag/url or the
            tag count) is pushed one past its documented cap
        When validate_document is called
        Then the matching size-limit problem is reported.

    Boundaries:
        - Pure Python; each case exceeds its bound by the smallest possible margin
        - The caps are the 03.0 security-review bounds on untrusted scraped text

    On failure, first check:
        - Whether the MAX_* constant changed, or a cap is checked against the wrong field
    """
    document = _full_record().to_document()
    document[field] = value
    assert fragment in validate_document(document)


def test_validate_document_rejects_non_mapping():
    """
    [Unit] validate_document: a non-dict input is reported, never raised on.

    Scenario:
        Given a value that is not a dict (a list)
        When validate_document is called
        Then it returns a single "document is not a mapping" problem without raising.

    Boundaries:
        - Pure Python; the validator is a pure reporter and must tolerate any input shape
        - A non-dict reaching the save boundary becomes one error, not a crash

    On failure, first check:
        - Whether the isinstance guard at the top of validate_document was removed
    """
    assert validate_document(["not", "a", "dict"]) == ["document is not a mapping"]


def test_validate_document_reports_wrong_field_types():
    """
    [Unit] validate_document: a document present-but-wrong-typed on every field is reported per field.

    Scenario:
        Given a document carrying all seventeen keys but with each value the wrong type
            (ints for strings, strings for ints/lists, a non-string list element)
        When validate_document is called
        Then every field's type problem is reported and no key is flagged as missing.

    Boundaries:
        - Pure Python; exercises the type-mismatch branch of each field, distinct from the
          value-domain and size-cap branches covered elsewhere
        - A bool is rejected for the integer fields (bool is an int subclass)

    On failure, first check:
        - Whether a field's isinstance guard was dropped, letting a wrong-typed value reach the store
    """
    document = {
        "schema_version": True,  # bool rejected for an int field
        "site": 123,
        "url": 1,
        "source_id": 2,
        "slug": 3,
        "asin": 4,
        "title": 5,
        "authors": "not-a-list",
        "isbn10": 6,
        "isbn13": 7,
        "publication_date": 8,
        "publication_year": "2021",
        "description": 9,
        "tags": [123],  # non-string element
        "ai_extracted_fields": 10,
        "scraped_at": 11,
        "hash": 12,
    }

    problems = validate_document(document)

    assert not any(p.startswith("missing required key") for p in problems)
    for fragment in (
        "schema_version must be an integer",
        "site must be a string",
        "url must be a string",
        "title must be a string",
        "scraped_at must be a string",
        "hash must be a string",
        "source_id must be a string or null",
        "slug must be a string or null",
        "asin must be a string or null",
        "isbn10 must be a string or null",
        "isbn13 must be a string or null",
        "publication_date must be a string or null",
        "publication_year must be an integer or null",
        "description must be a string or null",
        "authors must be a list",
        "tags entries must be strings",
        "ai_extracted_fields must be a list",
    ):
        assert fragment in problems
