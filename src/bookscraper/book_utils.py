from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# --- Global logging setup ---
# Define basic ANSI color codes (customize as needed)
# Moved COLORS and RESET_COLOR_CODE outside print_log for efficiency
COLORS = {
    "info": "\x1b[0m",  # White for info
    "step": "\x1b[36m",  # Cyan for important process steps
    "success": "\x1b[32m",  # Green for info
    "warning": "\x1b[33m",  # Yellow for warning
    "error": "\x1b[31m",  # Red for error
    "debug": "\x1b[36m",  # Cyan for debug
    "critical": "\x1b[41m\x1b[37m",  # White text on Red background for critical
}
RESET_COLOR_CODE = "\x1b[0m"  # Reset all attributes

CONFIG_DIR = Path.home() / ".bookscrapper"
LOG_DIR = CONFIG_DIR / "logs"
MAX_LOG_FILES = 10
# One log file per run/process, named so a directory listing sorts chronologically; the pid
# suffix keeps two runs started within the same second from colliding.
LOG_FILE_PATH = LOG_DIR / f"bookscraper_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log"

# Set up a dedicated logger for bookscraper output. Its own level is left NOTSET (never call
# .setLevel() on it) so it always defers to whatever level configure_logging() sets on the
# root logger below.
logger = logging.getLogger("bookscraper_app")


def _prune_old_logs(max_files: int = MAX_LOG_FILES):
    """Delete the oldest run logs in LOG_DIR beyond the `max_files` most recently modified."""
    log_files = sorted(LOG_DIR.glob("bookscraper_*.log"), key=lambda p: p.stat().st_mtime)
    for old_file in log_files[:-max_files]:
        old_file.unlink(missing_ok=True)


def configure_logging(level: int = logging.INFO):
    """
    Write every log record in the process to this run's file under ~/.bookscrapper/logs/ at the
    given severity, and prune old run logs down to MAX_LOG_FILES.

    The handler is attached to the ROOT logger rather than `logger` above: other modules in
    this package create their own loggers via `logging.getLogger(__name__)` or ad hoc names
    (database.py, search_utils.py, deduplicate.py, scrape_details.py, commands/search.py) that
    aren't children of "bookscraper_app", so a handler placed there would miss them. Every
    logger propagates to the root by default, so attaching there is the only way one call
    reliably captures all application logging regardless of which logger name a module uses.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    resolved_path = os.path.abspath(str(LOG_FILE_PATH))
    if not any(
        isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved_path
        for handler in root_logger.handlers
    ):
        file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        root_logger.addHandler(file_handler)
        _prune_old_logs()


configure_logging()


# Regex to remove ANSI escape codes from a string (for clean file logs)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def print_log(text: str, status: str = "info"):
    """
    Logs the given text with color-coding for console output,
    and plain text for file logs.
    """
    # Get the appropriate logging method (info, error, warning, etc.)
    log_method = getattr(logger, status.lower(), logger.info)  # Use .lower() for status consistency

    # Create the colored message for console output
    colored_text = f"{COLORS.get(status, '')}{text}{RESET_COLOR_CODE}"

    # Log the UNCOLORED text to the logger.
    # The file_handler will get this clean text.
    # The console_handler *also* gets this, but we'll additionally print colored text directly.
    clean_text = ANSI_ESCAPE_PATTERN.sub("", text)  # Strip ANSI codes for the logger message
    log_method(clean_text)

    # Attempt to print the colored message directly to sys.stdout.
    # This bypasses the logging handler's internal write method and its encoding,
    # relying on sys.stdout (which is hopefully UTF-8 enabled) and terminal ANSI support.
    try:
        sys.stdout.write(colored_text + "\n")
        sys.stdout.flush()  # Ensure it's written immediately
    except (OSError, ValueError) as e:
        # Fallback if direct console print fails: a broken pipe / closed stream (OSError),
        # a write to an already-closed stdout (ValueError), or an encoding failure
        # (UnicodeEncodeError, a ValueError subclass). Log it uncolored via the logger.
        logger.error(
            f"Failed to print colored message to console directly: {e}. Original message: {clean_text}", exc_info=False
        )


def atomic_write_bytes(path: Path, data: bytes, mode: Optional[int] = None):
    """
    Write ``data`` to ``path`` atomically: a crash or interrupt mid-write can never
    leave ``path`` truncated. Writes to a temp file in the same directory, ``fsync``s
    it, then ``os.replace``s it over the target - the replace is atomic on POSIX, so a
    reader sees either the complete old file or the complete new one, never a partial.

    :param path: Destination file; its parent directory is created if missing.
    :param data: Bytes to write.
    :param mode: If given, ``chmod`` the file to this permission mask before the replace
        (e.g. ``0o600`` for a secret). If omitted, an existing ``path`` keeps its current
        permissions and a new one gets ``0o644`` - the temp file is born ``0o600``
        (``mkstemp``), so without this the result would silently become owner-only.
    """
    if mode is None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = Path(tmp.name)

    try:
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    except BaseException:
        # Never leave a stray temp file behind; the target is untouched either way.
        temp_path.unlink(missing_ok=True)
        raise


def check_local_write_permission(directory: str = ".") -> bool:
    """
    Checks if the application has write permissions in the specified directory.
    Attempts to create, write to, and delete a dummy file.
    Returns True if successful, False otherwise.
    """
    test_file_path = os.path.join(directory, "test_write_permission.tmp")
    try:
        with open(test_file_path, "w") as f:
            f.write("test")
        os.remove(test_file_path)
        print_log("Successfully created write permission file.", "success")
        logger.info(f"Successfully verified write permission in '{directory}'.")
        return True
    except OSError as e:
        logger.error(f"Write permission test failed in '{directory}': {e}")
        print_log(
            f"Permission Error: Cannot write to the current directory ('{directory}'). Please ensure you have write access.",
            "error",
        )
        return False
    except Exception as e:  # noqa: X002 - defensive isolation boundary: the probe must never propagate; a unit test raises RuntimeError to prove it
        logger.error(f"An unexpected error occurred during local write permission test: {e}", exc_info=True)
        print_log("An unexpected error occurred during local write permission test. Check log for details.", "error")
        return False


# Leading characters a spreadsheet treats as the start of a formula (CWE-1236).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe_cell(value: object) -> str:
    """
    Return *value* as a CSV cell that a spreadsheet will not evaluate as a formula.

    Diagnostic CSVs (``failed_books.csv``, ``failed_urls.csv``, ``other_links.csv``) can carry
    page-derived text, so any cell starting with ``=``, ``+``, ``-``, ``@``, a tab or a carriage
    return is prefixed with a single quote. ``None`` becomes an empty cell.

    :param value: The cell value to write.
    :returns: The neutralized cell text.
    """
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


# English month names/abbreviations -> month number. Parsing is driven by this explicit
# table rather than strptime's %B/%b, which resolve against the process locale and would
# silently stop recognising English month names under e.g. a de_DE locale. Trailing periods
# ("Dec.", "Sep.") are stripped by the caller before lookup, and both "sep" and "sept" map
# to September.
_ENGLISH_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

# "December 17, 2019" / "Apr 23, 2021" / "Dec. 17 2019" - month name, day, 4-digit year.
_MONTH_DAY_YEAR_RE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})")
# "17 December 2019" / "17 Dec. 2019" - day, month name, 4-digit year (Packt/O'Reilly style).
_DAY_MONTH_YEAR_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})")
# "September 2016" - month name, 4-digit year (day-level precision unknown).
_MONTH_YEAR_RE = re.compile(r"([A-Za-z]+)\.?\s+(\d{4})")


def _build_full_date(year: int, month: int, day: int) -> Optional[tuple[str, int]]:
    """
    Validate a (year, month, day) triple and render it as ``(YYYY-MM-DD, year)``.

    :param year: Four-digit year.
    :param month: Month number 1-12.
    :param day: Day of month.
    :returns: ``(iso_date, year)`` for a real calendar date, or ``None`` if the triple
        is not a valid date (e.g. February 30), so the caller can fall through.
    """
    try:
        datetime(year, month, day)
    except ValueError:
        logger.debug(f"Rejected invalid calendar date {year:04d}-{month:02d}-{day:02d}; falling through.")
        return None
    return (f"{year:04d}-{month:02d}-{day:02d}", year)


def _parse_english_month_date(text: str) -> Optional[tuple[str, int]]:
    """
    Parse an English month-name date into ``(iso_date, year)`` with preserved precision.

    Recognises "Month DD, YYYY", "DD Month YYYY", and "Month YYYY" (the last keeping only
    month precision, i.e. ``YYYY-MM``). Month lookup is locale-independent via
    ``_ENGLISH_MONTHS``.

    :param text: The already-stripped date string.
    :returns: ``(iso_date, year)`` on a recognised month-name date, else ``None``.
    """
    match = _MONTH_DAY_YEAR_RE.fullmatch(text)
    if match:
        month = _ENGLISH_MONTHS.get(match.group(1).lower())
        if month is not None:
            return _build_full_date(int(match.group(3)), month, int(match.group(2)))

    match = _DAY_MONTH_YEAR_RE.fullmatch(text)
    if match:
        month = _ENGLISH_MONTHS.get(match.group(2).lower())
        if month is not None:
            return _build_full_date(int(match.group(3)), month, int(match.group(1)))

    match = _MONTH_YEAR_RE.fullmatch(text)
    if match:
        month = _ENGLISH_MONTHS.get(match.group(1).lower())
        if month is not None:
            year = int(match.group(2))
            return (f"{year:04d}-{month:02d}", year)

    return None


def normalize_date(raw: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """
    Normalize a raw site date string into ``(iso_date_with_preserved_precision, year)``.

    The ISO form preserves precision: a full date becomes ``YYYY-MM-DD``, a month-only date
    becomes ``YYYY-MM``, and a bare year stays ``YYYY``. An embedded ISO date (e.g.
    "Last updated on 2016-11-29" or "2023-12-19T08:00:00Z") is recognised anywhere in the
    text. Month-name parsing is locale-independent (explicit English month table, never
    ``strptime``'s ``%B``/``%b``). Unparseable, ``"N/A"``, empty, ``None`` or non-string
    input yields ``(None, None)``.

    This is the single source of truth behind :func:`extract_year_from_date`, so the year
    it returns must stay identical to that function's historical output (it feeds
    :func:`hash_book`, the book identity key).

    :param raw: The raw date string from a site, or ``None``.
    :returns: A ``(iso_date, year)`` tuple; either element is ``None`` when it cannot be
        determined (and both are ``None`` when nothing parses).
    """
    if raw is None or raw == "N/A":
        return (None, None)

    if not isinstance(raw, str):
        logger.warning(f"Invalid input type for date_string: {type(raw)}. Expected string.")
        return (None, None)

    text = raw.strip()
    if not text:
        return (None, None)

    try:
        # 1. An ISO YYYY-MM-DD anywhere in the text ("Last updated on 2016-11-29",
        #    "2023-12-19T08:00:00Z"). This is the Leanpub/ISO path and must win first so a
        #    trailing time component or surrounding words never defeat it.
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if match:
            iso_part = match.group(0)
            try:
                date_object = datetime.strptime(iso_part, "%Y-%m-%d")
                return (f"{date_object.year:04d}-{date_object.month:02d}-{date_object.day:02d}", date_object.year)
            except ValueError:
                logger.debug(f"Regex extracted '{iso_part}' but it failed to parse as %Y-%m-%d.")

        # 2. English month-name formats (locale-independent).
        month_name_result = _parse_english_month_date(text)
        if month_name_result is not None:
            return month_name_result

        # 3. A bare four-digit year. Parsed via strptime so the behaviour (and the outer
        #    defensive handler below) stays identical to the historical implementation.
        if re.fullmatch(r"\d{4}", text):
            date_object = datetime.strptime(text, "%Y")
            return (f"{date_object.year:04d}", date_object.year)

        logger.warning(f"Could not extract date from string: '{text}' using known formats or regex pattern.")
        return (None, None)

    except Exception as e:  # noqa: X002 - defensive isolation boundary: strptime only raises ValueError (handled inline); so this catches an unexpected non-ValueError without propagating
        logger.error(f"Error in normalize_date for '{raw}': {e}", exc_info=True)
        return (None, None)


def extract_year_from_date(date_string: str) -> Optional[int]:
    """
    Extract the publication year from a raw site date string.

    Thin backward-compatible wrapper over :func:`normalize_date` - returns only its year
    element. Kept because the year feeds :func:`hash_book` (the book identity key), so its
    output must remain byte-for-byte what callers and stored hashes already depend on.

    :param date_string: A date string in any supported format (e.g. "2023-12-19",
        "December 17, 2019", "1995", "Apr 23, 2021", "September 2016",
        "Last updated on 2016-11-29", "17 Dec. 2019").
    :returns: The extracted year, or ``None`` if no year can be determined.
    """
    return normalize_date(date_string)[1]


def hash_book(title: str = "", authors: Optional[list] = None, year: Optional[int] = None) -> str:
    """
    Calculates a SHA-256 hash for a book based on its title, authors, and year.

    :param title: The title of the book; hashed lower-cased and stripped (an empty title is
        allowed but warned about).
    :param authors: The author names; normalized (stripped, lower-cased) and sorted before
        hashing so order and case never change the digest. ``None`` is treated as no authors.
    :param year: The publication year, or ``None`` when unknown (contributes an empty segment).
    :returns: The lowercase-hexadecimal SHA-256 digest of the combined fields, or ``""`` when a
        non-string title makes hashing impossible.
    """

    # Normalize and sort authors for consistent hashing
    authors = [] if authors is None else sorted([str(a).strip().lower() for a in authors if a is not None])

    try:
        if not title:
            logger.warning("Attempted to hash a book with no title. Hash will be based on empty title.")
            # raise ValueError("Title is required.") # Could raise an error, but warning might be better to hash incomplete entries

        year_str = str(year) if year is not None else ""

        # Join authors for consistent hashing
        authors_str = "|".join(authors)

        # Normalize hash input and proceed
        combined_fields = f"{title.strip().lower()}|{authors_str}|{year_str}"
        hash_object = hashlib.sha256()
        hash_object.update(combined_fields.encode("utf-8"))
        return hash_object.hexdigest()

    except AttributeError as e:
        # A non-string title (e.g. None) makes `title.strip()` raise AttributeError;
        # return the "" sentinel rather than propagating, so an incomplete record never
        # crashes a batch.
        logger.error(f"Error hashing book (title: '{title}', authors: {authors}, year: {year}): {e}")
        return ""
