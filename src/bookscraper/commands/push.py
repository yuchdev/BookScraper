"""
The `bookscraper push` workflow - step 2 of the staged pipeline (ADR 0003).

validate the whole staging file → connect → fetch what MongoDB already holds → plan (pure) →
[--dry-run stops here] → refuse updates without --allow-updates → one bulk write → report.

Every refusal happens before the first write. Exit codes: 0 every item reached its planned
outcome (or dry run); 1 refused before writing anything; 3 wrote, but at least one item errored.
The staging file is never modified; MongoDB documents are never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from ..backends.local.staging import (
    STATUS_REJECTED,
    StagingFileError,
    load_staging,
    prepare_for_push,
    validate_staging_documents,
    write_staging,
)
from ..backends.mongo import database
from ..backends.mongo.export import ExportError
from ..backends.mongo.push_plan import PushPlan, build_push_plan, format_plan
from ..backends.mongo.push_writer import ItemOutcome, apply_push_plan, fetch_existing, oversized_documents
from ..book_utils import logger, print_log

EXIT_REFUSED = 1
EXIT_PARTIAL = 3


def build_report(plan: PushPlan, outcomes: Optional[dict[int, ItemOutcome]], default_outcome: str) -> list[dict]:
    """One record per staging document, in file order: what was planned and what actually happened."""
    records = []
    for item in plan.items:
        outcome = outcomes.get(item.index) if outcomes else None
        records.append(
            {
                "index": item.index,
                "action": item.action,
                "outcome": outcome.outcome if outcome else default_outcome,
                "title": item.title,
                "hash": item.hash,
                "hash_changed": item.hash_changed,
                "_id": item.object_id,
                "changed_fields": item.changed_fields,
                "error": outcome.error if outcome else None,
            }
        )
    return records


def write_report(report_file: Path, records: list[dict]) -> None:
    """Always written (even when every item is unchanged); atomic, owner-only for a new file."""
    try:
        write_staging(report_file, records)
        print_log(f"Push report written to {report_file}.", "info")
    except OSError as e:
        logger.error(f"Could not write push report {report_file}: {type(e).__name__}")
        print_log(f"Warning: could not write the push report to {report_file}: {e}", "warning")


def _refuse(message: str) -> None:
    print_log(message, "error")
    sys.exit(EXIT_REFUSED)


async def run(args: argparse.Namespace) -> None:
    staging_file = Path(args.staging_file)
    report_file = Path(args.report_file)
    mode = " (dry run: no changes will be made)" if args.dry_run else ""
    print_log(f"\nPushing {staging_file} to MongoDB{mode}", "step")

    # 1. Load + validate the whole file before touching the network.
    try:
        documents = load_staging(staging_file, missing_ok=False)
    except StagingFileError as e:
        logger.critical(f"Cannot push: {e}")
        _refuse(f"Critical Error: {e}")

    problems = validate_staging_documents(documents)
    if problems:
        for problem in problems:
            print_log(f"  {problem}", "error")
        _refuse(
            f"{len(problems)} problem(s) in {staging_file}; nothing was pushed. Fix them, or mark a document "
            '"_staging": {"status": "rejected"} to leave it out.'
        )

    prepared = [prepare_for_push(document, index) for index, document in enumerate(documents)]
    oversized = oversized_documents([p for p in prepared if p.status != STATUS_REJECTED])
    if oversized:
        for line in oversized:
            print_log(f"  {line}", "error")
        _refuse("Oversized document(s); nothing was pushed.")
    print_log(f"Validated {len(documents)} document(s).", "success")

    # 2. Connect. A dry run never creates indexes or writes anything.
    collection = await asyncio.to_thread(database.get_mongo_collection, ensure_indexes=not args.dry_run)
    if collection is None:
        _refuse("MongoDB connection: FAILED. Nothing was pushed.")
    print_log(f"Target: database '{collection.database.name}', collection '{collection.name}'.", "info")

    try:
        # 3. Plan.
        try:
            existing = await asyncio.to_thread(fetch_existing, collection, prepared)
        except ExportError as e:
            _refuse(f"Cannot compare with MongoDB: {e}")
        except Exception as e:
            logger.error(f"Reading existing documents failed: {type(e).__name__}")
            _refuse(f"Reading existing documents from MongoDB failed ({type(e).__name__}); nothing was pushed.")

        plan = build_push_plan(
            prepared,
            existing_hashes=existing.hashes,
            existing_by_id=existing.by_id,
            approved_only=args.approved_only,
        )
        if plan.errors:
            for error in plan.errors:
                print_log(f"  {error}", "error")
            _refuse(f"{len(plan.errors)} plan error(s); nothing was pushed.")

        print_log(format_plan(plan), "info")

        if args.dry_run:
            write_report(report_file, build_report(plan, None, "planned"))
            print_log(f"Dry run complete. To apply: bookscraper push --staging-file {staging_file}", "step")
            return

        if plan.requires_allow_updates and not args.allow_updates:
            write_report(report_file, build_report(plan, None, "refused"))
            _refuse(
                f"The plan replaces {plan.count('update')} existing MongoDB document(s) (listed above as 'update'). "
                "Nothing was pushed. Re-run with --allow-updates to apply them."
            )

        # 4. Write.
        if plan.has_writes:
            outcome_list = await asyncio.to_thread(apply_push_plan, collection, plan, prepared, existing)
        else:
            outcome_list = []
            print_log("Nothing to write: every document is a duplicate, unchanged, or skipped.", "info")
        outcomes = {o.index: o for o in outcome_list}
        records = build_report(plan, outcomes, "unchanged")
        for record, item in zip(records, plan.items, strict=True):
            if item.index not in outcomes:
                record["outcome"] = "skipped" if item.action.startswith("skipped") else item.action
        write_report(report_file, records)

        tally: dict[str, int] = {}
        for record in records:
            tally[record["outcome"]] = tally.get(record["outcome"], 0) + 1
        summary = ", ".join(
            f"{tally.get(k, 0)} {k}" for k in ("inserted", "updated", "duplicate", "unchanged", "skipped", "error")
        )
        errors = tally.get("error", 0)
        print_log(f"Push complete: {summary}.", "warning" if errors else "success")
        if errors:
            for record in records:
                if record["outcome"] == "error":
                    print_log(f"  #{record['index']} {record['title']}: {record['error']}", "error")
            sys.exit(EXIT_PARTIAL)
    finally:
        database.close_mongo_connection()
