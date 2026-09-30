from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional, Tuple

import bson
from dotenv import load_dotenv
from pymongo.collection import Collection
from pymongo.errors import (
    BulkWriteError,
    InvalidDocument,
)

if TYPE_CHECKING:
    from pymongo import MongoClient

    from ..storage import SaveSummary

from ...book_utils import print_log
from . import config

# The single MongoClient factory lives in client.py so there is exactly one client
# construction site in src/. These names are re-exported here (rather than only imported)
# so existing `from ...database import uri_selects_x509_auth` / patch targets keep working.
from .client import (  # noqa: F401 - re-exported for backward compatibility
    X509_AUTH_MECHANISM,
    MongoConnectionError,
    _warn_if_x509_mechanism_missing,
    build_mongo_client,
    ping,
    resolve_server_selection_timeout_ms,
    uri_has_embedded_credentials,
    uri_selects_x509_auth,
)

module_logger = logging.getLogger(__name__)


# Global variables to hold the MongoDB client, database, and collection
_mongo_client = None
_mongo_db = None
_mongo_collection = None

# The resolved database/collection names of the live connection, so the success line in
# get_mongo_collection() names exactly what was connected instead of re-reading env vars
# (which would disagree with a settings.json-driven namespace). Default to the shared
# constants until _initialize_mongodb_connection() resolves them.
_mongo_db_name = config.DEFAULT_DB_NAME
_mongo_collection_name = config.DEFAULT_COLLECTION_NAME


def _initialize_mongodb_connection() -> Tuple[Optional[MongoClient], Optional[Collection]]:
    """
    Initializes the MongoDB client and books collection.
    This function should only be called once when the application starts or if the connection is lost.
    Sets the global _mongo_client, _mongo_db, and _mongo_collection variables.
    """
    global _mongo_client, _mongo_db, _mongo_collection
    global _mongo_db_name, _mongo_collection_name

    # Initialize to None at the start for clear type handling
    _mongo_client = None
    _mongo_db = None
    _mongo_collection = None

    load_dotenv()
    tls_ca_file = os.environ.get("TLS_CA_FILE")  # CA bundle for server certificate verification
    try:
        mongodb_uri, tls_client_cert_key_file = config.resolve_mongo_connection()
        # Determinism: when settings.json exists, the namespace comes from it (its optional
        # 'database' block, else the defaults); the legacy db/collection env vars are only
        # consulted in legacy (no settings.json) mode. Resolved once here via config, not env.
        _mongo_db_name, _mongo_collection_name = config.resolve_mongo_namespace()
    except config.ConfigError as e:
        module_logger.critical(f"Invalid MongoDB configuration: {e}")
        print_log(f"Critical Error: Invalid MongoDB configuration: {e}", "error")
        return None, None

    if not mongodb_uri:
        module_logger.critical("MONGODB_URI not found in environment variables. Cannot connect to MongoDB.")
        # print_log("Error: MONGODB_URI not found in environment variables. Aborting MongoDB connection.", "error")
        return None, None

    # Log TLS file status before connection attempt
    if tls_ca_file:
        module_logger.info("TLS_CA_FILE set. Using explicit server CA verification.")
    else:
        module_logger.info(
            "TLS_CA_FILE not set. Proceeding without explicit server CA verification (may rely on system CAs)."
        )

    if tls_client_cert_key_file:
        module_logger.info(f"Using X.509 client certificate/key file for authentication: {tls_client_cert_key_file}")
    else:
        module_logger.info("TLS_CERT_FILE not set. X.509 authentication will not be used.")

    try:
        # Single factory: identical TLS kwargs to the pre-flight check for identical inputs.
        # A missing cert file, a failed ping, or any other pymongo failure surfaces here as a
        # redacted MongoConnectionError, so no raw pymongo text (host/URI/credentials) is ever
        # interpolated into a log or console message below.
        _mongo_client = build_mongo_client(
            mongodb_uri,
            tls_client_cert_key_file,
            tls_ca_file,
            timeout_ms=resolve_server_selection_timeout_ms(),
        )
        ping(_mongo_client)

        _mongo_db = _mongo_client[_mongo_db_name]
        _mongo_collection = _mongo_db[_mongo_collection_name]

        # Ensure the unique 'hash' index plus the sparse secondary lookup indexes.
        ensure_indexes(_mongo_collection)

        return _mongo_client, _mongo_collection

    except MongoConnectionError as e:
        # `e` is already redacted (class name + code/codeName only) - safe to interpolate.
        module_logger.critical(f"Failed to connect to MongoDB Atlas: {e}")
        print_log(
            f"Critical Error: Failed to connect to MongoDB Atlas. Please check your connection string and network. Details: {e}",
            "error",
        )
        _mongo_client = None  # Reset globals on failure
        _mongo_db = None
        _mongo_collection = None
        return None, None
    except Exception as e:
        # Defensive: any non-pymongo failure here. Log only the exception CLASS NAME - never
        # the raw text and never exc_info=True, since an OS/other exception may embed the
        # cert path or other sensitive detail (SEC-4).
        module_logger.critical(f"Unexpected error during MongoDB initialization: {type(e).__name__}.")
        print_log(
            "Critical Error: Unexpected error during MongoDB initialization. Check the log file.",
            "error",
        )
        _mongo_client = None  # Reset globals on failure
        _mongo_db = None
        _mongo_collection = None
        return None, None


def get_mongo_collection() -> Optional[Collection]:
    """
    Returns the globally managed MongoDB collection.
    Initializes the connection if it doesn't exist.
    """
    global _mongo_client, _mongo_db, _mongo_collection

    if _mongo_collection is None:
        try:
            # Attempt to initialize the connection if it's not already set up
            _mongo_client, _mongo_collection = _initialize_mongodb_connection()
            # Announce success only when a collection was actually obtained. A failed
            # init returns None having already logged the specific cause (and
            # resolve_store_backend prints the FAILED line), so emitting a "success"
            # line here too would be duplicate and contradictory. Name only the
            # database/collection, never the host or URI.
            if _mongo_collection is not None:
                # Name the namespace actually connected (resolved by _initialize_mongodb_connection),
                # never the host/URI, so the success line can't disagree with the live connection.
                success_message = f"Connection to MongoDB Atlas successful ({_mongo_db_name}.{_mongo_collection_name})."
                print_log(success_message, "success")
                module_logger.info(success_message)
        except Exception as e:
            # Defensive: _initialize_mongodb_connection() catches everything today, so
            # this branch is currently unreachable. It is kept for a future refactor
            # that lets init raise, and is covered by a unit test.
            print_log(
                f"MongoDB connection: FAILED. MongoDB output will not be available: {e}",
                "error",
            )

    return _mongo_collection


def close_mongo_connection() -> None:
    """
    Closes the global MongoDB client connection.
    """
    global _mongo_client, _mongo_db, _mongo_collection
    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None
        _mongo_db = None
        _mongo_collection = None
        module_logger.info("MongoDB connection closed.")


def save_books_to_mongodb(books: list[dict], mongo_collection: Optional[Collection] = None) -> SaveSummary:
    """
    Saves a list of book dictionaries to MongoDB in a single unordered bulk insert.

    Optionally accepts a pre-established MongoDB collection; otherwise, uses the
    globally managed MongoDB collection. Each document is validated and inserted as
    a *copy* so pymongo never mutates the caller's dicts with an ObjectId ``_id``
    (which would later break ``json.dumps`` in a diagnostic write).

    :param books: Book documents to insert.
    :param mongo_collection: Optional pre-resolved collection; the global one is used
        when omitted.
    :returns: A :class:`~bookscraper.backends.storage.SaveSummary` describing how many
        documents were inserted, skipped as duplicates, or rejected as errors. When the
        collection is unavailable, every book is reported as an error.
    """
    # Local import: backends.storage imports this module, so importing SaveSummary at
    # module level would be circular (mirrors book_utils's config import comment).
    from ..storage import SaveSummary

    # Use the passed collection if provided, otherwise get the global one
    books_collection = mongo_collection if mongo_collection is not None else get_mongo_collection()

    if books_collection is None:  # If it's still None after trying to get it, log error and return
        module_logger.error("MongoDB collection not available. Cannot save books.")
        print_log("Error: MongoDB collection not available. Cannot save books.", "error")
        return SaveSummary(inserted=0, duplicates=0, errors=len(books))

    if not books:
        return SaveSummary(inserted=0, duplicates=0, errors=0)

    # Pre-validate each document as a copy: a single un-encodable document is counted
    # as one error rather than aborting the whole insert_many batch, and the copy means
    # insert_many's in-place _id assignment never touches the caller's dicts.
    docs: list[dict] = []
    num_errors = 0
    for book in books:
        # A document with no 'hash' cannot be de-duplicated (the unique 'hash' index
        # would index it as null and silently accept it), so reject it up front as an
        # error rather than store an un-dedupable book - matching store.save_books on
        # the json backend, so both backends agree that a hash-less book is an error.
        if not (isinstance(book, dict) and book.get("hash")):
            module_logger.error(f"Book missing 'hash', cannot store: {book}")
            print_log("Error: Book missing 'hash', cannot store.", "error")
            num_errors += 1
            continue
        doc = dict(book)
        try:
            bson.encode(doc)
        except InvalidDocument as e:
            module_logger.error(
                f"Invalid document for insertion: {e}. Book: {book.get('title', 'Unknown Title')}",
                exc_info=True,
            )
            print_log(
                f"Error: Invalid document for insertion: {e}. Book: {book.get('title', 'Unknown Title')}",
                "error",
            )
            num_errors += 1
            continue
        docs.append(doc)

    num_inserted = 0
    num_duplicates = 0

    if docs:
        try:
            result = books_collection.insert_many(docs, ordered=False)
            num_inserted = len(result.inserted_ids)
        except BulkWriteError as e:
            # ordered=False keeps going after failures; nInserted counts the survivors,
            # and each write error is a duplicate (code 11000) or some other rejection.
            num_inserted = e.details.get("nInserted", 0)
            for write_error in e.details.get("writeErrors", []):
                if write_error.get("code") == 11000:
                    num_duplicates += 1
                else:
                    num_errors += 1
        except Exception as e:
            module_logger.error(f"MongoDB bulk insertion failed: {e}", exc_info=True)
            print_log(f"Error: MongoDB bulk insertion failed: {e}", "error")
            num_errors += len(docs)

    module_logger.info(
        f"MongoDB insertion summary: {num_inserted} new, {num_duplicates} duplicate{'s' if num_duplicates != 1 else ''}, {num_errors} error{'s' if num_errors != 1 else ''}."
    )
    print_log(
        f"MongoDB insertion summary: {num_inserted} new, {num_duplicates} duplicates, {num_errors} errors.",
        "success",
    )
    return SaveSummary(inserted=num_inserted, duplicates=num_duplicates, errors=num_errors)


def check_amazon_asin_exists_in_db(asin: str, mongo_collection: Optional[Collection] = None) -> bool:
    books_collection = mongo_collection if mongo_collection is not None else get_mongo_collection()

    if books_collection is None:
        module_logger.error("MongoDB collection not provided for duplicate check or not initialized.")
        print_log("Error: MongoDB collection not available for duplicate check.", "error")
        return False

    try:
        is_duplicate = books_collection.find_one({"asin": asin}) is not None
        if is_duplicate:
            module_logger.info(f"ASIN {asin} found in MongoDB Atlas database. Skipping detailed scrape.")
        return is_duplicate

    except Exception as e:
        module_logger.error(
            f"Error checking for duplicate Amazon book in DB by ASIN: {e}",
            exc_info=True,
        )
        print_log(
            f"Error: Error checking for duplicate Amazon book in DB by ASIN: {e}",
            "error",
        )
        return False


def check_book_exists_in_db(book_hash: str, mongo_collection: Optional[Collection] = None) -> bool:
    """
    Checks if a book with the given hash already exists in the database.
    """
    books_collection = mongo_collection if mongo_collection is not None else get_mongo_collection()

    # module_logger.info(f"Debug: Type of books_collection in check_book_exists_in_db: {type(books_collection)}")
    # print_log(f"Debug: Type of MongoDB collection in duplicate check: {type(books_collection)}", "info")

    if books_collection is None:
        module_logger.error("MongoDB collection not provided for duplicate check or not initialized.")
        print_log("Error: MongoDB collection not available for duplicate check.", "error")
        return False

    try:
        return books_collection.find_one({"hash": book_hash}) is not None
    except Exception as e:
        module_logger.error(f"Error checking for duplicate book in DB: {e}", exc_info=True)
        print_log(f"Error: Error checking for duplicate book in DB: {e}", "error")
        return False


def update_book_isbn(
    books_collection: Collection,
    book_hash: str,
    new_isbn10: Optional[str] = None,
    new_isbn13: Optional[str] = None,
) -> bool:
    """
    Updates the ISBNs of an existing book in the database.
    This function expects an already connected collection to be passed to it.
    """
    # This function is designed to receive an already valid collection.
    # If it's None, it implies an issue in the calling logic.
    if not books_collection:
        module_logger.error("MongoDB collection not available for ISBN update.")
        print_log("Error: MongoDB collection not available for ISBN update.", "error")
        return False

    update_fields = {}
    if new_isbn10 and new_isbn10 != "N/A":
        update_fields["isbn10"] = new_isbn10
    if new_isbn13 and new_isbn13 != "N/A":
        update_fields["isbn13"] = new_isbn13

    if not update_fields:
        module_logger.info(f"No valid ISBNs to update for hash: {book_hash}")
        print_log(f"Info: No valid ISBNs to update for hash: {book_hash}", "info")
        return False

    try:
        result = books_collection.update_one({"hash": book_hash}, {"$set": update_fields})
        if result.modified_count > 0:
            module_logger.info(f"Updated ISBNs for book with hash '{book_hash}'.")
            print_log(f"Updated ISBNs for book with hash '{book_hash}'.", "info")
            return True
        else:
            module_logger.warning(f"Book with hash '{book_hash}' not found for ISBN update or no change needed.")
            print_log(
                f"Warning: Book with hash '{book_hash}' not found for ISBN update or no change needed.",
                "warning",
            )
            return False
    except Exception as e:
        module_logger.error(f"Error updating ISBNs for hash '{book_hash}': {e}", exc_info=True)
        print_log(f"Error: Error updating ISBNs for hash '{book_hash}': {e}", "error")
        return False


# Sparse secondary lookup indexes, as (field, index name) pairs. Each accelerates a
# pre-scrape dedup lookup (asin -> Amazon, book_id/slug -> Leanpub) that would otherwise
# be a full collection scan. All are sparse (only documents carrying the field are
# indexed) and none is unique: two editions can legitimately share a slug across sites.
_SECONDARY_INDEXES: Tuple[Tuple[str, str], ...] = (
    ("asin", "asin_lookup_index"),
    ("book_id", "book_id_lookup_index"),
    ("slug", "slug_lookup_index"),
)


def ensure_indexes(books_collection: Collection) -> None:
    """
    Ensures the unique 'hash' index and the sparse secondary lookup indexes exist.

    Each index is created idempotently: `index_information()` is read once up front and
    an index is only created when its name is absent. The unique 'hash' index guards
    against duplicate inserts at write time; the sparse 'asin', 'book_id', and 'slug'
    indexes turn the pre-scrape deduplication lookups into index scans instead of full
    collection scans.

    A failure creating one secondary index is logged as a warning and the remaining
    indexes are still attempted. A failure reading `index_information()` or creating the
    unique 'hash' index is logged as an error and aborts the call, matching the prior
    behavior of the hash-only helper.

    :param books_collection: The MongoDB books collection, or None when unavailable.
    """
    if books_collection is None:
        module_logger.error("MongoDB collection not available to ensure indexes.")
        return

    try:
        # Check which indexes already exist before creating any of them.
        existing_indexes = books_collection.index_information()
        if "hash_unique_index" not in existing_indexes:
            books_collection.create_index("hash", unique=True, name="hash_unique_index")
            module_logger.info("Ensured unique index on 'hash' field.")
            print_log("Ensured unique index on 'hash' field.", "info")
        else:
            module_logger.info("Unique index on 'hash' field already exists.")
    except Exception as e:
        module_logger.error(f"Failed to ensure unique index on 'hash': {e}", exc_info=True)
        print_log(f"Error: Failed to ensure unique index on 'hash': {e}", "error")
        return

    for field, index_name in _SECONDARY_INDEXES:
        try:
            if index_name not in existing_indexes:
                books_collection.create_index(field, sparse=True, name=index_name)
                module_logger.info(f"Ensured sparse index on '{field}' field.")
                print_log(f"Ensured sparse index on '{field}' field.", "info")
            else:
                module_logger.info(f"Sparse index on '{field}' field already exists.")
        except Exception as e:
            module_logger.warning(f"Failed to ensure sparse index on '{field}': {e}", exc_info=True)
            print_log(f"Warning: Failed to ensure sparse index on '{field}': {e}", "warning")


def ensure_unique_index_on_hash(books_collection: Collection) -> None:
    """
    Ensures the collection indexes exist.

    .. deprecated::
        Use :func:`ensure_indexes` instead. This thin alias is retained so existing
        imports keep working; it now also creates the sparse secondary lookup indexes.

    :param books_collection: The MongoDB books collection, or None when unavailable.
    """
    ensure_indexes(books_collection)
