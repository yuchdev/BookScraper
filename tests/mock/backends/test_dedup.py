"""Mock tests for bookscraper.backends.dedup - the read-only duplicate checks of step 1 (02.0/02-03).

MongoDB is either the in-memory FakeCollection (read_only=True, so any write fails the test)
or a patched get_mongo_collection; the staging file is real, under tmp_path.
"""

from unittest.mock import MagicMock, patch

import pytest
from pymongo import ReadPreference

from bookscraper.backends import dedup
from bookscraper.backends.dedup import (
    CompositeDedupSource,
    DedupSource,
    MongoDedupSource,
    StagingDedupSource,
    resolve_dedup_source,
)
from bookscraper.backends.local.staging import StagingIndex, write_staging
from tests.mock.backends.fake_collection import FakeCollection


def test_staging_source_detects_hash_asin_leanpub() -> None:
    """
    [Unit] dedup: verifies staging source detects hash asin leanpub.

    Scenario:
        Given test inputs and fixtures for staging source detects hash asin leanpub
        When test_staging_source_detects_hash_asin_leanpub executes the target flow
        Then the expected outcome for staging source detects hash asin leanpub is confirmed.

    Boundaries:
        - Focus: staging source detects hash asin leanpub
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to staging source detects hash asin leanpub
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    source = StagingDedupSource(StagingIndex([{"hash": "h", "asin": "B1", "book_id": "7", "slug": "s"}]))
    assert source.book_exists_by_hash("h") and not source.book_exists_by_hash("x")
    assert source.book_exists_by_asin("B1") and not source.book_exists_by_asin("B2")
    assert source.leanpub_book_exists("7", None) and source.leanpub_book_exists(None, "s")
    assert not source.leanpub_book_exists("8", "t")
    source.close()  # no-op default


def test_mongo_source_lookups_delegate() -> None:
    """
    [Unit] dedup: verifies mongo source lookups delegate.

    Scenario:
        Given test inputs and fixtures for mongo source lookups delegate
        When test_mongo_source_lookups_delegate executes the target flow
        Then the expected outcome for mongo source lookups delegate is confirmed.

    Boundaries:
        - Focus: mongo source lookups delegate
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to mongo source lookups delegate
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    collection = FakeCollection(read_only=True)
    collection.seed({"hash": "h", "asin": "B1", "book_id": "7", "slug": "s"})
    source = MongoDedupSource(collection)

    assert source.book_exists_by_hash("h") and not source.book_exists_by_hash("x")
    assert not source.book_exists_by_hash("")
    assert (
        source.book_exists_by_asin("B1") and not source.book_exists_by_asin("") and not source.book_exists_by_asin("Z")
    )
    assert source.leanpub_book_exists(None, "s") and not source.leanpub_book_exists("8", None)
    assert collection.with_options_kwargs == [{"read_preference": ReadPreference.SECONDARY_PREFERRED}]


def test_mongo_source_close_closes_connection() -> None:
    """
    [Unit] dedup: verifies mongo source close closes connection.

    Scenario:
        Given test inputs and fixtures for mongo source close closes connection
        When test_mongo_source_close_closes_connection executes the target flow
        Then the expected outcome for mongo source close closes connection is confirmed.

    Boundaries:
        - Focus: mongo source close closes connection
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to mongo source close closes connection
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    with patch.object(dedup.database, "close_mongo_connection") as close:
        MongoDedupSource(FakeCollection(read_only=True)).close()
    close.assert_called_once_with()


def test_composite_true_if_any_source_true() -> None:
    """
    [Mock] dedup: verifies composite true if any source true.

    Scenario:
        Given test inputs and fixtures for composite true if any source true
        When test_composite_true_if_any_source_true executes the target flow
        Then the expected outcome for composite true if any source true is confirmed.

    Boundaries:
        - Focus: composite true if any source true
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to composite true if any source true
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    yes, no = MagicMock(spec=DedupSource), MagicMock(spec=DedupSource)
    for method in ("book_exists_by_hash", "book_exists_by_asin", "leanpub_book_exists"):
        getattr(yes, method).return_value = True
        getattr(no, method).return_value = False
    assert CompositeDedupSource(no, yes).book_exists_by_hash("h")
    assert CompositeDedupSource(no, yes).book_exists_by_asin("a")
    assert CompositeDedupSource(no, yes).leanpub_book_exists("1", None)
    assert not CompositeDedupSource(no, no).book_exists_by_hash("h")

    CompositeDedupSource(no, yes).close()
    no.close.assert_called_once_with()
    yes.close.assert_called_once_with()


def test_dedup_source_has_no_write_method() -> None:
    """Mock: the interface handed to scrapers cannot write - there is no save method to call.

    Scenario: inspect DedupSource and every concrete source for save/insert/update/delete names.
    Boundaries: class introspection only.
    On failure: scraping code could again write somewhere other than the staging file (ADR 0003).
    """
    for cls in (DedupSource, StagingDedupSource, MongoDedupSource, CompositeDedupSource):
        names = [n for n in dir(cls) if not n.startswith("_")]
        assert not [n for n in names if any(w in n for w in ("save", "insert", "update", "delete", "write"))]


# --------------------------------------------------------------------------- #
# resolve_dedup_source
# --------------------------------------------------------------------------- #
def test_resolve_without_check_mongo_never_touches_database(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve without check mongo never touches database.

    Scenario:
        Given test inputs and fixtures for resolve without check mongo never touches database
        When test_resolve_without_check_mongo_never_touches_database executes the target flow
        Then the expected outcome for resolve without check mongo never touches database is confirmed.

    Boundaries:
        - Focus: resolve without check mongo never touches database
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve without check mongo never touches database
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    staging = tmp_path / "staging" / "books.json"
    write_staging(staging, [{"hash": "h"}])
    with patch.object(dedup.database, "get_mongo_collection", side_effect=AssertionError("must not connect")):
        source = resolve_dedup_source(staging, check_mongo=False)
    assert isinstance(source, StagingDedupSource) and source.book_exists_by_hash("h")


def test_resolve_creates_missing_staging_directory(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve creates missing staging directory.

    Scenario:
        Given test inputs and fixtures for resolve creates missing staging directory
        When test_resolve_creates_missing_staging_directory executes the target flow
        Then the expected outcome for resolve creates missing staging directory is confirmed.

    Boundaries:
        - Focus: resolve creates missing staging directory
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve creates missing staging directory
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    staging = tmp_path / "new" / "dir" / "books.json"
    source = resolve_dedup_source(staging, check_mongo=False)
    assert staging.parent.is_dir() and not staging.exists()
    assert not source.book_exists_by_hash("h")


def test_resolve_with_check_mongo_is_read_only_composite(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve with check mongo is read only composite.

    Scenario:
        Given test inputs and fixtures for resolve with check mongo is read only composite
        When test_resolve_with_check_mongo_is_read_only_composite executes the target flow
        Then the expected outcome for resolve with check mongo is read only composite is confirmed.

    Boundaries:
        - Focus: resolve with check mongo is read only composite
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve with check mongo is read only composite
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    collection = FakeCollection(read_only=True)
    collection.seed({"hash": "in-mongo"})
    with patch.object(dedup.database, "get_mongo_collection", return_value=collection) as get:
        source = resolve_dedup_source(tmp_path / "books.json", check_mongo=True)
    get.assert_called_once_with(ensure_indexes=False)
    assert isinstance(source, CompositeDedupSource) and source.book_exists_by_hash("in-mongo")


def test_check_mongo_connection_failure_exits(tmp_path) -> None:
    """
    [Unit] dedup: verifies check mongo connection failure exits.

    Scenario:
        Given test inputs and fixtures for check mongo connection failure exits
        When test_check_mongo_connection_failure_exits executes the target flow
        Then the expected outcome for check mongo connection failure exits is confirmed.

    Boundaries:
        - Focus: check mongo connection failure exits
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to check mongo connection failure exits
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    with (
        patch.object(dedup.database, "get_mongo_collection", return_value=None),
        pytest.raises(SystemExit) as excinfo,
    ):
        resolve_dedup_source(tmp_path / "books.json", check_mongo=True)
    assert excinfo.value.code == 1


def test_resolve_corrupt_staging_file_exits(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve corrupt staging file exits.

    Scenario:
        Given test inputs and fixtures for resolve corrupt staging file exits
        When test_resolve_corrupt_staging_file_exits executes the target flow
        Then the expected outcome for resolve corrupt staging file exits is confirmed.

    Boundaries:
        - Focus: resolve corrupt staging file exits
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve corrupt staging file exits
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    staging = tmp_path / "books.json"
    staging.write_text("[{oops", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        resolve_dedup_source(staging, check_mongo=False)
    assert excinfo.value.code == 1
    assert staging.read_text(encoding="utf-8") == "[{oops"


def test_resolve_unwritable_directory_exits(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve unwritable directory exits.

    Scenario:
        Given test inputs and fixtures for resolve unwritable directory exits
        When test_resolve_unwritable_directory_exits executes the target flow
        Then the expected outcome for resolve unwritable directory exits is confirmed.

    Boundaries:
        - Focus: resolve unwritable directory exits
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve unwritable directory exits
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    with (
        patch.object(dedup, "check_local_write_permission", return_value=False),
        pytest.raises(SystemExit) as excinfo,
    ):
        resolve_dedup_source(tmp_path / "books.json", check_mongo=False)
    assert excinfo.value.code == 1


def test_resolve_uncreatable_directory_exits(tmp_path) -> None:
    """
    [Unit] dedup: verifies resolve uncreatable directory exits.

    Scenario:
        Given test inputs and fixtures for resolve uncreatable directory exits
        When test_resolve_uncreatable_directory_exits executes the target flow
        Then the expected outcome for resolve uncreatable directory exits is confirmed.

    Boundaries:
        - Focus: resolve uncreatable directory exits
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to resolve uncreatable directory exits
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by dedup
    """
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        resolve_dedup_source(blocker / "books.json", check_mongo=False)
    assert excinfo.value.code == 1
