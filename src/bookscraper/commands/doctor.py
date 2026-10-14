"""The ``bookscraper doctor`` workflow: one-screen health report of the local setup (task 05.0/07).

Diagnosing a broken setup used to mean reading CLAUDE.md and running pieces by hand. ``doctor``
collapses that into a single aligned table: the Python/package/Playwright toolchain, whether ``.env``
and ``settings.json`` are present (and in which auth mode), the resolved X.509 client certificate and
its expiry, the local JSON store and log directory, and the presence of the optional
``ANTHROPIC_API_KEY`` / ``ATLAS_*`` credentials. With ``--online`` it also pings MongoDB Atlas.

Every check returns a :class:`CheckResult` with a status of :data:`OK` / :data:`WARN` / :data:`FAIL`
/ :data:`SKIP`. The process exits :data:`~bookscraper.exitcodes.FATAL` if any check FAILed, otherwise
:data:`~bookscraper.exitcodes.OK`; a ``WARN`` never fails the run, so ``doctor`` stays usable as a
pre-flight sanity check.

Security contract (reviewed per the subtask's "Security Auditor (output review)" role): no check ever
puts a secret value, a full connection URI, or a host into a :class:`CheckResult.detail` string.
Credential checks report only ``set`` / ``not set``; the settings-mode check names a field's *source*
(``env <VARNAME>`` or ``literal``) but never a literal value; the cert check prints the cert *path*
(not a secret - see CLAUDE.md) but never the key material; the MongoDB ping reports only a redacted
pass/fail (the underlying :func:`database.check_mongodb_connection` already redacts pymongo text).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import logging
import os
import pathlib
import platform
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Optional

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from .. import book_utils, exitcodes
from ..backends.local import store
from ..backends.mongo import cert_rotation, config, database
from ..book_utils import print_log

logger = logging.getLogger(__name__)

#: Check-result statuses. ``OK``/``WARN``/``SKIP`` never fail the run; a single ``FAIL`` makes
#: ``doctor`` exit :data:`~bookscraper.exitcodes.FATAL`.
OK: Final = "OK"
WARN: Final = "WARN"
FAIL: Final = "FAIL"
SKIP: Final = "SKIP"

#: Maps a :class:`CheckResult` status to the ``print_log`` status that colors its row.
_STATUS_COLOR: Final = {OK: "success", WARN: "warning", FAIL: "error", SKIP: "info"}

#: The optional Atlas Service-Account credentials, reported presence-only (never their values).
_ATLAS_VARS: Final = ("ATLAS_CLIENT_ID", "ATLAS_CLIENT_SECRET", "ATLAS_PROJECT_ID", "ATLAS_DB_USER")

#: The ``.env`` file whose *presence* (never contents) is reported. A module constant so a test can
#: redirect it and never stat the real repo-root ``.env`` (see the standing brief's safety rules).
ENV_FILE: Final = pathlib.Path(".env")

#: Extracts the minimum ``major.minor`` from a ``Requires-Python`` specifier such as ``">=3.13"``.
_MIN_PYTHON_RE = re.compile(r">=\s*(\d+)\.(\d+)")


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic check's outcome.

    :ivar name: Short, human-readable check name (the table's left column).
    :ivar status: One of :data:`OK` / :data:`WARN` / :data:`FAIL` / :data:`SKIP`.
    :ivar detail: A one-line explanation safe to print - never a secret, URI, or host.
    """

    name: str
    status: str
    detail: str


def check_python_version() -> CheckResult:
    """Compare the running interpreter against the package's ``Requires-Python`` specifier.

    :returns: :data:`FAIL` if the interpreter is older than the declared minimum, else :data:`OK`;
        the detail names the running version and (when known) the requirement.
    """
    try:
        requires = importlib.metadata.metadata("bookscraper").get("Requires-Python")
    except importlib.metadata.PackageNotFoundError as exc:
        logger.debug(f"Requires-Python metadata unavailable: {type(exc).__name__}")
        requires = None
    current = platform.python_version()
    if requires:
        match = _MIN_PYTHON_RE.search(requires)
        if match is not None:
            minimum = (int(match.group(1)), int(match.group(2)))
            if sys.version_info[:2] < minimum:
                return CheckResult("Python version", FAIL, f"{current} (requires {requires})")
        return CheckResult("Python version", OK, f"{current} (requires {requires})")
    return CheckResult("Python version", OK, current)


def check_package_version() -> CheckResult:
    """Report the installed ``bookscraper`` distribution version.

    :returns: :data:`OK` with the version, or :data:`WARN` when the distribution metadata is
        unavailable (e.g. running from a source checkout without an editable install).
    """
    try:
        return CheckResult("Package version", OK, importlib.metadata.version("bookscraper"))
    except importlib.metadata.PackageNotFoundError as exc:
        logger.debug(f"bookscraper distribution metadata unavailable: {type(exc).__name__}")
        return CheckResult("Package version", WARN, "not installed (running from a source checkout?)")


async def check_chromium() -> CheckResult:
    """Check that Playwright's Chromium executable is installed on disk.

    Reads ``async_playwright().chromium.executable_path`` and confirms the file exists, without
    launching a browser.

    :returns: :data:`OK` with the executable path when it exists, else :data:`FAIL` (a missing
        browser or an unavailable Playwright driver). The path is a local filesystem path, not a
        secret.
    """
    try:
        async with async_playwright() as playwright:
            executable = playwright.chromium.executable_path
    except Exception as exc:  # noqa: X002 - a Playwright driver/launch failure must surface as a FAIL not a crash
        logger.debug(f"Playwright executable path unavailable: {type(exc).__name__}")
        return CheckResult("Playwright Chromium", FAIL, f"Playwright unavailable: {type(exc).__name__}")
    if executable and pathlib.Path(executable).exists():
        return CheckResult("Playwright Chromium", OK, executable)
    return CheckResult(
        "Playwright Chromium",
        FAIL,
        f"browser not found at {executable}; run 'uv run playwright install chromium'",
    )


def check_env_file() -> CheckResult:
    """Report whether a ``.env`` file is present - never its contents.

    :returns: :data:`OK` either way (its presence is informational, not a pass/fail), with a
        ``present`` / ``not present`` detail.
    """
    return CheckResult(".env file", OK, "present" if ENV_FILE.exists() else "not present")


def _settings_permission_note() -> Optional[str]:
    """Return a short permission-warning note for ``settings.json``, or ``None`` when it looks safe.

    POSIX only (skipped on Windows). Mirrors the advisory checks in
    :func:`config._check_settings_file_security`: the file can hold a literal connection string, so
    it should be an owner-only, non-symlinked regular file. Only the file path (not a secret) is ever
    named.

    :returns: A note naming the problem(s) and the ``chmod 600`` fix, or ``None`` when none apply.
    """
    if os.name == "nt":
        return None
    path = config.SETTINGS_FILE
    try:
        info = path.stat()
    except OSError as exc:
        logger.debug(f"settings.json stat failed: {type(exc).__name__}")
        return None
    problems: list[str] = []
    if path.is_symlink():
        problems.append("is a symlink")
    if info.st_mode & 0o077:
        problems.append("is group/other-readable")
    if info.st_uid != os.getuid():
        problems.append("is not owned by you")
    if not problems:
        return None
    return f"permission warning: settings.json {' and '.join(problems)} (chmod 600 {path})"


def check_settings() -> CheckResult:
    """Report the MongoDB auth mode and (in settings mode) the resolved auth type.

    In legacy mode (no ``settings.json``) it also reports whether ``MONGODB_URI`` is set - the
    2026-09-23 "no settings.json, MONGODB_URI not set" state this command exists to surface. In
    settings mode it reports ``auth_type`` and resolves the configuration to confirm its shape,
    folding a POSIX permission warning into a :data:`WARN`. A malformed ``settings.json`` is a
    :data:`FAIL`; the :class:`config.ConfigError` message names only keys, never a field value.

    :returns: A :class:`CheckResult` for the ``settings.json`` check. No URI, host, password, or
        literal field value ever appears in the detail.
    """
    try:
        settings = config.load_settings()
    except config.ConfigError as exc:
        logger.debug(f"settings.json load failed: {type(exc).__name__}")
        return CheckResult("settings.json", FAIL, str(exc))

    if settings is None:
        mongodb = "MONGODB_URI set" if os.environ.get("MONGODB_URI") else "MONGODB_URI not set"
        return CheckResult("settings.json", OK, f"legacy mode (no settings.json); {mongodb}")

    try:
        # Validate the shape; the resolved (uri, cert) tuple carries secrets and is deliberately
        # discarded here - only the auth_type and source metadata below are ever reported.
        config.resolve_mongo_connection()
    except config.ConfigError as exc:
        logger.debug(f"settings.json resolve failed: {type(exc).__name__}")
        return CheckResult("settings.json", FAIL, f"settings mode; {exc}")

    auth_type = settings.get("auth_type")
    detail = f"settings mode; auth_type={auth_type}; {_describe_uri_source(settings, auth_type)}"
    note = _settings_permission_note()
    if note:
        return CheckResult("settings.json", WARN, f"{detail}; {note}")
    return CheckResult("settings.json", OK, detail)


def _describe_uri_source(settings: dict, auth_type: Optional[str]) -> str:
    """Describe where the connection URI is sourced from, without ever revealing a literal value.

    :param settings: The parsed ``settings.json`` mapping.
    :param auth_type: The declared ``auth_type`` (``"pass"`` or ``"x509"``).
    :returns: ``"uri from env <VARNAME>"`` (the env *variable name* is safe to print) or
        ``"uri from literal"`` (never the literal connection string itself), or
        ``"uri source unknown"`` when the shape is unexpected.
    """
    block = settings.get(auth_type) if auth_type in ("pass", "x509") else None
    spec = block.get("uri") if isinstance(block, dict) else None
    if not isinstance(spec, dict):
        return "uri source unknown"
    source = spec.get("source")
    if source == "env":
        return f"uri from env {spec.get('value')}"
    if source == "literal":
        return "uri from literal"
    return "uri source unknown"


def check_client_cert() -> CheckResult:
    """Resolve the active X.509 client certificate and report its expiry.

    :returns: :data:`SKIP` when no cert is configured (or the Mongo config does not resolve),
        :data:`FAIL` when the configured cert is missing/unreadable or already expired, :data:`WARN`
        when it expires within :data:`cert_rotation.DEFAULT_WARN_DAYS` days, else :data:`OK`. Only the
        cert path and expiry date appear - never the key material (see :func:`read_certificate_expiry`).
    """
    try:
        _uri, cert_file = config.resolve_mongo_connection()
    except config.ConfigError as exc:
        logger.debug(f"client cert resolve skipped: {type(exc).__name__}")
        return CheckResult("Client cert", SKIP, "MongoDB config does not resolve (see settings.json check)")
    if not cert_file:
        return CheckResult("Client cert", SKIP, "no X.509 client certificate configured")

    cert_path = pathlib.Path(cert_file)
    if not cert_path.exists():
        return CheckResult("Client cert", FAIL, f"configured certificate not found: {cert_path}")
    try:
        expires = cert_rotation.read_certificate_expiry(cert_path)
    except cert_rotation.RotationError as exc:
        logger.debug(f"client cert expiry unreadable: {type(exc).__name__}")
        return CheckResult("Client cert", FAIL, str(exc))

    days = (expires - datetime.now(UTC)).days
    detail = f"{cert_path} expires {expires.date().isoformat()} ({days} day(s) left)"
    if days < 0:
        return CheckResult("Client cert", FAIL, detail)
    if days <= cert_rotation.DEFAULT_WARN_DAYS:
        return CheckResult("Client cert", WARN, detail)
    return CheckResult("Client cert", OK, detail)


def check_json_store(store_path: Optional[pathlib.Path] = None) -> CheckResult:
    """Report the local JSON store's directory writability, validity, and book count.

    :param store_path: Store file to inspect; ``None`` defers to :data:`store.LOCAL_STORE_FILE` read
        now (so a test monkeypatching that constant stays isolated).
    :returns: :data:`FAIL` when the store exists but is not valid JSON or its directory is not
        writable and the store is absent, :data:`WARN` when an existing store's directory is not
        writable, else :data:`OK` with the book count.
    """
    path = store_path if store_path is not None else store.LOCAL_STORE_FILE
    directory = path.parent
    writable = os.access(directory, os.W_OK)
    writable_text = "directory writable" if writable else "directory NOT writable"

    if not path.exists():
        status = OK if writable else FAIL
        return CheckResult("JSON store", status, f"{path}: no store yet (0 books); {writable_text}")
    try:
        books = store._load_books(path, raise_on_corrupt=True)
    except store.LocalStoreCorruptError as exc:
        logger.debug(f"json store unreadable: {type(exc).__name__}")
        return CheckResult("JSON store", FAIL, f"{path}: exists but is not valid JSON")
    status = OK if writable else WARN
    return CheckResult("JSON store", status, f"{path}: {len(books)} book(s); {writable_text}")


def check_log_dir() -> CheckResult:
    """Report the run-log directory's writability and how many run logs are retained.

    :returns: :data:`WARN` when the directory does not exist yet or is not writable, else :data:`OK`
        with the retained-log count (capped at :data:`book_utils.MAX_LOG_FILES` by the logger).
    """
    log_dir = book_utils.LOG_DIR
    if not log_dir.exists():
        return CheckResult("Log dir", WARN, f"{log_dir} does not exist yet")
    count = len(list(log_dir.glob("bookscraper_*.log")))
    if not os.access(log_dir, os.W_OK):
        return CheckResult("Log dir", WARN, f"{log_dir}: {count} log(s) retained; NOT writable")
    return CheckResult("Log dir", OK, f"{log_dir}: {count} log(s) retained; writable")


def check_anthropic_key() -> CheckResult:
    """Report the *presence* of ``ANTHROPIC_API_KEY`` (needed by detect-schema) - never its value.

    :returns: :data:`OK` with a ``set`` / ``not set`` detail (an absent key is not a failure; it is
        only needed for the optional detect-schema workflow).
    """
    return CheckResult("ANTHROPIC_API_KEY", OK, "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set")


def check_atlas_vars() -> list[CheckResult]:
    """Report the *presence* of each ``ATLAS_*`` rotation credential - never their values.

    :returns: One :data:`OK` :class:`CheckResult` per variable in :data:`_ATLAS_VARS`, each with a
        ``set`` / ``not set`` detail (they are only needed by ``rotate-cert``).
    """
    return [CheckResult(name, OK, "set" if os.environ.get(name) else "not set") for name in _ATLAS_VARS]


async def check_mongodb_ping(online: bool, store_backend: Optional[str]) -> CheckResult:
    """Ping MongoDB Atlas, but only when ``--online`` is set and the mongo backend is in play.

    :param online: Whether ``--online`` was passed; the ping is :data:`SKIP`ped without it so
        ``doctor`` stays instant and offline-safe by default.
    :param store_backend: The optional ``--store-backend`` value; a ``json`` choice :data:`SKIP`s the
        ping (it does not apply to the local backend).
    :returns: :data:`OK` on a successful ping, :data:`FAIL` on a failure (the reason is already
        redacted by :func:`database.check_mongodb_connection` and reaches only the run log), or
        :data:`SKIP`.
    """
    if store_backend == "json":
        return CheckResult("MongoDB ping", SKIP, "not applicable to the json backend")
    if not online:
        return CheckResult("MongoDB ping", SKIP, "pass --online to test the connection")
    connected = await asyncio.to_thread(database.check_mongodb_connection)
    if connected:
        return CheckResult("MongoDB ping", OK, "connected")
    return CheckResult("MongoDB ping", FAIL, "connection failed (see the run log for the redacted reason)")


def _print_table(results: list[CheckResult]):
    """Print the aligned diagnostic table, one color-coded row per result.

    :param results: The ordered check results to render.
    """
    width = max(len(result.name) for result in results)
    print_log("\nbookscraper doctor", "step")
    for result in results:
        print_log(f"  {result.name:<{width}}  {result.status:<4}  {result.detail}", _STATUS_COLOR[result.status])


async def run(args: argparse.Namespace) -> int:
    """Run every diagnostic check, print the table, and return the process exit code.

    Loads ``.env`` first (so ``set`` / ``not set`` reflects the environment the app would actually
    use), runs the offline checks unconditionally, gates the MongoDB ping on ``--online``, and
    returns :data:`~bookscraper.exitcodes.FATAL` if any check FAILed (a ``WARN`` never fails).

    :param args: Parsed ``doctor`` arguments (``online`` and the optional ``store_backend``).
    :returns: :data:`~bookscraper.exitcodes.OK` when no check FAILed, else
        :data:`~bookscraper.exitcodes.FATAL`.
    """
    load_dotenv()

    results: list[CheckResult] = [
        check_python_version(),
        check_package_version(),
        await check_chromium(),
        check_env_file(),
        check_settings(),
        check_client_cert(),
        check_json_store(),
        check_log_dir(),
        check_anthropic_key(),
        *check_atlas_vars(),
        await check_mongodb_ping(args.online, args.store_backend),
    ]
    _print_table(results)
    return exitcodes.FATAL if any(result.status == FAIL for result in results) else exitcodes.OK
