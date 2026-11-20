"""Integration-tier Atlas test for `bookscraper pull` (04.0/04).

The BSON-type mapping in 04.0/01 is only as good as the survey of what the real collection
holds. Marked `integration` (excluded from the default run) and gated on
TEST_MONGODB_URI_PASS, so it skips cleanly without credentials.

- Test A seeds and pulls a throwaway pull_it_<uuid4> collection (dropped on teardown),
  asserting count, order, and that the output validates as a staging file.
- Test B is READ-ONLY against the real books collection: it runs iter_collection +
  to_staging_document over every document (no file written), asserts no ExportError, and -
  with a CommandListener attached - asserts zero write commands were issued. The printed
  per-field BSON types are the evidence for the 04.0/01 mapping.
"""

import copy
import os
import uuid
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pymongo import MongoClient, monitoring, server_api

from bookscraper.backends.local.staging import load_staging, validate_staging_documents
from bookscraper.backends.mongo.export import ExportError, iter_collection, to_staging_document
from bookscraper.commands import pull
from tests.integration.conftest import require_env

WRITE_COMMANDS = frozenset(
    {"insert", "update", "delete", "findAndModify", "createIndexes", "dropIndexes", "create", "drop"}
)


class _WriteRecorder(monitoring.CommandListener):
    """Records the name of every command the driver starts, so a test can prove it issued no writes."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        self.commands.append(event.command_name)

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:  # pragma: no cover - unused
        pass

    def failed(self, event: monitoring.CommandFailedEvent) -> None:  # pragma: no cover - unused
        pass

    def write_commands(self) -> list[str]:
        return [name for name in self.commands if name in WRITE_COMMANDS]


def _test_database(client: MongoClient):
    """The URI's default database, else MONGODB_DB_NAME (default bookscraper_db) - never hardcoded."""
    try:
        default = client.get_default_database()
        if default is not None:
            return default
    except Exception:
        pass
    return client[os.environ.get("MONGODB_DB_NAME", "bookscraper_db")]


def _seed_docs() -> list[dict]:
    return [
        {"site": "amazon", "title": "Pull A", "authors": ["X"], "url": "https://x/a", "publication_year": 2020},
        {
            "site": "leanpub.com",
            "title": "Pull ☕ B",
            "authors": ["Y"],
            "url": "https://x/b",
            "last_published_at": datetime(2021, 5, 5, 8, 30, 0, tzinfo=UTC),
        },
        {"site": "oreilly", "title": "Pull C", "authors": [], "url": "https://x/c", "tags": []},
    ]


@pytest.fixture
def pull_collection():
    """A fresh pull_it_<uuid4> collection on the real test cluster, dropped on teardown."""
    uri = require_env("TEST_MONGODB_URI_PASS")
    client = MongoClient(uri, server_api=server_api.ServerApi("1"), serverSelectionTimeoutMS=10000)
    try:
        database = _test_database(client)
        name = f"pull_it_{uuid.uuid4().hex}"
        collection = database[name]
        try:
            yield collection
        finally:
            database.drop_collection(name)
    finally:
        client.close()


async def _run_pull(collection, output: Path) -> None:
    with (
        patch.object(pull.database, "get_mongo_collection", return_value=collection),
        patch.object(pull.database, "close_mongo_connection"),
    ):
        await pull.run(Namespace(output=str(output), force=True))


@pytest.mark.integration
async def test_pull_throwaway_collection(pull_collection, tmp_path) -> None:
    """Integration: pull exports a seeded throwaway collection, sorted by _id, as a valid staging file.

    Scenario: insert 3 docs (one with a datetime, one unicode title), pull to tmp_path, reload and validate.
    Boundaries: real throwaway collection; real find/sort; export BSON mapping over real data.
    On failure: a real export miscounts, misorders, or produces a file that push would reject.
    """
    collection = pull_collection
    inserted_ids = [collection.insert_one(copy.deepcopy(fields)).inserted_id for fields in _seed_docs()]
    output = tmp_path / "export.json"

    await _run_pull(collection, output)

    exported = load_staging(output, missing_ok=False)
    assert len(exported) == len(inserted_ids)
    assert [d["_id"] for d in exported] == [str(i) for i in sorted(inserted_ids)]
    assert validate_staging_documents(exported) == []
    assert all(d["_staging"] == {"status": "pending", "source": "pull"} for d in exported)


@pytest.mark.integration
def test_real_collection_exports_without_unsupported_types() -> None:
    """Integration: every real books document exports losslessly, and the scan issues zero write commands.

    Scenario: iterate the real books collection, convert each to a staging document, record per-field BSON types.
    Boundaries: READ-ONLY against the real collection; a CommandListener asserts no insert/update/delete/index writes.
    On failure: a BSON type exists in production that export can't represent, or the read path secretly writes.
    """
    uri = require_env("TEST_MONGODB_URI_PASS")
    recorder = _WriteRecorder()
    client = MongoClient(
        uri,
        server_api=server_api.ServerApi("1"),
        serverSelectionTimeoutMS=10000,
        event_listeners=[recorder],
    )
    types_by_field: dict[str, set[str]] = {}
    scanned = 0
    try:
        database = _test_database(client)
        collection = database[os.environ.get("MONGODB_COLLECTION_NAME", "books")]
        for raw in iter_collection(collection):
            for key, value in raw.items():
                types_by_field.setdefault(key, set()).add(type(value).__name__)
            try:
                to_staging_document(raw)
            except ExportError as e:
                pytest.fail(f"ExportError on a real books document: {e}")
            scanned += 1
    finally:
        client.close()

    # Evidence for 04.0/01's mapping - printed to test output, not written to a report file.
    print(f"\n[pull-export] scanned {scanned} real books documents; BSON types per field:")
    for field in sorted(types_by_field):
        print(f"  {field}: {sorted(types_by_field[field])}")

    writes = recorder.write_commands()
    assert writes == [], f"read-only export issued write commands: {writes}"
