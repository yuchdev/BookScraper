"""Unit tests for staging-file validation and push preparation (milestone 0002, 01.0/04).

Every rule in docs/dev/staging-file-format.md has at least one rejecting test here, including
the rules docs/security/staging-file-threat-model.md made preconditions for its verdict
(forbidden keys inside lists, `_id` type checked before format).
"""

import pytest

from bookscraper.backends.local import staging
from bookscraper.backends.local.staging import (
    compute_document_hash,
    prepare_for_push,
    validate_staging_documents,
)

PULLED_ID = "6744e7c2a1b2c3d4e5f60718"


def _new_doc(**overrides) -> dict:
    doc = {"site": "amazon", "url": "https://x/1", "title": "Fluent Python", "authors": ["Luciano Ramalho"]}
    doc.update(overrides)
    return doc


def _paths(problems) -> list[str]:
    return [p.path for p in problems]


def test_valid_new_and_pulled_documents_pass() -> None:
    """
    [Unit] staging validation: verifies valid new and pulled documents pass.

    Scenario:
        Given test inputs and fixtures for valid new and pulled documents pass
        When test_valid_new_and_pulled_documents_pass executes the target flow
        Then the expected outcome for valid new and pulled documents pass is confirmed.

    Boundaries:
        - Focus: valid new and pulled documents pass
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to valid new and pulled documents pass
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    documents = [
        _new_doc(_staging={"status": "pending", "scraped_at": "2026-11-19T00:00:00Z", "source": "scrape-urls"}),
        _new_doc(_id=PULLED_ID, hash="abc", _staging={"status": "approved", "note": "ok", "anything": [1]}),
        _new_doc(authors=[]),  # empty author list is allowed
    ]
    assert validate_staging_documents(documents) == []


@pytest.mark.parametrize("field", ["title", "site", "authors"])
def test_missing_required_field_reported(field: str) -> None:
    """
    [Unit] staging validation: verifies missing required field reported.

    Scenario:
        Given test inputs and fixtures for missing required field reported
        When test_missing_required_field_reported executes the target flow
        Then the expected outcome for missing required field reported is confirmed.

    Boundaries:
        - Focus: missing required field reported
        - Fixtures/params: field (parametrized, 3 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to missing required field reported
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc()
    del doc[field]
    assert _paths(validate_staging_documents([doc])) == [field]


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", ""), ("title", "   "), ("title", None), ("site", 3), ("authors", "Solo"), ("authors", ["ok", 1])],
)
def test_wrong_required_field_type_reported(field: str, value) -> None:
    """
    [Unit] staging validation: verifies wrong required field type reported.

    Scenario:
        Given test inputs and fixtures for wrong required field type reported
        When test_wrong_required_field_type_reported executes the target flow
        Then the expected outcome for wrong required field type reported is confirmed.

    Boundaries:
        - Focus: wrong required field type reported
        - Fixtures/params: field, value (parametrized, 6 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to wrong required field type reported
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    assert _paths(validate_staging_documents([_new_doc(**{field: value})])) == [field]


@pytest.mark.parametrize(("field", "value"), [("url", ""), ("url", 5), ("hash", 123)])
def test_optional_field_types_checked_when_present(field: str, value) -> None:
    """
    [Unit] staging validation: verifies optional field types checked when present.

    Scenario:
        Given test inputs and fixtures for optional field types checked when present
        When test_optional_field_types_checked_when_present executes the target flow
        Then the expected outcome for optional field types checked when present is confirmed.

    Boundaries:
        - Focus: optional field types checked when present
        - Fixtures/params: field, value (parametrized, 3 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to optional field types checked when present
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    assert _paths(validate_staging_documents([_new_doc(**{field: value})])) == [field]


def test_non_object_document_reported() -> None:
    """
    [Unit] staging validation: verifies non object document reported.

    Scenario:
        Given test inputs and fixtures for non object document reported
        When test_non_object_document_reported executes the target flow
        Then the expected outcome for non object document reported is confirmed.

    Boundaries:
        - Focus: non object document reported
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to non object document reported
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    problems = validate_staging_documents(["not a doc"])
    assert len(problems) == 1 and "must be a JSON object" in str(problems[0])


def test_all_problems_reported_not_just_first() -> None:
    """
    [Unit] staging validation: verifies all problems reported not just first.

    Scenario:
        Given test inputs and fixtures for all problems reported not just first
        When test_all_problems_reported_not_just_first executes the target flow
        Then the expected outcome for all problems reported not just first is confirmed.

    Boundaries:
        - Focus: all problems reported not just first
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to all problems reported not just first
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    documents = [_new_doc(title=""), _new_doc(), _new_doc(site="", authors=None, **{"$where": "1"})]
    problems = validate_staging_documents(documents)
    assert [(p.index, p.path) for p in problems] == [(0, "title"), (2, "$where"), (2, "site"), (2, "authors")]
    assert str(problems[0]).startswith("document #0, title:")


def test_dollar_key_rejected_at_depth() -> None:
    """
    [Unit] staging validation: verifies dollar key rejected at depth.

    Scenario:
        Given test inputs and fixtures for dollar key rejected at depth
        When test_dollar_key_rejected_at_depth executes the target flow
        Then the expected outcome for dollar key rejected at depth is confirmed.

    Boundaries:
        - Focus: dollar key rejected at depth
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to dollar key rejected at depth
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc(details={"inner": {"$set": {"title": "pwned"}}})
    assert _paths(validate_staging_documents([doc])) == ["details.inner.$set"]


def test_dollar_key_rejected_inside_list() -> None:
    """Unit: operator keys hidden in list elements (incl. nested lists) are rejected.

    Scenario: `authors`-like and nested-list values carrying {"$where": ...} objects.
    Boundaries: pure validation; threat-model rule 1 (staging-file-threat-model.md).
    On failure: a hand-edited list element could smuggle a MongoDB operator into a write.
    """
    doc = _new_doc(categories=[{"$where": "sleep(1000)"}, [[{"ok": 1, "$gt": ""}]]])
    assert _paths(validate_staging_documents([doc])) == ["categories[0].$where", "categories[1][0][0].$gt"]


def test_dotted_and_nul_keys_rejected() -> None:
    """
    [Unit] staging validation: verifies dotted and nul keys rejected.

    Scenario:
        Given test inputs and fixtures for dotted and nul keys rejected
        When test_dotted_and_nul_keys_rejected executes the target flow
        Then the expected outcome for dotted and nul keys rejected is confirmed.

    Boundaries:
        - Focus: dotted and nul keys rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to dotted and nul keys rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc(**{"a.b": 1, "bad\x00key": 2})
    messages = [p.message for p in validate_staging_documents([doc])]
    assert "key must not contain '.'" in messages
    assert "key must not contain a NUL character" in messages


def test_reserved_underscore_top_level_keys() -> None:
    """
    [Unit] staging validation: verifies reserved underscore top level keys.

    Scenario:
        Given test inputs and fixtures for reserved underscore top level keys
        When test_reserved_underscore_top_level_keys executes the target flow
        Then the expected outcome for reserved underscore top level keys is confirmed.

    Boundaries:
        - Focus: reserved underscore top level keys
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to reserved underscore top level keys
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc(_hidden=1, nested={"_ok_when_nested": 1})
    assert _paths(validate_staging_documents([doc])) == ["_hidden"]


@pytest.mark.parametrize(
    "bad_id",
    ["6744E7C2A1B2C3D4E5F60718", "6744e7c2", "zz44e7c2a1b2c3d4e5f60718", 12345, {"$oid": PULLED_ID}, None],
)
def test_bad_object_id_rejected(bad_id) -> None:
    """
    [Unit] staging validation: verifies bad object id rejected.

    Scenario:
        Given test inputs and fixtures for bad object id rejected
        When test_bad_object_id_rejected executes the target flow
        Then the expected outcome for bad object id rejected is confirmed.

    Boundaries:
        - Focus: bad object id rejected
        - Fixtures/params: bad_id (parametrized, 6 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to bad object id rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    problems = validate_staging_documents([_new_doc(_id=bad_id)])
    assert "_id" in _paths(problems)


def test_unknown_review_status_rejected() -> None:
    """
    [Unit] staging validation: verifies unknown review status rejected.

    Scenario:
        Given test inputs and fixtures for unknown review status rejected
        When test_unknown_review_status_rejected executes the target flow
        Then the expected outcome for unknown review status rejected is confirmed.

    Boundaries:
        - Focus: unknown review status rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unknown review status rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    assert _paths(validate_staging_documents([_new_doc(_staging={"status": "maybe"})])) == ["_staging.status"]


def test_staging_meta_must_be_object() -> None:
    """
    [Unit] staging validation: verifies staging meta must be object.

    Scenario:
        Given test inputs and fixtures for staging meta must be object
        When test_staging_meta_must_be_object executes the target flow
        Then the expected outcome for staging meta must be object is confirmed.

    Boundaries:
        - Focus: staging meta must be object
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to staging meta must be object
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    assert _paths(validate_staging_documents([_new_doc(_staging="approved")])) == ["_staging"]


def test_oversized_document_rejected(monkeypatch) -> None:
    """
    [Mock] staging validation: verifies oversized document rejected.

    Scenario:
        Given test inputs and fixtures for oversized document rejected
        When test_oversized_document_rejected executes the target flow
        Then the expected outcome for oversized document rejected is confirmed.

    Boundaries:
        - Focus: oversized document rejected
        - Fixtures/params: monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to oversized document rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    monkeypatch.setattr(staging, "MAX_DOCUMENT_BYTES", 50)
    problems = validate_staging_documents([_new_doc(description="x" * 100)])
    assert len(problems) == 1 and "limit is 16 MB" in problems[0].message


def test_rejected_document_skips_book_field_rules_but_not_structure() -> None:
    """Unit: marking a broken document rejected gets it past validation, but not a malformed one.

    Scenario: a rejected doc with no title passes; a rejected doc with a bad _id still fails.
    Boundaries: pure validation.
    On failure: either reviewers can't park a broken scrape, or rejected docs hide structural problems.
    """
    broken_but_rejected = {"url": "https://x", "_staging": {"status": "rejected"}}
    assert validate_staging_documents([broken_but_rejected]) == []
    malformed = {"_id": "nope", "_staging": {"status": "rejected"}}
    assert _paths(validate_staging_documents([malformed])) == ["_id"]


# --------------------------------------------------------------------------- #
# prepare_for_push
# --------------------------------------------------------------------------- #
def test_staging_meta_stripped_and_input_untouched() -> None:
    """
    [Unit] staging validation: verifies staging meta stripped and input untouched.

    Scenario:
        Given test inputs and fixtures for staging meta stripped and input untouched
        When test_staging_meta_stripped_and_input_untouched executes the target flow
        Then the expected outcome for staging meta stripped and input untouched is confirmed.

    Boundaries:
        - Focus: staging meta stripped and input untouched
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to staging meta stripped and input untouched
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    original = _new_doc(_staging={"status": "approved"}, tags=["a"])
    prepared = prepare_for_push(original, 4)
    assert "_staging" not in prepared.document
    assert prepared.status == "approved" and prepared.index == 4 and prepared.title == "Fluent Python"
    prepared.document["tags"].append("b")
    assert original["tags"] == ["a"] and "_staging" in original


def test_hash_recomputed_after_title_edit() -> None:
    """
    [Unit] staging validation: verifies hash recomputed after title edit.

    Scenario:
        Given test inputs and fixtures for hash recomputed after title edit
        When test_hash_recomputed_after_title_edit executes the target flow
        Then the expected outcome for hash recomputed after title edit is confirmed.

    Boundaries:
        - Focus: hash recomputed after title edit
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to hash recomputed after title edit
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc(publication_year=2022)
    doc["hash"] = compute_document_hash(doc)
    old_hash = doc["hash"]
    doc["title"] = "Fluent Python, 2nd Edition"

    prepared = prepare_for_push(doc, 0)

    assert prepared.hash == compute_document_hash(doc) != old_hash
    assert prepared.document["hash"] == prepared.hash
    assert prepared.hash_changed is True and prepared.previous_hash == old_hash


def test_unchanged_document_keeps_hash() -> None:
    """
    [Unit] staging validation: verifies unchanged document keeps hash.

    Scenario:
        Given test inputs and fixtures for unchanged document keeps hash
        When test_unchanged_document_keeps_hash executes the target flow
        Then the expected outcome for unchanged document keeps hash is confirmed.

    Boundaries:
        - Focus: unchanged document keeps hash
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unchanged document keeps hash
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    doc = _new_doc(publication_year=2022)
    doc["hash"] = compute_document_hash(doc)
    prepared = prepare_for_push(doc, 0)
    assert prepared.hash == doc["hash"] and prepared.hash_changed is False


def test_document_without_hash_gets_one_and_is_not_flagged_changed() -> None:
    """
    [Unit] staging validation: verifies document without hash gets one and is not flagged changed.

    Scenario:
        Given test inputs and fixtures for document without hash gets one and is not flagged changed
        When test_document_without_hash_gets_one_and_is_not_flagged_changed executes the target flow
        Then the expected outcome for document without hash gets one and is not flagged changed is confirmed.

    Boundaries:
        - Focus: document without hash gets one and is not flagged changed
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to document without hash gets one and is not flagged changed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    prepared = prepare_for_push(_new_doc(), 0)
    assert prepared.hash and prepared.previous_hash is None and prepared.hash_changed is False


def test_object_id_carried_as_string() -> None:
    """
    [Unit] staging validation: verifies object id carried as string.

    Scenario:
        Given test inputs and fixtures for object id carried as string
        When test_object_id_carried_as_string executes the target flow
        Then the expected outcome for object id carried as string is confirmed.

    Boundaries:
        - Focus: object id carried as string
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to object id carried as string
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging validation
    """
    assert prepare_for_push(_new_doc(_id=PULLED_ID), 0).object_id == PULLED_ID
    assert prepare_for_push(_new_doc(), 0).object_id is None
