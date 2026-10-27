from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlsplit

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

# This module's logger: ``bookscraper.book_utils``, a child of the ``bookscraper`` logger whose
# level configure_logging() sets from --log-severity. Named via __name__ like every other module
# (task 07.0/02) so the whole package shares one configurable logger hierarchy rather than a flat
# set of ad hoc names.
logger = logging.getLogger(__name__)

# Third-party loggers clamped to WARNING by configure_logging() so a `--log-severity debug` run
# doesn't drown in library internals (pymongo topology events, httpx/httpcore wire logs, the
# anthropic SDK, asyncio). BOOKSCRAPER_LIB_LOG_LEVEL (a level name, e.g. DEBUG) raises or lowers
# them in one place, e.g. when diagnosing a TLS handshake.
_THIRD_PARTY_LOGGERS: tuple[str, ...] = ("pymongo", "httpx", "httpcore", "anthropic", "asyncio")
_LIB_LOG_LEVEL_ENV = "BOOKSCRAPER_LIB_LOG_LEVEL"
_DEFAULT_LIB_LOG_LEVEL = logging.WARNING


# --- Per-task scrape log context (task 07.0/04) ---
# Each detail scrape sets this ContextVar to "<site>:<short-id>" (an ASIN, a Leanpub slug, or a URL
# path segment) for the duration of its call; the ContextFilter below stamps its current value onto
# every log record as the file formatter's ``[%(ctx)s]`` field. asyncio tasks inherit a COPY of the
# context at creation, so the 10-way-concurrent scrape batch keeps each book's log lines separable
# (``grep "amazon:1098131029" <run log>`` isolates one book's full history). A record emitted
# outside any scrape carries SCRAPE_CONTEXT_DEFAULT.
SCRAPE_CONTEXT_DEFAULT = "-"
scrape_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bookscraper_scrape_context", default=SCRAPE_CONTEXT_DEFAULT
)


# --- Secret redaction (task 07.0/01) ---
# Defence-in-depth: every string that reaches a log file or the console is run through redact()
# so a credential that slipped past an upstream control (e.g. a third-party exception embedding a
# connection URI) never lands in ~/.bookscrapper/logs/ or a CI transcript. Patterns are compiled
# once. Ordering is load-bearing in two places: the multiline PRIVATE KEY block runs FIRST so the
# single-line rules can never fragment a PEM body, and the Anthropic ``sk-ant-`` rule precedes the
# generic OpenAI ``sk-`` rule (both redact to the same shape, so beyond that the order is cosmetic
# because each replacement injects only a ``<redacted...>`` marker). Every pattern is linear (prefix
# anchor or non-overlapping negated classes with a single quantifier; the one ``[\s\S]*?`` is bound
# by a literal-or-``\Z`` terminator), so the set is ReDoS-free. See the threat model
# docs/security/2026-10-26-log-redaction.md for the derivation of each rule.
_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Private-key PEM block. The terminator is optional (``|\Z``) so a truncated block - a cut-off
    # line or a size-limited record - is still redacted to end-of-text instead of surviving verbatim.
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
        "<redacted private key>",
    ),
    # Any ``scheme://user:pass@`` userinfo (subsumes the mongodb+srv URI and the Postgres DSN type).
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
    # Authorization header, any auth scheme (Bearer/Basic/Token/Digest). Basic carries the Atlas
    # Service Account client_id:client_secret pair, so Bearer-only redaction would leak it.
    (
        re.compile(r"(?i)(authorization:\s*)(?:bearer|basic|token|digest)\s+[A-Za-z0-9._\-=/+~]+"),
        r"\1<redacted>",
    ),
    # Keyword-assigned secret, quoted or bare, ``=`` or ``:`` (dict/JSON reprs, query strings).
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|client_secret|access_token|api[_-]?key)"
            r"(['\"]?\s*[=:]\s*)['\"]?[^\s\"',}]{6,}"
        ),
        r"\1\2<redacted>",
    ),
    # Vendor-prefix API tokens (prefix-anchored, zero-ReDoS, reused verbatim from the secret-scan
    # catalog so the two cannot drift). sk-ant- is ordered before the generic OpenAI sk- rule.
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), "sk-ant-<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted aws key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "<redacted github token>"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "<redacted google key>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "<redacted slack token>"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"), "sk-<redacted>"),
)


def redact(text: str) -> str:
    """
    Return *text* with every known secret shape replaced by a ``<redacted...>`` marker.

    This is the single choke point every log/console sink runs through. It is idempotent -
    the injected markers contain no secret-shaped substring, so re-running ``redact`` on an
    already-redacted string is a no-op (the handler filter re-redacts the console copy
    :func:`print_log` already redacted, and must not corrupt it).

    :param text: The raw string that may embed a credential.
    :returns: The same string with MongoDB/Postgres userinfo, Authorization headers, PEM
        private-key blocks, keyword-assigned secrets, and vendor API tokens masked. A falsy
        input is returned unchanged.
    """
    if not text:
        return text
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """
    Logging filter that masks secrets in a record's fully ``%``-formatted message.

    Attached to the root **file handler** (not a logger) by :func:`configure_logging`, so it
    runs for every record propagated up from any module's ``logging.getLogger(__name__)``.
    It redacts :meth:`logging.LogRecord.getMessage` output - the message *after* ``%``-arg
    substitution - then collapses ``msg``/``args`` so a secret living only in a ``%``-arg
    (e.g. ``logger.error("conn failed: %s", uri)``) cannot survive by escaping the template.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Redact *record* in place and always allow it through.

        :param record: The log record to sanitise.
        :returns: ``True`` always - this filter sanitises, it never drops a record.
        """
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class ContextFilter(logging.Filter):
    """Logging filter that stamps the current per-task scrape context onto every record.

    Attached to the root **file handler** (like :class:`RedactingFilter`, never to a logger) by
    :func:`configure_logging`, so a record from any logger - the ``bookscraper.*`` hierarchy or a
    third-party library - gets a ``ctx`` attribute by the time the :class:`RedactingFormatter`'s
    ``[%(ctx)s]`` field is rendered. The value is read from :data:`scrape_context`, which each
    detail scrape sets to ``<site>:<short-id>`` for the duration of its call; a record emitted
    outside any scrape carries :data:`SCRAPE_CONTEXT_DEFAULT`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Stamp :data:`scrape_context`'s current value onto ``record.ctx`` and allow it through.

        :param record: The log record to annotate.
        :returns: ``True`` always - this filter annotates, it never drops a record.
        """
        record.ctx = scrape_context.get()
        return True


class RedactingFormatter(logging.Formatter):
    """
    Formatter that redacts the **whole** formatted record, message plus appended traceback.

    Overriding :meth:`format` (rather than only ``formatException``) is deliberate:
    :class:`logging.Formatter` caches a formatted traceback in ``record.exc_text`` after the
    first handler formats it, so redacting the final returned string is the only placement
    immune to another handler having populated that cache first. This is what strips a
    credential embedded in third-party exception text (pymongo/httpx) under ``exc_info=True``.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format *record* via the base implementation, then redact the complete result.

        A record that reaches formatting without having passed a :class:`ContextFilter` (e.g. a
        handler not carrying one) still formats: a missing ``ctx`` defaults to
        :data:`SCRAPE_CONTEXT_DEFAULT` so the ``[%(ctx)s]`` field never raises.

        :param record: The log record to format.
        :returns: The formatted line (message + any traceback) with secrets masked.
        """
        if not hasattr(record, "ctx"):
            record.ctx = SCRAPE_CONTEXT_DEFAULT
        return redact(super().format(record))


# --- Per-task scrape log context helpers (task 07.0/04) ---
# A short-id segment comes from UNTRUSTED page/URL data (an ASIN, a Leanpub slug, or a URL path
# segment), so before it can reach a log line it is stripped of control characters / newlines /
# line-separators, run through redact(), and length-bounded.
# Control chars (C0/C1/DEL) plus the Unicode line/paragraph separators U+2028/U+2029; the latter are
# built with chr() so the source file carries no literal separator byte (one would shift tooling that
# counts lines with str.splitlines()).
_CTX_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f" + chr(0x2028) + chr(0x2029) + "]")
_CTX_MAX_LEN = 60
# Amazon product URLs carry the stable ASIN in a /dp/<ASIN> or /gp/product/<ASIN> segment; this is
# the same pattern scrape_book uses, so the context id matches the ASIN it later records.
_AMAZON_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def _sanitise_ctx_segment(value: str) -> str:
    """Return *value* as a log-safe context segment: control-stripped, redacted, length-bounded.

    :param value: A raw site key or URL-derived short id (untrusted).
    :returns: The cleaned segment, or :data:`SCRAPE_CONTEXT_DEFAULT` when nothing usable remains.
    """
    cleaned = redact(_CTX_CONTROL_RE.sub("", value or "")).strip()
    if len(cleaned) > _CTX_MAX_LEN:
        cleaned = cleaned[:_CTX_MAX_LEN]
    return cleaned or SCRAPE_CONTEXT_DEFAULT


def short_id_from_url(url: str) -> str:
    """Return a short identifier for a scrape target: its last non-empty URL path segment.

    A trailing ``.json`` (the Leanpub JSON:API book endpoint) is stripped so the id reads as the
    bare slug. Query string and fragment are ignored.

    :param url: The target URL (absolute or relative).
    :returns: The last path segment (``.json`` stripped), or :data:`SCRAPE_CONTEXT_DEFAULT` when the
        path carries no segment.
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if not segments:
        return SCRAPE_CONTEXT_DEFAULT
    segment = segments[-1]
    if segment.endswith(".json"):
        segment = segment[: -len(".json")]
    return segment or SCRAPE_CONTEXT_DEFAULT


def scrape_short_id(site: str, url: str) -> str:
    """Derive a scrape target's short id: the ASIN for amazon, else the last URL path segment.

    :param site: The site key (e.g. ``"amazon"`` / ``"leanpub"``).
    :param url: The target URL the scrape fetches.
    :returns: The derived short id (not yet sanitised; :func:`scrape_log_context` sanitises it).
    """
    if site == "amazon":
        match = _AMAZON_ASIN_RE.search(url)
        if match:
            return match.group(1)
    return short_id_from_url(url)


@contextmanager
def scrape_log_context(site: str, url: str) -> Iterator[None]:
    """Set :data:`scrape_context` to ``<site>:<short-id>`` for the duration of a single scrape.

    Both segments are sanitised (control-stripped, redacted, length-bounded) because the short id
    is derived from untrusted page/URL data. The ContextVar token is reset in a ``finally`` so the
    value never leaks past this call (a later sequential scrape on the same task starts clean).

    :param site: The site key the scrape targets.
    :param url: The URL the scrape fetches, used to derive the short id.
    :returns: A context manager that yields nothing and restores the previous context on exit.
    """
    label = f"{_sanitise_ctx_segment(site)}:{_sanitise_ctx_segment(scrape_short_id(site, url))}"
    token = scrape_context.set(label)
    try:
        yield
    finally:
        scrape_context.reset(token)


def _prune_old_logs(max_files: int = MAX_LOG_FILES):
    """Delete the oldest run logs in LOG_DIR beyond the `max_files` most recently modified.

    Each run writes two sibling files sharing a stem - ``<stem>.log`` and ``<stem>.report.json``
    (the machine-readable run report, task 07.0 subtask 03) - so a pruned log takes its report with
    it; otherwise orphan reports would accumulate unbounded. Pruning is keyed on the ``.log`` files'
    modification times (the report may not exist for every log, e.g. a ``rotate-cert`` run).
    """
    log_files = sorted(LOG_DIR.glob("bookscraper_*.log"), key=lambda p: p.stat().st_mtime)
    for old_file in log_files[:-max_files]:
        old_file.unlink(missing_ok=True)
        old_file.with_name(f"{old_file.stem}.report.json").unlink(missing_ok=True)


def _resolve_library_log_level() -> int:
    """
    Resolve the level applied to third-party loggers from ``BOOKSCRAPER_LIB_LOG_LEVEL``.

    :returns: The numeric level named by the env var (case-insensitive level name, e.g.
        ``DEBUG``), or :data:`logging.WARNING` when the var is unset/empty or names an
        unrecognised level. An invalid value is ignored with a one-line warning rather than
        raising, so a typo never aborts start-up.
    """
    raw = os.environ.get(_LIB_LOG_LEVEL_ENV)
    if not raw:
        return _DEFAULT_LIB_LOG_LEVEL
    candidate = getattr(logging, raw.strip().upper(), None)
    if isinstance(candidate, int):
        return candidate
    logger.warning(
        "Ignoring invalid %s=%r; expected a level name (DEBUG/INFO/WARNING/ERROR/CRITICAL). "
        "Defaulting third-party loggers to WARNING.",
        _LIB_LOG_LEVEL_ENV,
        raw,
    )
    return _DEFAULT_LIB_LOG_LEVEL


def configure_logging(level: int = logging.INFO):
    """
    Write every log record in the process to this run's file under ~/.bookscrapper/logs/, set the
    per-logger levels of the logger hierarchy, and prune old run logs down to MAX_LOG_FILES.

    The redacting file handler is attached to the ROOT logger (never removed; it carries the
    task 07.0/01 RedactingFilter + RedactingFormatter and the task 07.0/04 ContextFilter), so a
    record from any logger - the ``bookscraper.*`` hierarchy or a third-party library - reaches it
    by propagation and is both redacted and stamped with the current per-task scrape context
    (``[%(ctx)s]``) once on the way out. Levels are set per-logger rather than on the root so
    library noise never follows ``--log-severity``:

    - the root logger stays at WARNING, so an unclamped third-party logger defaults quiet;
    - ``logging.getLogger("bookscraper")`` is set to ``level`` - the app's own messages (every
      module uses ``logging.getLogger(__name__)``, i.e. a ``bookscraper.*`` child) follow
      ``--log-severity``;
    - the named third-party loggers (pymongo/httpx/httpcore/anthropic/asyncio) are pinned to
      WARNING unless ``BOOKSCRAPER_LIB_LOG_LEVEL`` overrides them.

    Idempotent: callable more than once (book_utils imports call it once at INFO; main.py calls
    it again with the chosen level). The file handler is added - and old logs pruned - only on
    the first call; the per-logger levels are re-applied on every call.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    logging.getLogger("bookscraper").setLevel(level)

    library_level = _resolve_library_log_level()
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(library_level)

    resolved_path = os.path.abspath(str(LOG_FILE_PATH))
    if not any(
        isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved_path
        for handler in root_logger.handlers
    ):
        file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
        file_handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s [%(ctx)s] %(name)s: %(message)s"))
        # Both filters live on the HANDLER (not a logger) so records propagated from every logger
        # get both the redaction pass and the per-task scrape-context stamp before formatting.
        file_handler.addFilter(ContextFilter())
        file_handler.addFilter(RedactingFilter())
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
    # Redact at entry so BOTH sinks are covered: the direct sys.stdout write below bypasses the
    # logging handler (and thus its RedactingFilter), so masking here is the only control on the
    # console copy. The logged copy is redacted again by the handler filter; redact() is idempotent.
    text = redact(text)

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
