"""Mock-tier tests for the log-redaction integration in bookscraper.book_utils (task 07.0/01).

These exercise the handler-level wiring - RedactingFilter on the root file handler plus a
RedactingFormatter overriding format() - that makes redaction fire on every record, including
ones propagated from another module's logger and ones carrying a secret only in a %-arg or in an
exception traceback (exc_info=True). The pure redact() rules are unit-tested in
tests/unit/test_book_utils.py; here the external surface mocked is the logging framework's file
handler, pointed at a tmp_path file rather than ~/.bookscrapper/logs/.

Sample secrets are assembled by concatenation so the source never carries a contiguous
real-looking credential that would trip .claude/hooks/secret_scan.py.
"""

import asyncio
import logging
import os
import sys

import pytest

import bookscraper.book_utils as book_utils

_FAKE_MONGO_URI = "mongodb+srv://" + "bob" + ":" + "hunter2pw" + "@host0.example.net/db"


@pytest.fixture
def restore_logging():
    """Snapshot and restore the logger levels/handlers configure_logging() mutates.

    Yields nothing; on teardown it resets the root level, removes any handler added during the
    test, and restores the ``bookscraper`` plus clamped third-party logger levels so logging
    state never leaks between tests.
    """
    root = logging.getLogger()
    tracked = ("bookscraper", *book_utils._THIRD_PARTY_LOGGERS)
    before_handlers = list(root.handlers)
    before_root_level = root.level
    before_levels = {name: logging.getLogger(name).level for name in tracked}
    try:
        yield
    finally:
        for handler in [h for h in root.handlers if h not in before_handlers]:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(before_root_level)
        for name, level in before_levels.items():
            logging.getLogger(name).setLevel(level)


def test_app_logger_follows_log_severity(tmp_path, monkeypatch, restore_logging):
    """
    [Local] configure_logging: the bookscraper logger takes --log-severity, root stays WARNING.

    Scenario:
        Given configure_logging(DEBUG)
        When the logger hierarchy is inspected
        Then logging.getLogger("bookscraper") is at DEBUG while the root logger stays at WARNING.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path; restore_logging undoes level changes
        - Only the application logger follows the requested severity, not the root

    On failure, first check:
        - configure_logging in book_utils.py sets root WARNING + bookscraper <level>
    """
    monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", tmp_path / "logs" / "run.log")
    monkeypatch.delenv("BOOKSCRAPER_LIB_LOG_LEVEL", raising=False)

    book_utils.configure_logging(logging.DEBUG)

    assert logging.getLogger("bookscraper").level == logging.DEBUG
    assert logging.getLogger().level == logging.WARNING


def test_library_loggers_default_to_warning(tmp_path, monkeypatch, restore_logging):
    """
    [Local] configure_logging: third-party loggers are pinned to WARNING at a debug severity.

    Scenario:
        Given no BOOKSCRAPER_LIB_LOG_LEVEL and configure_logging(DEBUG)
        When pymongo/httpx/httpcore/anthropic/asyncio levels are read
        Then each is WARNING, so a debug run carries no library-internal debug lines.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path; env var removed for the test
        - Covers the success criterion: no httpcore/pymongo debug under --log-severity debug

    On failure, first check:
        - The _THIRD_PARTY_LOGGERS loop in configure_logging in book_utils.py
    """
    monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", tmp_path / "logs" / "run.log")
    monkeypatch.delenv("BOOKSCRAPER_LIB_LOG_LEVEL", raising=False)

    book_utils.configure_logging(logging.DEBUG)

    for name in book_utils._THIRD_PARTY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_lib_log_level_env_override(tmp_path, monkeypatch, restore_logging, caplog):
    """
    [Local] configure_logging: BOOKSCRAPER_LIB_LOG_LEVEL overrides, an invalid value warns.

    Scenario:
        Given BOOKSCRAPER_LIB_LOG_LEVEL=DEBUG and configure_logging(INFO)
        When the third-party loggers are read, then the var is set to a bogus value and re-run
        Then a valid value lifts them to DEBUG; a bogus value is ignored (WARNING) with a warning.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path; restore_logging undoes level changes
        - Covers both the override path and the invalid-value fallback branch

    On failure, first check:
        - _resolve_library_log_level in book_utils.py (getattr level lookup + warning)
    """
    monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", tmp_path / "logs" / "run.log")

    monkeypatch.setenv("BOOKSCRAPER_LIB_LOG_LEVEL", "debug")
    book_utils.configure_logging(logging.INFO)
    assert logging.getLogger("pymongo").level == logging.DEBUG

    monkeypatch.setenv("BOOKSCRAPER_LIB_LOG_LEVEL", "NOPE")
    with caplog.at_level(logging.WARNING, logger="bookscraper.book_utils"):
        book_utils.configure_logging(logging.INFO)

    assert logging.getLogger("pymongo").level == logging.WARNING
    assert any("BOOKSCRAPER_LIB_LOG_LEVEL" in record.getMessage() for record in caplog.records)


def test_configure_logging_still_idempotent(tmp_path, monkeypatch, restore_logging):
    """
    [Local] configure_logging: calling it twice adds exactly one file handler.

    Scenario:
        Given LOG_FILE_PATH pointed at tmp_path
        When configure_logging is called twice
        Then only one FileHandler for that path is attached to the root logger.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path; restore_logging removes the handler
        - Guards the task 07.0/01 idempotency contract across the 07.0/02 level rework

    On failure, first check:
        - The handler-dedup guard (baseFilename check) in configure_logging in book_utils.py
    """
    log_file = tmp_path / "logs" / "run.log"
    monkeypatch.setattr(book_utils, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", log_file)

    book_utils.configure_logging(logging.INFO)
    book_utils.configure_logging(logging.DEBUG)

    resolved = os.path.abspath(str(log_file))
    matching = [
        h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler) and h.baseFilename == resolved
    ]
    assert len(matching) == 1


def test_log_record_with_args_is_redacted_after_formatting(tmp_path):
    """
    [Local] RedactingFilter: a secret passed as a %-arg is masked after formatting.

    Scenario:
        Given a file handler carrying RedactingFilter and a record logged as ("uri=%s", uri)
        When the handler processes the record
        Then the written line masks the userinfo and no credential substring remains.

    Boundaries:
        - Logging framework only; file handler pointed at tmp_path
        - Proves redaction runs on getMessage() output, not the raw msg template

    On failure, first check:
        - RedactingFilter.filter in book_utils.py (record.getMessage / args reset)
    """
    log_file = tmp_path / "run.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(book_utils.RedactingFormatter("%(message)s"))
    handler.addFilter(book_utils.RedactingFilter())
    record = logging.LogRecord("x", logging.ERROR, "f", 1, "uri=%s", (_FAKE_MONGO_URI,), None)

    handler.emit(record)
    handler.close()

    contents = log_file.read_text(encoding="utf-8")
    assert "bob:hunter2pw@" not in contents
    assert "uri=mongodb+srv://<redacted>@host0.example.net/db" in contents


def test_secret_only_in_a_percent_arg_is_redacted(tmp_path):
    """
    [Local] RedactingFilter: a secret living ONLY in a %-arg (template clean) is still masked.

    Scenario:
        Given a record whose msg template has no secret and whose single %-arg is the secret
        When RedactingFilter.filter runs on the record
        Then record.args is cleared and the final message carries no credential.

    Boundaries:
        - Operates directly on a LogRecord; no file needed
        - Proves redacting the msg template alone would be insufficient

    On failure, first check:
        - That filter() reads record.getMessage() (post-substitution), not record.msg
    """
    record = logging.LogRecord("x", logging.INFO, "f", 1, "connecting to %s", (_FAKE_MONGO_URI,), None)

    assert book_utils.RedactingFilter().filter(record) is True
    assert record.args == ()
    assert "bob:hunter2pw@" not in record.getMessage()
    assert record.getMessage() == "connecting to mongodb+srv://<redacted>@host0.example.net/db"


def test_exception_traceback_is_redacted_in_log_file(tmp_path):
    """
    [Local] RedactingFormatter: a credential inside exception text is masked in the formatted record.

    Scenario:
        Given an exception whose message embeds a mongodb+srv URI, logged with exc_info=True
        When the RedactingFormatter-backed handler writes it
        Then the log file contains no u:p@ userinfo (success criterion of the subtask).

    Boundaries:
        - Logging framework only; emulates a pymongo-style error embedding a URI
        - Proves format() redacts the appended traceback, not only the message

    On failure, first check:
        - RedactingFormatter.format in book_utils.py (redacts super().format output)
    """
    log_file = tmp_path / "run.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(book_utils.RedactingFormatter("%(message)s"))
    handler.addFilter(book_utils.RedactingFilter())

    try:
        raise ValueError(f"configuration error: {_FAKE_MONGO_URI} is unreachable")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, "f", 1, "mongo connect failed", (), None)
        record.exc_info = sys.exc_info()
        handler.emit(record)
    handler.close()

    contents = log_file.read_text(encoding="utf-8")
    assert "bob:hunter2pw@" not in contents
    assert "<redacted>@host0.example.net" in contents


def test_leak_via_other_module_logger_is_redacted_by_root_handler(tmp_path, monkeypatch):
    """
    [Local] configure_logging: a secret logged by a sibling module's logger is masked at the root handler.

    Scenario:
        Given configure_logging() attaches the file handler to the ROOT logger
        When a differently-named logger (as other modules use) logs a URI via a %-arg
        Then the root file handler's filter/formatter mask it - proving the filter lives on the
        handler, not on the bookscraper module logger.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path so the real log dir is untouched
        - Removes the handler and restores the root level afterward

    On failure, first check:
        - configure_logging in book_utils.py attaches RedactingFilter + RedactingFormatter to root
    """
    log_dir = tmp_path / "logs"
    log_file = log_dir / "bookscraper_test.log"
    monkeypatch.setattr(book_utils, "LOG_DIR", log_dir)
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", log_file)

    root = logging.getLogger()
    before = list(root.handlers)
    previous_level = root.level
    book_utils.configure_logging(logging.DEBUG)
    added = [h for h in root.handlers if h not in before]

    try:
        assert added, "configure_logging should attach a file handler to the root logger"
        handler = added[0]
        assert any(isinstance(f, book_utils.RedactingFilter) for f in handler.filters)
        assert isinstance(handler.formatter, book_utils.RedactingFormatter)

        other = logging.getLogger("bookscraper.backends.mongo.database")
        other.error("connection failed: %s", _FAKE_MONGO_URI)
        handler.flush()

        contents = log_file.read_text(encoding="utf-8")
        assert "bob:hunter2pw@" not in contents
        assert "<redacted>@host0.example.net" in contents
    finally:
        for handler in added:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


def test_print_log_redacts_console_and_file(tmp_path, monkeypatch, capsys):
    """
    [Local] print_log: both the direct console write and the logged copy are redacted.

    Scenario:
        Given print_log() is called with text embedding a mongodb+srv URI
        When it writes the colored copy to stdout and logs the clean copy
        Then neither the captured stdout nor the run log file contains the userinfo.

    Boundaries:
        - Monkeypatches LOG_DIR/LOG_FILE_PATH to tmp_path; captures stdout via capsys
        - Covers the sys.stdout path that bypasses the handler filter

    On failure, first check:
        - The redact(text) call at the top of print_log in book_utils.py
    """
    log_dir = tmp_path / "logs"
    log_file = log_dir / "bookscraper_test.log"
    monkeypatch.setattr(book_utils, "LOG_DIR", log_dir)
    monkeypatch.setattr(book_utils, "LOG_FILE_PATH", log_file)

    root = logging.getLogger()
    before = list(root.handlers)
    previous_level = root.level
    book_utils.configure_logging(logging.DEBUG)
    added = [h for h in root.handlers if h not in before]

    try:
        book_utils.print_log(f"saving from {_FAKE_MONGO_URI}", "info")
        for handler in added:
            handler.flush()

        captured = capsys.readouterr()
        assert "bob:hunter2pw@" not in captured.out
        assert "<redacted>@host0.example.net" in captured.out

        contents = log_file.read_text(encoding="utf-8")
        assert "bob:hunter2pw@" not in contents
        assert "<redacted>@host0.example.net" in contents
    finally:
        for handler in added:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


# --- Per-task scrape log context (task 07.0/04) ---


def _ctx_handler(log_file, fmt="%(levelname)s [%(ctx)s] %(name)s: %(message)s") -> logging.FileHandler:
    """Build a FileHandler wired exactly like the production one: ContextFilter + RedactingFilter.

    :param log_file: The tmp_path file the handler writes to.
    :param fmt: The log format string (defaults to the production ``[%(ctx)s]`` layout).
    :returns: The ready-to-use :class:`logging.FileHandler`.
    """
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(book_utils.RedactingFormatter(fmt))
    handler.addFilter(book_utils.ContextFilter())
    handler.addFilter(book_utils.RedactingFilter())
    return handler


async def test_context_var_isolated_between_concurrent_tasks():
    """
    [Local] scrape_log_context: two concurrent tasks never see each other's context value.

    Scenario:
        Given two coroutines gathered concurrently, each inside its own scrape_log_context
        When each sets its context, yields the event loop (so the sibling runs), then re-reads it
        Then each still reads only its own ``<site>:<short-id>``, and the default is restored after.

    Boundaries:
        - Pure asyncio; proves ContextVar-per-task isolation underpinning the 10-way concurrent run
        - The await between set and re-read forces the sibling to interleave

    On failure, first check:
        - scrape_log_context uses a ContextVar (not a module global) in book_utils.py
    """

    async def _scrape(site, url) -> tuple[str, str]:
        """Enter a scrape context, yield, and return the value seen before and after the yield."""
        with book_utils.scrape_log_context(site, url):
            before = book_utils.scrape_context.get()
            await asyncio.sleep(0.01)
            return before, book_utils.scrape_context.get()

    amazon, leanpub = await asyncio.gather(
        _scrape("amazon", "https://www.amazon.com/dp/1098131029"),
        _scrape("leanpub", "https://leanpub.com/api/v1/cache/books/pyfun.json"),
    )

    assert amazon == ("amazon:1098131029", "amazon:1098131029")
    assert leanpub == ("leanpub:pyfun", "leanpub:pyfun")
    assert book_utils.scrape_context.get() == "-"


def test_file_log_lines_carry_scrape_context(tmp_path):
    """
    [Local] ContextFilter: file log lines inside a scrape carry its ``[<site>:<short-id>]``.

    Scenario:
        Given a handler carrying ContextFilter + the production ``[%(ctx)s]`` formatter
        When a record is logged inside scrape_log_context and another outside it
        Then the inside line reads ``[amazon:1098131029]`` and the outside line reads ``[-]``.

    Boundaries:
        - Logging framework only; file handler pointed at tmp_path
        - Proves the ctx stamp is per-record and resets when the context exits

    On failure, first check:
        - ContextFilter.filter / scrape_log_context token reset in book_utils.py
    """
    log_file = tmp_path / "run.log"
    handler = _ctx_handler(log_file)
    logger = logging.getLogger("bookscraper.test.ctx_lines")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        with book_utils.scrape_log_context("amazon", "https://www.amazon.com/dp/1098131029"):
            logger.info("navigating")
        logger.info("outside")
    finally:
        logger.removeHandler(handler)
        handler.close()

    contents = log_file.read_text(encoding="utf-8")
    assert "[amazon:1098131029] bookscraper.test.ctx_lines: navigating" in contents
    assert "[-] bookscraper.test.ctx_lines: outside" in contents


def test_console_output_unchanged_by_context(capsys):
    """
    [Local] print_log: console output is NOT annotated with the scrape context (file-only).

    Scenario:
        Given a scrape_log_context is active
        When print_log writes its colored line to stdout
        Then the captured stdout carries the message but neither the ctx label nor its brackets.

    Boundaries:
        - Captures stdout via capsys; the ctx is a file-log-only concern
        - Guards the requirement that the console stays readable

    On failure, first check:
        - print_log in book_utils.py must not include scrape_context in its stdout write
    """
    with book_utils.scrape_log_context("amazon", "https://www.amazon.com/dp/1098131029"):
        book_utils.print_log("navigating to product page", "info")

    out = capsys.readouterr().out
    assert "navigating to product page" in out
    assert "amazon:1098131029" not in out
    assert "[amazon" not in out


def test_scrape_context_short_id_is_sanitised(tmp_path):
    """
    [Local] scrape_log_context: an untrusted short id is control-stripped, redacted, length-bounded.

    Scenario:
        Given a URL whose last path segment embeds newlines, an ANSI escape, a line separator, a
        secret-shaped token, and 200 trailing characters
        When scrape_log_context builds the label and a line is logged under it
        Then the label carries no control char / newline / line separator, no raw secret, its id is
        bounded to _CTX_MAX_LEN, and the written log line stays a single physical line.

    Boundaries:
        - Covers the "short-ids come from untrusted page/URL data" hardening requirement
        - A log-injection newline in the id must never split or forge a log line

    On failure, first check:
        - _sanitise_ctx_segment in book_utils.py (control regex, redact(), length cap)
    """
    nasty = "sk-ant-" + "ABCDEFGHIJKL" + "\n\r\x1b[31m" + chr(0x2028) + "z" * 200
    log_file = tmp_path / "run.log"
    handler = _ctx_handler(log_file, fmt="[%(ctx)s] %(message)s")
    logger = logging.getLogger("bookscraper.test.ctx_sanitise")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        with book_utils.scrape_log_context("leanpub", "https://leanpub.com/" + nasty):
            label = book_utils.scrape_context.get()
            logger.info("line")
    finally:
        logger.removeHandler(handler)
        handler.close()

    for forbidden in ("\n", "\r", "\x1b", chr(0x2028), chr(0x2029)):
        assert forbidden not in label
    assert "sk-ant-ABCDEFGHIJKL" not in label
    site_part, _, id_part = label.partition(":")
    assert site_part == "leanpub"
    assert 0 < len(id_part) <= book_utils._CTX_MAX_LEN

    contents = log_file.read_text(encoding="utf-8")
    assert len(contents.splitlines()) == 1
