"""
Deterministic MongoDB connection/auth configuration.

Resolution order - a single, explicit path per call, never a guessing chain:
  1. ~/.bookscrapper/settings.json, if present, is the sole source of truth.
     Its declared `auth_type` ("pass" or "x509") and each field's declared
     `source` ("env" or "literal") are followed exactly - nothing else is
     consulted. A misconfigured settings.json fails loudly (ConfigError)
     instead of silently falling through to something else.
  2. If settings.json doesn't exist at all, the legacy behavior applies for
     backward compatibility: the MONGODB_URI / TLS_CERT_FILE env vars, with
     TLS_CERT_FILE falling back to the newest ~/.bookscrapper/X509-cert-*.pem.

See CLAUDE.md's "Authentication configuration" section for the settings.json
schema and all six valid (auth_type, source, source) combinations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple

CONFIG_DIR = Path.home() / ".bookscrapper"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


class ConfigError(Exception):
    """settings.json exists but is malformed, incomplete, or unresolvable."""


def get_default_tls_cert_file() -> Optional[str]:
    """
    Newest ~/.bookscrapper/X509-cert-*.pem by mtime, or None if none exist.
    Legacy fallback only, used when settings.json doesn't exist - see
    cert_rotation.py (`bookscraper rotate-cert`), which writes rotated certs
    there with a timestamped name.
    """
    candidates = sorted(CONFIG_DIR.glob("X509-cert-*.pem"), key=lambda p: p.stat().st_mtime)
    return str(candidates[-1]) if candidates else None


def _resolve_field(spec: dict, field_name: str) -> str:
    if not isinstance(spec, dict) or "source" not in spec or "value" not in spec:
        raise ConfigError(f"settings.json: '{field_name}' must be an object with 'source' and 'value'.")

    source, value = spec["source"], spec["value"]
    if source == "literal":
        return value
    if source == "env":
        resolved = os.environ.get(value)
        if not resolved:
            raise ConfigError(f"settings.json: '{field_name}' points at env var {value!r}, which is not set.")
        return resolved
    raise ConfigError(f"settings.json: '{field_name}.source' must be 'env' or 'literal', got {source!r}.")


def _resolve_cert_field(spec: dict, field_name: str) -> str:
    """Like _resolve_field, but the resolved string is a cert path: absolute
    paths are used as-is, bare filenames are resolved under CONFIG_DIR."""
    raw = _resolve_field(spec, field_name)
    path = Path(raw).expanduser()
    return str(path if path.is_absolute() else CONFIG_DIR / path)


def load_settings(path: Optional[Path] = None) -> Optional[dict]:
    """Returns the parsed settings.json, or None if the file doesn't exist."""
    settings_file = path or SETTINGS_FILE
    if not settings_file.exists():
        return None
    try:
        return json.loads(settings_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"Failed to read/parse {settings_file}: {e}") from e


def _legacy_resolve() -> Tuple[Optional[str], Optional[str]]:
    uri = os.environ.get("MONGODB_URI")
    cert = os.environ.get("TLS_CERT_FILE") or get_default_tls_cert_file()
    return uri, cert


def resolve_mongo_connection(path: Optional[Path] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (mongodb_uri, tls_cert_file_or_None).

    Deterministic when settings.json exists (see module docstring); otherwise
    falls back to the legacy MONGODB_URI / TLS_CERT_FILE env-var behavior.
    Caller is expected to have already called load_dotenv() so any "env"
    sources declared in settings.json can see .env-provided values too.
    """
    settings = load_settings(path)
    if settings is None:
        return _legacy_resolve()

    auth_type = settings.get("auth_type")

    if auth_type == "pass":
        block = settings.get("pass")
        if not block:
            raise ConfigError("settings.json: auth_type is 'pass' but no 'pass' block is present.")
        uri = _resolve_field(block["uri"], "pass.uri")
        return uri, None

    if auth_type == "x509":
        block = settings.get("x509")
        if not block:
            raise ConfigError("settings.json: auth_type is 'x509' but no 'x509' block is present.")
        uri = _resolve_field(block["uri"], "x509.uri")
        cert = _resolve_cert_field(block["cert"], "x509.cert")
        return uri, cert

    raise ConfigError(f"settings.json: 'auth_type' must be 'pass' or 'x509', got {auth_type!r}.")
