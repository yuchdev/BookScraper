"""Mock-tier round-trip fidelity tests for the pull -> push pipeline (ADR 0003, 04.0/03).

`pull` (backends/mongo/export.py) and `push` (backends/local/staging.py +
backends/mongo/push_plan.py + push_writer.py) are only a safe two-way sync if they are
inverses: an untouched export must plan 100% `unchanged`, and exactly the edits a reviewer
made must become the only writes. MongoDB is the in-memory FakeCollection; these tests drive
the same pure functions the real `push`/`pull` commands call, so a regression here is a
regression in the corpus's two-way integrity.

A deliberately broken datetime mapping fails at least one test here (success criterion,
04.0/03): breaking export.datetime_to_iso fails test_document_level_round_trip_identity's
explicit ISO assertion; breaking push_writer.restore_bson_types fails
test_field_removal_propagates's datetime-type assertion.
"""

import copy
from datetime import UTC, datetime

from bson import ObjectId

from bookscraper.backends.local.staging import (
    compute_document_hash,
    prepare_for_push,
    validate_staging_documents,
)
from bookscraper.backends.mongo.export import (
    iter_collection,
    normalize_document,
    to_staging_document,
)
from bookscraper.backends.mongo.push_plan import build_push_plan
from bookscraper.backends.mongo.push_writer import apply_push_plan, fetch_existing
from tests.mock.backends.fake_collection import FakeCollection

# A representative corpus: all four sites; str / int / float / bool / None / empty list /
# non-empty list / nested dict / datetime values; a unicode title; an empty authors list.
LEANPUB_DATETIME = datetime(2022, 4, 26, 10, 15, 3, tzinfo=UTC)


def _corpus_fields() -> list[dict]:
    return [
        {
            "site": "amazon",
            "title": "Fluent Python",
            "authors": ["Luciano Ramalho"],
            "url": "https://www.amazon.com/dp/1098131029",
            "publication_year": 2022,
            "isbn13": "978-1098131029",
            "description": "Clear, concise, effective Python.",
            "tags": [],
            "rating": 4.7,
            "in_stock": True,
            "note": None,
        },
        {
            "site": "packtpub",
            "title": "Python Machine Learning",
            "authors": ["Sebastian Raschka", "Vahid Mirjalili"],
            "url": "https://www.packtpub.com/product/python-machine-learning",
            "publication_year": 2019,
            "categories": ["Python", "ML"],
        },
        {
            "site": "oreilly",
            "title": "Learning Python",
            "authors": [],
            "url": "https://www.oreilly.com/library/view/learning-python/9781449355722/",
            "publication_year": 2013,
            "meta": {"pages": 1600, "edition": 5},
        },
        {
            "site": "leanpub.com",
            "title": "Café ☕ Python",
            "authors": ["Z. Zed"],
            "url": "https://leanpub.com/cafe-python",
            "last_published_at": LEANPUB_DATETIME,
            "book_id": "123",
            "slug": "cafe-python",
            "about_the_book": "A book about coffee and code.",
        },
    ]


def _seed_collection() -> FakeCollection:
    """A FakeCollection seeded with the corpus, each document carrying its correct stored hash."""
    collection = FakeCollection()
    for fields in _corpus_fields():
        stored = copy.deepcopy(fields)
        stored["hash"] = compute_document_hash(normalize_document(fields))
        collection.seed(stored)
    return collection


def _export(collection: FakeCollection) -> list[dict]:
    """What `bookscraper pull` would write for this collection."""
    return [to_staging_document(raw) for raw in iter_collection(collection)]


def _plan(collection: FakeCollection, documents: list[dict], *, approved_only: bool = False):
    """Mirrors the plan phase of commands/push.run: validate -> prepare -> fetch -> build plan."""
    assert validate_staging_documents(documents) == []
    prepared = [prepare_for_push(document, index) for index, document in enumerate(documents)]
    existing = fetch_existing(collection, prepared)
    plan = build_push_plan(
        prepared,
        existing_hashes=existing.hashes,
        existing_by_id=existing.by_id,
        approved_only=approved_only,
    )
    return plan, prepared, existing


def test_pull_then_push_is_noop() -> None:
    """Mock: an untouched export plans as 100% unchanged with no writes (ADR 0003 success criterion).

    Scenario: seed a four-site corpus, pull it, plan a push of the verbatim export.
    Boundaries: FakeCollection; pure plan phase, no bulk_write.
    On failure: a plain export->push would churn the corpus, re-writing documents nobody edited.
    """
    collection = _seed_collection()
    export = _export(collection)

    plan, _prepared, _existing = _plan(collection, export)

    assert plan.errors == []
    assert plan.count("unchanged") == len(export)
    assert all(item.action == "unchanged" for item in plan.items)
    assert not plan.has_writes


def test_single_edit_is_single_update() -> None:
    """Mock: editing one field yields exactly one update naming only that field.

    Scenario: pull the corpus, rewrite the amazon document's description, plan the push.
    Boundaries: FakeCollection; a non-hash field, so the stored hash is kept.
    On failure: a review edit is either lost or spreads into spurious updates of untouched books.
    """
    collection = _seed_collection()
    export = _export(collection)
    target = next(d for d in export if d["site"] == "amazon")
    target["description"] = "A reviewer rewrote this description."

    plan, _prepared, _existing = _plan(collection, export)

    updates = [item for item in plan.items if item.action == "update"]
    assert len(updates) == 1
    assert updates[0].changed_fields == ["description"]
    assert updates[0].hash_changed is False
    assert plan.count("unchanged") == len(export) - 1
    assert plan.errors == []


def test_field_removal_propagates() -> None:
    """Mock: deleting a field becomes an update that removes it; a datetime field stays a datetime.

    Scenario: pull the corpus, delete the leanpub document's about_the_book, push --allow-updates.
    Boundaries: FakeCollection; ReplaceOne semantics; push_writer.restore_bson_types on datetime.
    On failure: a reviewer's deletion silently survives in Mongo, or export's datetime round trip corrupts the type.
    """
    collection = _seed_collection()
    export = _export(collection)
    leanpub = next(d for d in export if d["site"] == "leanpub.com")
    object_id = leanpub["_id"]
    del leanpub["about_the_book"]

    plan, prepared, existing = _plan(collection, export)
    updates = [item for item in plan.items if item.action == "update"]
    assert len(updates) == 1
    assert updates[0].changed_fields == ["about_the_book"]

    outcomes = apply_push_plan(collection, plan, prepared, existing)
    assert sum(1 for o in outcomes if o.outcome == "updated") == 1
    assert sum(1 for o in outcomes if o.outcome == "unchanged") == len(export) - 1

    stored = collection.get(ObjectId(object_id))
    assert "about_the_book" not in stored
    # restore_bson_types must put the datetime type back from the exported ISO string.
    assert isinstance(stored["last_published_at"], datetime)
    assert stored["last_published_at"] == LEANPUB_DATETIME


def test_new_document_inserted() -> None:
    """Mock: appending a freshly scraped document is exactly one insert.

    Scenario: pull the corpus, append an _id-less document with a new hash, plan then push.
    Boundaries: FakeCollection; a hash absent from the collection and the file.
    On failure: a new book is misrouted to duplicate/update and never reaches the corpus.
    """
    collection = _seed_collection()
    export = _export(collection)
    export.append(
        {
            "site": "amazon",
            "title": "Brand New Book",
            "authors": ["New Author"],
            "url": "https://www.amazon.com/dp/9999999999",
            "publication_year": 2025,
        }
    )

    plan, prepared, existing = _plan(collection, export)
    assert plan.count("insert") == 1
    assert plan.count("unchanged") == len(export) - 1
    inserts = [item for item in plan.items if item.action == "insert"]
    assert inserts[0].title == "Brand New Book"

    outcomes = apply_push_plan(collection, plan, prepared, existing)
    assert sum(1 for o in outcomes if o.outcome == "inserted") == 1
    assert any(d["title"] == "Brand New Book" for d in collection.documents)


def test_removed_document_not_deleted() -> None:
    """Mock: dropping a document from the export deletes nothing in MongoDB (documented non-feature).

    Scenario: pull the corpus, remove one document from the file, push the remainder.
    Boundaries: FakeCollection; push never issues a delete.
    On failure: a truncated or partially edited export would silently wipe live documents.
    """
    collection = _seed_collection()
    before_ids = {str(d["_id"]) for d in collection.documents}
    export = _export(collection)
    removed = export.pop()

    plan, prepared, existing = _plan(collection, export)
    assert not plan.has_writes

    apply_push_plan(collection, plan, prepared, existing)
    after_ids = {str(d["_id"]) for d in collection.documents}
    assert after_ids == before_ids
    assert removed["_id"] in after_ids


def test_document_level_round_trip_identity() -> None:
    """Mock: prepare_for_push(to_staging_document(doc)) reproduces the normalized document, field for field.

    Scenario: for every seeded document, run it out through export and back through push preparation.
    Boundaries: FakeCollection; plus one explicit ISO assertion that pins export's datetime mapping.
    On failure: a BSON value is dropped or mistyped on the round trip, so an identity edit looks like a change.
    """
    collection = _seed_collection()
    for raw in collection.documents:
        staging_document = to_staging_document(copy.deepcopy(raw))
        prepared = prepare_for_push(staging_document, 0)
        assert prepared.document == normalize_document(raw)

    # Explicit datetime mapping - independent of the symmetric comparison above, so a broken
    # export.datetime_to_iso is caught instead of cancelling out on both sides.
    leanpub = next(d for d in collection.documents if d["site"] == "leanpub.com")
    assert to_staging_document(copy.deepcopy(leanpub))["last_published_at"] == "2022-04-26T10:15:03Z"
