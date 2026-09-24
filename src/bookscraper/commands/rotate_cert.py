from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from dotenv import load_dotenv

from ..backends.mongo import cert_rotation
from ..book_utils import print_log


async def run(args: argparse.Namespace) -> None:
    """The "rotate the Atlas X.509 client certificate" workflow. Exits non-zero on any failure."""
    # ATLAS_* credentials may live in .env rather than the real environment.
    load_dotenv()

    output = pathlib.Path(args.output).expanduser() if args.output else None

    print_log("Requesting a new Atlas X.509 client certificate...", "info")
    try:
        cert_path, settings_notes = await asyncio.to_thread(cert_rotation.rotate_certificate, args.months, output)
    except cert_rotation.RotationError as e:
        print_log(f"Certificate rotation failed: {e}", "error")
        sys.exit(1)

    print_log(f"Certificate written to {cert_path}", "success")
    for note in settings_notes:
        print_log(note, "warning" if note.startswith("WARNING") else "info")
