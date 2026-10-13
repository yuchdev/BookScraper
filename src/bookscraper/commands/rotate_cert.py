from __future__ import annotations

import argparse
import asyncio
import pathlib
from datetime import UTC, datetime

from dotenv import load_dotenv

from .. import exitcodes
from ..backends.mongo import cert_rotation, config
from ..book_utils import print_log


def _run_check(warn_days: int) -> int:
    """
    ``--check`` mode: report the currently active X.509 certificate's expiry and return a
    cron/CI-friendly status code.

    Resolves the active cert exactly as the app does (``config.resolve_mongo_connection()``'s
    cert). It performs a local file read only - it never contacts Atlas and never needs the
    ``ATLAS_*`` variables (ROT-16). No PEM/key bytes ever reach a log or the console (ROT-3).

    :param warn_days: Days-before-expiry threshold that triggers the "expiring soon" code.
    :returns: :data:`~bookscraper.exitcodes.OK` if more than ``warn_days`` remain (or no x509 cert
        is configured), :data:`~bookscraper.exitcodes.PARTIAL` if within the window, or
        :data:`~bookscraper.exitcodes.FATAL` if the cert is expired or unreadable. The numeric
        values (0 / 3 / 1) match the established ``rotate-cert --check`` contract exactly.
    """
    try:
        _uri, cert_file = config.resolve_mongo_connection()
    except config.ConfigError as e:
        print_log(f"Certificate check failed: {e}", "error")
        return exitcodes.FATAL

    if not cert_file:
        print_log("No X.509 client certificate is configured - nothing to check.", "info")
        return exitcodes.OK

    cert_path = pathlib.Path(cert_file)
    try:
        expires = cert_rotation.read_certificate_expiry(cert_path)
    except cert_rotation.RotationError as e:
        print_log(f"Certificate check failed: {e}", "error")
        return exitcodes.FATAL

    days_remaining = (expires - datetime.now(UTC)).days
    print_log(f"path: {cert_path}", "info")
    print_log(f"expires: {expires.date().isoformat()}", "info")
    print_log(f"days remaining: {days_remaining}", "info")

    if days_remaining < 0:
        print_log(f"Certificate expired {abs(days_remaining)} day(s) ago - rotate it now.", "error")
        return exitcodes.FATAL
    if days_remaining <= warn_days:
        print_log(f"Certificate expires within {warn_days} day(s) - rotate it soon.", "warning")
        return exitcodes.PARTIAL
    return exitcodes.OK


async def run(args: argparse.Namespace) -> int:
    """The "rotate the Atlas X.509 client certificate" workflow. Returns a non-zero code on any failure.

    With ``--check`` it reports the active certificate's expiry instead of rotating (see
    :func:`_run_check`); otherwise it issues a new certificate, verifies it authenticates to
    Atlas (unless ``--no-verify``), and prints its expiry date. If verification fails,
    ``settings.json`` is left untouched (the previous certificate keeps working) and the command
    returns :data:`~bookscraper.exitcodes.FATAL`. On a successful rotation the newest ``--keep``
    certificates are retained and older ones pruned, unless ``--no-prune`` is given (see
    :func:`cert_rotation.prune_old_certificates`).

    :param args: Parsed ``rotate-cert`` arguments.
    :returns: An exit code from :mod:`bookscraper.exitcodes` (see :func:`_run_check` for ``--check``;
        :data:`~bookscraper.exitcodes.FATAL` on a rotation or verification failure, otherwise
        :data:`~bookscraper.exitcodes.OK`).
    """
    # ATLAS_* credentials may live in .env rather than the real environment; --check reads no
    # ATLAS_* but still loads .env so settings.json "env"-sourced cert paths resolve.
    load_dotenv()

    if args.check:
        return _run_check(args.warn_days)

    output = pathlib.Path(args.output).expanduser() if args.output else None

    print_log("Requesting a new Atlas X.509 client certificate...", "info")
    try:
        cert_path, settings_notes, verified = await asyncio.to_thread(
            cert_rotation.rotate_certificate,
            args.months,
            output,
            verify=not args.no_verify,
            verify_attempts=args.verify_attempts,
            keep=args.keep,
            prune=not args.no_prune,
        )
    except cert_rotation.RotationError as e:
        print_log(f"Certificate rotation failed: {e}", "error")
        return exitcodes.FATAL

    print_log(f"Certificate written to {cert_path}", "success")
    try:
        expires = cert_rotation.read_certificate_expiry(cert_path)
        print_log(f"New certificate expires on {expires.date().isoformat()}.", "info")
    except cert_rotation.RotationError as e:
        # The cert was written; only reading its expiry back failed. Report, don't fail.
        print_log(f"Could not read the new certificate's expiry: {e}", "warning")
    for note in settings_notes:
        print_log(note, "warning" if note.startswith("WARNING") else "info")

    if not verified:
        # Verification failed and settings.json was deliberately left untouched: return non-zero
        # so callers/CI notice, while the previously configured certificate keeps working.
        return exitcodes.FATAL
    return exitcodes.OK
