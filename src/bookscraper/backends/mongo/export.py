"""
MongoDB → staging-file conversion for `bookscraper pull` (ADR 0003).

Every BSON value is mapped by an explicit rule, never stringified by accident:
- the top-level `_id` ObjectId becomes its 24-hex string (the identity `push` matches on);
- `datetime` becomes ISO-8601 UTC with a `Z` suffix (push restores the datetime type for
  fields whose live value is a datetime - see push_writer.restore_bson_types);
- JSON-native values (str, int, float, bool, None, list, dict) pass through;
- anything else (Decimal128, Binary, a nested ObjectId, ...) raises ExportError naming the
  field path, so a new BSON type in the collection fails loudly instead of corrupting the
  round trip.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Iterator

from bson import ObjectId
from pymongo.collection import Collection

from ..local.staging import RESERVED_ID_KEY, STAGING_META_KEY, STATUS_PENDING

module_logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500


class ExportError(Exception):
    """A document holds a BSON value with no lossless staging-file representation."""


def datetime_to_iso(value: datetime) -> str:
    if value.tzinfo is None:  # pymongo returns naive UTC datetimes by default
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    text = value.isoformat(timespec="milliseconds" if value.microsecond else "seconds")
    return text.replace("+00:00", "Z")


def _convert(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, datetime):
        return datetime_to_iso(value)
    if isinstance(value, dict):
        return {key: _convert(child, f"{path}.{key}" if path else key) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert(child, f"{path}[{i}]") for i, child in enumerate(value)]
    raise ExportError(f"Field '{path}' holds a {type(value).__name__}, which has no staging-file representation.")


def normalize_document(raw: dict) -> dict:
    """A live MongoDB document in staging-file form (no `_staging` stamp) - used for export and comparison."""
    converted: dict = {}
    for key, value in raw.items():
        if key == RESERVED_ID_KEY:
            if not isinstance(value, ObjectId):
                raise ExportError(
                    f"Field '_id' holds a {type(value).__name__}; only ObjectId identities are supported."
                )
            converted[key] = str(value)
        else:
            converted[key] = _convert(value, key)
    return converted


def to_staging_document(raw: dict) -> dict:
    """A live MongoDB document as a pulled staging document, stamped `_staging = {pending, pull}`."""
    document = normalize_document(raw)
    document[STAGING_META_KEY] = {"status": STATUS_PENDING, "source": "pull"}
    return document


def iter_collection(collection: Collection, *, batch_size: int = DEFAULT_BATCH_SIZE) -> Iterator[dict]:
    """Every document in the collection, sorted by `_id` for stable diffs, fetched in batches."""
    yield from collection.find({}).sort(RESERVED_ID_KEY, 1).batch_size(batch_size)
