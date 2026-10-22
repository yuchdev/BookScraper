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
import logging
import os
import time
from collections import Counter

from ..book_utils import csv_safe_cell, print_log
from ..scraping.results import ScrapeStatus

module_logger = logging.getLogger(__name__)


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
