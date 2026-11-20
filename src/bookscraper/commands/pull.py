"""
The `bookscraper pull` workflow - the reverse direction of the staged pipeline (ADR 0003).

Exports every document in the MongoDB books collection to a staging-format JSON file (each
document keeps its identity as a hex `_id` string), so it can be reviewed, edited, and sent
back with `bookscraper push --allow-updates`. Read-only against MongoDB: connects without
creating indexes and only ever calls find(). Never overwrites an existing file without --force,
and even then only atomically, after the whole export has succeeded.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pymongo.collection import Collection

from ..backends.local.staging import write_staging
from ..backends.mongo import database
from ..backends.mongo.export import ExportError, iter_collection, to_staging_document
from ..book_utils import logger, print_log


def export_collection(collection: Collection) -> list[dict]:
    return [to_staging_document(raw) for raw in iter_collection(collection)]


async def run(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and not args.force:
        print_log(f"'{output}' already exists; it may hold review work. Use --force to overwrite it.", "error")
        sys.exit(1)

    print_log(f"\nPulling the MongoDB books collection into {output}", "step")
    collection = await asyncio.to_thread(database.get_mongo_collection, ensure_indexes=False)
    if collection is None:
        print_log("MongoDB connection: FAILED. Nothing was written.", "error")
        sys.exit(1)

    try:
        documents = await asyncio.to_thread(export_collection, collection)
    except ExportError as e:
        print_log(f"Export aborted: {e} Nothing was written.", "error")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pull failed while reading MongoDB: {type(e).__name__}")
        print_log(f"Reading from MongoDB failed ({type(e).__name__}). Nothing was written.", "error")
        sys.exit(1)
    finally:
        database.close_mongo_connection()

    try:
        write_staging(output, documents, overwrite=args.force)
    except OSError as e:
        print_log(f"Could not write {output}: {e}", "error")
        sys.exit(1)

    print_log(f"Pulled {len(documents)} document(s) into {output}.", "success")
    print_log(f"Next: edit {output}, then run: bookscraper push --staging-file {output} --dry-run", "step")
