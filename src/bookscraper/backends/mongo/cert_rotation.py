"""
Rotates the MongoDB Atlas X.509 client certificate for a database user via the
Atlas Admin API, and writes the result to ~/.bookscrapper/X509-cert-<timestamp>.pem.
If ~/.bookscrapper/settings.json exists and is configured for x509 auth with an
explicit ("literal") cert filename, this also repoints it at the new cert; see
update_settings_after_rotation() and CLAUDE.md's "TLS certificate rotation"
section. Otherwise the legacy fallback (config.get_default_tls_cert_file(),
newest X509-cert-*.pem by mtime) picks it up automatically.

Requires an Atlas API Service Account (Project Owner role on the target project)
configured via .env or the environment:
  ATLAS_CLIENT_ID
  ATLAS_CLIENT_SECRET
  ATLAS_PROJECT_ID
  ATLAS_DB_USER          - database user to issue the cert for; must have
                           x509Type "MANAGED" (Atlas-managed X.509)

Optional (each can also be overridden by a `bookscraper rotate-cert` flag):
  ATLAS_CERT_MONTHS      - certificate validity in months, 1-24 (default: 6)
  ATLAS_CERT_FILE        - override the output path (default: timestamped file
                           under ~/.bookscrapper/)

Run: uv run bookscraper rotate-cert
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

import httpx
from cryptography import x509

from ...book_utils import atomic_write_bytes
from . import config
from .client import MongoConnectionError, build_mongo_client, ping

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_CERT_MONTHS = 6
MIN_CERT_MONTHS = 1
MAX_CERT_MONTHS = 24

#: Default number of post-rotation verification ping attempts (Atlas can take a short while to
#: propagate a freshly issued user certificate). Overridable via ``--verify-attempts``.
DEFAULT_VERIFY_ATTEMPTS = 5

#: Fixed delay (seconds) between verification ping attempts.
DEFAULT_VERIFY_DELAY_S = 6.0

#: Default expiry-warning window (days) shared by ``--check`` and the pre-flight helper.
DEFAULT_WARN_DAYS = 30

#: Default number of newest ``X509-cert-*.pem`` files retained by post-rotation pruning.
#: Overridable via ``--keep``; the CLI rejects values below 1 so pruning can never delete every
#: certificate (see :func:`prune_old_certificates`).
DEFAULT_KEEP_CERTS = 2

#: Matches a single PEM ``CERTIFICATE`` block. Used to extract *only* the certificate from a
#: combined key+cert file, so the private-key block is never handed to the parser (ROT-1).
_CERTIFICATE_PEM_PATTERN = re.compile(
    rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)

ATLAS_OAUTH_URL = "https://cloud.mongodb.com/api/oauth/token"
ATLAS_API_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"


class RotationError(Exception):
    """Certificate rotation can't proceed (missing/invalid configuration) or Atlas rejected the request."""


def _require_env(name: str) -> str:
    """
    Return the value of environment variable ``name``, or raise if it is unset/empty.

    :param name: Environment variable to read.
    :returns: The non-empty value.
    :raises RotationError: If the variable is missing or blank.
    """
    value = os.environ.get(name)
    if not value:
        raise RotationError(f"{name} not set (check .env or your environment).")
    return value


def resolve_months(months: Optional[int] = None) -> int:
    """`months` if given, else ATLAS_CERT_MONTHS, else DEFAULT_CERT_MONTHS; validated against Atlas's 1-24 range."""
    if months is None:
        raw = os.environ.get("ATLAS_CERT_MONTHS")
        if raw:
            try:
                months = int(raw)
            except ValueError as e:
                raise RotationError(f"ATLAS_CERT_MONTHS must be an integer, got {raw!r}.") from e
        else:
            months = DEFAULT_CERT_MONTHS

    if not MIN_CERT_MONTHS <= months <= MAX_CERT_MONTHS:
        raise RotationError(f"Certificate validity must be {MIN_CERT_MONTHS}-{MAX_CERT_MONTHS} months, got {months}.")
    return months


def get_access_token(client_id: str, client_secret: str) -> str:
    """
    Exchange Atlas Service Account credentials for a short-lived OAuth access token.

    :param client_id: Atlas API Service Account client id.
    :param client_secret: Atlas API Service Account client secret.
    :returns: The bearer access token for subsequent Admin API calls.
    :raises httpx.HTTPStatusError: If the OAuth endpoint returns a non-2xx status.
    """
    response = httpx.post(
        ATLAS_OAUTH_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def issue_certificate(token: str, project_id: str, db_user: str, months: int) -> bytes:
    """
    Issue a fresh Atlas-managed X.509 client certificate for a database user.

    :param token: Bearer access token from :func:`get_access_token`.
    :param project_id: Atlas project (group) id.
    :param db_user: Atlas database username to issue the certificate for.
    :param months: Certificate validity in months.
    :returns: The combined PEM (private key + certificate) bytes returned by Atlas.
    :raises httpx.HTTPStatusError: If the Admin API returns a non-2xx status.
    """
    url = f"{ATLAS_API_BASE_URL}/groups/{project_id}/databaseUsers/{db_user}/certs"

    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.atlas.2023-01-01+json",
            "Content-Type": "application/json",
        },
        # Atlas Admin API v2 POST endpoints take resource parameters in the JSON body,
        # not the query string (query params there are reserved for envelope/pretty) -
        # sending this as params={...} previously caused a 400 Bad Request.
        json={"monthsUntilExpiration": months},
        timeout=30,
    )
    response.raise_for_status()
    return response.content


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    """
    Atomically write ``data`` to ``path`` with owner-only (``0600``) permissions.

    :param path: Destination file.
    :param data: Bytes to write.
    """
    # The cert (and settings.json, which can hold a connection string) is owner-only.
    atomic_write_bytes(path, data, mode=0o600)


def default_output_path() -> pathlib.Path:
    """
    Return the default cert output path, a timestamped file under ``~/.bookscrapper/``.

    :returns: ``CONFIG_DIR / X509-cert-<UTC timestamp>.pem``.
    """
    # Matches book_utils.py's LOG_FILE_PATH timestamp format, so filenames across
    # ~/.bookscrapper/ stay consistently sortable.
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return config.CONFIG_DIR / f"X509-cert-{timestamp}.pem"


def update_settings_after_rotation(new_cert_path: pathlib.Path) -> list[str]:
    """
    If ~/.bookscrapper/settings.json exists and is configured for x509 auth with
    an explicit ("literal") cert filename, repoints it at the newly rotated cert -
    keeping resolution deterministic without a manual edit. If the cert is
    sourced from an env var instead, this only reports a reminder: rewriting the
    caller's environment/.env is out of scope. Does nothing if settings.json
    doesn't exist or isn't configured for x509 - see CLAUDE.md's "TLS certificate
    rotation" section for how this fits into the auth system.

    Returns human-readable status lines for the caller to display.
    """
    try:
        settings = config.load_settings()
    except config.ConfigError as e:
        logger.debug(f"settings.json repoint skipped: {type(e).__name__}")
        return [f"WARNING: could not read {config.SETTINGS_FILE}, leaving it untouched: {e}"]

    if settings is None:
        return [
            f"No {config.SETTINGS_FILE} found - nothing to update. The legacy TLS_CERT_FILE "
            "fallback (newest X509-cert-*.pem) will pick up the new cert automatically."
        ]

    if settings.get("auth_type") != "x509":
        return [f"{config.SETTINGS_FILE} is not configured for x509 auth - leaving it untouched."]

    cert_spec = settings.get("x509", {}).get("cert", {})
    source = cert_spec.get("source")

    if source == "literal":
        settings["x509"]["cert"]["value"] = new_cert_path.name
        # settings.json can hold a full connection string, so it gets the same
        # atomic, owner-only write as the cert itself.
        atomic_write(config.SETTINGS_FILE, (json.dumps(settings, indent=2) + "\n").encode())
        return [f"Updated {config.SETTINGS_FILE}: x509.cert -> {new_cert_path.name}"]

    if source == "env":
        env_var = cert_spec.get("value")
        return [
            f"{config.SETTINGS_FILE} sources the cert from env var {env_var!r} - update that "
            f"variable to {new_cert_path} yourself; this script does not modify your "
            "environment or .env file."
        ]

    return [f"{config.SETTINGS_FILE}'s x509.cert has an unrecognized shape - leaving it untouched."]


def verify_certificate(
    cert_path: pathlib.Path,
    uri: str,
    *,
    attempts: int = DEFAULT_VERIFY_ATTEMPTS,
    delay_s: float = DEFAULT_VERIFY_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """
    Confirm that the freshly written certificate at ``cert_path`` actually authenticates to
    MongoDB Atlas, before any caller repoints ``settings.json`` at it.

    The client is built **only** via :func:`client.build_mongo_client` and pinged via
    :func:`client.ping` (never a hand-rolled ``MongoClient``), so it inherits the redaction
    contract SEC-3/4/8: any connection/auth failure surfaces as a :class:`MongoConnectionError`
    carrying only the exception class name plus pymongo ``code``/``codeName`` - never the URI,
    host, DB user, cert contents, or a raw pymongo/Atlas error (ROT-11, ROT-13). The server CA
    bundle comes from ``TLS_CA_FILE`` exactly as the real connection reads it.

    Atlas can take a short while to propagate a newly issued user certificate, so a
    :class:`MongoConnectionError` is retried up to ``attempts`` times with a fixed ``delay_s``
    pause (``sleep`` is injectable so tests never actually wait). The client is closed after
    **every** attempt, success or failure. On the final failed attempt the last redacted
    :class:`MongoConnectionError` is re-raised unchanged.

    :param cert_path: Path to the newly written combined key+cert PEM to verify.
    :param uri: The resolved MongoDB connection URI to authenticate against.
    :param attempts: Maximum number of ping attempts (clamped to at least 1).
    :param delay_s: Seconds to wait between attempts.
    :param sleep: Sleep callable, injectable for tests.
    :raises MongoConnectionError: If every attempt fails; the message is already redacted.
    """
    ca_file = os.environ.get("TLS_CA_FILE")
    total = max(1, attempts)
    last_error: Optional[MongoConnectionError] = None
    for attempt in range(1, total + 1):
        client = None
        try:
            client = build_mongo_client(uri, str(cert_path), ca_file)
            ping(client)
            return
        except MongoConnectionError as e:
            last_error = e
            if attempt < total:
                sleep(delay_s)
        finally:
            if client is not None:
                client.close()
    # ``total >= 1`` guarantees at least one failed attempt set ``last_error`` before we get here.
    assert last_error is not None
    raise last_error


def rotate_certificate(
    months: Optional[int] = None,
    output: Optional[pathlib.Path] = None,
    *,
    verify: bool = True,
    verify_attempts: int = DEFAULT_VERIFY_ATTEMPTS,
    keep: int = DEFAULT_KEEP_CERTS,
    prune: bool = True,
) -> tuple[pathlib.Path, list[str], bool]:
    """
    Issues a new Atlas-managed X.509 client certificate, writes it to disk, verifies it
    authenticates, and only then repoints ``settings.json`` at it and prunes superseded certs.

    Credentials come from the ATLAS_* environment variables (the caller is expected
    to have called load_dotenv() first). `months` and `output` take precedence over
    ATLAS_CERT_MONTHS / ATLAS_CERT_FILE, which in turn take precedence over the
    defaults.

    After the PEM is written, and unless ``verify`` is ``False``, the new certificate is
    pinged against the **current** resolver URI (:func:`config.resolve_mongo_connection`) via
    :func:`verify_certificate`. If no URI is configured, verification is skipped with a status
    line. If verification fails for good, ``settings.json`` is **not** repointed (ROT-12): the
    new PEM is left on disk, the previously configured certificate keeps working, and the
    returned ``verified`` flag is ``False`` so the caller exits non-zero. Only on a passing (or
    skipped) verification is :func:`update_settings_after_rotation` called.

    When ``prune`` is True, superseded ``X509-cert-*.pem`` files are removed **after** the
    successful repoint via :func:`_prune_after_rotation` (keeping the newest ``keep`` plus the
    active cert - see ROT-6/7/8). Pruning runs only on a verified success or when verification
    was skipped for lack of a configured URI; it is deliberately **not** run after ``--no-verify``
    (there is no confirmation the new cert works, so old certs are kept to avoid a lockout) nor
    ever after a failed verification (ROT-13).

    :param months: Certificate validity in months, or None to defer to env/default.
    :param output: Output PEM path, or None to defer to env/default.
    :param verify: When True (default), ping the new cert before repointing.
    :param verify_attempts: Maximum verification ping attempts.
    :param keep: Number of newest certificates to retain when pruning (>= 1).
    :param prune: When True (default), prune superseded certs after a successful repoint.
    :returns: (path the certificate was written to, status lines, ``verified``). ``verified`` is
        False only when verification ran and failed; it is True when it passed *or* was skipped
        (``--no-verify``, or no URI configured) - i.e. it means "the command should exit 0".
    :raises RotationError: For missing/invalid configuration, an Atlas API failure, or a
        MongoDB configuration that cannot be resolved for verification.
    """
    client_id = _require_env("ATLAS_CLIENT_ID")
    client_secret = _require_env("ATLAS_CLIENT_SECRET")
    project_id = _require_env("ATLAS_PROJECT_ID")
    db_user = _require_env("ATLAS_DB_USER")
    months = resolve_months(months)

    if output is None:
        override = os.environ.get("ATLAS_CERT_FILE")
        output = pathlib.Path(override).expanduser() if override else default_output_path()

    try:
        token = get_access_token(client_id, client_secret)
        cert = issue_certificate(token, project_id, db_user, months)
    except httpx.HTTPStatusError as e:
        # Report the status only: the response body/request can echo credentials.
        raise RotationError(f"Atlas API request to {e.request.url} failed with HTTP {e.response.status_code}.") from e
    except httpx.HTTPError as e:
        raise RotationError(f"Could not reach the Atlas API: {type(e).__name__}.") from e

    atomic_write(output, cert)

    if not verify:
        notes = ["Verification skipped (--no-verify); repointing settings.json without pinging Atlas."]
        notes += update_settings_after_rotation(output)
        if prune:
            # No confirmation the new cert works, so old certs are kept to avoid a lockout (the
            # HIGH self-inflicted-lockout finding). Pruning only ever runs on a positive signal.
            notes.append(
                "Skipping cert pruning: verification was skipped (--no-verify), so there is no "
                "confirmation the new certificate authenticates - old certificates are kept."
            )
        return output, notes, True

    try:
        uri = config.resolve_mongo_connection()[0]
    except config.ConfigError as e:
        # Cannot verify without a resolvable URI, and we will not repoint blindly. Leave the new
        # PEM on disk and fail loudly - the previously configured cert keeps working. The
        # ConfigError message is about settings.json shape, not credentials.
        raise RotationError(
            f"Cannot verify the new certificate: the MongoDB configuration could not be resolved ({e}). "
            "Re-run with --no-verify to skip verification."
        ) from e

    if not uri:
        notes = ["Skipping verification: no MongoDB URI is configured (nothing to ping)."]
        notes += update_settings_after_rotation(output)
        if prune:
            # Nothing to authenticate against means no identity to lock out of, so pruning is safe.
            notes += _prune_after_rotation(output, keep)
        return output, notes, True

    try:
        verify_certificate(output, uri, attempts=verify_attempts)
    except MongoConnectionError as e:
        # Verification failed for good: do NOT repoint (ROT-12) and do NOT prune (ROT-13). The new
        # PEM stays on disk and the previously configured certificate remains in use. The message
        # carries only the redacted code/codeName (ROT-11, ROT-13) - never the URI, host, DB user,
        # or Atlas response body.
        note = (
            f"WARNING: the new certificate at {output} failed verification ({e}); "
            "settings.json left unchanged - the previously configured certificate remains in use."
        )
        return output, [note], False

    # Verification passed: it is now safe to repoint and then prune superseded certs on this
    # success-only path - never before verification succeeds, per ROT-13.
    notes = [f"Verified: the new certificate authenticates to MongoDB Atlas ({output.name})."]
    notes += update_settings_after_rotation(output)
    if prune:
        notes += _prune_after_rotation(output, keep)
    return output, notes, True


def _prune_after_rotation(new_cert: pathlib.Path, keep: int) -> list[str]:
    """
    Build the ``protected`` set and prune superseded certs after a successful repoint.

    ``protected`` = {the just-written ``new_cert``} ∪ {the cert
    :func:`config.resolve_mongo_connection` resolves *after* repointing} ∪ {``TLS_CERT_FILE`` if
    set} (ROT-6). Resolution happens *after* :func:`update_settings_after_rotation` has run, so the
    resolved cert reflects the freshly repointed configuration - except in env-source mode, where
    settings.json is not rewritten and this is still the *old* cert (kept, correctly; the new one
    is protected via ``new_cert`` regardless). If the active cert cannot be
    resolved (a :class:`config.ConfigError` or any resolver failure), pruning is **skipped
    entirely** with a status line - never prune on uncertainty (ROT-8).

    :param new_cert: The certificate just written by this rotation (always protected).
    :param keep: Number of newest certificates to retain.
    :returns: Status lines from the prune, or a single skip line if the active cert is unresolvable.
    """
    protected = {new_cert}
    try:
        _uri, active_cert = config.resolve_mongo_connection()
    except config.ConfigError as e:
        logger.debug(f"cert pruning skipped, active cert unresolved: {type(e).__name__}")
        return [
            f"Skipping cert pruning: the active certificate could not be resolved ({e}); "
            "no old certificate is removed on uncertainty."
        ]
    if active_cert:
        protected.add(pathlib.Path(active_cert))
    tls_cert = os.environ.get("TLS_CERT_FILE")
    if tls_cert:
        protected.add(pathlib.Path(tls_cert).expanduser())
    return prune_old_certificates(keep, protected)


def prune_old_certificates(keep: int, protected: set[pathlib.Path]) -> list[str]:
    """
    Delete superseded ``X509-cert-*.pem`` files from :data:`config.CONFIG_DIR`, keeping the newest
    ``keep`` plus every path in ``protected`` however old.

    Candidates come **solely** from ``config.CONFIG_DIR.glob("X509-cert-*.pem")`` sorted
    newest-first by ``st_mtime``; nothing outside ``CONFIG_DIR`` and nothing not matching the glob
    is ever a candidate, so a ``--output`` cert written elsewhere is never pruned (ROT-9). Each
    candidate and each protected path is canonicalized with :meth:`Path.resolve` before comparison,
    so a symlink-, relative-, or ``..``-spelled reference to the active cert is still recognized as
    protected and never deleted (ROT-7). Deletion unlinks the **matched glob path itself** - it
    never ``resolve()``\\ s a symlink and deletes the target outside ``CONFIG_DIR`` (ROT-10). A
    failed unlink becomes a ``WARNING:`` status line naming only the path and the exception class,
    never an exception (ROT-10).

    :param keep: Number of newest certificates to retain (>= 1; the CLI rejects lower values so
        pruning can never delete every certificate).
    :param protected: Paths that must never be pruned however old; each is canonicalized here.
    :returns: One status line per deletion or failed unlink, or a single "nothing to prune" line.
    """
    # Age a cert by its content (stat follows a symlink to its target); fall back to the link's
    # own lstat for a dangling symlink, and drop a file that vanished between glob() and here.
    # Either would otherwise raise out of a prune that runs after the rotation already succeeded.
    dated: list[tuple[float, pathlib.Path]] = []
    for path in config.CONFIG_DIR.glob("X509-cert-*.pem"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            try:
                mtime = path.lstat().st_mtime
            except OSError as e:
                # The file vanished between glob() and lstat(); there is nothing left to prune.
                logger.debug(f"cert pruning candidate disappeared: {type(e).__name__}")
                continue
        dated.append((mtime, path))
    candidates = [path for _mtime, path in sorted(dated, key=lambda item: item[0], reverse=True)]
    protected_resolved = {p.resolve() for p in protected}
    status: list[str] = []
    for candidate in candidates[keep:]:
        if candidate.resolve() in protected_resolved:
            continue
        try:
            candidate.unlink()
        except OSError as e:
            status.append(f"WARNING: could not delete old certificate {candidate}: {type(e).__name__}.")
        else:
            status.append(f"Pruned superseded certificate {candidate.name}.")
    if not status:
        status.append(f"No superseded certificates to prune (kept the newest {keep} plus any in use).")
    return status


def read_certificate_expiry(path: pathlib.Path) -> datetime:
    """
    Return the expiry (``not_valid_after``) of the X.509 certificate at ``path``.

    ``path`` is the combined private-key + certificate PEM produced by rotation. Only the
    **first** ``CERTIFICATE`` PEM block is extracted and parsed; the private-key block is
    never read, loaded, or retained (ROT-1). The result is timezone-aware UTC.

    Any failure - a missing/unreadable file, no certificate block, or an unparseable block -
    raises :class:`RotationError` naming **only the file path**, never the file contents, the
    offending bytes, or any key material (ROT-2, ROT-3).

    :param path: Path to the combined key+cert PEM file.
    :returns: The certificate's expiry as a timezone-aware UTC ``datetime``.
    :raises RotationError: If the file is missing/unreadable, has no certificate block, or the
        certificate cannot be parsed.
    """
    try:
        pem_bytes = path.read_bytes()
    except OSError:
        # Name only the path - never the errno/exception text, keeping the leak-free contract
        # uniform across every failure of this path (ROT-2, ROT-3).
        raise RotationError(f"Could not read certificate file: {path}") from None

    match = _CERTIFICATE_PEM_PATTERN.search(pem_bytes)
    if match is None:
        raise RotationError(f"No X.509 CERTIFICATE block found in {path}.")

    try:
        certificate = x509.load_pem_x509_certificate(match.group(0))
    except ValueError:
        # Never interpolate the exception or the PEM bytes - they can echo key material (ROT-3).
        raise RotationError(f"Could not parse the X.509 certificate in {path}.") from None

    return certificate.not_valid_after_utc


def cert_expiry_warning(cert_path: Optional[pathlib.Path], warn_days: int = DEFAULT_WARN_DAYS) -> Optional[str]:
    """
    Non-raising pre-flight helper: a warning string if the certificate at ``cert_path`` is
    expired or expires within ``warn_days`` days, else ``None``.

    Called by ``storage.resolve_store_backend`` before connecting, so it must never raise: a
    parse failure returns a warning string rather than crashing the connection path (ROT-17).
    It performs a local file read only and never contacts Atlas (ROT-16). No PEM or key bytes
    ever appear in the returned string (ROT-3) - a parse failure is reported via the leak-free
    :class:`RotationError` message (path only).

    :param cert_path: The active TLS client certificate path, or ``None`` when no x509 cert is
        configured (in which case there is nothing to warn about).
    :param warn_days: Warn when this many days or fewer remain before expiry (default 30).
    :returns: A warning string, or ``None`` when there is nothing to warn about.
    """
    if cert_path is None:
        return None
    try:
        expires = read_certificate_expiry(cert_path)
    except RotationError as e:
        logger.debug(f"cert expiry unreadable: {type(e).__name__}")
        return f"WARNING: could not read TLS client certificate expiry: {e}"

    days_remaining = (expires - datetime.now(UTC)).days
    if days_remaining < 0:
        return f"WARNING: the TLS client certificate {cert_path} expired on {expires.date().isoformat()}."
    if days_remaining <= warn_days:
        return (
            f"WARNING: the TLS client certificate {cert_path} expires on {expires.date().isoformat()} "
            f"({days_remaining} day(s) remaining). Run 'bookscraper rotate-cert' to renew it."
        )
    return None
