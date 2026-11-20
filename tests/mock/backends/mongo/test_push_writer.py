"""Mock tests for bookscraper.backends.mongo.push_writer (03.0/02) and export.iter_collection.

Every collection is the in-memory FakeCollection (tests/mock/backends/fake_collection.py);
no network, no real MongoDB.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import BulkWriteError

from bookscraper.backends.local.staging import compute_document_hash, prepare_for_push
from bookscraper.backends.mongo import push_writer
from bookscraper.backends.mongo.export import iter_collection, normalize_document
from bookscraper.backends.mongo.push_plan import build_push_plan
from bookscraper.backends.mongo.push_writer import (
    apply_push_plan,
    fetch_existing,
    oversized_documents,
    restore_bson_types,
)
from tests.mock.backends.fake_collection import FakeCollection


def _book(title: str, **extra) -> dict:
    doc = {"site": "amazon", "title": title, "authors": ["A"], "publication_year": 2020}
    doc.update(extra)
    doc["hash"] = compute_document_hash(doc)
    return doc


def _run(collection: FakeCollection, documents: list[dict]):
    """prepare → fetch_existing → plan → apply, exactly as commands/push.py does."""
    prepared = [prepare_for_push(d, i) for i, d in enumerate(documents)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes=existing.hashes, existing_by_id=existing.by_id)
    assert plan.errors == []
    return plan, apply_push_plan(collection, plan, prepared, existing)


def _pulled(collection: FakeCollection, object_id: ObjectId, **edits) -> dict:
    return {**normalize_document(collection.get(object_id)), **edits}


# --------------------------------------------------------------------------- #
# fetch_existing
# --------------------------------------------------------------------------- #
def test_fetch_existing_maps_hashes_and_ids() -> None:
    """
    [Unit] push writer: verifies fetch existing maps hashes and ids.

    Scenario:
        Given test inputs and fixtures for fetch existing maps hashes and ids
        When test_fetch_existing_maps_hashes_and_ids executes the target flow
        Then the expected outcome for fetch existing maps hashes and ids is confirmed.

    Boundaries:
        - Focus: fetch existing maps hashes and ids
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to fetch existing maps hashes and ids
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Live"))
    prepared = [prepare_for_push(_book("Live"), 0), prepare_for_push(_pulled(collection, oid), 1)]

    existing = fetch_existing(collection, prepared)

    assert existing.hashes == {_book("Live")["hash"]: str(oid)}
    assert set(existing.by_id) == {str(oid)} and existing.by_id[str(oid)]["_id"] == str(oid)
    assert existing.raw_by_id[str(oid)]["_id"] == oid


def test_in_queries_chunked(monkeypatch) -> None:
    """
    [Mock] push writer: verifies in queries chunked.

    Scenario:
        Given test inputs and fixtures for in queries chunked
        When test_in_queries_chunked executes the target flow
        Then the expected outcome for in queries chunked is confirmed.

    Boundaries:
        - Focus: in queries chunked
        - Fixtures/params: monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to in queries chunked
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    monkeypatch.setattr(push_writer, "IN_QUERY_CHUNK", 2)
    collection = FakeCollection()
    prepared = [prepare_for_push(_book(f"B{i}"), i) for i in range(5)]
    fetch_existing(collection, prepared)
    hash_queries = [q for q in collection.find_queries if "hash" in q]
    assert [len(q["hash"]["$in"]) for q in hash_queries] == [2, 2, 1]


# --------------------------------------------------------------------------- #
# apply_push_plan
# --------------------------------------------------------------------------- #
def test_inserts_and_replaces_in_one_bulk_write() -> None:
    """
    [Unit] push writer: verifies inserts and replaces in one bulk write.

    Scenario:
        Given test inputs and fixtures for inserts and replaces in one bulk write
        When test_inserts_and_replaces_in_one_bulk_write executes the target flow
        Then the expected outcome for inserts and replaces in one bulk write is confirmed.

    Boundaries:
        - Focus: inserts and replaces in one bulk write
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to inserts and replaces in one bulk write
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Old", description="old"))
    plan, outcomes = _run(collection, [_book("New"), _pulled(collection, oid, description="new")])

    assert [o.outcome for o in outcomes] == ["inserted", "updated"]
    assert collection.calls.count("bulk_write") == 1
    assert collection.get(oid)["description"] == "new"
    assert len(collection.documents) == 2
    assert "hash_unique_index" in collection.indexes  # unique index ensured before writing


def test_replace_removes_deleted_field() -> None:
    """
    [Unit] push writer: verifies replace removes deleted field.

    Scenario:
        Given test inputs and fixtures for replace removes deleted field
        When test_replace_removes_deleted_field executes the target flow
        Then the expected outcome for replace removes deleted field is confirmed.

    Boundaries:
        - Focus: replace removes deleted field
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to replace removes deleted field
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Book", isbn10="123"))
    edited = _pulled(collection, oid)
    del edited["isbn10"]
    _run(collection, [edited])
    assert "isbn10" not in collection.get(oid)


def test_replace_restores_datetime_types() -> None:
    """
    [Unit] push writer: verifies replace restores datetime types.

    Scenario:
        Given test inputs and fixtures for replace restores datetime types
        When test_replace_restores_datetime_types executes the target flow
        Then the expected outcome for replace restores datetime types is confirmed.

    Boundaries:
        - Focus: replace restores datetime types
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to replace restores datetime types
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Book", fetched_at=datetime(2026, 1, 2, 3, 4, 5), note="a"))
    _run(collection, [_pulled(collection, oid, note="b")])
    stored = collection.get(oid)
    assert isinstance(stored["fetched_at"], datetime) and stored["note"] == "b"


def test_unchanged_duplicate_and_skipped_make_no_write() -> None:
    """
    [Unit] push writer: verifies unchanged duplicate and skipped make no write.

    Scenario:
        Given test inputs and fixtures for unchanged duplicate and skipped make no write
        When test_unchanged_duplicate_and_skipped_make_no_write executes the target flow
        Then the expected outcome for unchanged duplicate and skipped make no write is confirmed.

    Boundaries:
        - Focus: unchanged duplicate and skipped make no write
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unchanged duplicate and skipped make no write
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection(read_only=False)
    oid = collection.seed(_book("Live"))
    plan, outcomes = _run(
        collection,
        [_pulled(collection, oid), _book("Live"), {**_book("Nope"), "_staging": {"status": "rejected"}}],
    )
    assert [o.outcome for o in outcomes] == ["unchanged", "duplicate", "skipped"]
    assert "bulk_write" not in collection.calls and "create_index" not in collection.calls


def test_no_write_when_plan_empty() -> None:
    """
    [Unit] push writer: verifies no write when plan empty.

    Scenario:
        Given test inputs and fixtures for no write when plan empty
        When test_no_write_when_plan_empty executes the target flow
        Then the expected outcome for no write when plan empty is confirmed.

    Boundaries:
        - Focus: no write when plan empty
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to no write when plan empty
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection(read_only=True)
    plan, outcomes = _run(collection, [])
    assert outcomes == [] and plan.items == []


def test_concurrent_duplicate_becomes_duplicate() -> None:
    """Mock: a hash inserted by someone else between planning and writing is a duplicate, not an error.

    Scenario: plan says insert; another writer inserts the same hash before bulk_write runs.
    Boundaries: FakeCollection enforces the unique hash index and raises BulkWriteError code 11000.
    On failure: a benign race would surface as an error and exit code 3.
    """
    collection = FakeCollection()
    doc = _book("Racy")
    prepared = [prepare_for_push(doc, 0), prepare_for_push(_book("Fine"), 1)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes=existing.hashes, existing_by_id=existing.by_id)
    collection.seed(doc)  # the concurrent writer

    outcomes = apply_push_plan(collection, plan, prepared, existing)

    assert [o.outcome for o in outcomes] == ["duplicate", "inserted"]


def test_bulk_write_error_mapped_to_items_without_server_message() -> None:
    """
    [Mock] push writer: verifies bulk write error mapped to items without server message.

    Scenario:
        Given test inputs and fixtures for bulk write error mapped to items without server message
        When test_bulk_write_error_mapped_to_items_without_server_message executes the target flow
        Then the expected outcome for bulk write error mapped to items without server message is confirmed.

    Boundaries:
        - Focus: bulk write error mapped to items without server message
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to bulk write error mapped to items without server message
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Live", description="x"))
    prepared = [prepare_for_push(_book("New"), 0), prepare_for_push(_pulled(collection, oid, description="y"), 1)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes=existing.hashes, existing_by_id=existing.by_id)
    collection.bulk_write = MagicMock(
        side_effect=BulkWriteError(
            {"writeErrors": [{"index": 1, "code": 121, "errmsg": "Document failed validation: SECRET"}], "nMatched": 0}
        )
    )
    collection.create_index = MagicMock()

    outcomes = apply_push_plan(collection, plan, prepared, existing)

    assert outcomes[0].outcome == "inserted"
    assert outcomes[1].outcome == "error" and outcomes[1].error == "BulkWriteError (code 121)"
    assert all("SECRET" not in (o.error or "") for o in outcomes)


def test_bulk_write_error_with_unmappable_index_is_ignored() -> None:
    """
    [Mock] push writer: verifies bulk write error with unmappable index is ignored.

    Scenario:
        Given test inputs and fixtures for bulk write error with unmappable index is ignored
        When test_bulk_write_error_with_unmappable_index_is_ignored executes the target flow
        Then the expected outcome for bulk write error with unmappable index is ignored is confirmed.

    Boundaries:
        - Focus: bulk write error with unmappable index is ignored
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to bulk write error with unmappable index is ignored
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    prepared = [prepare_for_push(_book("New"), 0)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes={}, existing_by_id={})
    collection.bulk_write = MagicMock(side_effect=BulkWriteError({"writeErrors": [{"index": 9, "code": 1}]}))
    collection.create_index = MagicMock()
    assert [o.outcome for o in apply_push_plan(collection, plan, prepared, existing)] == ["inserted"]


def test_connection_lost_mid_write_marks_every_write_unknown() -> None:
    """
    [Mock] push writer: verifies connection lost mid write marks every write unknown.

    Scenario:
        Given test inputs and fixtures for connection lost mid write marks every write unknown
        When test_connection_lost_mid_write_marks_every_write_unknown executes the target flow
        Then the expected outcome for connection lost mid write marks every write unknown is confirmed.

    Boundaries:
        - Focus: connection lost mid write marks every write unknown
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to connection lost mid write marks every write unknown
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    prepared = [prepare_for_push(_book("A"), 0), prepare_for_push(_book("B"), 1)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes={}, existing_by_id={})
    collection.bulk_write = MagicMock(side_effect=ConnectionError("mongodb+srv://user:pw@host"))
    collection.create_index = MagicMock()

    outcomes = apply_push_plan(collection, plan, prepared, existing)

    assert {o.outcome for o in outcomes} == {"error"}
    assert all("ConnectionError" in o.error and "pw@" not in o.error for o in outcomes)


def test_update_whose_document_vanished_is_error() -> None:
    """
    [Unit] push writer: verifies update whose document vanished is error.

    Scenario:
        Given test inputs and fixtures for update whose document vanished is error
        When test_update_whose_document_vanished_is_error executes the target flow
        Then the expected outcome for update whose document vanished is error is confirmed.

    Boundaries:
        - Focus: update whose document vanished is error
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to update whose document vanished is error
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Doomed", description="x"))
    prepared = [prepare_for_push(_pulled(collection, oid, description="y"), 0)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(prepared, existing_hashes=existing.hashes, existing_by_id=existing.by_id)
    collection.documents.clear()  # deleted by someone else after fetch_existing

    outcomes = apply_push_plan(collection, plan, prepared, existing)

    assert outcomes[0].outcome == "error" and "deleted" in outcomes[0].error


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_restore_bson_types_only_where_live_value_was_datetime() -> None:
    """
    [Unit] push writer: verifies restore bson types only where live value was datetime.

    Scenario:
        Given test inputs and fixtures for restore bson types only where live value was datetime
        When test_restore_bson_types_only_where_live_value_was_datetime executes the target flow
        Then the expected outcome for restore bson types only where live value was datetime is confirmed.

    Boundaries:
        - Focus: restore bson types only where live value was datetime
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to restore bson types only where live value was datetime
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    live = {"when": datetime(2026, 1, 1), "text": "2026-01-01T00:00:00Z", "nested": {"at": datetime(2026, 2, 2)}}
    staged = {"when": "2026-03-03T00:00:00Z", "text": "2026-01-01T00:00:00Z", "nested": {"at": "bad-date"}, "new": 1}
    restored = restore_bson_types(staged, live)
    assert restored["when"] == datetime.fromisoformat("2026-03-03T00:00:00+00:00")
    assert restored["text"] == "2026-01-01T00:00:00Z"  # live value is a string, so it stays one
    assert restored["nested"]["at"] == "bad-date"  # unparseable stays as typed
    assert restored["new"] == 1


def test_oversized_documents_uses_bson_size(monkeypatch) -> None:
    """
    [Mock] push writer: verifies oversized documents uses bson size.

    Scenario:
        Given test inputs and fixtures for oversized documents uses bson size
        When test_oversized_documents_uses_bson_size executes the target flow
        Then the expected outcome for oversized documents uses bson size is confirmed.

    Boundaries:
        - Focus: oversized documents uses bson size
        - Fixtures/params: monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to oversized documents uses bson size
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    monkeypatch.setattr(push_writer, "MAX_BSON_BYTES", 200)
    prepared = [prepare_for_push(_book("Small"), 0), prepare_for_push(_book("Big", description="x" * 200), 1)]
    problems = oversized_documents(prepared)
    assert len(problems) == 1 and problems[0].startswith("document #1:") and "limit is 200" in problems[0]


@pytest.mark.parametrize("batch_size", [500, 2])
def test_iter_collection_sorted_by_id_in_batches(batch_size: int) -> None:
    """
    [Unit] push writer: verifies iter collection sorted by id in batches.

    Scenario:
        Given test inputs and fixtures for iter collection sorted by id in batches
        When test_iter_collection_sorted_by_id_in_batches executes the target flow
        Then the expected outcome for iter collection sorted by id in batches is confirmed.

    Boundaries:
        - Focus: iter collection sorted by id in batches
        - Fixtures/params: batch_size (parametrized, 2 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to iter collection sorted by id in batches
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    collection = FakeCollection(read_only=True)
    ids = [ObjectId(f"6744e7c2a1b2c3d4e5f6071{n}") for n in (3, 1, 2)]
    for oid in ids:
        collection.seed({"_id": oid, "title": str(oid)})

    exported = list(iter_collection(collection, batch_size=batch_size))

    assert [d["_id"] for d in exported] == sorted(ids)
    assert collection.last_cursor.batch_sizes == [batch_size]


def test_iter_empty_collection_yields_nothing() -> None:
    """
    [Unit] push writer: verifies iter empty collection yields nothing.

    Scenario:
        Given test inputs and fixtures for iter empty collection yields nothing
        When test_iter_empty_collection_yields_nothing executes the target flow
        Then the expected outcome for iter empty collection yields nothing is confirmed.

    Boundaries:
        - Focus: iter empty collection yields nothing
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to iter empty collection yields nothing
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push writer
    """
    assert list(iter_collection(FakeCollection(read_only=True))) == []
