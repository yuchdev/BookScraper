from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from ..backends.mongo import cert_rotation, config
from ..book_utils import print_log


def _run_check(warn_days: int) -> None:
    """
    ``--check`` mode: report the currently active X.509 certificate's expiry and exit with a
    cron/CI-friendly status code.

    Resolves the active cert exactly as the app does (``config.resolve_mongo_connection()``'s
    cert). It performs a local file read only - it never contacts Atlas and never needs the
    ``ATLAS_*`` variables (ROT-16). Exit codes: ``0`` if more than ``warn_days`` remain (or no
    x509 cert is configured), ``3`` if within the window, ``1`` if expired or unreadable. No
    PEM/key bytes ever reach a log or the console (ROT-3).

    :param warn_days: Days-before-expiry threshold that triggers exit code 3.
    """
    try:
        _uri, cert_file = config.resolve_mongo_connection()
    except config.ConfigError as e:
        print_log(f"Certificate check failed: {e}", "error")
        sys.exit(1)

    if not cert_file:
        print_log("No X.509 client certificate is configured - nothing to check.", "info")
        sys.exit(0)

    cert_path = pathlib.Path(cert_file)
    try:
        expires = cert_rotation.read_certificate_expiry(cert_path)
    except cert_rotation.RotationError as e:
        print_log(f"Certificate check failed: {e}", "error")
        sys.exit(1)

    days_remaining = (expires - datetime.now(UTC)).days
    print_log(f"path: {cert_path}", "info")
    print_log(f"expires: {expires.date().isoformat()}", "info")
    print_log(f"days remaining: {days_remaining}", "info")

    if days_remaining < 0:
        print_log(f"Certificate expired {abs(days_remaining)} day(s) ago - rotate it now.", "error")
        sys.exit(1)
    if days_remaining <= warn_days:
        print_log(f"Certificate expires within {warn_days} day(s) - rotate it soon.", "warning")
        sys.exit(3)
    sys.exit(0)


async def run(args: argparse.Namespace) -> None:
    """The "rotate the Atlas X.509 client certificate" workflow. Exits non-zero on any failure.

    With ``--check`` it reports the active certificate's expiry instead of rotating (see
    :func:`_run_check`); otherwise it issues a new certificate and prints its expiry date.
    """
    # ATLAS_* credentials may live in .env rather than the real environment; --check reads no
    # ATLAS_* but still loads .env so settings.json "env"-sourced cert paths resolve.
    load_dotenv()

    if args.check:
        _run_check(args.warn_days)
        return

    output = pathlib.Path(args.output).expanduser() if args.output else None

    print_log("Requesting a new Atlas X.509 client certificate...", "info")
    try:
        cert_path, settings_notes = await asyncio.to_thread(cert_rotation.rotate_certificate, args.months, output)
    except cert_rotation.RotationError as e:
        print_log(f"Certificate rotation failed: {e}", "error")
        sys.exit(1)

    print_log(f"Certificate written to {cert_path}", "success")
    try:
        expires = cert_rotation.read_certificate_expiry(cert_path)
        print_log(f"New certificate expires on {expires.date().isoformat()}.", "info")
    except cert_rotation.RotationError as e:
        # The cert was written; only reading its expiry back failed. Report, don't fail.
        print_log(f"Could not read the new certificate's expiry: {e}", "warning")
    for note in settings_notes:
        print_log(note, "warning" if note.startswith("WARNING") else "info")
