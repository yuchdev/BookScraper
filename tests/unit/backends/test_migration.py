"""Unit tests for bookscraper.backends.migration, the v1 to v2 data migration engine.

The migration rewrites every pre-03.1 document to the canonical schema-2 shape and recomputes its
hash. The dangerous failure modes are silent data loss (dropping a document), a hash that drifts
without being reported, and a collision that overwrites a different book. These tests pin the
planner (classification, collision detection) and the json apply path (backup-before-write via a
real tmp_path store), injecting the real transform ``normalize.from_v1_document`` the same way the
command does, so none touches the repo-root books.json. The pure transform itself is covered in
tests/unit/scraping/test_normalize.py; the mongo apply path and the command surface are covered in
tests/mock/commands/test_migrate.py.
"""

import json

import pytest

from bookscraper.backends import migration
from bookscraper.backends.local import store
from bookscraper.book_utils import hash_book
from bookscraper.models import SCHEMA_VERSION, validate_document
from bookscraper.scraping import normalize

_TRANSFORM = normalize.from_v1_document


def _amazon_v1() -> dict:
    """A realistic v1 Amazon document (raw date, hyphenated ISBN-13, bare site key)."""
    return {
        "asin": "0136816487",
        "authors": ["Bjarne Stroustrup"],
        "description": "A concise introduction to C++.",
        "hash": "1b93bf19f83335db49b6af3fc2700565f80d42e1dabb44834b2c55a9ff4ad648",
        "isbn10": "0136816487",
        "isbn13": "978-0136816485",
        "publication_date": "September 24, 2022",
        "publication_year": 2022,
        "site": "amazon",
        "tags": ["C++ Programming Language", "Object-Oriented Design"],
        "title": "Tour of C++, A (C++ In-Depth Series)",
        "url": "https://www.amazon.com/dp/0136816487",
    }


def _leanpub_v1() -> dict:
    """A realistic v1 Leanpub document (``leanpub.com`` host, API-derived keys, no url)."""
    return {
        "about_the_book": "<p>Everything about C++ lambdas.</p>",
        "authors": ["Bartlomiej Filipek"],
        "book_id": "102",
        "categories": ["Computer Science", "C and C++"],
        "hash": "3b1beb4b6d58060b587c648b566e9af155241f7c6405a9bfa8c3249f2cff26c5",
        "last_published_at": "2020-09-01",
        "site": "leanpub.com",
        "slug": "cpplambda",
        "title": "C++ Lambda Story",
    }


class TestBuildPlanAndJsonApply:
    """Tests for the planner and the json apply path (real tmp_path store I/O)."""

    def test_dry_run_writes_nothing_json(self, isolated_local_store):
        """
        [Local] migration json dry-run: verifies a dry run reports changes but writes no file.

        Scenario:
            Given a tmp_path json store holding one v1 Amazon document
            When migrate_json_store is called with apply=False
            Then the report says one document would be migrated, applied is False, and the store
                file is left exactly as it was (no rewrite, no backup)

        Boundaries:
            - apply=False must never touch the store or create books.json.bak
            - The report still counts the pending migration

        On failure, first check:
            - The ``if apply and plan.rewrites`` guard in migrate_json_store
            - Whether _build_plan miscounted the pending rewrite
        """
        original = [_amazon_v1()]
        isolated_local_store.write_text(json.dumps(original), encoding="utf-8")

        report = migration.migrate_json_store(isolated_local_store, apply=False, transform=_TRANSFORM)

        assert report.applied is False
        assert report.migrated == 1
        assert json.loads(isolated_local_store.read_text(encoding="utf-8")) == original
        assert not isolated_local_store.with_name("books.json.bak").exists()

    def test_apply_json_creates_backup_first(self, isolated_local_store):
        """
        [Local] migration json apply: verifies --apply backs up the old store before rewriting.

        Scenario:
            Given a tmp_path json store holding one v1 Amazon document
            When migrate_json_store is called with apply=True
            Then books.json.bak holds the original v1 content, the live store now holds the valid
                schema-2 document, and no document was dropped

        Boundaries:
            - The backup must capture the PRE-migration content (one generation)
            - Every migrated document passes validate_document()

        On failure, first check:
            - store._write_books's copy-to-.bak-then-atomic-replace ordering
            - Whether migrate_json_store wrote the transformed list (not the originals)
        """
        original = [_amazon_v1()]
        isolated_local_store.write_text(json.dumps(original), encoding="utf-8")

        report = migration.migrate_json_store(isolated_local_store, apply=True, transform=_TRANSFORM)

        backup = isolated_local_store.with_name("books.json.bak")
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == original

        stored = store._load_books(isolated_local_store)
        assert len(stored) == 1
        assert stored[0]["schema_version"] == SCHEMA_VERSION
        assert validate_document(stored[0]) == []
        assert report.applied is True
        assert report.migrated == 1

    def test_apply_twice_is_a_no_op(self, isolated_local_store):
        """
        [Local] migration json apply: verifies a second apply changes nothing (idempotent).

        Scenario:
            Given a tmp_path json store that has already been migrated once with apply=True
            When migrate_json_store is called a second time with apply=True
            Then the second report shows zero migrated and applied False, and the store content is
                byte-for-byte unchanged from after the first pass

        Boundaries:
            - Success criterion: running migrate --apply twice yields zero second-pass changes
            - applied is False when there is nothing to write

        On failure, first check:
            - Whether transform_v1_to_v2 is idempotent on the already-stored v2 documents
            - The ``transformed == content`` unchanged branch in _build_plan
        """
        isolated_local_store.write_text(json.dumps([_amazon_v1(), _leanpub_v1()]), encoding="utf-8")
        migration.migrate_json_store(isolated_local_store, apply=True, transform=_TRANSFORM)
        after_first = isolated_local_store.read_text(encoding="utf-8")

        report = migration.migrate_json_store(isolated_local_store, apply=True, transform=_TRANSFORM)

        assert report.migrated == 0
        assert report.applied is False
        assert isolated_local_store.read_text(encoding="utf-8") == after_first

    def test_hash_drift_document_left_at_v1_and_reported(self):
        """
        [Unit] migration planner: verifies a colliding recomputed hash leaves the document at v1.

        Scenario:
            Given two v1 Leanpub documents with the same title/year/author identity, where the
                first repeats an author case-insensitively (so its de-duplicated hash collides with
                the second document's already-correct hash)
            When _build_plan runs over both
            Then the first is reported as a hash collision (NOT rewritten, so it stays v1) while the
                second migrates normally, and no document is dropped

        Boundaries:
            - Collision path: the recomputed hash duplicates another document's hash
            - The colliding document must be left untouched (schema v1) and reported for a human

        On failure, first check:
            - The collision branch in _build_plan (existing_hashes minus the doc's own old hash)
            - Whether author de-duplication still produces the colliding hash
        """
        shared_hash = hash_book("C++ Lambda Story", ["Bartlomiej Filipek"], 2020)

        colliding = _leanpub_v1()
        colliding["authors"] = ["Bartlomiej Filipek", "bartlomiej filipek"]
        colliding["slug"] = "dup"
        colliding["book_id"] = "900"
        colliding["hash"] = "a" * 64

        canonical = _leanpub_v1()
        canonical["hash"] = shared_hash

        plan = migration._build_plan([colliding, canonical], _TRANSFORM)

        assert len(plan.hash_collisions) == 1
        collision = plan.hash_collisions[0]
        assert collision["collides_with"] == shared_hash
        assert collision["hash"] == "a" * 64
        # The colliding document is not among the rewrites, so it stays at v1.
        assert all(rewrite[0] != 0 for rewrite in plan.rewrites)
        # The canonical document still migrates (index 1 rewritten).
        assert any(rewrite[0] == 1 for rewrite in plan.rewrites)

    def test_invalid_after_migration_is_left_untouched(self, isolated_local_store):
        """
        [Local] migration json apply: verifies a document invalid after transform is not written.

        Scenario:
            Given a tmp_path store with one valid v1 document and one v1 document whose site is not a
                known bare key (which transforms to a schema-2 record validate_document rejects)
            When migrate_json_store is called with apply=True
            Then the invalid document is reported and left verbatim, while the valid one migrates,
                and the store still holds both documents (nothing deleted)

        Boundaries:
            - Invalid-after-migration path: validate_document rejects the transformed record
            - Never deletes a document, even an invalid one

        On failure, first check:
            - The ``if problems`` branch in _build_plan
            - Whether validate_document still rejects an unknown site value
        """
        valid = _amazon_v1()
        invalid = _amazon_v1()
        invalid["title"] = "A Different Book"
        invalid["site"] = "bogus-site"
        invalid["asin"] = "BROKEN00001"
        invalid["url"] = "https://www.amazon.com/dp/BROKEN00001"
        invalid["hash"] = "b" * 64
        isolated_local_store.write_text(json.dumps([valid, invalid]), encoding="utf-8")

        report = migration.migrate_json_store(isolated_local_store, apply=True, transform=_TRANSFORM)

        assert report.invalid == 1
        assert report.migrated == 1
        stored = store._load_books(isolated_local_store)
        assert len(stored) == 2
        untouched = [doc for doc in stored if doc.get("site") == "bogus-site"]
        assert len(untouched) == 1
        assert "schema_version" not in untouched[0]

    def test_non_dict_and_broken_v2_entries_are_skipped_not_dropped(self):
        """
        [Unit] migration planner: verifies junk entries and a malformed v2 document are skipped.

        Scenario:
            Given a document list holding a non-dict junk entry, a v2 document missing required keys
                (so the passthrough transform raises), and one healthy v1 document
            When _build_plan runs
            Then the junk entry is counted unchanged, the malformed v2 document is reported invalid
                by exception reason (not crashing the pass), and only the healthy document is a rewrite

        Boundaries:
            - Non-dict entry branch and the transform exception handler
            - A malformed document must never abort the whole migration

        On failure, first check:
            - The ``not isinstance(doc, dict)`` and ``except (KeyError, TypeError, ValueError)``
              branches in _build_plan
        """
        plan = migration._build_plan(["junk", {"schema_version": SCHEMA_VERSION}, _amazon_v1()], _TRANSFORM)

        assert plan.unchanged == 1
        assert len(plan.invalid_documents) == 1
        assert "reason" in plan.invalid_documents[0]
        assert plan.migrated == 1


def test_write_report_contains_counts_and_detail(tmp_path):
    """
    [Local] migration report: verifies the written report carries counts and the detail lists.

    Scenario:
        Given a report produced by planning a single-document migration
        When write_report serializes it to a tmp_path directory
        Then a migration-report-*.json file exists holding the counts block and the
            changed_hashes / hash_collisions / invalid_documents keys

    Boundaries:
        - The report is the audit artifact; its JSON shape is relied on by a human/CI
        - Written under the given directory (the run log dir in production)

    On failure, first check:
        - MigrationReport.to_dict's key names
        - write_report's filename pattern and atomic write
    """
    plan = migration._build_plan([_amazon_v1()], _TRANSFORM)
    report = migration._report_from_plan("json", False, plan)

    path = migration.write_report(report, tmp_path)

    assert path.exists()
    assert path.name.startswith("migration-report-")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["counts"]["total"] == 1
    assert payload["store_backend"] == "json"
    assert "changed_hashes" in payload
    assert "hash_collisions" in payload
    assert "invalid_documents" in payload


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
