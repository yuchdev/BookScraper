"""Shared run-summary and diagnostics helpers for the scrape commands (task 03.3 subtask 06).

:mod:`bookscraper.commands.search` and :mod:`bookscraper.commands.scrape_urls` close out a run the
same way: tally each :class:`~bookscraper.scraping.results.ScrapeStatus`, write a best-effort
failed-records CSV, and report elapsed wall-clock time. Each concern lives here exactly once so the
two commands can never drift again - before this subtask they carried two subtly different
``save_failed_urls_to_csv`` copies (one tuple-based, one dict-based), which this module reconciles
into a single :func:`write_failed_csv` taking dict rows plus explicit ``fieldnames``.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Optional

import playwright

from .. import book_utils
from ..book_utils import atomic_write_bytes, csv_safe_cell, print_log, redact
from ..scraping.results import ScrapeStatus

module_logger = logging.getLogger(__name__)

#: Schema version of the machine-readable run report (task 07.0 subtask 03). Bumped only on a
#: breaking shape change, so a consumer can gate on it; the current shape is documented in the
#: Logging section of CLAUDE.md and docs/cli/cli.md.
RUN_REPORT_SCHEMA = 1


def _utc_now_iso() -> str:
    """Return the current UTC time as an ``...Z`` ISO-8601 string (second precision)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _package_version(name: str) -> Optional[str]:
    """Return an installed distribution's version, or ``None`` when its metadata is unavailable."""
    try:
        return version(name)
    except PackageNotFoundError as exc:
        module_logger.debug("Distribution %r version unavailable: %s", name, type(exc).__name__)
        return None


def _chromium_version() -> Optional[str]:
    """Return the Playwright-bundled Chromium version best-effort, or ``None`` if unavailable.

    Reads the version straight out of Playwright's committed ``browsers.json`` rather than
    launching a browser, so it is cheap and offline. Any failure (Playwright absent, the file
    moved, malformed JSON) yields ``None`` - a version probe must never raise or abort a run.

    :returns: The Chromium version string, or ``None`` when it cannot be determined.
    """
    try:
        browsers_file = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        data = json.loads(browsers_file.read_text(encoding="utf-8"))
        for entry in data.get("browsers", []):
            if entry.get("name") == "chromium":
                return entry.get("browserVersion") or entry.get("revision")
    except Exception as exc:  # noqa: X002 - best-effort probe: a version lookup must never abort a run
        module_logger.debug("Chromium version unavailable: %s", type(exc).__name__)
    return None


def _collect_versions() -> dict[str, Optional[str]]:
    """Collect the toolchain versions recorded in a run report (``chromium`` is best-effort)."""
    return {
        "bookscraper": _package_version("bookscraper"),
        "python": platform.python_version(),
        "playwright": _package_version("playwright"),
        "chromium": _chromium_version(),
    }


def _default_report_path() -> Path:
    """Return the run report's default path: this run's log file with a ``.report.json`` suffix.

    The report shares the log file's stem so :func:`bookscraper.book_utils._prune_old_logs` prunes
    the two as one unit. ``book_utils.LOG_FILE_PATH`` is read through the module (not imported by
    value) so a test can monkeypatch it.

    :returns: The ``~/.bookscrapper/logs/bookscraper_<ts>_<pid>.report.json`` path for this run.
    """
    log_path = book_utils.LOG_FILE_PATH
    return log_path.with_name(f"{log_path.stem}.report.json")


class RunReport:
    """Accumulates a machine-readable JSON summary of one ``scrape-urls`` / ``search`` / ``migrate``
    run (task 07.0 subtask 03).

    A command records into the report as it runs (per-site status counts, the save summary, the AI
    fallback runtime, the resolved flags); :func:`bookscraper.main.main` creates it, finalizes it
    with the process exit code in a ``finally``, and writes it - so even an interrupted run leaves a
    report (``exit_code`` 130). The report is written to the default log-sibling path and, when
    ``--report PATH`` is given, an additional copy there.

    It never carries a credential: ``argv`` is run through :func:`bookscraper.book_utils.redact`,
    and the report holds only counts, flags, versions, and the resolved backend name - never a URI,
    a document body, or page HTML.

    :ivar command: The subcommand name (``"search"`` etc.).
    :ivar argv: The redacted process argv (``sys.argv[1:]``).
    :ivar store_backend: The resolved ``--store-backend`` name, or ``None`` until recorded.
    :ivar flags: The run's boolean knobs (dry-run, robots, filters, ai-fallback) as recorded.
    :ivar save: The ``{inserted, duplicates, errors}`` save summary, or ``None`` when nothing saved.
    """

    def __init__(self, command: str, argv: list[str]):
        """Start an empty report, capturing the start time and redacting ``argv``.

        :param command: The subcommand name.
        :param argv: The process argv to record (each token is run through :func:`redact`).
        """
        self.command = command
        self.argv = [redact(token) for token in argv]
        self.store_backend: Optional[str] = None
        self.flags: dict[str, Any] = {}
        self.save: Optional[dict[str, int]] = None
        self._per_site: dict[str, Counter] = {}
        self._ai_fallback: Optional[Any] = None
        self.started_at = _utc_now_iso()
        self._start_monotonic = time.monotonic()
        self.finished_at: Optional[str] = None
        self.duration_s: Optional[float] = None
        self.exit_code: Optional[int] = None

    def set_context(self, *, store_backend: Optional[str], flags: dict[str, Any]):
        """Record the resolved storage backend and the run's boolean flags.

        :param store_backend: The resolved ``--store-backend`` name (or ``None``).
        :param flags: The run's flag values recorded verbatim (booleans only).
        """
        self.store_backend = store_backend
        self.flags = dict(flags)

    def record_status(self, site: str, status: Any):
        """Tally one scrape outcome for ``site`` under its :class:`ScrapeStatus` name.

        :param site: The site key the result belongs to (e.g. ``"amazon"``).
        :param status: A :class:`ScrapeStatus` (or its string value) to count.
        """
        name = status.value if isinstance(status, ScrapeStatus) else str(status)
        self._per_site.setdefault(site, Counter())[name] += 1

    def set_save(self, summary: Optional[Any]):
        """Record the ``save_books`` summary (``None`` leaves ``save`` unset).

        :param summary: A ``SaveSummary`` (read via its ``inserted``/``duplicates``/``errors``
            attributes), or ``None`` when no save happened (dry run / nothing to save).
        """
        if summary is None:
            return
        self.save = {
            "inserted": summary.inserted,
            "duplicates": summary.duplicates,
            "errors": summary.errors,
        }

    def attach_ai_fallback(self, runtime: Optional[Any]):
        """Attach the per-run AI-fallback runtime so its final counts land in the report.

        The runtime is snapshotted lazily in :meth:`to_dict`, not here, so counts accrued right up
        to an interrupt are captured. ``None`` (fallback disabled) leaves the block absent.

        :param runtime: The ``AIFallbackRuntime``, or ``None`` when ``--ai-fallback`` was off.
        """
        self._ai_fallback = runtime

    def _ai_fallback_block(self) -> Optional[dict[str, Any]]:
        """Snapshot the attached AI-fallback runtime's counts (no prices, no key), or ``None``."""
        runtime = self._ai_fallback
        if runtime is None:
            return None
        return {
            "requests": runtime.cap.used,
            "filled": runtime.filled,
            "dropped": runtime.dropped,
            "cap_hit": runtime.cap.hit,
            "tokens": runtime.usage.totals(),
        }

    def finalize(self, exit_code: int):
        """Stamp the finish time, elapsed duration, and process exit code.

        :param exit_code: The process exit code the run resolved to (130 on interrupt).
        """
        self.exit_code = exit_code
        self.finished_at = _utc_now_iso()
        self.duration_s = round(time.monotonic() - self._start_monotonic, 1)

    def to_dict(self) -> dict[str, Any]:
        """Render the report as the schema-1 JSON-serializable dict.

        :returns: The report document (see the Logging section of CLAUDE.md for the shape).
        """
        return {
            "schema": RUN_REPORT_SCHEMA,
            "command": self.command,
            "argv": self.argv,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "versions": _collect_versions(),
            "store_backend": self.store_backend,
            "flags": self.flags,
            "per_site": {site: dict(counts) for site, counts in self._per_site.items()},
            "save": self.save,
            "ai_fallback": self._ai_fallback_block(),
            "exit_code": self.exit_code,
        }

    def write(self, extra_path: Optional[str] = None):
        """Write the report atomically (``0600``) to the default path and, optionally, ``--report``.

        Best-effort: an I/O failure on either target emits a :func:`print_log` warning and is
        swallowed, so writing the report never changes the run's exit code (task 07.0 subtask 03).

        :param extra_path: The ``--report PATH`` destination to also write to, or ``None``.
        """
        payload = json.dumps(self.to_dict(), indent=2, default=str).encode("utf-8")
        targets = [_default_report_path()]
        if extra_path:
            targets.append(Path(extra_path))
        for target in targets:
            try:
                # 0o600: argv is redacted but the report is kept alongside the run log, so match the
                # owner-only permissions the log and migration reports already use.
                atomic_write_bytes(target, payload, mode=0o600)
                module_logger.info("Run report written to %s", target)
            except OSError as exc:
                print_log(f"Could not write run report to {target}: {exc}", "warning")


def elapsed_since(start_time: float) -> float:
    """Return the wall-clock seconds elapsed since ``start_time``.

    :param start_time: A :func:`time.time` value captured at the start of the run.
    :returns: The elapsed seconds, as a float.
    """
    return time.time() - start_time


def format_status_breakdown(status_counts: Counter) -> str:
    """Render a per-status tally as ``NAME=count`` pairs in :class:`ScrapeStatus` enum order.

    Only statuses that actually occurred appear, so a clean run shows no ``DUPLICATE=0`` noise.

    :param status_counts: The per-:class:`ScrapeStatus` tally accumulated over the run.
    :returns: A comma-separated breakdown string (empty when nothing was counted).
    """
    return ", ".join(f"{status.value}={status_counts[status]}" for status in ScrapeStatus if status_counts[status])


def write_failed_csv(
    rows: list[dict], *, fieldnames: list[str], filename: str = "failed_books.csv", directory: str = "."
):
    """Write failure/duplicate diagnostic records to a CSV, best-effort.

    The single writer both scrape commands use: ``scrape-urls`` passes ``url``/``status``/``error``
    rows into ``failed_books.csv`` and ``search`` passes ``url``/``site``/``title``/``error`` rows
    into ``failed_urls.csv``, each with its own ``fieldnames``. Every cell goes through
    :func:`~bookscraper.book_utils.csv_safe_cell` so page-derived text can't be evaluated as a
    spreadsheet formula. A missing key is written as an empty string (``restval=""``); a per-row
    serialization error skips just that row and the loop continues; an open- or serialization-level
    failure for the whole file is swallowed, because a diagnostic CSV must never crash the run.

    :param rows: The failure records, each a ``dict`` keyed by a subset of ``fieldnames``.
    :param fieldnames: The CSV column order (the superset of keys ``rows`` may carry).
    :param filename: The fixed name of the CSV file to write (unaffected by ``--output-dir``).
    :param directory: The directory to write ``filename`` into (``--output-dir``; default ``.``).
    """
    if not rows:
        module_logger.info(f"No failed/duplicate records to save to {filename}.")
        return

    target = os.path.join(directory, filename)
    try:
        module_logger.info(f"Saving failed/duplicate records to CSV: {target}")
        print_log(f"Saving {len(rows)} failed/duplicate record(s) to {target}...", "info")

        with open(target, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, restval="")
            writer.writeheader()
            for item in rows:
                try:
                    writer.writerow({key: csv_safe_cell(value) for key, value in item.items()})
                except ValueError as ve:
                    module_logger.error(f"Error writing failed record {item.get('url', 'Unknown URL')} to CSV: {ve}")
                except csv.Error as e:
                    module_logger.error(
                        f"Unexpected error writing failed record {item.get('url', 'Unknown URL')} to CSV: {e}",
                        exc_info=True,
                    )
        print_log(f"Failed/duplicate records successfully saved to {target}.", "success")
        module_logger.info(f"Failed/duplicate records successfully saved to {target}.")
    except IOError as e:
        module_logger.error(f"I/O error saving failed records to CSV {target}: {e}")
        print_log(f"Error saving failed records to {target}: {e}", "error")
    except csv.Error as e:
        module_logger.critical(
            f"An unexpected error occurred while saving failed records to CSV {target}: {e}",
            exc_info=True,
        )
        print_log(f"Critical Error saving failed records to {target}. Check log file.", "error")
