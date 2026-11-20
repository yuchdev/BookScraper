"""Unit tests for bookscraper.backends.mongo.export's BSON → staging-file mapping (04.0/01).

Pure conversions on hand-built documents; no collection, no network.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from bson import Binary, Decimal128, ObjectId

from bookscraper.backends.mongo.export import (
    ExportError,
    datetime_to_iso,
    normalize_document,
    to_staging_document,
)

OID = ObjectId("6744e7c2a1b2c3d4e5f60718")


def test_object_id_to_hex() -> None:
    """
    [Unit] export: verifies object id to hex.

    Scenario:
        Given test inputs and fixtures for object id to hex
        When test_object_id_to_hex executes the target flow
        Then the expected outcome for object id to hex is confirmed.

    Boundaries:
        - Focus: object id to hex
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to object id to hex
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    assert normalize_document({"_id": OID, "title": "T"}) == {"_id": "6744e7c2a1b2c3d4e5f60718", "title": "T"}


def test_datetime_to_iso_z() -> None:
    """
    [Unit] export: verifies datetime to iso z.

    Scenario:
        Given test inputs and fixtures for datetime to iso z
        When test_datetime_to_iso_z executes the target flow
        Then the expected outcome for datetime to iso z is confirmed.

    Boundaries:
        - Focus: datetime to iso z
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to datetime to iso z
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    assert datetime_to_iso(datetime(2026, 11, 19, 10, 15, 3)) == "2026-11-19T10:15:03Z"  # naive = UTC
    assert datetime_to_iso(datetime(2026, 11, 19, 10, 15, 3, 250000)) == "2026-11-19T10:15:03.250Z"
    plus_two = datetime(2026, 11, 19, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert datetime_to_iso(plus_two) == "2026-11-19T10:00:00Z"


def test_nested_values_converted_recursively() -> None:
    """
    [Unit] export: verifies nested values converted recursively.

    Scenario:
        Given test inputs and fixtures for nested values converted recursively
        When test_nested_values_converted_recursively executes the target flow
        Then the expected outcome for nested values converted recursively is confirmed.

    Boundaries:
        - Focus: nested values converted recursively
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to nested values converted recursively
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    raw = {
        "_id": OID,
        "meta": {"fetched": datetime(2026, 1, 2), "tags": ["a", {"when": datetime(2026, 1, 3)}]},
        "n": 3,
        "f": 1.5,
        "ok": True,
        "none": None,
    }
    assert normalize_document(raw) == {
        "_id": str(OID),
        "meta": {"fetched": "2026-01-02T00:00:00Z", "tags": ["a", {"when": "2026-01-03T00:00:00Z"}]},
        "n": 3,
        "f": 1.5,
        "ok": True,
        "none": None,
    }


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({"price": Decimal128(Decimal("9.99"))}, "price"),
        ({"cover": Binary(b"\x00")}, "cover"),
        ({"related": [OID]}, "related[0]"),
        ({"meta": {"ref": OID}}, "meta.ref"),
    ],
)
def test_unsupported_bson_type_raises_with_path(value, path) -> None:
    """
    [Unit] export: verifies unsupported bson type raises with path.

    Scenario:
        Given test inputs and fixtures for unsupported bson type raises with path
        When test_unsupported_bson_type_raises_with_path executes the target flow
        Then the expected outcome for unsupported bson type raises with path is confirmed.

    Boundaries:
        - Focus: unsupported bson type raises with path
        - Fixtures/params: value, path (parametrized, 4 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unsupported bson type raises with path
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    with pytest.raises(ExportError, match=rf"Field '{path.replace('[', r'\[').replace(']', r'\]')}'"):
        normalize_document({"_id": OID, **value})


def test_non_object_id_identity_rejected() -> None:
    """
    [Unit] export: verifies non object id identity rejected.

    Scenario:
        Given test inputs and fixtures for non object id identity rejected
        When test_non_object_id_identity_rejected executes the target flow
        Then the expected outcome for non object id identity rejected is confirmed.

    Boundaries:
        - Focus: non object id identity rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to non object id identity rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    with pytest.raises(ExportError, match="only ObjectId"):
        normalize_document({"_id": "custom-string-id", "title": "T"})


def test_staging_meta_stamped() -> None:
    """
    [Unit] export: verifies staging meta stamped.

    Scenario:
        Given test inputs and fixtures for staging meta stamped
        When test_staging_meta_stamped executes the target flow
        Then the expected outcome for staging meta stamped is confirmed.

    Boundaries:
        - Focus: staging meta stamped
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to staging meta stamped
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by export
    """
    doc = to_staging_document({"_id": OID, "title": "T"})
    assert doc["_staging"] == {"status": "pending", "source": "pull"}
    assert doc["_id"] == str(OID)
