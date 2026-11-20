"""Integration-tier Atlas test for `bookscraper push` (03.0/05).

Mocks prove the plan/writer logic; only a real Atlas run proves bulk_write, ObjectId
round-tripping, and the unique 'hash' index behave as the mock tier assumes. Marked
`integration` (excluded from the default run) and gated on TEST_MONGODB_URI_PASS, so it
skips cleanly without credentials.

Every write lands in a throwaway collection named push_it_<uuid4>; the fixture drops it on
teardown even when the test fails, and never touches the real books collection.
"""

import json
import os
import uuid
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from bson import ObjectId
from pymongo import MongoClient, server_api

from bookscraper.backends.local.staging import compute_document_hash, load_staging, write_staging
from bookscraper.backends.mongo.export import normalize_document
from bookscraper.commands import pull, push
from tests.integration.conftest import require_env


def _test_database(client: MongoClient):
    """The URI's default database, else MONGODB_DB_NAME (default bookscraper_db) - never hardcoded."""
    try:
        default = client.get_default_database()
        if default is not None:
            return default
    except Exception:
        pass
    return client[os.environ.get("MONGODB_DB_NAME", "bookscraper_db")]


@pytest.fixture
def throwaway_collection():
    """A fresh push_it_<uuid4> collection on the real test cluster, dropped on teardown."""
    uri = require_env("TEST_MONGODB_URI_PASS")
    client = MongoClient(uri, server_api=server_api.ServerApi("1"), serverSelectionTimeoutMS=10000)
    try:
        database = _test_database(client)
        name = f"push_it_{uuid.uuid4().hex}"
        collection = database[name]
        try:
            yield collection
        finally:
            database.drop_collection(name)
    finally:
        client.close()


async def _run_push(collection, staging_file: Path, report_file: Path, **flags) -> object:
    args = Namespace(
        staging_file=str(staging_file),
        report_file=str(report_file),
        dry_run=flags.get("dry_run", False),
        allow_updates=flags.get("allow_updates", False),
        approved_only=flags.get("approved_only", False),
    )
    with (
        patch.object(push.database, "get_mongo_collection", return_value=collection),
        patch.object(push.database, "close_mongo_connection"),
    ):
        try:
            await push.run(args)
            return None
        except SystemExit as e:
            return e.code


async def _run_pull(collection, output: Path) -> None:
    with (
        patch.object(pull.database, "get_mongo_collection", return_value=collection),
        patch.object(pull.database, "close_mongo_connection"),
    ):
        await pull.run(Namespace(output=str(output), force=True))


def _outcomes(report_file: Path) -> list[str]:
    return [r["outcome"] for r in json.loads(report_file.read_text(encoding="utf-8"))]


@pytest.mark.integration
async def test_push_insert_duplicate_update_unchanged_cycle(throwaway_collection, tmp_path) -> None:
    """Integration: push inserts, re-detects duplicates, refuses then applies an update, then no-ops, on real Atlas.

    Scenario: push 3 new docs, re-push (duplicate), pull+edit a title, push without then with --allow-updates, re-push.
    Boundaries: real throwaway collection; real bulk_write, unique 'hash' index, ObjectId round-trip; exit codes 1/0.
    On failure: Atlas's real write path disagrees with the mock-tier plan - duplicates, updates, or exit codes are wrong.
    """
    collection = throwaway_collection
    docs = [
        {"site": "amazon", "title": "IT A", "authors": ["X"], "url": "https://x/a", "publication_year": 2020},
        {
            "site": "leanpub.com",
            "title": "IT B",
            "authors": ["Y"],
            "url": "https://x/b",
            "last_published_at": "2021-05-05",
        },
        {"site": "packtpub", "title": "IT C", "authors": ["Z"], "url": "https://x/c", "publication_year": 2019},
    ]
    staging = tmp_path / "staging.json"
    write_staging(staging, docs)

    # 1. Insert 3.
    assert await _run_push(collection, staging, tmp_path / "r1.json") is None
    assert collection.count_documents({}) == 3
    assert _outcomes(tmp_path / "r1.json") == ["inserted", "inserted", "inserted"]

    # 2. Re-push the same file: all duplicates, no writes.
    assert await _run_push(collection, staging, tmp_path / "r2.json") is None
    assert _outcomes(tmp_path / "r2.json") == ["duplicate", "duplicate", "duplicate"]
    assert collection.count_documents({}) == 3

    # 3. Pull them, edit one title.
    export = tmp_path / "export.json"
    await _run_pull(collection, export)
    exported = load_staging(export, missing_ok=False)
    assert len(exported) == 3
    target = next(d for d in exported if d["title"] == "IT A")
    object_id = ObjectId(target["_id"])
    old_hash = collection.find_one({"_id": object_id})["hash"]
    target["title"] = "IT A (edited)"
    write_staging(export, exported)

    # 4. Push without --allow-updates: refused (exit 1), nothing changed.
    assert await _run_push(collection, export, tmp_path / "r3.json") == 1
    assert collection.find_one({"_id": object_id})["title"] == "IT A"

    # 5. Push with --allow-updates: one replace, hash recomputed from the edited title.
    assert await _run_push(collection, export, tmp_path / "r4.json", allow_updates=True) is None
    live = collection.find_one({"_id": object_id})
    assert live["title"] == "IT A (edited)"
    assert live["hash"] != old_hash
    assert live["hash"] == compute_document_hash(normalize_document(live))
    assert collection.count_documents({}) == 3

    # 6. Re-push a fresh pull: everything unchanged.
    export2 = tmp_path / "export2.json"
    await _run_pull(collection, export2)
    assert await _run_push(collection, export2, tmp_path / "r5.json", dry_run=True) is None
    report5 = json.loads((tmp_path / "r5.json").read_text(encoding="utf-8"))
    assert {r["action"] for r in report5} == {"unchanged"}
