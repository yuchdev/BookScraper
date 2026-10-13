"""The ``migrate`` CLI workflow: rewrite stored v1 documents to schema-2 (subtask 03.1/06).

Thin command layer over :mod:`bookscraper.backends.migration`: it resolves the requested storage
backend (reusing :func:`bookscraper.backends.resolve_store_backend`, so the same pre-flight checks
and explicit-choice rules as the scraping subcommands apply), runs the migration (dry-run unless
``--apply``), writes the ``migration-report-*.json`` audit artifact to the run log directory, and
prints a per-field change summary plus every changed hash and collision.

The console/log output names only ids, hashes, and counts - never a credential, URI, or document
body - so the run log stays safe to keep. The document title appears only inside the written
report file (for a collision a human must resolve), never in a printed line.
"""

from __future__ import annotations

import argparse

from .. import book_utils, exitcodes
from ..backends import MongoBackend, migration, resolve_store_backend
from ..backends.local import store
from ..book_utils import print_log
from ..scraping import normalize
from ..scraping.safe_text import sanitize_untrusted


def _print_summary(report: migration.MigrationReport, report_path, apply: bool):
    """Print the human-readable migration summary to the console (and run log).

    Emits the counts, the per-field change summary, each changed hash (old to new), and each
    collision by id/hash only (no document body), then where the report was written and whether the
    store was touched. Every printed id/hash is run through
    :func:`bookscraper.scraping.safe_text.sanitize_untrusted` first, because a JSON-backend id can
    be a page-derived URL carrying terminal escape/control characters that would otherwise spoof
    console output.

    :param report: The migration report to summarize.
    :param report_path: Where the full JSON report was written.
    :param apply: Whether the store was written (``--apply``) or this was a dry run.
    """
    mode = "APPLIED" if apply else "DRY RUN (no store changes; pass --apply to write)"
    print_log(f"\nSchema v1->v2 migration ({report.store_backend}) - {mode}", "step")
    print_log(
        f"  total={report.total} migrated={report.migrated} hash_changed={report.hash_changed} "
        f"collisions={report.collisions} invalid={report.invalid} unchanged={report.unchanged}",
        "info",
    )

    if report.field_changes:
        print_log("  Per-field changes:", "info")
        for field_name in sorted(report.field_changes):
            print_log(f"    {field_name}: {report.field_changes[field_name]}", "info")

    for entry in report.changed_hashes:
        entry_id = sanitize_untrusted(str(entry["id"]))
        print_log(f"  hash changed: {entry_id} {entry['old']} -> {entry['new']}", "info")

    for entry in report.hash_collisions:
        entry_id = sanitize_untrusted(str(entry["_id"]))
        print_log(
            f"  Warning: hash collision (NOT rewritten, resolve manually): id={entry_id} "
            f"hash={entry['hash']} collides_with={entry['collides_with']}",
            "warning",
        )

    for entry in report.invalid_documents:
        entry_id = sanitize_untrusted(str(entry["id"]))
        print_log(f"  Warning: document invalid after migration (left untouched): id={entry_id}", "warning")

    if report.apply_failed:
        print_log(
            f"  Error: apply failed part-way ({report.apply_error}); {report.applied_count} written, "
            f"{report.failed_count} not written. Re-run --apply to resume.",
            "error",
        )

    print_log(f"  Report written to {report_path}", "info")


async def run(args: argparse.Namespace) -> int:
    """Run the migration for the chosen backend, writing only when ``--apply`` is given.

    Resolves the backend, migrates it (mongo via guarded per-document ``ReplaceOne`` batches, json
    via the atomic store write with a ``books.json.bak`` backup), writes the report, and prints the
    summary. The resolved backend connection is always closed.

    :param args: Parsed CLI arguments (``store_backend`` and ``apply``).
    :returns: :data:`~bookscraper.exitcodes.FATAL` on a corrupt local store, or after writing the
        report when the mongo apply failed part-way; otherwise :data:`~bookscraper.exitcodes.OK`.
    """
    backend = resolve_store_backend(args.store_backend)
    try:
        if isinstance(backend, MongoBackend):
            report = migration.migrate_mongo_collection(
                backend._collection, apply=args.apply, transform=normalize.from_v1_document
            )
        else:
            report = migration.migrate_json_store(
                store.LOCAL_STORE_FILE, apply=args.apply, transform=normalize.from_v1_document
            )
    except store.LocalStoreCorruptError as exc:
        print_log(f"Migration failed: {exc}", "error")
        return exitcodes.FATAL
    finally:
        backend.close()

    report_path = migration.write_report(report, book_utils.LOG_DIR)
    _print_summary(report, report_path, args.apply)
    if report.apply_failed:
        return exitcodes.FATAL
    return exitcodes.OK
