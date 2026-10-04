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
import logging
import os
from pathlib import Path
from typing import Optional, Set, Tuple

from ...book_utils import print_log

CONFIG_DIR = Path.home() / ".bookscrapper"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

module_logger = logging.getLogger(__name__)

#: Defaults for the MongoDB database/collection names, used when settings.json has no
#: ``database`` block and (in legacy mode) when the env vars below are unset.
DEFAULT_DB_NAME = "bookscraper_db"
DEFAULT_COLLECTION_NAME = "books"

#: The only keys allowed at the top level of settings.json. Anything else (a typo such as
#: "x059", or a stray field) fails loudly rather than being silently ignored.
_ALLOWED_TOP_LEVEL_KEYS = frozenset({"auth_type", "pass", "x509", "database"})

#: Once-per-process guard so a given advisory warning about settings.json (world-readable,
#: symlink, foreign owner) is logged at most once, keyed on ``"<path>:<condition>"``.
_SECURITY_WARNINGS_EMITTED: Set[str] = set()


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
    """
    Resolve one ``{"source", "value"}`` settings field to its concrete string value.

    :param spec: The field object, which must carry both ``source`` and ``value`` keys.
    :param field_name: Field name, used only in error messages (never its value).
    :returns: ``value`` as-is for a ``literal`` source, or the looked-up environment
        variable for an ``env`` source.
    :raises ConfigError: If the shape is invalid, the source is unrecognized, or an
        ``env`` source names a variable that is not set.
    """
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


def _warn_once(key: str, message: str) -> None:
    """
    Warn once per process, deduplicated on ``key``. Goes through ``print_log`` so the
    warning reaches the console as well as the run log - the root logger only has a file
    handler, so a bare ``module_logger.warning`` would never be seen by the user.
    """
    if key in _SECURITY_WARNINGS_EMITTED:
        return
    _SECURITY_WARNINGS_EMITTED.add(key)
    print_log(message, "warning")


def _check_settings_file_security(settings_file: Path) -> None:
    """
    Emit advisory warnings about an untrustworthy settings.json (SEC-9).

    settings.json can hold a literal connection string with an embedded password, so it
    should be an owner-only, non-symlinked regular file. On POSIX this warns (never aborts,
    so existing setups keep working) when the file is group/other-readable, is a symlink
    (whose target could be attacker-controlled), or is not owned by the current user. Each
    warning names the file and the fix, and is emitted at most once per process. The check
    is skipped on Windows, whose POSIX permission bits/ownership don't apply.

    :param settings_file: The resolved settings.json path (already confirmed to exist).
    """
    if os.name == "nt":
        return
    fix = f"Restrict it to the current user with: chmod 600 {settings_file}"
    is_symlink = settings_file.is_symlink()
    if is_symlink:
        _warn_once(
            f"{settings_file}:symlink",
            f"{settings_file} is a symlink; its target is trusted as your MongoDB "
            f"credentials/config. Replace it with a regular, owner-only file (chmod on a "
            f"symlink changes its target, not the link).",
        )
    info = settings_file.stat()
    if info.st_mode & 0o077:
        _warn_once(
            f"{settings_file}:mode",
            f"{settings_file} is readable by group/other; it may hold a MongoDB connection "
            f"string with an embedded password. {fix}",
        )
    if info.st_uid != os.getuid():
        _warn_once(
            f"{settings_file}:owner",
            f"{settings_file} is not owned by the current user; it may have been planted by another account. "
            f"Recreate it as your own file (copy it, delete the original, then chmod 600 {settings_file}).",
        )


def _validate_top_level_keys(settings: dict) -> None:
    """
    Reject any top-level key outside :data:`_ALLOWED_TOP_LEVEL_KEYS` (SEC-11).

    A typo such as ``"x059"`` for ``"x509"`` fails loudly instead of being silently
    ignored (which would leave the intended block unread and the config wrongly resolved).
    Only key names are listed - never a field value, which could be a secret.

    :param settings: The parsed settings.json mapping.
    :raises ConfigError: If any unrecognized top-level key is present.
    """
    unknown = sorted(k for k in settings if k not in _ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigError(
            f"settings.json: unrecognized top-level key(s): {', '.join(unknown)}. "
            f"Allowed keys: {', '.join(sorted(_ALLOWED_TOP_LEVEL_KEYS))}."
        )


def load_settings(path: Optional[Path] = None) -> Optional[dict]:
    """Returns the parsed settings.json, or None if the file doesn't exist."""
    settings_file = path or SETTINGS_FILE
    if not settings_file.exists():
        return None
    _check_settings_file_security(settings_file)
    try:
        settings = json.loads(settings_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"Failed to read/parse {settings_file}: {e}") from e
    if isinstance(settings, dict):
        _validate_top_level_keys(settings)
    return settings


def _legacy_resolve() -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve the connection the legacy way, used when no ``settings.json`` exists.

    :returns: ``(uri, cert)`` where ``uri`` comes from ``MONGODB_URI`` and ``cert`` from
        ``TLS_CERT_FILE`` or, failing that, the newest ``~/.bookscrapper/X509-cert-*.pem``.
    """
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


def resolve_mongo_namespace(path: Optional[Path] = None) -> Tuple[str, str]:
    """
    Returns (database_name, collection_name).

    Deterministic when settings.json exists: an optional ``database`` block resolves the
    two names via the same ``{"source", "value"}`` mechanism as the auth fields, and when
    the block is absent the defaults :data:`DEFAULT_DB_NAME` / :data:`DEFAULT_COLLECTION_NAME`
    apply - the ``MONGODB_DB_NAME`` / ``MONGODB_COLLECTION_NAME`` env vars are **not**
    consulted (settings.json is the sole source of truth). Only in legacy mode (no
    settings.json) do those env vars apply, each falling back to the same default.

    :param path: Optional settings.json path; defaults to :data:`SETTINGS_FILE`.
    :returns: The resolved (database name, collection name) pair.
    :raises ConfigError: If a present ``database`` block is malformed.
    """
    settings = load_settings(path)
    if settings is None:
        return (
            os.environ.get("MONGODB_DB_NAME", DEFAULT_DB_NAME),
            os.environ.get("MONGODB_COLLECTION_NAME", DEFAULT_COLLECTION_NAME),
        )

    block = settings.get("database")
    if block is None:
        return DEFAULT_DB_NAME, DEFAULT_COLLECTION_NAME
    if not isinstance(block, dict):
        raise ConfigError("settings.json: 'database' must be an object with 'name' and 'collection'.")

    name_spec = block.get("name")
    collection_spec = block.get("collection")
    if name_spec is None or collection_spec is None:
        raise ConfigError("settings.json: 'database' block must define both 'name' and 'collection'.")
    return (
        _resolve_field(name_spec, "database.name"),
        _resolve_field(collection_spec, "database.collection"),
    )
