"""Mock-tier tests for bookscraper.commands.migrate and the mongo apply path.

The mongo backend is an in-memory mongomock collection with the real production indexes, so the
unique ``hash`` index behaves exactly as Atlas would; the json backend writes to a tmp_path store
via isolated_local_store. resolve_store_backend is patched so run() never opens a real connection,
and book_utils.LOG_DIR is redirected to tmp_path so the migration report is written in isolation -
no test touches the real ~/.bookscrapper or repo-root books.json.
"""

import hashlib
import json
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

import mongomock
import pytest
from pymongo.errors import BulkWriteError

from bookscraper import book_utils, exitcodes
from bookscraper.backends import migration
from bookscraper.backends.local import store
from bookscraper.backends.mongo import database
from bookscraper.backends.storage import JsonBackend, MongoBackend
from bookscraper.book_utils import hash_book
from bookscraper.cli import build_parser
from bookscraper.commands import migrate
from bookscraper.models import SCHEMA_VERSION, validate_document
from bookscraper.scraping import normalize

_TRANSFORM = normalize.from_v1_document

MODULE = "bookscraper.commands.migrate"


def _amazon_v1(asin: str, title: str) -> dict:
    """A distinct v1 Amazon document for the given ASIN/title."""
    return {
        "asin": asin,
        "authors": ["Author " + asin],
        "description": "desc",
        "hash": hashlib.sha256(asin.encode()).hexdigest(),
        "isbn10": "N/A",
        "isbn13": "N/A",
        "publication_date": "September 2016",
        "publication_year": 2016,
        "site": "amazon",
        "tags": ["C++"],
        "title": title,
        "url": f"https://www.amazon.com/dp/{asin}",
    }


def _args(store_backend: str, apply: bool = False) -> SimpleNamespace:
    """Build an argparse-style namespace for migrate.run()."""
    return SimpleNamespace(command="migrate", store_backend=store_backend, apply=apply, log_severity="info")


def _mongo_collection() -> mongomock.collection.Collection:
    """A fresh in-memory books collection with the production indexes applied."""
    collection = mongomock.MongoClient().db.books
    database.ensure_indexes(collection)
    return collection


def _bulk_executor(collection) -> Callable:
    """A ``bulk_write`` stand-in that replays each ReplaceOne via ``replace_one``.

    mongomock 4.3.0's bulk builder rejects the ``sort`` keyword pymongo 4.18's ReplaceOne passes,
    so an in-memory ``bulk_write`` of a ReplaceOne raises ``TypeError`` even though real Atlas
    accepts it. Production deliberately uses ``bulk_write`` (subtask 06 spec); the test substitutes
    this executor so the guarded ReplaceOne operations still run against the fake collection and the
    resulting end state can be asserted, while the spy keeps counting the batched calls.

    :param collection: The mongomock collection to apply the operations to.
    :returns: A side-effect callable accepting the ``(requests, ...)`` bulk_write signature.
    """

    def _execute(requests, *args, **kwargs) -> SimpleNamespace:
        """Apply each ReplaceOne through replace_one and return a minimal result stub."""
        for op in requests:
            collection.replace_one(op._filter, op._doc)
        return SimpleNamespace(modified_count=len(requests))

    return _execute


class TestMongoApply:
    """Tests for migration.migrate_mongo_collection()."""

    def test_apply_mongo_uses_guarded_replace_one_batches(self):
        """
        [Unit] migration mongo apply: verifies guarded ReplaceOne operations in bounded batches.

        Scenario:
            Given a mongomock collection holding three distinct v1 documents
            When migrate_mongo_collection runs with apply=True and batch_size=2
            Then bulk_write is called twice (batches of 2 and 1), every operation is a ReplaceOne
                filtered on {"_id": id, "schema_version": {"$ne": 2}} with a v2 replacement carrying
                no _id, and afterwards every stored document is schema-2 and valid

        Boundaries:
            - Batch size caps operations per bulk_write call
            - The $ne guard makes the replace skip an already-v2 document (idempotency)

        On failure, first check:
            - The ReplaceOne filter and batching loop in migrate_mongo_collection
            - Whether the replacement document still omits _id
        """
        collection = _mongo_collection()
        collection.insert_many(
            [_amazon_v1("A0000000001", "One"), _amazon_v1("A0000000002", "Two"), _amazon_v1("A0000000003", "Three")]
        )

        with patch.object(collection, "bulk_write", side_effect=_bulk_executor(collection)) as spy:
            report = migration.migrate_mongo_collection(collection, apply=True, transform=_TRANSFORM, batch_size=2)

        assert spy.call_count == 2
        operations = [op for call in spy.call_args_list for op in call.args[0]]
        assert len(operations) == 3
        for op in operations:
            assert set(op._filter) == {"_id", "schema_version"}
            assert op._filter["schema_version"] == {"$ne": SCHEMA_VERSION}
            assert "_id" not in op._doc
            assert op._doc["schema_version"] == SCHEMA_VERSION

        assert report.migrated == 3
        stored = list(collection.find({}))
        assert len(stored) == 3
        for doc in stored:
            assert doc["schema_version"] == SCHEMA_VERSION
            doc.pop("_id", None)
            assert validate_document(doc) == []

    def test_dry_run_writes_nothing_mongo(self):
        """
        [Unit] migration mongo dry-run: verifies apply=False issues no write to the collection.

        Scenario:
            Given a mongomock collection holding one v1 document
            When migrate_mongo_collection runs with apply=False
            Then bulk_write is never called, the stored document is still v1 (no schema_version),
                and the report counts one pending migration with applied False

        Boundaries:
            - apply=False must not mutate the collection
            - The report still reflects the pending change

        On failure, first check:
            - The ``if apply and plan.rewrites`` guard in migrate_mongo_collection
        """
        collection = _mongo_collection()
        collection.insert_one(_amazon_v1("A0000000001", "One"))

        with patch.object(collection, "bulk_write", wraps=collection.bulk_write) as spy:
            report = migration.migrate_mongo_collection(collection, apply=False, transform=_TRANSFORM)

        spy.assert_not_called()
        assert report.applied is False
        assert report.migrated == 1
        assert "schema_version" not in collection.find_one({})

    def test_apply_mongo_bulk_write_error_is_caught_and_reported_redacted(self):
        """
        [Unit] migration mongo apply: verifies a mid-run BulkWriteError is caught, counted, redacted.

        Scenario:
            Given a mongomock collection with three v1 documents and batch_size=2, where the first
                bulk_write batch applies but the second raises a BulkWriteError whose message embeds
                a document value
            When migrate_mongo_collection runs with apply=True
            Then it does not propagate the traceback; the report flags apply_failed, records
                applied_count=2 / failed_count=1, and apply_error carries only the redacted class +
                pymongo code (never the embedded document value)

        Boundaries:
            - Invalid/failure path: a pymongo error mid-run must not abort before the report
            - Redaction: raw pymongo text (which can carry key values) never reaches the report

        On failure, first check:
            - The ``except (BulkWriteError, PyMongoError)`` guard and applied_count math in
              migrate_mongo_collection
            - That apply_error comes from client._redact_pymongo_error (class + code only)
        """
        collection = _mongo_collection()
        collection.insert_many(
            [_amazon_v1("A0000000001", "One"), _amazon_v1("A0000000002", "Two"), _amazon_v1("A0000000003", "Three")]
        )
        calls = {"n": 0}

        def _fail_on_second(requests, *args, **kwargs) -> SimpleNamespace:
            """Apply the first batch, then raise a BulkWriteError carrying a secret-looking value."""
            calls["n"] += 1
            if calls["n"] == 1:
                for op in requests:
                    collection.replace_one(op._filter, op._doc)
                return SimpleNamespace(modified_count=len(requests))
            raise BulkWriteError({"writeErrors": [{"errmsg": "super-secret-key-value"}], "nInserted": 0})

        with patch.object(collection, "bulk_write", side_effect=_fail_on_second):
            report = migration.migrate_mongo_collection(collection, apply=True, transform=_TRANSFORM, batch_size=2)

        assert report.apply_failed is True
        assert report.applied_count == 2
        assert report.failed_count == 1
        assert "super-secret-key-value" not in (report.apply_error or "")
        assert report.apply_error.startswith("BulkWriteError")

    def test_apply_mongo_twice_is_a_no_op(self):
        """
        [Unit] migration mongo apply: verifies the second apply rewrites nothing (idempotent).

        Scenario:
            Given a mongomock collection already migrated once with apply=True
            When migrate_mongo_collection runs a second time with apply=True
            Then the second report shows zero migrated and bulk_write is not called

        Boundaries:
            - Success criterion: a second --apply produces zero changes
            - The guarded filter and the unchanged branch together make the re-run inert

        On failure, first check:
            - transform_v1_to_v2 idempotency on already-stored v2 documents
        """
        collection = _mongo_collection()
        collection.insert_many([_amazon_v1("A0000000001", "One"), _amazon_v1("A0000000002", "Two")])
        with patch.object(collection, "bulk_write", side_effect=_bulk_executor(collection)):
            migration.migrate_mongo_collection(collection, apply=True, transform=_TRANSFORM)

        with patch.object(collection, "bulk_write", side_effect=_bulk_executor(collection)) as spy:
            report = migration.migrate_mongo_collection(collection, apply=True, transform=_TRANSFORM)

        spy.assert_not_called()
        assert report.migrated == 0


class TestRunCommand:
    """Tests for migrate.run() across both backends."""

    @pytest.mark.asyncio
    async def test_run_json_apply_migrates_and_writes_report(self, isolated_local_store, tmp_path, monkeypatch):
        """
        [Local] migrate run json: verifies run(--apply) migrates the json store and writes a report.

        Scenario:
            Given resolve_store_backend returns a real JsonBackend over a tmp_path store holding one
                v1 document, and the run log dir is redirected to tmp_path
            When migrate.run is awaited with store_backend=json and apply=True
            Then the store holds a valid schema-2 document and a migration-report-*.json file was
                written to the redirected log dir

        Boundaries:
            - End-to-end run wiring (resolve -> migrate -> write_report -> summary)
            - Uses the isolated store and log dir; never the real paths

        On failure, first check:
            - migrate.run's backend-type branch and report-path assembly
            - Whether book_utils.LOG_DIR was honored at call time
        """
        isolated_local_store.write_text(json.dumps([_amazon_v1("A0000000001", "One")]), encoding="utf-8")
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(book_utils, "LOG_DIR", log_dir)

        with patch(f"{MODULE}.resolve_store_backend", return_value=JsonBackend()):
            await migrate.run(_args("json", apply=True))

        stored = store._load_books(isolated_local_store)
        assert stored[0]["schema_version"] == SCHEMA_VERSION
        assert validate_document(stored[0]) == []
        reports = list(log_dir.glob("migration-report-*.json"))
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_run_json_reports_collision_and_invalid_without_leaking_body(
        self, isolated_local_store, tmp_path, monkeypatch, capsys
    ):
        """
        [Local] migrate run json: verifies collisions and invalid documents are summarized safely.

        Scenario:
            Given a store with a document whose de-duplicated hash collides with another's, plus a
                document invalid after migration, and the log dir redirected to tmp_path
            When migrate.run is awaited with apply=True
            Then the console summary names the collision and invalid document by id/hash only (no
                title or description), and the written report records both

        Boundaries:
            - Exercises the collision and invalid summary print branches
            - Secret/body safety: no document title appears in the printed summary

        On failure, first check:
            - The hash_collisions / invalid_documents loops in migrate._print_summary
            - That only ids/hashes/counts reach the console
        """
        shared_hash = hash_book("Shared Title", ["Jane Doe"], 2016)
        colliding = _amazon_v1("A0000000009", "Shared Title")
        colliding["authors"] = ["Jane Doe", "jane doe"]
        canonical = _amazon_v1("A0000000010", "Shared Title")
        canonical["authors"] = ["Jane Doe"]
        canonical["hash"] = shared_hash
        invalid = _amazon_v1("A0000000011", "Secret Prose Title")
        invalid["site"] = "bogus-site"
        monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
        isolated_local_store.write_text(json.dumps([colliding, canonical, invalid]), encoding="utf-8")

        with patch(f"{MODULE}.resolve_store_backend", return_value=JsonBackend()):
            await migrate.run(_args("json", apply=True))

        out = capsys.readouterr().out
        assert "hash collision" in out
        assert "invalid after migration" in out
        assert "Secret Prose Title" not in out
        report = json.loads(next((tmp_path / "logs").glob("migration-report-*.json")).read_text(encoding="utf-8"))
        assert report["counts"]["collisions"] == 1
        assert report["counts"]["invalid"] == 1

    @pytest.mark.asyncio
    async def test_run_mongo_apply_failure_writes_report_then_exits_one(self, tmp_path, monkeypatch):
        """
        [Unit] migrate run mongo: verifies an apply failure still writes the report, then exits 1.

        Scenario:
            Given a MongoBackend over a mongomock collection whose bulk_write raises a
                BulkWriteError, and the log dir redirected to tmp_path
            When migrate.run is awaited with apply=True
            Then it returns exitcodes.FATAL (1) AFTER writing a migration-report-*.json whose apply
                block records failed=true - never a raw traceback and never a skipped report

        Boundaries:
            - Partial-failure path: the audit artifact is written before the non-zero return
            - The run returns non-zero so a CI/cron job notices the incomplete migration

        On failure, first check:
            - The ``if report.apply_failed: return exitcodes.FATAL`` tail of migrate.run (after write_report)
            - That write_report runs before the return
        """
        collection = _mongo_collection()
        collection.insert_one(_amazon_v1("A0000000001", "One"))
        monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")

        def _raise(requests, *args, **kwargs):
            """Raise a BulkWriteError to simulate an Atlas write failure mid-run."""
            raise BulkWriteError({"writeErrors": [{"errmsg": "boom"}], "nInserted": 0})

        with patch.object(collection, "bulk_write", side_effect=_raise):
            with patch(f"{MODULE}.resolve_store_backend", return_value=MongoBackend(collection)):
                code = await migrate.run(_args("mongo", apply=True))

        assert code == exitcodes.FATAL
        report = json.loads(next((tmp_path / "logs").glob("migration-report-*.json")).read_text(encoding="utf-8"))
        assert report["apply"]["failed"] is True
        assert report["apply"]["error"].startswith("BulkWriteError")

    @pytest.mark.asyncio
    async def test_run_json_summary_strips_control_chars_from_page_derived_id(
        self, isolated_local_store, tmp_path, monkeypatch, capsys
    ):
        """
        [Local] migrate run json: verifies a page-derived id with ANSI/control chars is sanitized on print.

        Scenario:
            Given a json store whose document url (the report id for the json backend) carries an
                ESC/ANSI escape and a C1 control byte, and a second document whose de-duplicated
                hash collides with it so the id reaches the collision summary line
            When migrate.run is awaited with apply=True
            Then the printed summary contains neither the raw ESC byte nor the C1 byte (spoofing is
                prevented), while the printable remainder of the url survives

        Boundaries:
            - Security: a hostile page-derived id must not emit terminal escapes into the console
            - Reuses scraping.safe_text.sanitize_untrusted via migrate._print_summary

        On failure, first check:
            - The sanitize_untrusted wrapping of entry ids in migrate._print_summary
        """
        shared_hash = hash_book("Shared Title", ["Jane Doe"], 2016)
        colliding = _amazon_v1("A0000000009", "Shared Title")
        colliding["authors"] = ["Jane Doe", "jane doe"]
        colliding["url"] = "https://evil.example/\x1b[31m\x85book"
        canonical = _amazon_v1("A0000000010", "Shared Title")
        canonical["authors"] = ["Jane Doe"]
        canonical["hash"] = shared_hash
        monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
        isolated_local_store.write_text(json.dumps([colliding, canonical]), encoding="utf-8")

        with patch(f"{MODULE}.resolve_store_backend", return_value=JsonBackend()):
            await migrate.run(_args("json", apply=True))

        out = capsys.readouterr().out
        assert "hash collision" in out
        # print_log adds its own ANSI colour codes around each line, so assert on the injected
        # sequence itself: the ESC before "[31m" and the C1 NEL must be gone, the text kept.
        assert "\x1b[31m" not in out
        assert "\x85" not in out
        assert "evil.example/[31mbook" in out

    @pytest.mark.asyncio
    async def test_run_mongo_dry_run_leaves_collection_untouched(self, tmp_path, monkeypatch):
        """
        [Unit] migrate run mongo: verifies a dry-run run() does not mutate the mongo collection.

        Scenario:
            Given resolve_store_backend returns a MongoBackend over a mongomock collection with one
                v1 document, and the run log dir is redirected to tmp_path
            When migrate.run is awaited with store_backend=mongo and apply=False
            Then the document is still v1 and the report file records applied=false

        Boundaries:
            - Dry run through the command surface (not just the engine)
            - MongoBackend.close() is still called (no connection leak path)

        On failure, first check:
            - migrate.run's isinstance(backend, MongoBackend) branch
            - That apply defaulted to False
        """
        collection = _mongo_collection()
        collection.insert_one(_amazon_v1("A0000000001", "One"))
        monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")

        with patch(f"{MODULE}.resolve_store_backend", return_value=MongoBackend(collection)):
            await migrate.run(_args("mongo", apply=False))

        assert "schema_version" not in collection.find_one({})
        report = json.loads(next((tmp_path / "logs").glob("migration-report-*.json")).read_text(encoding="utf-8"))
        assert report["applied"] is False

    @pytest.mark.asyncio
    async def test_run_json_corrupt_store_exits_one(self, isolated_local_store, tmp_path, monkeypatch):
        """
        [Local] migrate run json: verifies a corrupt store surfaces as a loud non-zero exit.

        Scenario:
            Given a tmp_path store file that is not valid JSON, with resolve_store_backend patched
                to a JsonBackend (bypassing its own pre-flight) and the log dir redirected
            When migrate.run is awaited
            Then it returns exitcodes.FATAL (1) rather than clobbering the unreadable store

        Boundaries:
            - Fail loudly on a corrupt store; never overwrite it
            - Invalid-input path for the command

        On failure, first check:
            - The except LocalStoreCorruptError branch in migrate.run
            - store._load_books(raise_on_corrupt=True) behavior
        """
        isolated_local_store.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")

        with patch(f"{MODULE}.resolve_store_backend", return_value=JsonBackend()):
            code = await migrate.run(_args("json", apply=True))

        assert code == exitcodes.FATAL


class TestCliSurface:
    """Tests for the migrate subcommand's argparse surface."""

    def test_cli_migrate_requires_store_backend(self):
        """
        [Unit] migrate CLI: verifies --store-backend is mandatory for the migrate subcommand.

        Scenario:
            Given the top-level parser
            When 'migrate' is parsed with no --store-backend
            Then argparse exits with an error (SystemExit), matching the scraping subcommands'
                explicit-choice rule

        Boundaries:
            - Required flag enforced at parse time (no interactive fallback)
            - Invalid-input path

        On failure, first check:
            - The required=True on migrate's --store-backend in cli.build_parser
        """
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["migrate"])

    def test_cli_migrate_apply_defaults_false(self):
        """
        [Unit] migrate CLI: verifies --apply is an opt-in flag defaulting to False (dry run).

        Scenario:
            Given the top-level parser
            When 'migrate --store-backend json' is parsed without --apply
            Then args.apply is False, and adding --apply sets it True

        Boundaries:
            - Dry-run-by-default contract lives in the flag default
            - store_backend choice is captured

        On failure, first check:
            - migrate's --apply action="store_true" default in cli.build_parser
        """
        parser = build_parser()
        assert parser.parse_args(["migrate", "--store-backend", "json"]).apply is False
        assert parser.parse_args(["migrate", "--store-backend", "json", "--apply"]).apply is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
