"""
The I/O half of `bookscraper push`: read what the plan needs from MongoDB, then apply the plan
in one unordered bulk write. The only code in the project that inserts or replaces book documents.

Security: documents reaching this module have passed staging.validate_staging_documents, which
rejects every key starting with '$' or containing '.', so a replacement document can never smuggle
an update operator. Errors are reported by exception class and server error code only - never the
server's message, which can echo document content or connection details.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

import bson
from bson import ObjectId
from pymongo import InsertOne, ReplaceOne
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

from ..local.staging import RESERVED_ID_KEY, PreparedDocument
from .database import ensure_unique_index_on_hash
from .export import normalize_document
from .push_plan import PushPlan

module_logger = logging.getLogger(__name__)

IN_QUERY_CHUNK = 1000
DUPLICATE_KEY_CODE = 11000
MAX_BSON_BYTES = 16 * 1024 * 1024


@dataclass
class ExistingState:
    """What MongoDB already holds for the documents in a staging file."""

    hashes: dict[str, str]  # hash -> _id hex
    by_id: dict[str, dict]  # _id hex -> live document in staging-file form
    raw_by_id: dict[str, dict]  # _id hex -> live document as returned by pymongo


@dataclass
class ItemOutcome:
    index: int
    outcome: str  # inserted | updated | duplicate | unchanged | skipped | error
    error: Optional[str] = None


def oversized_documents(prepared_docs: list[PreparedDocument]) -> list[str]:
    """Exact BSON-size check (the staging validator's JSON-size check is only an early guard)."""
    problems = []
    for prepared in prepared_docs:
        document = {k: v for k, v in prepared.document.items() if k != RESERVED_ID_KEY}
        size = len(bson.encode(document))
        if size > MAX_BSON_BYTES:
            problems.append(f"document #{prepared.index}: {size} bytes as BSON; MongoDB's limit is {MAX_BSON_BYTES}")
    return problems


def _chunks(values: list, size: Optional[int] = None) -> Iterable[list]:
    size = size or IN_QUERY_CHUNK  # read at call time, not bound at definition time
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_existing(collection: Collection, prepared_docs: list[PreparedDocument]) -> ExistingState:
    """Two batched lookups: which candidate hashes exist, and the live documents for every referenced `_id`."""
    hashes = sorted({p.hash for p in prepared_docs if p.hash})
    object_ids = sorted({p.object_id for p in prepared_docs if p.object_id})

    existing_hashes: dict[str, str] = {}
    for chunk in _chunks(hashes):
        for doc in collection.find({"hash": {"$in": chunk}}, {"hash": 1}):
            existing_hashes[doc["hash"]] = str(doc[RESERVED_ID_KEY])

    by_id: dict[str, dict] = {}
    raw_by_id: dict[str, dict] = {}
    for chunk in _chunks(object_ids):
        for doc in collection.find({RESERVED_ID_KEY: {"$in": [ObjectId(oid) for oid in chunk]}}):
            key = str(doc[RESERVED_ID_KEY])
            raw_by_id[key] = doc
            by_id[key] = normalize_document(doc)
    return ExistingState(hashes=existing_hashes, by_id=by_id, raw_by_id=raw_by_id)


def _parse_iso(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def restore_bson_types(staged: Any, live: Any) -> Any:
    """
    Undo export's datetime → ISO-string mapping, but only where the live value at the same path
    is a datetime - the original type decides, never the string's shape.
    """
    if isinstance(live, datetime) and isinstance(staged, str):
        parsed = _parse_iso(staged)
        return parsed if parsed is not None else staged
    if isinstance(staged, dict) and isinstance(live, dict):
        return {key: restore_bson_types(value, live.get(key)) for key, value in staged.items()}
    return staged


def _error_label(exc: BaseException, code: Optional[int] = None) -> str:
    return f"{type(exc).__name__}" + (f" (code {code})" if code is not None else "")


def apply_push_plan(
    collection: Collection,
    plan: PushPlan,
    prepared_docs: list[PreparedDocument],
    existing: ExistingState,
) -> list[ItemOutcome]:
    """
    Applies every `insert` and `update` in one bulk_write(ordered=False) and returns exactly one
    outcome per plan item, in plan order. Updates are full replacements (ReplaceOne), so a field the
    reviewer deleted is removed. A duplicate-key error from a concurrent writer becomes `duplicate`.
    """
    by_index = {p.index: p for p in prepared_docs}
    outcomes: dict[int, ItemOutcome] = {}
    operations: list = []
    op_items: list[int] = []  # operation position -> staging index

    for planned in plan.items:
        prepared = by_index[planned.index]
        if planned.action == "insert":
            document = copy.deepcopy(prepared.document)
            document.pop(RESERVED_ID_KEY, None)
            operations.append(InsertOne(document))
            op_items.append(planned.index)
            outcomes[planned.index] = ItemOutcome(planned.index, "inserted")
        elif planned.action == "update":
            object_id = prepared.object_id
            replacement = {k: v for k, v in prepared.document.items() if k != RESERVED_ID_KEY}
            replacement = restore_bson_types(replacement, existing.raw_by_id.get(object_id, {}))
            operations.append(ReplaceOne({RESERVED_ID_KEY: ObjectId(object_id)}, replacement))
            op_items.append(planned.index)
            outcomes[planned.index] = ItemOutcome(planned.index, "updated")
        elif planned.action in ("duplicate", "unchanged"):
            outcomes[planned.index] = ItemOutcome(planned.index, planned.action)
        else:
            outcomes[planned.index] = ItemOutcome(planned.index, "skipped")

    if operations:
        ensure_unique_index_on_hash(collection)
        matched: Optional[int] = None
        try:
            result = collection.bulk_write(operations, ordered=False)
            matched = result.matched_count
        except BulkWriteError as exc:
            details = exc.details or {}
            matched = details.get("nMatched")
            for write_error in details.get("writeErrors", []):
                position = write_error.get("index")
                if position is None or not 0 <= position < len(op_items):
                    continue
                staging_index = op_items[position]
                code = write_error.get("code")
                if code == DUPLICATE_KEY_CODE and outcomes[staging_index].outcome == "inserted":
                    outcomes[staging_index] = ItemOutcome(staging_index, "duplicate")
                else:
                    outcomes[staging_index] = ItemOutcome(staging_index, "error", _error_label(exc, code))
        except Exception as exc:  # connection lost mid-write: the state of every write is unknown
            module_logger.error(f"Bulk write failed: {type(exc).__name__}")
            for staging_index in op_items:
                outcomes[staging_index] = ItemOutcome(
                    staging_index, "error", f"{_error_label(exc)}; write state unknown - re-run push --dry-run"
                )
            return [outcomes[p.index] for p in plan.items]

        _flag_vanished_updates(collection, outcomes, by_index, matched)

    return [outcomes[p.index] for p in plan.items]


def _flag_vanished_updates(
    collection: Collection,
    outcomes: dict[int, ItemOutcome],
    by_index: dict[int, PreparedDocument],
    matched: Optional[int],
) -> None:
    """If fewer replacements matched than were sent, find which `_id`s vanished since fetch_existing."""
    updated = [i for i, o in outcomes.items() if o.outcome == "updated"]
    if matched is None or matched >= len(updated):
        return
    ids = [ObjectId(by_index[i].object_id) for i in updated]
    present = {str(d[RESERVED_ID_KEY]) for d in collection.find({RESERVED_ID_KEY: {"$in": ids}}, {RESERVED_ID_KEY: 1})}
    for staging_index in updated:
        if by_index[staging_index].object_id not in present:
            outcomes[staging_index] = ItemOutcome(staging_index, "error", "document deleted in MongoDB during push")
