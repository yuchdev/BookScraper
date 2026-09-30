"""
Single MongoClient factory shared by the real connection and the pre-flight check.

Both ``database._initialize_mongodb_connection()`` (the real connection used by
``resolve_store_backend``) and ``book_utils.check_mongodb_connection()`` (the pre-flight /
``doctor`` check) build their client here, so there is exactly one place that constructs a
``MongoClient`` and exactly one set of TLS kwargs to reason about - the two paths can no
longer drift (``TLS_CA_FILE`` honored on one but not the other, a missing cert silently
dropped on one, mismatched ``serverSelectionTimeoutMS``).

Security notes (see ``docs/security/2026-09-30-mongodb-auth-configuration.md``):

- :func:`ping` re-raises every connection/auth failure as :class:`MongoConnectionError`
  whose message is limited to the exception class name plus pymongo ``code``/``codeName``
  when present (e.g. ``OperationFailure 8000 AtlasError``). Raw pymongo exception text can
  embed the resolved host list or the full connection string (with password); it never
  reaches a caller, a log, or the console from here (SEC-3, SEC-8).
- Certificate validation is never disableable: ``tlsAllowInvalidCertificates=False`` is
  hard-coded and no parameter, env var, or URI option can flip it (SEC-5).
- A non-``None`` ``cert_file`` that does not exist raises rather than silently connecting
  certless (SEC-6).
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from ...book_utils import print_log

module_logger = logging.getLogger(__name__)

X509_AUTH_MECHANISM = "MONGODB-X509"

#: Default pymongo ``serverSelectionTimeoutMS``. Lower than pymongo's own 30 s default so a
#: pre-flight check fails fast, higher than the old 5 s so a healthy-but-slow Atlas region
#: is not misdiagnosed as down. Overridable via ``MONGODB_SERVER_SELECTION_TIMEOUT_MS``.
DEFAULT_SERVER_SELECTION_TIMEOUT_MS = 10_000

#: Environment variable that overrides :data:`DEFAULT_SERVER_SELECTION_TIMEOUT_MS`.
SERVER_SELECTION_TIMEOUT_ENV = "MONGODB_SERVER_SELECTION_TIMEOUT_MS"


class MongoConnectionError(Exception):
    """
    A MongoDB connection/auth failure with a redacted message.

    Carries only the originating exception class name plus pymongo ``code``/``codeName``
    when present - never a host, URI, credential, or raw pymongo exception text - so it is
    safe to log or print anywhere. Raised by :func:`build_mongo_client` (missing cert file)
    and :func:`ping` (connection/auth failure).
    """


def uri_selects_x509_auth(uri: str) -> bool:
    """
    True if ``uri`` explicitly asks for MONGODB-X509 authentication.

    pymongo never infers the auth mechanism from a client certificate: passing
    tlsCertificateKeyFile performs mutual TLS at the transport layer but leaves the
    connection *unauthenticated* unless authMechanism=MONGODB-X509 is requested (Atlas's
    own "Connect with X.509" string includes it, alongside authSource=$external). A cert
    paired with a URI that omits it is therefore a misconfiguration worth surfacing - see
    :func:`_warn_if_x509_mechanism_missing`.

    :param uri: The MongoDB connection URI.
    :returns: True when the URI's ``authMechanism`` query option selects MONGODB-X509.
    """
    mechanisms = parse_qs(urlsplit(uri).query).get("authMechanism", [])
    return any(m.strip().upper() == X509_AUTH_MECHANISM for m in mechanisms)


def uri_has_embedded_credentials(uri: str) -> bool:
    """
    True if the URI carries username[:password] userinfo (i.e. SCRAM credentials).

    :param uri: The MongoDB connection URI.
    :returns: True when the URI's userinfo carries a username.
    """
    return bool(urlsplit(uri).username)


def _warn_if_x509_mechanism_missing(uri: str, cert_file: str) -> None:
    """
    Logs a diagnostic when a client cert is supplied but the URI doesn't select X.509.

    Deliberately a warning, not an abort: in the legacy (no settings.json) resolution
    path a cert is attached whenever one merely exists in ~/.bookscrapper, so a working
    SCRAM setup can pick one up incidentally - aborting there would break it. What this
    prevents is the silent case, where the cert looks configured but no X.509
    authentication is actually negotiated.

    :param uri: The MongoDB connection URI.
    :param cert_file: The resolved TLS client certificate/key path.
    """
    if uri_selects_x509_auth(uri):
        return

    if uri_has_embedded_credentials(uri):
        module_logger.warning(
            f"A TLS client certificate is configured ({cert_file}) but the URI selects password (SCRAM) "
            f"auth, not {X509_AUTH_MECHANISM}. The certificate will be presented for mutual TLS and "
            "otherwise ignored. If X.509 auth was intended, the URI needs "
            f"'?authMechanism={X509_AUTH_MECHANISM}&authSource=$external' and no username:password."
        )
        return

    module_logger.critical(
        f"A TLS client certificate is configured ({cert_file}) but the URI neither selects "
        f"{X509_AUTH_MECHANISM} nor carries credentials, so this connection will not be authenticated. "
        f"Add '?authMechanism={X509_AUTH_MECHANISM}&authSource=$external' to the URI."
    )
    print_log(
        f"Warning: TLS certificate configured but the URI does not request {X509_AUTH_MECHANISM} "
        "authentication - the connection will be unauthenticated.",
        "warning",
    )


def resolve_server_selection_timeout_ms(default: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS) -> int:
    """
    Resolve the server-selection timeout, honoring ``MONGODB_SERVER_SELECTION_TIMEOUT_MS``.

    A missing, empty, non-integer, or non-positive env value falls back to ``default`` with
    a warning rather than failing the connection over a mistyped knob.

    :param default: Timeout in milliseconds used when the env var is absent or invalid.
    :returns: The resolved timeout in milliseconds.
    """
    raw = os.environ.get(SERVER_SELECTION_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        module_logger.warning(f"{SERVER_SELECTION_TIMEOUT_ENV}={raw!r} is not an integer; using default {default} ms.")
        return default
    if value <= 0:
        module_logger.warning(f"{SERVER_SELECTION_TIMEOUT_ENV}={raw!r} must be positive; using default {default} ms.")
        return default
    return value


def build_mongo_client(
    uri: str,
    cert_file: Optional[str],
    ca_file: Optional[str],
    timeout_ms: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
) -> MongoClient:
    """
    Construct the single ``MongoClient`` used by both the real connection and the pre-flight
    check, with identical TLS kwargs for identical inputs.

    Always sets ``server_api=ServerApi("1")``, ``tlsAllowInvalidCertificates=False`` (never
    disableable), and ``serverSelectionTimeoutMS=timeout_ms``. Adds ``tls=True`` +
    ``tlsCertificateKeyFile`` only when ``cert_file`` is given, and ``tlsCAFile`` only when
    ``ca_file`` is given. When a cert is supplied it also runs
    :func:`_warn_if_x509_mechanism_missing`.

    :param uri: The resolved MongoDB connection URI.
    :param cert_file: TLS client certificate/key path, or None for no client cert.
    :param ca_file: Server-CA bundle path, or None to rely on system CAs.
    :param timeout_ms: pymongo ``serverSelectionTimeoutMS`` in milliseconds.
    :returns: A configured (not-yet-pinged) ``MongoClient``.
    :raises MongoConnectionError: If ``cert_file`` is non-``None`` but does not exist; the
        cert is never silently dropped.
    """
    if cert_file is not None and not os.path.exists(cert_file):
        raise MongoConnectionError(f"TLS client certificate file not found: {cert_file}")

    kwargs: dict = {
        "server_api": ServerApi("1"),
        "serverSelectionTimeoutMS": timeout_ms,
        "tlsAllowInvalidCertificates": False,
    }
    if cert_file is not None:
        kwargs["tls"] = True
        kwargs["tlsCertificateKeyFile"] = cert_file
        _warn_if_x509_mechanism_missing(uri, cert_file)
    if ca_file is not None:
        kwargs["tlsCAFile"] = ca_file

    try:
        return MongoClient(uri, **kwargs)
    except PyMongoError as e:
        # MongoClient parses/validates the URI synchronously; a bad URI raises
        # ConfigurationError/InvalidURI whose message can embed the connection string
        # (with password). Redact it - never let that text escape (SEC-8).
        raise MongoConnectionError(_redact_pymongo_error(e)) from None


def _redact_pymongo_error(exc: BaseException) -> str:
    """
    Reduce a pymongo exception to a leak-free summary: class name plus ``code`` and
    ``codeName`` when present (e.g. ``OperationFailure 8000 AtlasError``).

    pymongo exception messages routinely embed the resolved host list, and on some
    auth/parse errors the full connection string including the password. Nothing but the
    class name and numeric/name error codes is retained here.

    :param exc: The originating pymongo (or other) exception.
    :returns: A redacted single-line summary safe to log or print.
    """
    parts = [type(exc).__name__]
    code = getattr(exc, "code", None)
    if code is not None:
        parts.append(str(code))
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        code_name = details.get("codeName")
        if code_name:
            parts.append(str(code_name))
    return " ".join(parts)


def ping(client: MongoClient) -> None:
    """
    Confirm the connection with an ``admin.command("ping")``, re-raising any failure as a
    redacted :class:`MongoConnectionError`.

    :param client: The ``MongoClient`` to ping.
    :raises MongoConnectionError: On any connection/auth failure; the original exception is
        suppressed (``from None``) so its raw text never leaks through ``__cause__``.
    """
    try:
        client.admin.command("ping")
    except PyMongoError as e:
        raise MongoConnectionError(_redact_pymongo_error(e)) from None
