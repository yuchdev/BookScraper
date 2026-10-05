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


def extract_year_from_date(date_string: str) -> Optional[int]:
    """
    Extracts the year from a date string.

    Args:
        date_string: The date string in any supported format (e.g., "2023-12-19", "December 17, 2019", "1995", "Apr 23, 2021", "September 2016", "Last updated on 2016-11-29").

    Returns:
        int: The extracted year, or None if the year cannot be extracted.
    """
    if date_string == "N/A" or date_string is None:
        return None

    if not isinstance(date_string, str):
        logger.warning(f"Invalid input type for date_string: {type(date_string)}. Expected string.")
        return None

    # 1. Try to extract YYYY-MM-DD using regex if it's embedded in a larger string
    # This handles "Last updated on YYYY-MM-DD" from leanpub.com or similar
    date_pattern_in_text = r"\d{4}-\d{2}-\d{2}"
    match = re.search(date_pattern_in_text, date_string)
    if match:
        extracted_date_part = match.group(0)
        try:
            date_object = datetime.strptime(extracted_date_part, "%Y-%m-%d")
            return date_object.year
        except ValueError:
            logger.debug(f"Regex extracted '{extracted_date_part}' but it failed to parse as %Y-%m-%d.")
            pass

    # 2. Attempt to parse the date string using different direct formats
    supported_formats = ["%Y-%m-%d", "%B %d, %Y", "%Y", "%b %d, %Y", "%B %Y"]

    try:
        for date_format in supported_formats:
            try:
                date_object = datetime.strptime(date_string, date_format)
                return date_object.year
            except ValueError:
                logger.debug(f"Date string did not match format {date_format!r}; trying the next one.")
                continue

        # If none of the formats worked
        logger.warning(
            f"Could not extract year from date string: '{date_string}' using known formats or regex pattern."
        )
        return None

    except Exception as e:  # noqa: X002 - defensive isolation boundary: strptime only raises ValueError (handled inline); so this catches an unexpected non-ValueError without propagating
        logger.error(f"Error in extract_year_from_date for '{date_string}': {e}", exc_info=True)
        return None


def hash_book(title: str = "", authors: Optional[list] = None, year: Optional[int] = None) -> str:
    """
    Calculates a SHA-256 hash for a book based on its title, authors, and year.

    Args:
            title: The title of the book (required).
            authors: The authors of the book, separated by commas (required).
            year: The publication year of the book (optional, default is 0).

    Returns:
            A hexadecimal string representing the SHA-256 hash of the combined fields.
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
