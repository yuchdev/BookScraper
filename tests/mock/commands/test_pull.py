"""Mock tests for bookscraper.commands.pull - the MongoDB → JSON export (04.0/02).

MongoDB is a read-only FakeCollection (any write fails the test) behind a patched
database.get_mongo_collection; output files live under tmp_path.
"""

import json
from argparse import Namespace
from datetime import datetime
from unittest.mock import patch

import pytest
from bson import Decimal128, ObjectId

from bookscraper.commands import pull
from tests.mock.backends.fake_collection import FakeCollection


@pytest.fixture(autouse=True)
def _cwd(isolated_staging_file):
    return isolated_staging_file


async def _pull(collection, output, force=False):
    with (
        patch.object(pull.database, "get_mongo_collection", return_value=collection) as get,
        patch.object(pull.database, "close_mongo_connection") as close,
    ):
        try:
            await pull.run(Namespace(output=str(output), force=force))
            code = None
        except SystemExit as e:
            code = e.code
    return code, get, close


async def test_pull_writes_every_document_sorted_with_hex_ids(tmp_path) -> None:
    """
    [Unit] pull: verifies pull writes every document sorted with hex ids.

    Scenario:
        Given test inputs and fixtures for pull writes every document sorted with hex ids
        When test_pull_writes_every_document_sorted_with_hex_ids executes the target flow
        Then the expected outcome for pull writes every document sorted with hex ids is confirmed.

    Boundaries:
        - Focus: pull writes every document sorted with hex ids
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pull writes every document sorted with hex ids
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    collection = FakeCollection(read_only=True)
    second = collection.seed({"_id": ObjectId("6744e7c2a1b2c3d4e5f60719"), "title": "B", "at": datetime(2026, 1, 1)})
    first = collection.seed({"_id": ObjectId("6744e7c2a1b2c3d4e5f60718"), "title": "A"})
    output = tmp_path / "staging" / "books_mongo.json"

    code, get, close = await _pull(collection, output)

    assert code is None
    get.assert_called_once_with(ensure_indexes=False)
    close.assert_called_once_with()
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert [d["_id"] for d in exported] == [str(first), str(second)]
    assert exported[1]["at"] == "2026-01-01T00:00:00Z"
    assert all(d["_staging"] == {"status": "pending", "source": "pull"} for d in exported)


async def test_existing_output_without_force_exits_before_connecting(tmp_path) -> None:
    """
    [Unit] pull: verifies existing output without force exits before connecting.

    Scenario:
        Given test inputs and fixtures for existing output without force exits before connecting
        When test_existing_output_without_force_exits_before_connecting executes the target flow
        Then the expected outcome for existing output without force exits before connecting is confirmed.

    Boundaries:
        - Focus: existing output without force exits before connecting
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to existing output without force exits before connecting
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    output = tmp_path / "review.json"
    output.write_text("[]  my review work", encoding="utf-8")
    code, get, _ = await _pull(FakeCollection(read_only=True), output)
    assert code == 1
    get.assert_not_called()
    assert output.read_text(encoding="utf-8") == "[]  my review work"


async def test_force_overwrites_atomically(tmp_path) -> None:
    """
    [Unit] pull: verifies force overwrites atomically.

    Scenario:
        Given test inputs and fixtures for force overwrites atomically
        When test_force_overwrites_atomically executes the target flow
        Then the expected outcome for force overwrites atomically is confirmed.

    Boundaries:
        - Focus: force overwrites atomically
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to force overwrites atomically
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    collection = FakeCollection(read_only=True)
    collection.seed({"title": "A"})
    output = tmp_path / "out.json"
    output.write_text("old", encoding="utf-8")
    code, _, _ = await _pull(collection, output, force=True)
    assert code is None and json.loads(output.read_text(encoding="utf-8"))[0]["title"] == "A"
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]  # no temp file left behind


async def test_connection_failure_exits_1(tmp_path) -> None:
    """
    [Unit] pull: verifies connection failure exits 1.

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
        - Recent changes in code paths exercised by pull
    """
    code, _, _ = await _pull(None, tmp_path / "out.json")
    assert code == 1 and not (tmp_path / "out.json").exists()


async def test_export_error_leaves_file_untouched(tmp_path) -> None:
    """
    [Unit] pull: verifies export error leaves file untouched.

    Scenario:
        Given test inputs and fixtures for export error leaves file untouched
        When test_export_error_leaves_file_untouched executes the target flow
        Then the expected outcome for export error leaves file untouched is confirmed.

    Boundaries:
        - Focus: export error leaves file untouched
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to export error leaves file untouched
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    collection = FakeCollection(read_only=True)
    collection.seed({"title": "A", "price": Decimal128("9.99")})
    output = tmp_path / "out.json"
    output.write_text("keep me", encoding="utf-8")
    code, _, close = await _pull(collection, output, force=True)
    assert code == 1 and output.read_text(encoding="utf-8") == "keep me"
    close.assert_called_once_with()


async def test_read_failure_reports_class_only(tmp_path) -> None:
    """
    [Unit] pull: verifies read failure reports class only.

    Scenario:
        Given test inputs and fixtures for read failure reports class only
        When test_read_failure_reports_class_only executes the target flow
        Then the expected outcome for read failure reports class only is confirmed.

    Boundaries:
        - Focus: read failure reports class only
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to read failure reports class only
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    collection = FakeCollection(read_only=True)
    with (
        patch.object(pull, "export_collection", side_effect=RuntimeError("mongodb+srv://u:secret@h")),
        patch.object(pull, "print_log") as log,
    ):
        code, _, _ = await _pull(collection, tmp_path / "out.json")
    assert code == 1
    printed = "\n".join(c.args[0] for c in log.call_args_list)
    assert "RuntimeError" in printed and "secret" not in printed


async def test_unwritable_output_exits_1(tmp_path) -> None:
    """
    [Unit] pull: verifies unwritable output exits 1.

    Scenario:
        Given test inputs and fixtures for unwritable output exits 1
        When test_unwritable_output_exits_1 executes the target flow
        Then the expected outcome for unwritable output exits 1 is confirmed.

    Boundaries:
        - Focus: unwritable output exits 1
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unwritable output exits 1
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    with patch.object(pull, "write_staging", side_effect=OSError("read-only fs")):
        code, _, _ = await _pull(FakeCollection(read_only=True), tmp_path / "out.json")
    assert code == 1


async def test_pull_never_calls_write_methods(tmp_path) -> None:
    """
    [Unit] pull: verifies pull never calls write methods.

    Scenario:
        Given test inputs and fixtures for pull never calls write methods
        When test_pull_never_calls_write_methods executes the target flow
        Then the expected outcome for pull never calls write methods is confirmed.

    Boundaries:
        - Focus: pull never calls write methods
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pull never calls write methods
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    collection = FakeCollection(read_only=True)
    collection.seed({"title": "A"})
    await _pull(collection, tmp_path / "out.json")
    assert set(collection.calls) == {"find"}


async def test_empty_collection_writes_empty_array(tmp_path) -> None:
    """
    [Unit] pull: verifies empty collection writes empty array.

    Scenario:
        Given test inputs and fixtures for empty collection writes empty array
        When test_empty_collection_writes_empty_array executes the target flow
        Then the expected outcome for empty collection writes empty array is confirmed.

    Boundaries:
        - Focus: empty collection writes empty array
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to empty collection writes empty array
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by pull
    """
    output = tmp_path / "out.json"
    code, _, _ = await _pull(FakeCollection(read_only=True), output)
    assert code is None and json.loads(output.read_text(encoding="utf-8")) == []
