"""Schema v1 to v2 data migration (subtask 03.1/06).

Every document written before task 03.1 is a *v1* record: the Playwright sites (Amazon,
Packtpub, O'Reilly) shared one shape (``publication_date`` raw site text, ``"N/A"`` ISBN
sentinels, no ``schema_version``) and Leanpub wrote another (``about_the_book`` / ``categories``
/ ``last_published_at`` / a ``"leanpub.com"`` site key / no ``url``). This module rewrites those
v1 documents into the canonical schema-2 :class:`bookscraper.models.BookRecord` shape that both
backends now store, and is the engine behind the ``bookscraper migrate`` subcommand.

**Transform is injected, never imported.** The v1 to v2 field transform is
:func:`bookscraper.scraping.normalize.from_v1_document` - a stored v1 document is just another raw
input to the same per-site normalizers a fresh scrape uses, which is what guarantees a migrated
record is byte-for-byte what a fresh scrape would produce. This module stays within the one-way
import direction (``backends`` never imports ``scraping``) by taking that transform as a
``Callable[[dict], dict]`` parameter; ``commands/migrate.py`` (``commands`` may import both layers)
wires in the concrete function.

**Hash recomputation (user decision 2026-10-06).** The normalizer's author de-duplication is part
of the hash input, so a stored document whose author list repeats a name case-insensitively earns
a *new* hash under v2. The migration therefore recomputes ``hash_book(title, authors,
publication_year)`` for **every** document and rewrites the hashes that change, reporting each one
(old to new, with the document id). A recomputed hash that collides with another document's hash is
**not** rewritten - that document is left untouched (at v1) and reported for a human to resolve, so
the migration never silently overwrites or merges.

The migration is dry-run by default (``--apply`` writes), idempotent (re-running on v2 documents
is a no-op), and never deletes a document. The field schema and the full v1 to v2 mapping live in
``docs/scraping/book-record-schema.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pymongo import ReplaceOne
from pymongo.collection import Collection

from ..book_utils import atomic_write_bytes
from ..models import SCHEMA_VERSION, validate_document
from .local import store

#: The v1-to-v2 field transform signature callers inject (normally
#: :func:`bookscraper.scraping.normalize.from_v1_document`). Keeping it a parameter is what lets
#: this ``backends`` module stay free of any ``scraping`` import.
Transform = Callable[[dict], dict]

#: Documents per ``bulk_write`` batch on the mongo backend (subtask 06 spec).
MONGO_BATCH_SIZE = 500

#: The v2 schema keys compared field-by-field for the dry-run per-field change summary.
_SCHEMA_KEYS = (
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


@dataclass(frozen=True)
class MigrationReport:
    """The outcome of one migration pass over a store, dry-run or applied.

    :ivar store_backend: The backend that was migrated (``"mongo"`` or ``"json"``).
    :ivar applied: ``True`` when the store was actually written, ``False`` for a dry run.
    :ivar generated_at: UTC ISO-8601 timestamp the report was produced.
    :ivar total: The number of documents examined.
    :ivar migrated: The number of documents rewritten to v2 (dry run: that would be rewritten).
    :ivar hash_changed: How many of ``migrated`` changed their ``hash`` (author de-duplication).
    :ivar collisions: How many documents were left untouched because the recomputed hash collided.
    :ivar invalid: How many documents failed :func:`bookscraper.models.validate_document` after
        transformation and were left untouched.
    :ivar unchanged: How many documents were already valid v2 (no rewrite needed).
    :ivar changed_hashes: One ``{"id", "old", "new"}`` entry per rewritten hash.
    :ivar hash_collisions: One ``{"_id", "hash", "title", "collides_with"}`` entry per collision
        (the document NOT rewritten), for a human to resolve.
    :ivar invalid_documents: One ``{"id", ...}`` entry per document invalid after transformation.
    :ivar field_changes: Per schema field, how many migrated documents changed that field.
    """

    store_backend: str
    applied: bool
    generated_at: str
    total: int
    migrated: int
    hash_changed: int
    collisions: int
    invalid: int
    unchanged: int
    changed_hashes: list[dict]
    hash_collisions: list[dict]
    invalid_documents: list[dict]
    field_changes: dict[str, int]

    def to_dict(self) -> dict:
        """Return the JSON-serializable report document written to the run log directory.

        :returns: A dict with a ``counts`` block plus the ``changed_hashes`` / ``hash_collisions``
            / ``invalid_documents`` / ``field_changes`` detail lists.
        """
        return {
            "schema_migration": "v1->v2",
            "store_backend": self.store_backend,
            "applied": self.applied,
            "generated_at": self.generated_at,
            "counts": {
                "total": self.total,
                "migrated": self.migrated,
                "hash_changed": self.hash_changed,
                "collisions": self.collisions,
                "invalid": self.invalid,
                "unchanged": self.unchanged,
            },
            "field_changes": dict(self.field_changes),
            "changed_hashes": list(self.changed_hashes),
            "hash_collisions": list(self.hash_collisions),
            "invalid_documents": list(self.invalid_documents),
        }


@dataclass(frozen=True)
class _Plan:
    """The internal result of planning a migration, before any write.

    :ivar rewrites: ``(index, new_document)`` pairs - the position in the source list and the
        fully-formed v2 document to store there.
    :ivar total: Number of documents examined.
    :ivar migrated: Number of documents in ``rewrites``.
    :ivar hash_changed: Of ``migrated``, how many changed their ``hash``.
    :ivar unchanged: Documents already valid v2 (no rewrite).
    :ivar changed_hashes: Report entries for each changed hash.
    :ivar hash_collisions: Report entries for each collision (document left untouched).
    :ivar invalid_documents: Report entries for each document invalid after transformation.
    :ivar field_changes: Per-field change tally across ``rewrites``.
    """

    rewrites: list[tuple[int, dict]]
    total: int
    migrated: int
    hash_changed: int
    unchanged: int
    changed_hashes: list[dict]
    hash_collisions: list[dict]
    invalid_documents: list[dict]
    field_changes: dict[str, int]


def _content_without_id(doc: dict) -> dict:
    """Return a copy of ``doc`` with any Mongo ``_id`` removed.

    The ``_id`` is storage metadata, not part of the canonical record, so it is excluded from both
    the transform input and the replacement document (``ReplaceOne`` keeps the existing ``_id``).

    :param doc: A stored document, possibly carrying a Mongo ``_id``.
    :returns: A new dict with every key except ``_id``.
    """
    return {key: value for key, value in doc.items() if key != "_id"}


def _document_id(doc: dict, index: int) -> str:
    """Return a stable, body-free identifier for ``doc`` for the migration report.

    Prefers the Mongo ``_id``, then the human-facing ``url``, then the existing ``hash``, and
    finally the positional index - never a title or description, so a report line cannot leak
    scraped prose.

    :param doc: The stored document.
    :param index: Its position in the source list, used as a last-resort identifier.
    :returns: A short string identifying the document.
    """
    raw_id = doc.get("_id")
    if raw_id is not None:
        return str(raw_id)
    return doc.get("url") or doc.get("hash") or f"#{index}"


def _accumulate_field_changes(field_changes: dict[str, int], old: dict, new: dict):
    """Tally, per schema field, every field whose value differs between ``old`` and ``new``.

    Drives the dry-run per-field change summary. A v1 document stores different key names
    (``about_the_book`` rather than ``description``), so a renamed-and-populated field counts as a
    change, which is the intended signal.

    :param field_changes: The running per-field tally, mutated in place.
    :param old: The document before transformation (without any ``_id``).
    :param new: The transformed v2 document.
    """
    for key in _SCHEMA_KEYS:
        if old.get(key) != new.get(key):
            field_changes[key] = field_changes.get(key, 0) + 1


def _build_plan(documents: list, transform: Transform) -> _Plan:
    """Plan the migration of ``documents`` without writing anything.

    Classifies each document as unchanged (already valid v2), migrated (rewritten, optionally with
    a changed hash), a hash collision (recomputed hash duplicates another document's, left
    untouched), or invalid-after-transformation (left untouched). A collision is detected against
    both the pre-existing hashes and the hashes already assigned earlier in this pass, so two
    documents that would converge on one hash never both get written.

    :param documents: The stored documents in order (non-dict entries are preserved and skipped).
    :param transform: The injected v1-to-v2 field transform (produces a full v2 document, or raises
        on a malformed input).
    :returns: The :class:`_Plan` describing the rewrites and the report detail.
    """
    existing_hashes = {doc["hash"] for doc in documents if isinstance(doc, dict) and doc.get("hash")}
    assigned_new: set[str] = set()
    rewrites: list[tuple[int, dict]] = []
    changed_hashes: list[dict] = []
    hash_collisions: list[dict] = []
    invalid_documents: list[dict] = []
    field_changes: dict[str, int] = {}
    migrated = 0
    hash_changed = 0
    unchanged = 0

    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            unchanged += 1
            continue
        doc_id = _document_id(doc, index)
        content = _content_without_id(doc)
        try:
            transformed = transform(doc)
        except (KeyError, TypeError, ValueError) as exc:
            invalid_documents.append({"id": doc_id, "reason": type(exc).__name__})
            continue

        problems = validate_document(transformed)
        if problems:
            invalid_documents.append({"id": doc_id, "problems": problems})
            continue

        if transformed == content:
            unchanged += 1
            continue

        old_hash = content.get("hash")
        new_hash = transformed["hash"]
        if new_hash != old_hash:
            if new_hash in (existing_hashes - {old_hash}) or new_hash in assigned_new:
                hash_collisions.append(
                    {
                        "_id": doc_id,
                        "hash": old_hash,
                        "title": content.get("title"),
                        "collides_with": new_hash,
                    }
                )
                continue
            changed_hashes.append({"id": doc_id, "old": old_hash, "new": new_hash})
            assigned_new.add(new_hash)
            hash_changed += 1

        _accumulate_field_changes(field_changes, content, transformed)
        rewrites.append((index, transformed))
        migrated += 1

    return _Plan(
        rewrites=rewrites,
        total=len(documents),
        migrated=migrated,
        hash_changed=hash_changed,
        unchanged=unchanged,
        changed_hashes=changed_hashes,
        hash_collisions=hash_collisions,
        invalid_documents=invalid_documents,
        field_changes=field_changes,
    )


def _report_from_plan(store_backend: str, applied: bool, plan: _Plan) -> MigrationReport:
    """Assemble a :class:`MigrationReport` from a planned migration.

    :param store_backend: The backend that was migrated.
    :param applied: Whether the store was actually written.
    :param plan: The plan produced by :func:`_build_plan`.
    :returns: The finished report.
    """
    return MigrationReport(
        store_backend=store_backend,
        applied=applied,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        total=plan.total,
        migrated=plan.migrated,
        hash_changed=plan.hash_changed,
        collisions=len(plan.hash_collisions),
        invalid=len(plan.invalid_documents),
        unchanged=plan.unchanged,
        changed_hashes=plan.changed_hashes,
        hash_collisions=plan.hash_collisions,
        invalid_documents=plan.invalid_documents,
        field_changes=plan.field_changes,
    )


def migrate_json_store(path: Path, *, apply: bool, transform: Transform) -> MigrationReport:
    """Migrate the local ``json`` store at ``path`` in place.

    Reads the store (refusing to proceed on a corrupt file, so it is never clobbered), plans the
    migration, and - only with ``apply`` and at least one rewrite - writes the result through
    :func:`bookscraper.backends.local.store._write_books`, which creates the one-generation
    ``books.json.bak`` backup first and writes atomically. Documents not rewritten (unchanged,
    colliding, invalid, or non-dict junk) are preserved verbatim, so nothing is ever deleted.

    :param path: The ``books.json`` store location.
    :param apply: ``True`` to write the migrated store; ``False`` for a dry run.
    :param transform: The injected v1-to-v2 field transform (keyword-only, required).
    :returns: The :class:`MigrationReport` describing what changed (or would change).
    :raises bookscraper.backends.local.store.LocalStoreCorruptError: if ``path`` is not valid JSON.
    """
    documents = store._load_books(path, raise_on_corrupt=True)
    plan = _build_plan(documents, transform)
    if apply and plan.rewrites:
        new_books = list(documents)
        for index, new_document in plan.rewrites:
            new_books[index] = new_document
        store._write_books(new_books, path)
    return _report_from_plan("json", apply and bool(plan.rewrites), plan)


def migrate_mongo_collection(
    collection: Collection,
    *,
    apply: bool,
    transform: Transform,
    batch_size: int = MONGO_BATCH_SIZE,
) -> MigrationReport:
    """Migrate a MongoDB ``collection`` document by document.

    Plans the migration over a snapshot of the collection, then - only with ``apply`` - applies it
    as guarded :class:`pymongo.ReplaceOne` operations
    (``filter={"_id": id, "schema_version": {"$ne": 2}}``) in ``bulk_write`` batches of
    ``batch_size``. The ``$ne`` guard makes a re-run a no-op (an already-v2 document matches
    nothing), the per-document replace never touches an un-migrated document, and collisions are
    excluded from the plan so the unique ``hash`` index can never reject a write.

    :param collection: The MongoDB books collection.
    :param apply: ``True`` to write the migrated documents; ``False`` for a dry run.
    :param transform: The injected v1-to-v2 field transform (keyword-only, required).
    :param batch_size: Maximum operations per ``bulk_write`` call.
    :returns: The :class:`MigrationReport` describing what changed (or would change).
    """
    documents = list(collection.find({}))
    plan = _build_plan(documents, transform)
    if apply and plan.rewrites:
        operations = [
            ReplaceOne(
                {"_id": documents[index]["_id"], "schema_version": {"$ne": SCHEMA_VERSION}},
                new_document,
            )
            for index, new_document in plan.rewrites
        ]
        for start in range(0, len(operations), batch_size):
            collection.bulk_write(operations[start : start + batch_size], ordered=False)
    return _report_from_plan("mongo", apply and bool(plan.rewrites), plan)


def write_report(report: MigrationReport, directory: Path) -> Path:
    """Write ``report`` to a timestamped ``migration-report-*.json`` file under ``directory``.

    The report is the migration's audit artifact: it names every changed hash and every collision
    for a human. It carries only ids, hashes, counts, and (for a collision) the document title -
    never a credential, URI, or document body.

    :param report: The report to serialize.
    :param directory: The run log directory to write into (created if missing).
    :returns: The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = directory / f"migration-report-{timestamp}.json"
    payload = json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8")
    atomic_write_bytes(path, payload)
    return path
