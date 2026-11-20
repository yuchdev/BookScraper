"""
A small in-memory stand-in for pymongo.collection.Collection, used by the push/pull/dedup tests.

Hand-rolled instead of adding mongomock as a dependency: it implements exactly the surface the
staged pipeline uses - find (equality, $in, $or; projection; sort/batch_size), find_one,
bulk_write (InsertOne / ReplaceOne, unordered, BulkWriteError with per-op writeErrors),
index_information / create_index (unique 'hash' enforced once the index exists), and with_options.

`FakeCollection(read_only=True)` raises AssertionError on any write-ish call, which is how tests
prove a command (scrape --check-mongo, pull, push --dry-run) never modifies the database.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from bson import ObjectId
from pymongo import InsertOne, ReplaceOne
from pymongo.errors import BulkWriteError

WRITE_METHODS = (
    "bulk_write",
    "create_index",
    "insert_one",
    "insert_many",
    "replace_one",
    "update_one",
    "update_many",
    "delete_one",
    "delete_many",
    "drop",
)


def _matches(document: dict, query: dict) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, sub) for sub in condition):
                return False
            continue
        value = document.get(key)
        if isinstance(condition, dict) and "$in" in condition:
            if value not in condition["$in"]:
                return False
        elif value != condition:
            return False
    return True


def _project(document: dict, projection: Optional[dict]) -> dict:
    if not projection:
        return copy.deepcopy(document)
    keys = {k for k, v in projection.items() if v} | {"_id"}
    return {k: copy.deepcopy(v) for k, v in document.items() if k in keys}


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self._documents = documents
        self.batch_sizes: list[int] = []

    def sort(self, key: str, direction: int = 1) -> FakeCursor:
        self._documents.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def batch_size(self, size: int) -> FakeCursor:
        self.batch_sizes.append(size)
        return self

    def __iter__(self):
        return iter(self._documents)


class FakeCollection:
    def __init__(self, documents: Iterable[dict] = (), *, read_only: bool = False, name: str = "books") -> None:
        self.documents: list[dict] = []
        self.indexes: dict[str, dict] = {"_id_": {"key": [("_id", 1)]}}
        self.read_only = read_only
        self.name = name
        self.database = SimpleNamespace(name="bookscraper_test_db")
        self.calls: list[str] = []
        self.find_queries: list[dict] = []
        self.with_options_kwargs: list[dict] = []
        self.last_cursor: Optional[FakeCursor] = None
        for document in documents:
            self.seed(document)

    # -- helpers for tests ------------------------------------------------- #
    def seed(self, document: dict) -> ObjectId:
        """Insert test data directly (bypasses the read_only guard and the unique index)."""
        stored = copy.deepcopy(document)
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        return stored["_id"]

    def get(self, object_id: ObjectId) -> Optional[dict]:
        return next((d for d in self.documents if d["_id"] == object_id), None)

    def _guard(self, method: str) -> None:
        self.calls.append(method)
        if self.read_only:
            raise AssertionError(f"read-only FakeCollection: unexpected write call {method}()")

    def _hash_unique(self) -> bool:
        return any(info.get("unique") and info["key"] == [("hash", 1)] for info in self.indexes.values())

    # -- read API ----------------------------------------------------------- #
    def with_options(self, **kwargs: Any) -> FakeCollection:
        self.with_options_kwargs.append(kwargs)
        return self

    def find(self, query: Optional[dict] = None, projection: Optional[dict] = None) -> FakeCursor:
        self.calls.append("find")
        query = query or {}
        self.find_queries.append(query)
        self.last_cursor = FakeCursor([_project(d, projection) for d in self.documents if _matches(d, query)])
        return self.last_cursor

    def find_one(self, query: Optional[dict] = None) -> Optional[dict]:
        self.calls.append("find_one")
        return next((copy.deepcopy(d) for d in self.documents if _matches(d, query or {})), None)

    def index_information(self) -> dict:
        return copy.deepcopy(self.indexes)

    # -- write API ---------------------------------------------------------- #
    def create_index(self, key: str, unique: bool = False, name: Optional[str] = None) -> str:
        self._guard("create_index")
        index_name = name or f"{key}_1"
        self.indexes[index_name] = {"key": [(key, 1)], "unique": unique}
        return index_name

    def bulk_write(self, operations: list, ordered: bool = True) -> SimpleNamespace:
        self._guard("bulk_write")
        inserted = matched = 0
        write_errors: list[dict] = []
        for position, operation in enumerate(operations):
            if isinstance(operation, InsertOne):
                document = copy.deepcopy(operation._doc)
                if self._hash_unique() and any(d.get("hash") == document.get("hash") for d in self.documents):
                    write_errors.append({"index": position, "code": 11000, "errmsg": "E11000 dup key: secret-ish"})
                    if ordered:
                        break
                    continue
                document.setdefault("_id", ObjectId())
                self.documents.append(document)
                inserted += 1
            elif isinstance(operation, ReplaceOne):
                target = next((d for d in self.documents if _matches(d, operation._filter)), None)
                if target is None:
                    continue
                replacement = copy.deepcopy(operation._doc)
                if self._hash_unique() and any(
                    d.get("hash") == replacement.get("hash") and d is not target for d in self.documents
                ):
                    write_errors.append({"index": position, "code": 11000, "errmsg": "E11000 dup key"})
                    continue
                replacement["_id"] = target["_id"]
                self.documents[self.documents.index(target)] = replacement
                matched += 1
            else:  # pragma: no cover - only the two operation types are used
                raise TypeError(f"unsupported bulk operation {operation!r}")
        if write_errors:
            raise BulkWriteError(
                {"writeErrors": write_errors, "nInserted": inserted, "nMatched": matched, "nModified": matched}
            )
        return SimpleNamespace(inserted_count=inserted, matched_count=matched, modified_count=matched)

    def __getattr__(self, name: str) -> Any:
        if name in WRITE_METHODS:

            def _write(*_args: Any, **_kwargs: Any) -> None:
                self._guard(name)
                raise NotImplementedError(f"FakeCollection.{name} is not implemented")

            return _write
        raise AttributeError(name)
