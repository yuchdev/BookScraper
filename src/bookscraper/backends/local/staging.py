"""
The staging file: the JSON work area between step 1 (scrape) and step 2 (push to MongoDB).

See docs/dev/staging-file-format.md for the reviewer-facing contract and ADR 0003
(docs/adr/0003-staged-review-and-two-way-sync.md) for the design. This module owns the
whole contract - load, atomic write, append, in-memory dedup index, validation, and
push-time preparation - and is deliberately free of pymongo: anything that turns an `_id`
string into an ObjectId, or talks to the database, lives under backends/mongo/.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from ...book_utils import dumps_documents, extract_year_from_date, hash_book

module_logger = logging.getLogger(__name__)

DEFAULT_STAGING_FILE = Path("staging") / "books.json"
LEGACY_LOCAL_STORE_FILE = Path("books.json")

RESERVED_ID_KEY = "_id"
STAGING_META_KEY = "_staging"
RESERVED_KEYS = frozenset({RESERVED_ID_KEY, STAGING_META_KEY})

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
REVIEW_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

# Fields that feed book_utils.hash_book; an edit to any of them changes a book's identity hash.
HASH_SOURCE_FIELDS = ("title", "authors", "publication_year", "last_published_at")

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024  # MongoDB's BSON document limit (exact BSON check: push_writer)
MAX_STAGING_FILE_BYTES = 1024 * 1024 * 1024  # refuse to json-parse anything larger than 1 GiB
_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")


class StagingFileError(Exception):
    """The staging file is unreadable, not valid JSON, or not an array of objects."""


# --------------------------------------------------------------------------- #
# Load / write
# --------------------------------------------------------------------------- #
def load_staging(path: Path, *, missing_ok: bool) -> list[dict]:
    """
    Reads a staging file.

    Args:
        path: The staging file.
        missing_ok: True (step 1) returns [] for a missing file; False (push) raises.

    Raises:
        StagingFileError: the file is missing (and not missing_ok), unreadable, not valid JSON,
            not a top-level array, or contains a non-object entry. A corrupt file is never
            treated as empty - the next write would otherwise destroy a reviewer's work.
    """
    path = Path(path)
    if not path.exists():
        if missing_ok:
            return []
        raise StagingFileError(f"Staging file '{path}' does not exist.")
    try:
        size = path.stat().st_size
    except OSError as e:
        raise StagingFileError(f"Could not read staging file '{path}': {e}") from e
    if size > MAX_STAGING_FILE_BYTES:
        raise StagingFileError(f"Staging file '{path}' is {size} bytes; the limit is {MAX_STAGING_FILE_BYTES}.")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise StagingFileError(f"Could not read staging file '{path}': {e}") from e
    try:
        documents = json.loads(text)
    except json.JSONDecodeError as e:
        raise StagingFileError(
            f"Staging file '{path}' is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}"
        ) from e
    if not isinstance(documents, list):
        raise StagingFileError(
            f"Staging file '{path}' must contain a JSON array of book documents, got {type(documents).__name__}."
        )
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise StagingFileError(
                f"Entry #{index} in staging file '{path}' is a {type(document).__name__}, not a JSON object."
            )
    return documents


def write_staging(path: Path, documents: list[dict], *, overwrite: bool = True) -> None:
    """
    Atomically writes documents as a staging file: a sibling temp file is written, fsynced,
    then published. With overwrite=True, os.replace() installs it over the target; otherwise,
    a hard link publishes it only if the target does not already exist. A failure leaves any
    previous file byte-identical. Creates the parent directory if needed and keeps an existing
    file's permission bits when overwriting.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dumps_documents(documents) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite and existing_mode is not None:
            os.chmod(tmp_name, existing_mode)
        if overwrite:
            os.replace(tmp_name, path)
        else:
            os.link(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    if not overwrite:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Review metadata / hashing helpers
# --------------------------------------------------------------------------- #
def review_status(document: dict) -> str:
    """The document's `_staging.status`, defaulting to pending (invalid values are caught by validation)."""
    meta = document.get(STAGING_META_KEY)
    if isinstance(meta, dict) and meta.get("status") in REVIEW_STATUSES:
        return meta["status"]
    return STATUS_PENDING


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_document_hash(document: dict) -> str:
    """
    Recomputes a document's identity hash with the same inputs the scrapers use: the
    Playwright scraper hashes `publication_year`; the Leanpub API scraper hashes the year
    of `last_published_at`.
    """
    authors = document.get("authors")
    if isinstance(authors, str):
        authors = [authors]
    elif not isinstance(authors, list):
        authors = []
    if "publication_year" in document:
        year = document.get("publication_year")
    else:
        year = extract_year_from_date(document.get("last_published_at"))
    title = document.get("title")
    return hash_book(title if isinstance(title, str) else "", authors, year)


def hash_inputs_equal(a: dict, b: dict) -> bool:
    """True when two documents agree on every field that feeds the identity hash."""
    return all(a.get(name) == b.get(name) for name in HASH_SOURCE_FIELDS) and (
        ("publication_year" in a) == ("publication_year" in b)
    )


def strip_staging_meta(document: dict) -> dict:
    """A deep copy of the document without its `_staging` review metadata."""
    clean = copy.deepcopy(document)
    clean.pop(STAGING_META_KEY, None)
    return clean


# --------------------------------------------------------------------------- #
# Dedup index (step 1)
# --------------------------------------------------------------------------- #
class StagingIndex:
    """O(1) duplicate lookups over a loaded staging file - built once per run."""

    def __init__(self, documents: Iterable[dict]) -> None:
        self._hashes: set[str] = set()
        self._asins: set[str] = set()
        self._book_ids: set[str] = set()
        self._slugs: set[str] = set()
        for document in documents:
            self.add(document)

    def add(self, document: dict) -> None:
        for key, bucket in (
            ("hash", self._hashes),
            ("asin", self._asins),
            ("book_id", self._book_ids),
            ("slug", self._slugs),
        ):
            value = document.get(key)
            if value:
                bucket.add(str(value))

    def has_hash(self, book_hash: Optional[str]) -> bool:
        return bool(book_hash) and book_hash in self._hashes

    def has_asin(self, asin: Optional[str]) -> bool:
        return bool(asin) and asin in self._asins

    def has_leanpub(self, book_id: Optional[str], slug: Optional[str]) -> bool:
        return (bool(book_id) and str(book_id) in self._book_ids) or (bool(slug) and slug in self._slugs)


# --------------------------------------------------------------------------- #
# Append (step 1)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppendSummary:
    appended: int
    duplicates: int
    total_in_file: int
    pending_in_file: int


def append_to_staging(path: Path, new_documents: list[dict], *, source: str) -> AppendSummary:
    """
    Appends newly scraped documents to the staging file without touching existing ones.

    The file is re-loaded at write time (not reused from the start-of-run snapshot) so edits a
    reviewer saved while the scrape was running survive. New documents whose hash is already
    in the file - or earlier in this batch - are skipped as duplicates; the rest are stamped
    with `_staging = {status: pending, scraped_at, source}` and appended in order.

    Raises:
        StagingFileError: the file became unreadable/invalid since the run started.
    """
    path = Path(path)
    existing = load_staging(path, missing_ok=True)
    index = StagingIndex(existing)
    scraped_at = utc_now_iso()

    appended: list[dict] = []
    duplicates = 0
    for document in new_documents:
        book_hash = document.get("hash") or compute_document_hash(document)
        if index.has_hash(book_hash):
            duplicates += 1
            continue
        staged = dict(document)
        staged["hash"] = book_hash
        staged[STAGING_META_KEY] = {"status": STATUS_PENDING, "scraped_at": scraped_at, "source": source}
        appended.append(staged)
        index.add(staged)

    combined = existing + appended
    if appended:
        write_staging(path, combined)
    pending = sum(1 for d in combined if review_status(d) == STATUS_PENDING)
    module_logger.info(f"Staging append to {path}: {len(appended)} appended, {duplicates} duplicates.")
    return AppendSummary(
        appended=len(appended), duplicates=duplicates, total_in_file=len(combined), pending_in_file=pending
    )


def dump_unsaved(path: Path, documents: list[dict]) -> Path:
    """Last-resort dump of scraped results next to an unwritable/corrupted staging file."""
    path = Path(path)
    target = path.with_name(f"{path.stem}.unsaved-{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
    write_staging(target, documents)
    return target


# --------------------------------------------------------------------------- #
# Validation (step 2) - every rule in docs/dev/staging-file-format.md
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationProblem:
    index: int
    path: str
    message: str

    def __str__(self) -> str:
        return f"document #{self.index}, {self.path}: {self.message}"


def _check_key_names(value: Any, path: str, index: int, problems: list[ValidationProblem]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):  # pragma: no cover - json.loads only produces str keys
                problems.append(ValidationProblem(index, child_path, "key is not a string"))
                continue
            if key.startswith("$"):
                problems.append(ValidationProblem(index, child_path, "key must not start with '$'"))
            if "." in key:
                problems.append(ValidationProblem(index, child_path, "key must not contain '.'"))
            if "\x00" in key:
                problems.append(ValidationProblem(index, child_path, "key must not contain a NUL character"))
            _check_key_names(child, child_path, index, problems)
    elif isinstance(value, list):
        for position, item in enumerate(value):
            _check_key_names(item, f"{path}[{position}]", index, problems)


def _validate_document(index: int, document: Any) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    if not isinstance(document, dict):
        return [ValidationProblem(index, "<document>", f"must be a JSON object, got {type(document).__name__}")]

    # Structural rules - apply to every document, rejected or not.
    if RESERVED_ID_KEY in document:
        object_id = document[RESERVED_ID_KEY]
        if not isinstance(object_id, str) or not _OBJECT_ID_PATTERN.match(object_id):
            problems.append(
                ValidationProblem(index, RESERVED_ID_KEY, "must be a 24-character lowercase hex string (from pull)")
            )
    if STAGING_META_KEY in document:
        meta = document[STAGING_META_KEY]
        if not isinstance(meta, dict):
            problems.append(ValidationProblem(index, STAGING_META_KEY, "must be a JSON object"))
        elif "status" in meta and meta["status"] not in REVIEW_STATUSES:
            problems.append(
                ValidationProblem(index, f"{STAGING_META_KEY}.status", f"must be one of {', '.join(REVIEW_STATUSES)}")
            )
    for key in document:
        if isinstance(key, str) and key.startswith("_") and key not in RESERVED_KEYS:
            problems.append(ValidationProblem(index, key, "top-level keys starting with '_' are reserved"))
    book_fields = {k: v for k, v in document.items() if k != STAGING_META_KEY}
    _check_key_names(book_fields, "", index, problems)

    try:
        size = len(json.dumps(book_fields, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover - json-loaded data always serializes
        size = 0
    if size > MAX_DOCUMENT_BYTES:
        problems.append(ValidationProblem(index, "<document>", f"is {size} bytes; MongoDB's limit is 16 MB"))

    if review_status(document) == STATUS_REJECTED:
        return problems  # never sent, so only the structural rules above apply

    # Book-field rules.
    for name in ("title", "site"):
        value = document.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(ValidationProblem(index, name, "is required and must be a non-empty string"))
    authors = document.get("authors")
    if not isinstance(authors, list) or not all(isinstance(a, str) for a in authors):
        problems.append(ValidationProblem(index, "authors", "is required and must be a list of strings"))
    if "url" in document and (not isinstance(document["url"], str) or not document["url"].strip()):
        problems.append(ValidationProblem(index, "url", "must be a non-empty string when present"))
    if "hash" in document and not isinstance(document["hash"], str):
        problems.append(ValidationProblem(index, "hash", "must be a string when present"))
    return problems


def validate_staging_documents(documents: list[Any]) -> list[ValidationProblem]:
    """Every problem in the file, in document order - never just the first one."""
    problems: list[ValidationProblem] = []
    first_index_by_id: dict[str, int] = {}
    for index, document in enumerate(documents):
        problems.extend(_validate_document(index, document))
        # Whole-file rule: two documents with one `_id` would become two ReplaceOnes on the same
        # live document in an unordered bulk write, and whichever lands last silently discards the
        # other's edits. Checked regardless of review status - identity is structural.
        object_id = document.get(RESERVED_ID_KEY) if isinstance(document, dict) else None
        if isinstance(object_id, str):
            if object_id in first_index_by_id:
                problems.append(
                    ValidationProblem(
                        index,
                        RESERVED_ID_KEY,
                        f"duplicates the _id of document #{first_index_by_id[object_id]}; "
                        "each pulled document may appear only once",
                    )
                )
            else:
                first_index_by_id[object_id] = index
    return problems


# --------------------------------------------------------------------------- #
# Push preparation
# --------------------------------------------------------------------------- #
@dataclass
class PreparedDocument:
    """One staging document made ready for push: `_staging` stripped, hash recomputed."""

    index: int
    document: dict  # book fields only; `_id` (if any) still a hex string
    status: str
    object_id: Optional[str]
    previous_hash: Optional[str]
    hash: str
    hash_changed: bool
    title: str = field(default="")


def prepare_for_push(document: dict, index: int) -> PreparedDocument:
    """
    Deep-copies a validated document, strips `_staging`, and recomputes `hash` from the
    (possibly edited) title/authors/year. The planner may later keep a pulled document's
    stored hash when none of its hash inputs changed (see push_plan.build_push_plan).
    """
    clean = strip_staging_meta(document)
    previous_hash = clean.get("hash") if isinstance(clean.get("hash"), str) else None
    new_hash = compute_document_hash(clean)
    clean["hash"] = new_hash
    object_id = clean.get(RESERVED_ID_KEY)
    return PreparedDocument(
        index=index,
        document=clean,
        status=review_status(document),
        object_id=object_id if isinstance(object_id, str) else None,
        previous_hash=previous_hash,
        hash=new_hash,
        hash_changed=previous_hash is not None and previous_hash != new_hash,
        title=str(clean.get("title") or ""),
    )
