# Logging reference

**See also:** `src/bookscraper/book_utils.py` (`configure_logging`, `print_log`, `LOG_FILE_PATH`).

---

## Log file location

Every run writes to its own file:

```
~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log
```

`LOG_FILE_PATH` is computed once when `book_utils.py` is imported and stays stable for the
lifetime of the process. After the file is created, `_prune_old_logs()` deletes the oldest files
matching `bookscraper_*.log` beyond `MAX_LOG_FILES` (10), keyed on `st_mtime`, so the directory
never exceeds 10 run logs.

---

## Logger hierarchy

Every module names its logger `logging.getLogger(__name__)` (task 07.0/02), forming one
`bookscraper.<subpackage>.<module>` hierarchy under a shared `bookscraper` parent.
`tests/unit/test_logger_names.py` is an AST guard that fails if a literal logger name comes back.

`configure_logging()` attaches a single **redacting file handler** to the **root** logger. Every
logger propagates there by default, so one handler captures all application *and* library records
and redacts each once on the way out.

---

## RedactingFilter and RedactingFormatter (task 07.0/01)

The file handler carries both:

- **`RedactingFilter`** — masks credentials, PEM private keys, and tokens in the `%`-formatted
  log message and in any `exc_info` traceback text. It is never removed.
- **`RedactingFormatter`** — applies the same redaction and formats the line as
  `%(asctime)s %(levelname)s [%(ctx)s] %(name)s: %(message)s`.

---

## Per-task scrape context (task 07.0/04)

`[%(ctx)s]` in the file format is injected by a **`ContextFilter`** attached to the file
**handler** (like `RedactingFilter`, so records from every logger get a `ctx`). The filter reads
`book_utils.scrape_context`, a `contextvars.ContextVar[str]` (default `"-"`).

`scrape_book` and `get_leanpub_book_details` set it to `<site>:<short-id>` (the Amazon ASIN, the
Leanpub slug, else the last URL path segment) for the duration of the call via the
`scrape_log_context` context manager. The ContextVar token is reset in `try/finally`, so a value
never leaks across calls; asyncio tasks inherit a copy of the context, so a 10-way-concurrent
batch stays separable:

```bash
grep "amazon:1098131029" ~/.bookscrapper/logs/bookscraper_<run>.log
```

The short-id is derived from untrusted page/URL data — it is control-/newline-stripped, run
through `redact()`, and length-bounded before it reaches a line. The `RedactingFormatter` also
defaults a missing `ctx` to `"-"` defensively.

`print_log()`'s console output is **not** annotated with context (file-only, keeping the console
readable).

---

## Level configuration

Levels are set **per-logger, not on the root**, so library noise never follows `--log-severity`:

- Root logger: `WARNING`
- `bookscraper` logger: the requested level (`--log-severity {debug|info|warning|error}`,
  default `info`)
- Noisy third-party loggers (`pymongo`, `httpx`, `httpcore`, `anthropic`, `asyncio`): pinned to
  `WARNING`

`BOOKSCRAPER_LIB_LOG_LEVEL` (a level name, e.g. `DEBUG`, case-insensitive) overrides the
third-party clamp in one place — useful when diagnosing a TLS handshake. An unset/empty value
keeps `WARNING`; an unrecognized value is ignored with a one-line warning.

`main.py` calls `configure_logging()` once at startup. `book_utils.py` also calls it once at
import time (default `info`) so the logger works for any code that imports it without going
through the CLI. The second call is safe: it only adds the `FileHandler` (and prunes old logs)
the first time, keyed on `LOG_FILE_PATH` already being attached, while re-applying per-logger
levels on every call.

---

## Run report (task 07.0/03)

Alongside the log file, every `scrape-urls` / `search` / `migrate` run writes a
machine-readable JSON run report at:

```
~/.bookscrapper/logs/bookscraper_<ts>_<pid>.report.json
```

`_prune_old_logs()` treats `<stem>.log` and `<stem>.report.json` as one unit — a log evicted
past `MAX_LOG_FILES` takes its report with it.

`--report PATH` (on the shared scrape-runtime parent and declared individually on `migrate`)
writes an extra copy to `PATH`; both are written via `book_utils.atomic_write_bytes` with `0600`
perms.

The writer is `commands/_reporting.py`'s `RunReport`. The command records per-site
`ScrapeStatus` counts, the `SaveSummary`, resolved flags, and the `AIFallbackRuntime`; `main()`
creates it, finalizes it with the exit code in a `finally`, and writes it — so an interrupted run
still produces a report carrying `exit_code: 130`.

**Security contract:** `argv` runs through `book_utils.redact()` (a secret passed on the command
line never lands in the report), and it carries **no** connection URI, document body, or page
HTML — only counts, flags, versions, and the backend name. The optional `ai_fallback` block
(requests / filled / dropped / cap_hit / token-usage totals — no prices, no key) appears only
when `--ai-fallback` ran. Report-writing failures warn and never change the exit code.

Full shape in [`docs/cli/cli.md`](../cli/cli.md#run-report).
