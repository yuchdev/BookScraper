"""Mock tests for bookscraper.commands.push - step 2 of the staged pipeline (03.0/03-04).

MongoDB is the in-memory FakeCollection behind a patched database.get_mongo_collection;
the staging file and report are real files under tmp_path (the working directory).
"""

import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from bookscraper.backends.local.staging import compute_document_hash, write_staging
from bookscraper.backends.mongo.export import normalize_document
from bookscraper.commands import push
from tests.mock.backends.fake_collection import FakeCollection


@pytest.fixture(autouse=True)
def _cwd(isolated_staging_file):
    return isolated_staging_file


def _book(title: str, **extra) -> dict:
    doc = {"site": "amazon", "url": f"https://x/{title}", "title": title, "authors": ["A"], "publication_year": 2020}
    doc.update(extra)
    doc["hash"] = compute_document_hash(doc)
    return doc


def _args(tmp_path, **flags) -> Namespace:
    defaults = {
        "staging_file": str(tmp_path / "staging" / "books.json"),
        "report_file": str(tmp_path / "push_report.json"),
        "dry_run": False,
        "allow_updates": False,
        "approved_only": False,
    }
    defaults.update(flags)
    return Namespace(**defaults)


def _stage(tmp_path, documents) -> None:
    write_staging(tmp_path / "staging" / "books.json", documents)


def _report(tmp_path) -> list[dict]:
    return json.loads((tmp_path / "push_report.json").read_text(encoding="utf-8"))


async def _push(tmp_path, collection, **flags):
    """Runs push.run with the collection patched in; returns (exit code or None, get_mongo_collection mock)."""
    with (
        patch.object(push.database, "get_mongo_collection", return_value=collection) as get,
        patch.object(push.database, "close_mongo_connection") as close,
    ):
        try:
            await push.run(_args(tmp_path, **flags))
            code = None
        except SystemExit as e:
            code = e.code
    if collection is not None and get.called:
        close.assert_called_once_with()
    return code, get


# --------------------------------------------------------------------------- #
# refusals before any write (exit 1)
# --------------------------------------------------------------------------- #
async def test_missing_staging_file_exits_1(tmp_path) -> None:
    """
    [Unit] push: verifies missing staging file exits 1.

    Scenario:
        Given test inputs and fixtures for missing staging file exits 1
        When test_missing_staging_file_exits_1 executes the target flow
        Then the expected outcome for missing staging file exits 1 is confirmed.

    Boundaries:
        - Focus: missing staging file exits 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to missing staging file exits 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    code, get = await _push(tmp_path, FakeCollection(read_only=True))
    assert code == 1
    get.assert_not_called()  # refused before connecting


async def test_validation_problems_all_printed_exit_1(tmp_path) -> None:
    """
    [Unit] push: verifies validation problems all printed exit 1.

    Scenario:
        Given test inputs and fixtures for validation problems all printed exit 1
        When test_validation_problems_all_printed_exit_1 executes the target flow
        Then the expected outcome for validation problems all printed exit 1 is confirmed.

    Boundaries:
        - Focus: validation problems all printed exit 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to validation problems all printed exit 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("ok"), {**_book(""), "$where": "1"}, {**_book("x"), "_id": "bad"}])
    with patch.object(push, "print_log") as log:
        code, get = await _push(tmp_path, FakeCollection(read_only=True))
    assert code == 1
    get.assert_not_called()
    printed = "\n".join(c.args[0] for c in log.call_args_list)
    assert "document #1, title" in printed and "document #1, $where" in printed and "document #2, _id" in printed
    assert "3 problem(s)" in printed
    assert not (tmp_path / "push_report.json").exists()


async def test_oversized_document_refused(tmp_path) -> None:
    """
    [Unit] push: verifies oversized document refused.

    Scenario:
        Given test inputs and fixtures for oversized document refused
        When test_oversized_document_refused executes the target flow
        Then the expected outcome for oversized document refused is confirmed.

    Boundaries:
        - Focus: oversized document refused
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to oversized document refused
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("Big")])
    with patch.object(push, "oversized_documents", return_value=["document #0: too big"]):
        code, get = await _push(tmp_path, FakeCollection(read_only=True))
    assert code == 1
    get.assert_not_called()


async def test_connection_failure_exits_1(tmp_path) -> None:
    """
    [Unit] push: verifies connection failure exits 1.

    Scenario:
        Given test inputs and fixtures for connection failure exits 1
        When test_connection_failure_exits_1 executes the target flow
        Then the expected outcome for connection failure exits 1 is confirmed.

    Boundaries:
        - Focus: connection failure exits 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to connection failure exits 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A")])
    code, get = await _push(tmp_path, None)
    assert code == 1
    get.assert_called_once_with(ensure_indexes=True)


async def test_plan_error_exits_1_without_writing(tmp_path) -> None:
    """
    [Unit] push: verifies plan error exits 1 without writing.

    Scenario:
        Given test inputs and fixtures for plan error exits 1 without writing
        When test_plan_error_exits_1_without_writing executes the target flow
        Then the expected outcome for plan error exits 1 without writing is confirmed.

    Boundaries:
        - Focus: plan error exits 1 without writing
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to plan error exits 1 without writing
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [{**_book("Gone"), "_id": "6744e7c2a1b2c3d4e5f60718"}])
    collection = FakeCollection(read_only=True)
    code, _ = await _push(tmp_path, collection)
    assert code == 1 and "bulk_write" not in collection.calls


async def test_reading_existing_documents_failure_exits_1(tmp_path) -> None:
    """
    [Unit] push: verifies reading existing documents failure exits 1.

    Scenario:
        Given test inputs and fixtures for reading existing documents failure exits 1
        When test_reading_existing_documents_failure_exits_1 executes the target flow
        Then the expected outcome for reading existing documents failure exits 1 is confirmed.

    Boundaries:
        - Focus: reading existing documents failure exits 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to reading existing documents failure exits 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A")])
    with (
        patch.object(push, "fetch_existing", side_effect=RuntimeError("mongodb+srv://u:p@h")),
        patch.object(push, "print_log") as log,
    ):
        code, _ = await _push(tmp_path, FakeCollection(read_only=True))
    assert code == 1
    assert "p@h" not in "\n".join(c.args[0] for c in log.call_args_list)


async def test_export_error_while_comparing_exits_1(tmp_path) -> None:
    """
    [Unit] push: verifies export error while comparing exits 1.

    Scenario:
        Given test inputs and fixtures for export error while comparing exits 1
        When test_export_error_while_comparing_exits_1 executes the target flow
        Then the expected outcome for export error while comparing exits 1 is confirmed.

    Boundaries:
        - Focus: export error while comparing exits 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to export error while comparing exits 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    from bookscraper.backends.mongo.export import ExportError

    _stage(tmp_path, [_book("A")])
    with patch.object(push, "fetch_existing", side_effect=ExportError("Field 'x' holds a Decimal128")):
        code, _ = await _push(tmp_path, FakeCollection(read_only=True))
    assert code == 1


async def test_updates_without_flag_refused_before_any_write(tmp_path) -> None:
    """Mock: an edited pulled document is never replaced unless --allow-updates is given.

    Scenario: file holds one new book and one edited pulled book; push without --allow-updates.
    Boundaries: FakeCollection; the refusal report is written, MongoDB is not.
    On failure: a push would silently overwrite existing database documents (ADR 0003: updates are opt-in).
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Live", description="old"))
    _stage(tmp_path, [_book("New"), {**normalize_document(collection.get(oid)), "description": "new"}])

    code, _ = await _push(tmp_path, collection)

    assert code == 1
    assert "bulk_write" not in collection.calls and len(collection.documents) == 1
    assert [r["outcome"] for r in _report(tmp_path)] == ["refused", "refused"]


# --------------------------------------------------------------------------- #
# dry run
# --------------------------------------------------------------------------- #
async def test_dry_run_makes_no_write_calls(tmp_path) -> None:
    """
    [Unit] push: verifies dry run makes no write calls.

    Scenario:
        Given test inputs and fixtures for dry run makes no write calls
        When test_dry_run_makes_no_write_calls executes the target flow
        Then the expected outcome for dry run makes no write calls is confirmed.

    Boundaries:
        - Focus: dry run makes no write calls
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to dry run makes no write calls
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    collection = FakeCollection(read_only=True)  # any write raises AssertionError
    oid = collection.seed(_book("Live", description="old"))
    _stage(tmp_path, [_book("New"), {**normalize_document(collection.get(oid)), "description": "new"}])

    with patch.object(push, "print_log") as log:
        code, get = await _push(tmp_path, collection, dry_run=True)

    assert code is None
    get.assert_called_once_with(ensure_indexes=False)
    report = _report(tmp_path)
    assert [(r["action"], r["outcome"]) for r in report] == [("insert", "planned"), ("update", "planned")]
    assert report[1]["changed_fields"] == ["description"] and report[1]["_id"] == str(oid)
    assert any("Push plan:" in c.args[0] for c in log.call_args_list)


async def test_report_written_on_dry_run_even_for_empty_file(tmp_path) -> None:
    """
    [Unit] push: verifies report written on dry run even for empty file.

    Scenario:
        Given test inputs and fixtures for report written on dry run even for empty file
        When test_report_written_on_dry_run_even_for_empty_file executes the target flow
        Then the expected outcome for report written on dry run even for empty file is confirmed.

    Boundaries:
        - Focus: report written on dry run even for empty file
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to report written on dry run even for empty file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [])
    code, _ = await _push(tmp_path, FakeCollection(read_only=True), dry_run=True)
    assert code is None and _report(tmp_path) == []


# --------------------------------------------------------------------------- #
# real runs
# --------------------------------------------------------------------------- #
async def test_inserts_applied_and_staging_file_untouched(tmp_path) -> None:
    """
    [Unit] push: verifies inserts applied and staging file untouched.

    Scenario:
        Given test inputs and fixtures for inserts applied and staging file untouched
        When test_inserts_applied_and_staging_file_untouched executes the target flow
        Then the expected outcome for inserts applied and staging file untouched is confirmed.

    Boundaries:
        - Focus: inserts applied and staging file untouched
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to inserts applied and staging file untouched
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A"), _book("B"), {**_book("C"), "_staging": {"status": "rejected"}}])
    staging = tmp_path / "staging" / "books.json"
    before = staging.read_bytes()
    collection = FakeCollection()

    code, _ = await _push(tmp_path, collection)

    assert code is None
    assert sorted(d["title"] for d in collection.documents) == ["A", "B"]
    assert all("_staging" not in d for d in collection.documents)
    assert staging.read_bytes() == before
    assert [r["outcome"] for r in _report(tmp_path)] == ["inserted", "inserted", "skipped"]


async def test_updates_with_flag_applied(tmp_path) -> None:
    """
    [Unit] push: verifies updates with flag applied.

    Scenario:
        Given test inputs and fixtures for updates with flag applied
        When test_updates_with_flag_applied executes the target flow
        Then the expected outcome for updates with flag applied is confirmed.

    Boundaries:
        - Focus: updates with flag applied
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to updates with flag applied
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Live", description="old"))
    _stage(tmp_path, [{**normalize_document(collection.get(oid)), "description": "new"}])

    code, _ = await _push(tmp_path, collection, allow_updates=True)

    assert code is None and collection.get(oid)["description"] == "new"
    assert _report(tmp_path)[0]["outcome"] == "updated"


async def test_second_push_is_noop(tmp_path) -> None:
    """
    [Unit] push: verifies second push is noop.

    Scenario:
        Given test inputs and fixtures for second push is noop
        When test_second_push_is_noop executes the target flow
        Then the expected outcome for second push is noop is confirmed.

    Boundaries:
        - Focus: second push is noop
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to second push is noop
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A"), _book("B")])
    collection = FakeCollection()
    await _push(tmp_path, collection)
    collection.calls.clear()

    code, _ = await _push(tmp_path, collection)

    assert code is None and "bulk_write" not in collection.calls
    assert [r["outcome"] for r in _report(tmp_path)] == ["duplicate", "duplicate"]
    assert len(collection.documents) == 2


async def test_report_written_when_all_unchanged(tmp_path) -> None:
    """
    [Unit] push: verifies report written when all unchanged.

    Scenario:
        Given test inputs and fixtures for report written when all unchanged
        When test_report_written_when_all_unchanged executes the target flow
        Then the expected outcome for report written when all unchanged is confirmed.

    Boundaries:
        - Focus: report written when all unchanged
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to report written when all unchanged
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    collection = FakeCollection()
    oid = collection.seed(_book("Live"))
    _stage(tmp_path, [normalize_document(collection.get(oid))])
    code, _ = await _push(tmp_path, collection)
    assert code is None and [r["outcome"] for r in _report(tmp_path)] == ["unchanged"]


async def test_approved_only_pushes_only_approved(tmp_path) -> None:
    """
    [Unit] push: verifies approved only pushes only approved.

    Scenario:
        Given test inputs and fixtures for approved only pushes only approved
        When test_approved_only_pushes_only_approved executes the target flow
        Then the expected outcome for approved only pushes only approved is confirmed.

    Boundaries:
        - Focus: approved only pushes only approved
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to approved only pushes only approved
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("Pending"), {**_book("Yes"), "_staging": {"status": "approved"}}])
    collection = FakeCollection()
    await _push(tmp_path, collection, approved_only=True)
    assert [d["title"] for d in collection.documents] == ["Yes"]
    assert [r["outcome"] for r in _report(tmp_path)] == ["skipped", "inserted"]


async def test_partial_write_error_exits_3(tmp_path) -> None:
    """
    [Unit] push: verifies partial write error exits 3.

    Scenario:
        Given test inputs and fixtures for partial write error exits 3
        When test_partial_write_error_exits_3 executes the target flow
        Then the expected outcome for partial write error exits 3 is confirmed.

    Boundaries:
        - Focus: partial write error exits 3
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to partial write error exits 3
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A"), _book("B")])
    outcomes = [push.ItemOutcome(0, "inserted"), push.ItemOutcome(1, "error", "BulkWriteError (code 121)")]
    with patch.object(push, "apply_push_plan", return_value=outcomes):
        code, _ = await _push(tmp_path, FakeCollection())
    assert code == 3
    report = _report(tmp_path)
    assert [r["outcome"] for r in report] == ["inserted", "error"]
    assert report[1]["error"] == "BulkWriteError (code 121)"


async def test_report_contains_no_secrets(tmp_path, monkeypatch) -> None:
    """
    [Mock] push: verifies report contains no secrets.

    Scenario:
        Given test inputs and fixtures for report contains no secrets
        When test_report_contains_no_secrets executes the target flow
        Then the expected outcome for report contains no secrets is confirmed.

    Boundaries:
        - Focus: report contains no secrets
        - Fixtures/params: tmp_path, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to report contains no secrets
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://user:hunter2@cluster.example/db")
    _stage(tmp_path, [_book("A")])
    await _push(tmp_path, FakeCollection())
    text = (tmp_path / "push_report.json").read_text(encoding="utf-8")
    assert "hunter2" not in text and "mongodb+srv" not in text
    assert set(_report(tmp_path)[0]) == {
        "index",
        "action",
        "outcome",
        "title",
        "hash",
        "hash_changed",
        "_id",
        "changed_fields",
        "error",
    }


async def test_report_write_failure_is_warning_not_crash(tmp_path) -> None:
    """
    [Unit] push: verifies report write failure is warning not crash.

    Scenario:
        Given test inputs and fixtures for report write failure is warning not crash
        When test_report_write_failure_is_warning_not_crash executes the target flow
        Then the expected outcome for report write failure is warning not crash is confirmed.

    Boundaries:
        - Focus: report write failure is warning not crash
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to report write failure is warning not crash
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A")])
    with (
        patch.object(push, "write_staging", side_effect=OSError("read-only fs")),
        patch.object(push, "print_log") as log,
    ):
        code, _ = await _push(tmp_path, FakeCollection())
    assert code is None
    assert any("could not write the push report" in c.args[0] for c in log.call_args_list)


async def test_target_printed_without_uri(tmp_path) -> None:
    """
    [Unit] push: verifies target printed without uri.

    Scenario:
        Given test inputs and fixtures for target printed without uri
        When test_target_printed_without_uri executes the target flow
        Then the expected outcome for target printed without uri is confirmed.

    Boundaries:
        - Focus: target printed without uri
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to target printed without uri
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push
    """
    _stage(tmp_path, [_book("A")])
    with patch.object(push, "print_log") as log:
        await _push(tmp_path, FakeCollection(), dry_run=True)
    printed = "\n".join(c.args[0] for c in log.call_args_list)
    assert "database 'bookscraper_test_db', collection 'books'" in printed
