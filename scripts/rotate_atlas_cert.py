#!/usr/bin/env python3
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

Optional:
  ATLAS_CERT_MONTHS      - certificate validity in months, 1-24 (default: 6)
  ATLAS_CERT_FILE        - override the output path (default: timestamped file
                           under ~/.bookscrapper/)

Run: uv run python scripts/rotate_atlas_cert.py
"""

import json
import os
import pathlib
import sys
import tempfile
from datetime import UTC, datetime

import httpx
from dotenv import load_dotenv

from bookscraper.backends.mongo import config

DEFAULT_CERT_DIR = pathlib.Path.home() / ".bookscrapper"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} not set (check .env or your environment).", file=sys.stderr)
        sys.exit(1)
    return value


def get_access_token(client_id: str, client_secret: str) -> str:
    response = httpx.post(
        "https://cloud.mongodb.com/api/oauth/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def issue_certificate(token: str, project_id: str, db_user: str, months: int) -> bytes:
    url = f"https://cloud.mongodb.com/api/atlas/v2/groups/{project_id}/databaseUsers/{db_user}/certs"

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
    return DEFAULT_CERT_DIR / f"X509-cert-{timestamp}.pem"


def update_settings_after_rotation(new_cert_path: pathlib.Path) -> None:
    """
    If ~/.bookscrapper/settings.json exists and is configured for x509 auth with
    an explicit ("literal") cert filename, repoints it at the newly rotated cert -
    keeping resolution deterministic without a manual edit. If the cert is
    sourced from an env var instead, this only prints a reminder: rewriting the
    caller's environment/.env is out of scope for this script. Does nothing if
    settings.json doesn't exist or isn't configured for x509 - see CLAUDE.md's
    "TLS certificate rotation" section for how this fits into the auth system.
    """
    try:
        settings = config.load_settings()
    except config.ConfigError as e:
        print(f"WARNING: could not read {config.SETTINGS_FILE}, leaving it untouched: {e}")
        return

    if settings is None:
        print(
            f"No {config.SETTINGS_FILE} found - nothing to update. The legacy TLS_CERT_FILE "
            "fallback (newest X509-cert-*.pem) will pick up the new cert automatically."
        )
        return

    if settings.get("auth_type") != "x509":
        print(f"{config.SETTINGS_FILE} is not configured for x509 auth - leaving it untouched.")
        return

    cert_spec = settings.get("x509", {}).get("cert", {})
    source = cert_spec.get("source")

    if source == "literal":
        settings["x509"]["cert"]["value"] = new_cert_path.name
        config.SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n")
        print(f"Updated {config.SETTINGS_FILE}: x509.cert -> {new_cert_path.name}")
    elif source == "env":
        env_var = cert_spec.get("value")
        print(
            f"{config.SETTINGS_FILE} sources the cert from env var {env_var!r} - update that "
            f"variable to {new_cert_path} yourself; this script does not modify your "
            "environment or .env file."
        )
    else:
        print(f"{config.SETTINGS_FILE}'s x509.cert has an unrecognized shape - leaving it untouched.")


def main() -> int:
    load_dotenv()

    client_id = _require_env("ATLAS_CLIENT_ID")
    client_secret = _require_env("ATLAS_CLIENT_SECRET")
    project_id = _require_env("ATLAS_PROJECT_ID")
    db_user = _require_env("ATLAS_DB_USER")
    months = int(os.environ.get("ATLAS_CERT_MONTHS", "6"))

    override = os.environ.get("ATLAS_CERT_FILE")
    output = pathlib.Path(override).expanduser() if override else default_output_path()

    token = get_access_token(client_id, client_secret)
    cert = issue_certificate(token, project_id, db_user, months)
    atomic_write(output, cert)
    print(f"Certificate written to {output}")

    update_settings_after_rotation(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
