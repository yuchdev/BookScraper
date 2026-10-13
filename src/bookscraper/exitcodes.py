"""Process exit-code contract shared by ``main`` and every ``commands/*`` workflow (task 05.0/05).

A single, named set of exit codes so the CLI's result is unambiguous to a shell, cron job, or CI
step. Each ``commands/*.run()`` **returns** one of these codes rather than calling ``sys.exit``
mid-function; :func:`bookscraper.main.main` propagates the command's return value and
:func:`bookscraper.main.main_sync` is the single place that turns it into a real process exit.

The meanings are deliberately stable - this module changes *how* a code is produced, never what a
code means:

- :data:`OK` - the run completed and nothing failed.
- :data:`FATAL` - the run could not start or was aborted (config error, pre-flight failure, an
  unhandled exception, or a backend that is unavailable).
- :data:`USAGE` - an argparse usage error. This is argparse's own convention (it calls
  ``sys.exit(2)`` itself); it is documented here, not re-implemented.
- :data:`PARTIAL` - the run completed but at least one item failed (a scrape ending ``FAILED`` /
  ``TIMEOUT`` / ``PARSE_ERROR`` / ``BLOCKED``, or a ``SaveSummary`` with ``errors > 0``); also
  ``rotate-cert --check`` reporting a certificate that is expiring soon. A ``DUPLICATE`` or a
  filtered-out item is **not** a failure - the run did what it should.
- :data:`DRIFT` - ``detect-schema`` found at least one current selector that matched no sample
  page (task 04.0 subtask 08).
- :data:`INTERRUPTED` - the run was cancelled with Ctrl+C.
"""

from __future__ import annotations

from typing import Final

#: Completed; nothing failed.
OK: Final = 0

#: Could not run or was aborted: config error, pre-flight failure, unavailable backend, or an
#: unhandled exception.
FATAL: Final = 1

#: argparse usage error - argparse's own convention (``sys.exit(2)``), documented not re-implemented.
USAGE: Final = 2

#: Completed, but at least one item failed (see module docstring); also ``rotate-cert --check``
#: "expiring soon".
PARTIAL: Final = 3

#: ``detect-schema``: at least one current selector matched no sample page.
DRIFT: Final = 4

#: The run was interrupted with Ctrl+C.
INTERRUPTED: Final = 130
