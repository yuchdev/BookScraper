"""Unit tests for bookscraper.backends.local.staging - file I/O, dedup index, append, hashing.

Real file I/O under tmp_path only; nothing here touches the repository's staging/ directory.
Validation and push preparation live in test_staging_validation.py.
"""

import json
import os
import stat

import pytest

from bookscraper.backends.local import staging
from bookscraper.backends.local.staging import (
    AppendSummary,
    StagingFileError,
    StagingIndex,
    append_to_staging,
    compute_document_hash,
    dump_unsaved,
    hash_inputs_equal,
    load_staging,
    review_status,
    strip_staging_meta,
    write_staging,
)
from bookscraper.book_utils import extract_year_from_date, hash_book


def _book(title: str = "Fluent Python", **extra) -> dict:
    doc = {"site": "amazon", "url": f"https://x/{title}", "title": title, "authors": ["Luciano Ramalho"]}
    doc["publication_year"] = 2022
    doc.update(extra)
    doc.setdefault("hash", compute_document_hash(doc))
    return doc


# --------------------------------------------------------------------------- #
# load_staging
# --------------------------------------------------------------------------- #
def test_missing_file_ok_returns_empty(tmp_path) -> None:
    """
    [Unit] staging: verifies missing file ok returns empty.

    Scenario:
        Given test inputs and fixtures for missing file ok returns empty
        When test_missing_file_ok_returns_empty executes the target flow
        Then the expected outcome for missing file ok returns empty is confirmed.

    Boundaries:
        - Focus: missing file ok returns empty
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to missing file ok returns empty
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    assert load_staging(tmp_path / "nope.json", missing_ok=True) == []


def test_missing_file_not_ok_raises(tmp_path) -> None:
    """
    [Unit] staging: verifies missing file not ok raises.

    Scenario:
        Given test inputs and fixtures for missing file not ok raises
        When test_missing_file_not_ok_raises executes the target flow
        Then the expected outcome for missing file not ok raises is confirmed.

    Boundaries:
        - Focus: missing file not ok raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to missing file not ok raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    with pytest.raises(StagingFileError, match="does not exist"):
        load_staging(tmp_path / "nope.json", missing_ok=False)


def test_corrupt_json_raises_with_position(tmp_path) -> None:
    """
    [Unit] staging: verifies corrupt json raises with position.

    Scenario:
        Given test inputs and fixtures for corrupt json raises with position
        When test_corrupt_json_raises_with_position executes the target flow
        Then the expected outcome for corrupt json raises with position is confirmed.

    Boundaries:
        - Focus: corrupt json raises with position
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to corrupt json raises with position
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text('[\n  {"title": "a",}\n]', encoding="utf-8")
    with pytest.raises(StagingFileError, match=r"line 2, column \d+"):
        load_staging(path, missing_ok=True)


def test_top_level_object_raises(tmp_path) -> None:
    """
    [Unit] staging: verifies top level object raises.

    Scenario:
        Given test inputs and fixtures for top level object raises
        When test_top_level_object_raises executes the target flow
        Then the expected outcome for top level object raises is confirmed.

    Boundaries:
        - Focus: top level object raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to top level object raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text('{"title": "a"}', encoding="utf-8")
    with pytest.raises(StagingFileError, match="JSON array"):
        load_staging(path, missing_ok=True)


def test_non_object_entry_raises(tmp_path) -> None:
    """
    [Unit] staging: verifies non object entry raises.

    Scenario:
        Given test inputs and fixtures for non object entry raises
        When test_non_object_entry_raises executes the target flow
        Then the expected outcome for non object entry raises is confirmed.

    Boundaries:
        - Focus: non object entry raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to non object entry raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text('[{"title": "a"}, "oops"]', encoding="utf-8")
    with pytest.raises(StagingFileError, match="Entry #1"):
        load_staging(path, missing_ok=True)


def test_bom_accepted(tmp_path) -> None:
    """
    [Unit] staging: verifies bom accepted.

    Scenario:
        Given test inputs and fixtures for bom accepted
        When test_bom_accepted executes the target flow
        Then the expected outcome for bom accepted is confirmed.

    Boundaries:
        - Focus: bom accepted
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to bom accepted
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"title": "a"}]).encode("utf-8"))
    assert load_staging(path, missing_ok=False) == [{"title": "a"}]


def test_unreadable_bytes_raise(tmp_path) -> None:
    """
    [Unit] staging: verifies unreadable bytes raise.

    Scenario:
        Given test inputs and fixtures for unreadable bytes raise
        When test_unreadable_bytes_raise executes the target flow
        Then the expected outcome for unreadable bytes raise is confirmed.

    Boundaries:
        - Focus: unreadable bytes raise
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unreadable bytes raise
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(StagingFileError, match="Could not read"):
        load_staging(path, missing_ok=True)


def test_oversized_file_rejected_before_parsing(tmp_path, monkeypatch) -> None:
    """
    [Mock] staging: verifies oversized file rejected before parsing.

    Scenario:
        Given test inputs and fixtures for oversized file rejected before parsing
        When test_oversized_file_rejected_before_parsing executes the target flow
        Then the expected outcome for oversized file rejected before parsing is confirmed.

    Boundaries:
        - Focus: oversized file rejected before parsing
        - Fixtures/params: tmp_path, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to oversized file rejected before parsing
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(staging, "MAX_STAGING_FILE_BYTES", 1)
    with pytest.raises(StagingFileError, match="the limit is 1"):
        load_staging(path, missing_ok=False)


def test_file_vanishing_after_exists_check_raises_staging_error(tmp_path, monkeypatch) -> None:
    """
    [Mock] staging: verifies file vanishing after exists check raises staging error.

    Scenario:
        Given test inputs and fixtures for file vanishing after exists check raises staging error
        When test_file_vanishing_after_exists_check_raises_staging_error executes the target flow
        Then the expected outcome for file vanishing after exists check raises staging error is confirmed.

    Boundaries:
        - Focus: file vanishing after exists check raises staging error
        - Fixtures/params: tmp_path, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to file vanishing after exists check raises staging error
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"  # never created
    monkeypatch.setattr(type(path), "exists", lambda self: True)
    with pytest.raises(StagingFileError, match="Could not read"):
        load_staging(path, missing_ok=False)


# --------------------------------------------------------------------------- #
# write_staging
# --------------------------------------------------------------------------- #
def test_write_creates_parent_dir_and_round_trips(tmp_path) -> None:
    """
    [Unit] staging: verifies write creates parent dir and round trips.

    Scenario:
        Given test inputs and fixtures for write creates parent dir and round trips
        When test_write_creates_parent_dir_and_round_trips executes the target flow
        Then the expected outcome for write creates parent dir and round trips is confirmed.

    Boundaries:
        - Focus: write creates parent dir and round trips
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to write creates parent dir and round trips
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "staging" / "books.json"
    docs = [{"title": "Ünïcode", "authors": ["A"]}]
    write_staging(path, docs)
    assert load_staging(path, missing_ok=False) == docs
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert "Ünïcode" in path.read_text(encoding="utf-8")  # UTF-8 verbatim, not \\u escapes


def test_new_file_is_owner_only(tmp_path) -> None:
    """
    [Unit] staging: verifies new file is owner only.

    Scenario:
        Given test inputs and fixtures for new file is owner only
        When test_new_file_is_owner_only executes the target flow
        Then the expected outcome for new file is owner only is confirmed.

    Boundaries:
        - Focus: new file is owner only
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to new file is owner only
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    write_staging(path, [])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_is_atomic_on_failure(tmp_path, monkeypatch) -> None:
    """
    [Mock] staging: verifies write is atomic on failure.

    Scenario:
        Given test inputs and fixtures for write is atomic on failure
        When test_write_is_atomic_on_failure executes the target flow
        Then the expected outcome for write is atomic on failure is confirmed.

    Boundaries:
        - Focus: write is atomic on failure
        - Fixtures/params: tmp_path, monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to write is atomic on failure
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    write_staging(path, [{"title": "original"}])
    before = path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(staging.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_staging(path, [{"title": "new"}])

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["books.json"]  # temp file cleaned up


def test_write_preserves_permissions(tmp_path) -> None:
    """
    [Unit] staging: verifies write preserves permissions.

    Scenario:
        Given test inputs and fixtures for write preserves permissions
        When test_write_preserves_permissions executes the target flow
        Then the expected outcome for write preserves permissions is confirmed.

    Boundaries:
        - Focus: write preserves permissions
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to write preserves permissions
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text("[]", encoding="utf-8")
    os.chmod(path, 0o640)
    write_staging(path, [{"title": "a"}])
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


# --------------------------------------------------------------------------- #
# StagingIndex / review_status / helpers
# --------------------------------------------------------------------------- #
def test_index_lookups() -> None:
    """
    [Unit] staging: verifies index lookups.

    Scenario:
        Given test inputs and fixtures for index lookups
        When test_index_lookups executes the target flow
        Then the expected outcome for index lookups is confirmed.

    Boundaries:
        - Focus: index lookups
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to index lookups
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    index = StagingIndex(
        [
            {"hash": "h1", "asin": "B01"},
            {"hash": "h2", "book_id": 42, "slug": "some-book"},
            {"title": "no keys"},
        ]
    )
    assert index.has_hash("h1") and index.has_hash("h2")
    assert not index.has_hash("h3") and not index.has_hash("") and not index.has_hash(None)
    assert index.has_asin("B01") and not index.has_asin("B02") and not index.has_asin("")
    assert index.has_leanpub("42", None)  # ids compared as strings
    assert index.has_leanpub(None, "some-book")
    assert not index.has_leanpub("7", "other") and not index.has_leanpub(None, None)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({}, "pending"),
        ({"_staging": {"status": "approved"}}, "approved"),
        ({"_staging": {"status": "rejected"}}, "rejected"),
        ({"_staging": {"status": "bogus"}}, "pending"),
        ({"_staging": "not-a-dict"}, "pending"),
    ],
)
def test_review_status(document, expected) -> None:
    """
    [Unit] staging: verifies review status.

    Scenario:
        Given test inputs and fixtures for review status
        When test_review_status executes the target flow
        Then the expected outcome for review status is confirmed.

    Boundaries:
        - Focus: review status
        - Fixtures/params: document, expected (parametrized, 5 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to review status
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    assert review_status(document) == expected


def test_strip_staging_meta_is_a_deep_copy() -> None:
    """
    [Unit] staging: verifies strip staging meta is a deep copy.

    Scenario:
        Given test inputs and fixtures for strip staging meta is a deep copy
        When test_strip_staging_meta_is_a_deep_copy executes the target flow
        Then the expected outcome for strip staging meta is a deep copy is confirmed.

    Boundaries:
        - Focus: strip staging meta is a deep copy
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to strip staging meta is a deep copy
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    original = {"title": "a", "authors": ["x"], "_staging": {"status": "approved"}}
    clean = strip_staging_meta(original)
    clean["authors"].append("y")
    assert "_staging" not in clean and original["_staging"] == {"status": "approved"}
    assert original["authors"] == ["x"]


# --------------------------------------------------------------------------- #
# compute_document_hash / hash_inputs_equal
# --------------------------------------------------------------------------- #
def test_hash_matches_playwright_scraper_inputs() -> None:
    """
    [Unit] staging: verifies hash matches playwright scraper inputs.

    Scenario:
        Given test inputs and fixtures for hash matches playwright scraper inputs
        When test_hash_matches_playwright_scraper_inputs executes the target flow
        Then the expected outcome for hash matches playwright scraper inputs is confirmed.

    Boundaries:
        - Focus: hash matches playwright scraper inputs
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to hash matches playwright scraper inputs
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    doc = {"title": "T", "authors": ["B", "A"], "publication_year": 2020}
    assert compute_document_hash(doc) == hash_book("T", ["B", "A"], 2020)


def test_hash_matches_leanpub_scraper_inputs() -> None:
    """
    [Unit] staging: verifies hash matches leanpub scraper inputs.

    Scenario:
        Given test inputs and fixtures for hash matches leanpub scraper inputs
        When test_hash_matches_leanpub_scraper_inputs executes the target flow
        Then the expected outcome for hash matches leanpub scraper inputs is confirmed.

    Boundaries:
        - Focus: hash matches leanpub scraper inputs
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to hash matches leanpub scraper inputs
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    doc = {"title": "T", "authors": ["A"], "last_published_at": "2024-05-01"}
    assert compute_document_hash(doc) == hash_book("T", ["A"], extract_year_from_date("2024-05-01"))


def test_hash_tolerates_string_authors_and_missing_title() -> None:
    """
    [Unit] staging: verifies hash tolerates string authors and missing title.

    Scenario:
        Given test inputs and fixtures for hash tolerates string authors and missing title
        When test_hash_tolerates_string_authors_and_missing_title executes the target flow
        Then the expected outcome for hash tolerates string authors and missing title is confirmed.

    Boundaries:
        - Focus: hash tolerates string authors and missing title
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to hash tolerates string authors and missing title
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    assert compute_document_hash({"title": "T", "authors": "Solo"}) == hash_book("T", ["Solo"], None)
    assert compute_document_hash({"title": None, "authors": None}) == hash_book("", [], None)


def test_hash_inputs_equal() -> None:
    """
    [Unit] staging: verifies hash inputs equal.

    Scenario:
        Given test inputs and fixtures for hash inputs equal
        When test_hash_inputs_equal executes the target flow
        Then the expected outcome for hash inputs equal is confirmed.

    Boundaries:
        - Focus: hash inputs equal
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to hash inputs equal
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    a = {"title": "T", "authors": ["A"], "publication_year": 2020, "description": "x"}
    assert hash_inputs_equal(a, {**a, "description": "changed"})
    assert not hash_inputs_equal(a, {**a, "title": "T2"})
    assert not hash_inputs_equal(a, {k: v for k, v in a.items() if k != "publication_year"})


# --------------------------------------------------------------------------- #
# append_to_staging / dump_unsaved
# --------------------------------------------------------------------------- #
def test_append_creates_file_and_stamps_staging_meta(tmp_path) -> None:
    """
    [Unit] staging: verifies append creates file and stamps staging meta.

    Scenario:
        Given test inputs and fixtures for append creates file and stamps staging meta
        When test_append_creates_file_and_stamps_staging_meta executes the target flow
        Then the expected outcome for append creates file and stamps staging meta is confirmed.

    Boundaries:
        - Focus: append creates file and stamps staging meta
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append creates file and stamps staging meta
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "staging" / "books.json"
    summary = append_to_staging(path, [_book("A"), _book("B")], source="scrape-urls")

    assert summary == AppendSummary(appended=2, duplicates=0, total_in_file=2, pending_in_file=2)
    stored = load_staging(path, missing_ok=False)
    assert [d["title"] for d in stored] == ["A", "B"]
    for doc in stored:
        assert doc["_staging"]["status"] == "pending"
        assert doc["_staging"]["source"] == "scrape-urls"
        assert doc["_staging"]["scraped_at"].endswith("Z")


def test_append_preserves_existing_documents_exactly(tmp_path) -> None:
    """
    [Unit] staging: verifies append preserves existing documents exactly.

    Scenario:
        Given test inputs and fixtures for append preserves existing documents exactly
        When test_append_preserves_existing_documents_exactly executes the target flow
        Then the expected outcome for append preserves existing documents exactly is confirmed.

    Boundaries:
        - Focus: append preserves existing documents exactly
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append preserves existing documents exactly
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    reviewed = [
        {**_book("Edited title"), "_staging": {"status": "approved", "note": "fixed typo"}},
        {**_book("Bad"), "_staging": {"status": "rejected"}},
    ]
    write_staging(path, reviewed)
    before = json.loads(path.read_text(encoding="utf-8"))

    summary = append_to_staging(path, [_book("New")], source="search")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after[:2] == before  # content and order untouched
    assert after[2]["title"] == "New"
    assert summary.appended == 1 and summary.total_in_file == 3 and summary.pending_in_file == 1


def test_append_skips_hash_already_in_file_and_within_batch(tmp_path) -> None:
    """
    [Unit] staging: verifies append skips hash already in file and within batch.

    Scenario:
        Given test inputs and fixtures for append skips hash already in file and within batch
        When test_append_skips_hash_already_in_file_and_within_batch executes the target flow
        Then the expected outcome for append skips hash already in file and within batch is confirmed.

    Boundaries:
        - Focus: append skips hash already in file and within batch
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append skips hash already in file and within batch
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    write_staging(path, [_book("A")])
    summary = append_to_staging(path, [_book("A"), _book("B"), _book("B")], source="scrape-urls")
    assert (summary.appended, summary.duplicates) == (1, 2)
    assert [d["title"] for d in load_staging(path, missing_ok=False)] == ["A", "B"]


def test_append_computes_missing_hash(tmp_path) -> None:
    """
    [Unit] staging: verifies append computes missing hash.

    Scenario:
        Given test inputs and fixtures for append computes missing hash
        When test_append_computes_missing_hash executes the target flow
        Then the expected outcome for append computes missing hash is confirmed.

    Boundaries:
        - Focus: append computes missing hash
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append computes missing hash
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    doc = {"title": "No hash", "authors": ["A"], "publication_year": 2021}
    append_to_staging(path, [doc], source="scrape-urls")
    assert load_staging(path, missing_ok=False)[0]["hash"] == compute_document_hash(doc)


def test_append_nothing_new_does_not_rewrite_file(tmp_path) -> None:
    """
    [Unit] staging: verifies append nothing new does not rewrite file.

    Scenario:
        Given test inputs and fixtures for append nothing new does not rewrite file
        When test_append_nothing_new_does_not_rewrite_file executes the target flow
        Then the expected outcome for append nothing new does not rewrite file is confirmed.

    Boundaries:
        - Focus: append nothing new does not rewrite file
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append nothing new does not rewrite file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    write_staging(path, [_book("A")])
    mtime = path.stat().st_mtime_ns
    summary = append_to_staging(path, [_book("A")], source="scrape-urls")
    assert summary.appended == 0 and path.stat().st_mtime_ns == mtime


def test_append_picks_up_concurrent_edit(tmp_path) -> None:
    """Unit: the file is re-read at append time, so edits saved during the scrape survive.

    Scenario: a reviewer approves a document after the run started (simulated by editing the
        file before append_to_staging is called), then the run appends.
    Boundaries: real files under tmp_path.
    On failure: a scrape run would overwrite review work saved while it was running.
    """
    path = tmp_path / "books.json"
    write_staging(path, [_book("A")])
    edited = load_staging(path, missing_ok=False)
    edited[0]["_staging"] = {"status": "approved"}
    write_staging(path, edited)

    append_to_staging(path, [_book("B")], source="scrape-urls")

    assert load_staging(path, missing_ok=False)[0]["_staging"] == {"status": "approved"}


def test_append_to_corrupted_file_raises_without_writing(tmp_path) -> None:
    """
    [Unit] staging: verifies append to corrupted file raises without writing.

    Scenario:
        Given test inputs and fixtures for append to corrupted file raises without writing
        When test_append_to_corrupted_file_raises_without_writing executes the target flow
        Then the expected outcome for append to corrupted file raises without writing is confirmed.

    Boundaries:
        - Focus: append to corrupted file raises without writing
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to append to corrupted file raises without writing
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "books.json"
    path.write_text("[{broken", encoding="utf-8")
    with pytest.raises(StagingFileError):
        append_to_staging(path, [_book("A")], source="scrape-urls")
    assert path.read_text(encoding="utf-8") == "[{broken"


def test_dump_unsaved_writes_sibling_file(tmp_path) -> None:
    """
    [Unit] staging: verifies dump unsaved writes sibling file.

    Scenario:
        Given test inputs and fixtures for dump unsaved writes sibling file
        When test_dump_unsaved_writes_sibling_file executes the target flow
        Then the expected outcome for dump unsaved writes sibling file is confirmed.

    Boundaries:
        - Focus: dump unsaved writes sibling file
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to dump unsaved writes sibling file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging
    """
    path = tmp_path / "staging" / "books.json"
    dumped = dump_unsaved(path, [_book("A")])
    assert dumped.parent == path.parent
    assert dumped.name.startswith("books.unsaved-") and dumped.suffix == ".json"
    assert load_staging(dumped, missing_ok=False)[0]["title"] == "A"
