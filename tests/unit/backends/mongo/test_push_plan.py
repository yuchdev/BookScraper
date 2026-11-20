"""Unit tests for bookscraper.backends.mongo.push_plan - the pure push planner (03.0/01).

Inputs are PreparedDocuments built with staging.prepare_for_push plus hand-built
"existing" maps standing in for what push_writer.fetch_existing would return.
"""

from bookscraper.backends.local.staging import compute_document_hash, prepare_for_push
from bookscraper.backends.mongo.push_plan import ACTIONS, build_push_plan, changed_fields, format_plan

ID_A = "6744e7c2a1b2c3d4e5f60718"
ID_B = "6744e7c2a1b2c3d4e5f60719"


def _doc(title: str, **extra) -> dict:
    doc = {"site": "amazon", "title": title, "authors": ["A"], "publication_year": 2020}
    doc.update(extra)
    doc.setdefault("hash", compute_document_hash(doc))
    return doc


def _plan(documents, existing_hashes=None, existing_by_id=None, approved_only=False):
    prepared = [prepare_for_push(d, i) for i, d in enumerate(documents)]
    plan = build_push_plan(
        prepared,
        existing_hashes=existing_hashes or {},
        existing_by_id=existing_by_id or {},
        approved_only=approved_only,
    )
    return plan, prepared


def _live(object_id: str, title: str, **extra) -> dict:
    """A live document as export.normalize_document would render it (no _staging)."""
    doc = _doc(title, **extra)
    doc["_id"] = object_id
    return doc


def _pulled(live: dict, **edits) -> dict:
    return {**live, "_staging": {"status": "pending", "source": "pull"}, **edits}


def test_new_document_inserts() -> None:
    """
    [Unit] push plan: verifies new document inserts.

    Scenario:
        Given test inputs and fixtures for new document inserts
        When test_new_document_inserts executes the target flow
        Then the expected outcome for new document inserts is confirmed.

    Boundaries:
        - Focus: new document inserts
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to new document inserts
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    plan, _ = _plan([_doc("New")])
    assert [i.action for i in plan.items] == ["insert"]
    assert plan.has_writes and not plan.requires_allow_updates and plan.errors == []


def test_existing_hash_is_duplicate() -> None:
    """
    [Unit] push plan: verifies existing hash is duplicate.

    Scenario:
        Given test inputs and fixtures for existing hash is duplicate
        When test_existing_hash_is_duplicate executes the target flow
        Then the expected outcome for existing hash is duplicate is confirmed.

    Boundaries:
        - Focus: existing hash is duplicate
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to existing hash is duplicate
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    doc = _doc("Known")
    plan, _ = _plan([doc], existing_hashes={doc["hash"]: ID_A})
    assert [i.action for i in plan.items] == ["duplicate"]
    assert not plan.has_writes


def test_in_file_duplicate_counted_once() -> None:
    """
    [Unit] push plan: verifies in file duplicate counted once.

    Scenario:
        Given test inputs and fixtures for in file duplicate counted once
        When test_in_file_duplicate_counted_once executes the target flow
        Then the expected outcome for in file duplicate counted once is confirmed.

    Boundaries:
        - Focus: in file duplicate counted once
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to in file duplicate counted once
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    plan, _ = _plan([_doc("Same"), _doc("Same"), _doc("Other")])
    assert [i.action for i in plan.items] == ["insert", "duplicate", "insert"]


def test_pulled_untouched_is_unchanged() -> None:
    """
    [Unit] push plan: verifies pulled untouched is unchanged.

    Scenario:
        Given test inputs and fixtures for pulled untouched is unchanged
        When test_pulled_untouched_is_unchanged executes the target flow
        Then the expected outcome for pulled untouched is unchanged is confirmed.

    Boundaries:
        - Focus: pulled untouched is unchanged
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pulled untouched is unchanged
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Book")
    plan, _ = _plan([_pulled(live)], existing_hashes={live["hash"]: ID_A}, existing_by_id={ID_A: live})
    assert [i.action for i in plan.items] == ["unchanged"]
    assert not plan.has_writes


def test_pulled_with_legacy_hash_stays_unchanged() -> None:
    """Unit: a stored hash computed under an older rule never turns an untouched document into an update.

    Scenario: the live doc's hash differs from what compute_document_hash gives today; the
        pulled copy is not edited.
    Boundaries: pure planner.
    On failure: pull → push would report every legacy document as an update (and need --allow-updates).
    """
    live = _live(ID_A, "Book", hash="legacy-hash-from-2024")
    plan, prepared = _plan(
        [_pulled(live)], existing_hashes={"legacy-hash-from-2024": ID_A}, existing_by_id={ID_A: live}
    )
    assert [i.action for i in plan.items] == ["unchanged"]
    assert prepared[0].hash == "legacy-hash-from-2024" and prepared[0].hash_changed is False


def test_pulled_edited_is_update_with_changed_fields() -> None:
    """
    [Unit] push plan: verifies pulled edited is update with changed fields.

    Scenario:
        Given test inputs and fixtures for pulled edited is update with changed fields
        When test_pulled_edited_is_update_with_changed_fields executes the target flow
        Then the expected outcome for pulled edited is update with changed fields is confirmed.

    Boundaries:
        - Focus: pulled edited is update with changed fields
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pulled edited is update with changed fields
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Book", description="old", isbn13="1")
    edited = _pulled(live, description="new")
    del edited["isbn13"]
    plan, _ = _plan([edited], existing_hashes={live["hash"]: ID_A}, existing_by_id={ID_A: live})
    item = plan.items[0]
    assert item.action == "update" and item.changed_fields == ["description", "isbn13"]
    assert item.object_id == ID_A and item.hash_changed is False
    assert plan.requires_allow_updates


def test_title_edit_on_pulled_doc_recomputes_hash() -> None:
    """
    [Unit] push plan: verifies title edit on pulled doc recomputes hash.

    Scenario:
        Given test inputs and fixtures for title edit on pulled doc recomputes hash
        When test_title_edit_on_pulled_doc_recomputes_hash executes the target flow
        Then the expected outcome for title edit on pulled doc recomputes hash is confirmed.

    Boundaries:
        - Focus: title edit on pulled doc recomputes hash
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to title edit on pulled doc recomputes hash
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Bok")
    plan, _ = _plan([_pulled(live, title="Book")], existing_hashes={live["hash"]: ID_A}, existing_by_id={ID_A: live})
    item = plan.items[0]
    assert item.action == "update" and item.hash_changed is True
    assert set(item.changed_fields) == {"title", "hash"}


def test_unknown_object_id_is_plan_error() -> None:
    """
    [Unit] push plan: verifies unknown object id is plan error.

    Scenario:
        Given test inputs and fixtures for unknown object id is plan error
        When test_unknown_object_id_is_plan_error executes the target flow
        Then the expected outcome for unknown object id is plan error is confirmed.

    Boundaries:
        - Focus: unknown object id is plan error
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to unknown object id is plan error
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    plan, _ = _plan([_pulled(_live(ID_A, "Gone"))])
    assert plan.items == [] and len(plan.errors) == 1
    assert ID_A in plan.errors[0] and "does not exist" in plan.errors[0]


def test_update_hash_collision_is_plan_error() -> None:
    """
    [Unit] push plan: verifies update hash collision is plan error.

    Scenario:
        Given test inputs and fixtures for update hash collision is plan error
        When test_update_hash_collision_is_plan_error executes the target flow
        Then the expected outcome for update hash collision is plan error is confirmed.

    Boundaries:
        - Focus: update hash collision is plan error
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to update hash collision is plan error
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    other = _doc("Taken title")
    live = _live(ID_A, "Original")
    edited = _pulled(live, title="Taken title")
    plan, _ = _plan([edited], existing_hashes={live["hash"]: ID_A, other["hash"]: ID_B}, existing_by_id={ID_A: live})
    assert len(plan.errors) == 1 and ID_B in plan.errors[0]


def test_two_documents_colliding_within_file_is_plan_error() -> None:
    """
    [Unit] push plan: verifies two documents colliding within file is plan error.

    Scenario:
        Given test inputs and fixtures for two documents colliding within file is plan error
        When test_two_documents_colliding_within_file_is_plan_error executes the target flow
        Then the expected outcome for two documents colliding within file is plan error is confirmed.

    Boundaries:
        - Focus: two documents colliding within file is plan error
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to two documents colliding within file is plan error
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Original")
    plan, _ = _plan(
        [_doc("Same title"), _pulled(live, title="Same title")],
        existing_hashes={live["hash"]: ID_A},
        existing_by_id={ID_A: live},
    )
    assert [i.action for i in plan.items] == ["insert"]
    assert len(plan.errors) == 1 and "#0" in plan.errors[0]


def test_rejected_skipped_even_if_otherwise_invalid_for_planning() -> None:
    """
    [Unit] push plan: verifies rejected skipped even if otherwise invalid for planning.

    Scenario:
        Given test inputs and fixtures for rejected skipped even if otherwise invalid for planning
        When test_rejected_skipped_even_if_otherwise_invalid_for_planning executes the target flow
        Then the expected outcome for rejected skipped even if otherwise invalid for planning is confirmed.

    Boundaries:
        - Focus: rejected skipped even if otherwise invalid for planning
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rejected skipped even if otherwise invalid for planning
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    rejected = _pulled(_live(ID_A, "Gone"), _staging={"status": "rejected"})
    plan, _ = _plan([rejected, _doc("New")])
    assert [i.action for i in plan.items] == ["skipped_rejected", "insert"]
    assert plan.errors == []  # a rejected doc's unknown _id is not an error


def test_approved_only_skips_pending() -> None:
    """
    [Unit] push plan: verifies approved only skips pending.

    Scenario:
        Given test inputs and fixtures for approved only skips pending
        When test_approved_only_skips_pending executes the target flow
        Then the expected outcome for approved only skips pending is confirmed.

    Boundaries:
        - Focus: approved only skips pending
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to approved only skips pending
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    plan, _ = _plan(
        [_doc("Pending"), _doc("Approved", _staging={"status": "approved"})],
        approved_only=True,
    )
    assert [i.action for i in plan.items] == ["skipped_unapproved", "insert"]


def test_requires_allow_updates_flag() -> None:
    """
    [Unit] push plan: verifies requires allow updates flag.

    Scenario:
        Given test inputs and fixtures for requires allow updates flag
        When test_requires_allow_updates_flag executes the target flow
        Then the expected outcome for requires allow updates flag is confirmed.

    Boundaries:
        - Focus: requires allow updates flag
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to requires allow updates flag
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    assert _plan([_doc("New")])[0].requires_allow_updates is False


def test_counts_cover_every_action() -> None:
    """
    [Unit] push plan: verifies counts cover every action.

    Scenario:
        Given test inputs and fixtures for counts cover every action
        When test_counts_cover_every_action executes the target flow
        Then the expected outcome for counts cover every action is confirmed.

    Boundaries:
        - Focus: counts cover every action
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to counts cover every action
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    plan, _ = _plan([_doc("A"), _doc("A")])
    assert set(plan.counts) == set(ACTIONS)
    assert plan.counts["insert"] == 1 and plan.counts["duplicate"] == 1


def test_changed_fields_ignores_id() -> None:
    """
    [Unit] push plan: verifies changed fields ignores id.

    Scenario:
        Given test inputs and fixtures for changed fields ignores id
        When test_changed_fields_ignores_id executes the target flow
        Then the expected outcome for changed fields ignores id is confirmed.

    Boundaries:
        - Focus: changed fields ignores id
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to changed fields ignores id
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    assert changed_fields({"_id": "x", "a": 1}, {"_id": "y", "a": 1, "b": 2}) == ["b"]


def test_format_plan_lists_non_unchanged() -> None:
    """
    [Unit] push plan: verifies format plan lists non unchanged.

    Scenario:
        Given test inputs and fixtures for format plan lists non unchanged
        When test_format_plan_lists_non_unchanged executes the target flow
        Then the expected outcome for format plan lists non unchanged is confirmed.

    Boundaries:
        - Focus: format plan lists non unchanged
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to format plan lists non unchanged
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Live")
    edited_live = _live(ID_B, "Edited", description="old")
    plan, _ = _plan(
        [_pulled(live), _pulled(edited_live, description="new"), _doc("Brand new"), _doc("", authors=[])],
        existing_hashes={live["hash"]: ID_A, edited_live["hash"]: ID_B},
        existing_by_id={ID_A: live, ID_B: edited_live},
    )
    text = format_plan(plan)
    first, *lines = text.splitlines()
    assert first.startswith("Push plan: 2 insert, 1 update, 0 duplicate, 1 unchanged") and "(4 documents)" in first
    assert len(lines) == 3  # the unchanged document is not listed
    assert any("update" in line and f"(_id {ID_B})" in line and "[changed: description]" in line for line in lines)
    assert any("<no title>" in line for line in lines)
    assert not any("Live" in line for line in lines)


def test_format_plan_marks_hash_change() -> None:
    """
    [Unit] push plan: verifies format plan marks hash change.

    Scenario:
        Given test inputs and fixtures for format plan marks hash change
        When test_format_plan_marks_hash_change executes the target flow
        Then the expected outcome for format plan marks hash change is confirmed.

    Boundaries:
        - Focus: format plan marks hash change
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to format plan marks hash change
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by push plan
    """
    live = _live(ID_A, "Bok")
    plan, _ = _plan([_pulled(live, title="Book")], existing_hashes={live["hash"]: ID_A}, existing_by_id={ID_A: live})
    assert "(hash changed)" in format_plan(plan)
