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
import os
import pathlib
import tempfile
from datetime import UTC, datetime
from typing import Optional

import httpx

from . import config

DEFAULT_CERT_MONTHS = 6
MIN_CERT_MONTHS = 1
MAX_CERT_MONTHS = 24

ATLAS_OAUTH_URL = "https://cloud.mongodb.com/api/oauth/token"
ATLAS_API_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"


class RotationError(Exception):
    """Certificate rotation can't proceed (missing/invalid configuration) or Atlas rejected the request."""


def _require_env(name: str) -> str:
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
    response = httpx.post(
        ATLAS_OAUTH_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def issue_certificate(token: str, project_id: str, db_user: str, months: int) -> bytes:
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
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = pathlib.Path(tmp.name)

    temp_path.chmod(0o600)
    temp_path.replace(path)


def default_output_path() -> pathlib.Path:
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


def rotate_certificate(
    months: Optional[int] = None, output: Optional[pathlib.Path] = None
) -> tuple[pathlib.Path, list[str]]:
    """
    Issues a new Atlas-managed X.509 client certificate and writes it to disk.

    Credentials come from the ATLAS_* environment variables (the caller is expected
    to have called load_dotenv() first). `months` and `output` take precedence over
    ATLAS_CERT_MONTHS / ATLAS_CERT_FILE, which in turn take precedence over the
    defaults.

    Returns (path the certificate was written to, settings.json status lines).
    Raises RotationError for missing/invalid configuration or an Atlas API failure.
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
    return output, update_settings_after_rotation(output)
