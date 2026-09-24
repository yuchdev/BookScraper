from __future__ import annotations

import logging
from typing import Optional

from pymongo.collection import Collection

from ...book_utils import print_log
from .database import get_mongo_collection

module_logger = logging.getLogger("deduplicate")


def leanpub_prescrape_deduplicate(book_id: str, book_slug: str, mongo_collection: Optional[Collection] = None) -> bool:
    """
    Checks if a Leanpub book exists in the database by either its book_id or book_slug.
    At least one of book_id or book_slug must be provided.
    """
    books_collection = mongo_collection if mongo_collection is not None else get_mongo_collection()

    if books_collection is None:
        module_logger.error("MongoDB collection not provided for duplicate check or not initialized.")
        print_log("Error: MongoDB collection not available for duplicate check.", "error")
        return False

    # Build the query using $or
    query_conditions = []
    log_messages = []

    if book_id:
        query_conditions.append({"book_id": book_id})
        log_messages.append(f"Book ID: {book_id}")
    if book_slug:
        query_conditions.append({"slug": book_slug})
        log_messages.append(f"Slug: {book_slug}")

    if not query_conditions:
        module_logger.warning("No book_id or book_slug provided for Leanpub book existence check.")
        print_log("Warning: No book_id or book_slug provided for Leanpub book existence check.", "warning")
        return False

    # The $or operator takes a list of conditions.
    # The document will match if any of the conditions are met.
    query = {"$or": query_conditions}
    log_identifier = ", ".join(log_messages)

    try:
        is_duplicate = books_collection.find_one(query) is not None
        if is_duplicate:
            module_logger.info(
                f"Leanpub book ({log_identifier}) found in MongoDB Atlas database. Skipping detailed scrape."
            )
        return is_duplicate

    except Exception as e:
        module_logger.error(f"Error checking for Leanpub book in DB by ({log_identifier}): {e}", exc_info=True)
        print_log(f"Error: Error checking for Leanpub book in DB by ({log_identifier}): {e}", "error")
        return False
