#!/usr/bin/env python3
"""
Shared helpers for the MongoDB connection diagnostic scripts.

Deliberately standalone (no imports from src/bookscraper) so these scripts keep
working independent of the package's import structure - see each
connection_test_*.py's own docstring.

Credential layout:
  - Production auth lives in ~/.bookscrapper/settings.json (see
    bookscraper.backends.mongo.config) plus X509-cert-*.pem for the client cert.
  - Test-only driver strings come from TEST_MONGODB_URI_PASS / TEST_MONGODB_URI_TLS,
    set in .env (repo root, git-ignored) - env-var only, no fallback file, so a
    bug in either the production resolver or a test script can never
    cross-load the other's credentials, and ~/.bookscrapper/ never has to hold
    test-only secrets.
"""

import os
import re
from pathlib import Path

from pymongo import MongoClient, server_api
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

PRODUCTION_CONFIG_DIR = Path.home() / ".bookscrapper"
DEFAULT_TLS_CERT_FILE = PRODUCTION_CONFIG_DIR / "X509-cert-142838411852079927.pem"

_URI_PATTERN = re.compile(r"^mongodb(\+srv)?:\/\/(([^:]+):([^@]+)@)?([^\/\?]+)(\/([^\?]*))?(\?.*)?$")


def validate_mongodb_uri(uri):
    """Validates the basic shape of a MongoDB URI (SCRAM or X.509)."""
    return bool(uri) and bool(_URI_PATTERN.match(uri))


def mask_uri(uri):
    """Masks embedded username:password so secrets never hit stdout/logs."""
    if "@" not in uri:
        return uri
    parts = uri.split("@")
    protocol_and_auth = parts[0].split("://")
    return f"{protocol_and_auth[0]}://****:****@{parts[1]}"


def print_separator():
    print("-" * 80)


def get_mongodb_uri(env_var_name):
    """Resolves a MongoDB URI from the given environment variable (set via .env or the
    environment directly) - no fallback file, so a missing/misspelled var fails loudly
    rather than silently reading something else."""
    uri = os.environ.get(env_var_name)
    if uri:
        print(f"Using MongoDB URI from environment variable {env_var_name}.")
        return uri
    return None


def get_tls_cert_file(env_var_name="TLS_CERT_FILE", default_path: Path = DEFAULT_TLS_CERT_FILE):
    """
    Resolves the TLS client certificate path: env var first, then the production
    default. Shared with production on purpose - there is one X.509 cert, not a
    separate test cert, so the TLS test script authenticates as the same identity
    the production app would.
    """
    tls_cert_file = os.environ.get(env_var_name)
    if tls_cert_file and os.path.exists(tls_cert_file):
        print(f"Using TLS certificate file from environment variable {env_var_name}: {tls_cert_file}")
        return tls_cert_file

    if default_path.exists():
        print(f"Using default TLS certificate file: {default_path}")
        return str(default_path)

    print(
        f"WARNING: TLS certificate file not found in environment variable {env_var_name} "
        f"or default location: {default_path}"
    )
    return None


def run_connection_test(mongodb_uri, tls_cert_file=None):
    """Attempts a MongoDB connection and pings the server. Returns True/False."""
    masked_uri = mask_uri(mongodb_uri)
    print(f"MongoDB URI: {masked_uri}")

    if not validate_mongodb_uri(mongodb_uri):
        print(f"ERROR: Invalid MongoDB URI format: {masked_uri}")
        return False
    print("MongoDB URI format is valid.")

    if tls_cert_file:
        print(f"TLS certificate file exists: {tls_cert_file}")
        print(f"Certificate file size: {os.path.getsize(tls_cert_file)} bytes")

    print_separator()
    print("Attempting to connect to MongoDB...")
    client = None
    try:
        if tls_cert_file:
            print("Using TLS certificate for secure connection (X.509 auth).")
            client = MongoClient(
                mongodb_uri,
                tls=True,
                tlsCertificateKeyFile=tls_cert_file,
                server_api=server_api.ServerApi("1"),
                serverSelectionTimeoutMS=10000,
            )
        else:
            print("Connecting without a TLS client certificate (password/SCRAM auth).")
            client = MongoClient(
                mongodb_uri,
                server_api=server_api.ServerApi("1"),
                serverSelectionTimeoutMS=10000,
            )

        print("Sending ping command to verify connection...")
        client.admin.command("ping")

        server_info = client.server_info()
        print("Connection successful!")
        print_separator()
        print("MongoDB Server Information:")
        print(f"  Version: {server_info.get('version', 'Unknown')}")
        print(f"  Uptime: {server_info.get('uptime', 'Unknown')} seconds")

        print_separator()
        print("Available Databases:")
        for db in client.list_database_names():
            print(f"  - {db}")

        return True

    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        print(f"ERROR: Failed to connect to MongoDB Atlas: {e}")
        print_separator()
        print("Troubleshooting Tips:")
        print("1. Check if the MongoDB URI is correct")
        print("2. Verify that the TLS certificate is valid and properly formatted")
        print("3. Ensure your network allows connections to MongoDB Atlas")
        print("4. Check if your IP address is whitelisted in MongoDB Atlas")
        return False

    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")
        return False

    finally:
        if client:
            client.close()
            print("MongoDB connection closed.")
