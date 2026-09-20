#!/usr/bin/env python3
"""
MongoDB Connection Test

This script tests the connection to MongoDB Atlas using the default connection string
and TLS certificate file. It prints the connection variables and attempts to establish
a connection, providing detailed status information.
"""

import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, server_api
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

# Configure direct access to default paths instead of importing from the project
# This avoids import issues with the project's module structure

# Configure logging first so it's available for all functions
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('connection_test')

def get_default_atlas_dir():
    """
    Returns the default Bookscrapper directory path.

    Returns:
        Path: The default Bookscrapper directory path.
    """
    return Path.home() / ".bookscrapper"

def get_default_tls_cert_file():
    """
    Returns the default path for the TLS certificate file.

    Returns:
        str: The default path for the TLS certificate file.
    """
    return str(get_default_atlas_dir() / "X509-cert-142838411852079927.pem")

def validate_mongodb_uri(uri):
    """
    Validates the format of a MongoDB URI.

    Args:
        uri (str): The MongoDB URI to validate.

    Returns:
        bool: True if the URI is valid, False otherwise.
    """
    if not uri:
        return False

    # Basic pattern for MongoDB URI validation
    # This pattern supports both standard username:password authentication
    # and X509 authentication without username:password
    pattern = r'^mongodb(\+srv)?:\/\/(([^:]+):([^@]+)@)?([^\/\?]+)(\/([^\?]*))?(\?.*)?$'
    return bool(re.match(pattern, uri))

def get_default_mongodb_uri():
    """
    Returns the MongoDB URI from the default driver string file if it exists.

    Returns:
        str or None: The MongoDB URI from the driver string file, or None if the file doesn't exist or is empty.
    """
    driver_string_file = get_default_atlas_dir() / "driver_string.txt"
    if driver_string_file.exists():
        try:
            with open(driver_string_file, 'r') as f:
                uri = f.read().strip()
                if uri and validate_mongodb_uri(uri):
                    return uri
        except Exception as e:
            logger.error(f"Error reading MongoDB URI from {driver_string_file}: {e}")
    return None

def print_separator():
    """Print a separator line for better readability."""
    print('-' * 80)

def test_mongodb_connection():
    """
    Test the connection to MongoDB Atlas using the default connection string
    and TLS certificate file.
    """
    print_separator()
    print("MONGODB CONNECTION TEST")
    print_separator()

    # Load environment variables from .env file if it exists
    load_dotenv()

    # Get MongoDB URI from environment variables or default location
    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        mongodb_uri = get_default_mongodb_uri()
        if mongodb_uri:
            print(f"Using MongoDB URI from default location: {get_default_atlas_dir() / 'driver_string.txt'}")
        else:
            print("ERROR: MongoDB URI not found in environment variables or default location.")
            return False
    else:
        print("Using MongoDB URI from environment variables.")

    # Print a masked version of the URI for security
    masked_uri = mongodb_uri
    if '@' in mongodb_uri:
        # Mask username and password in the URI
        parts = mongodb_uri.split('@')
        protocol_and_auth = parts[0].split('://')
        masked_auth = f"{protocol_and_auth[0]}://****:****"
        masked_uri = f"{masked_auth}@{parts[1]}"
    print(f"MongoDB URI: {masked_uri}")

    # Validate the URI format
    if not validate_mongodb_uri(mongodb_uri):
        print(f"ERROR: Invalid MongoDB URI format: {masked_uri}")
        return False
    print("MongoDB URI format is valid.")

    # Get TLS certificate file from environment variables or default location
    tls_cert_file = os.environ.get("TLS_CERT_FILE")
    if not tls_cert_file or not os.path.exists(tls_cert_file):
        default_tls_cert_file = get_default_tls_cert_file()
        if os.path.exists(default_tls_cert_file):
            tls_cert_file = default_tls_cert_file
            print(f"Using default TLS certificate file: {tls_cert_file}")
        else:
            print(f"WARNING: TLS certificate file not found in environment variables or default location: {default_tls_cert_file}")
            tls_cert_file = None
    else:
        print(f"Using TLS certificate file from environment variables: {tls_cert_file}")

    # Check if the TLS certificate file exists
    if tls_cert_file:
        if os.path.exists(tls_cert_file):
            print(f"TLS certificate file exists: {tls_cert_file}")
            # Print certificate file size
            cert_size = os.path.getsize(tls_cert_file)
            print(f"Certificate file size: {cert_size} bytes")
        else:
            print(f"ERROR: TLS certificate file does not exist: {tls_cert_file}")
            tls_cert_file = None

    # Attempt to connect to MongoDB
    print_separator()
    print("Attempting to connect to MongoDB...")
    client = None
    try:
        # Create MongoDB client with appropriate options
        if tls_cert_file and os.path.exists(tls_cert_file):
            print("Using TLS certificate for secure connection.")
            client = MongoClient(
                mongodb_uri,
                tls=True,
                tlsCertificateKeyFile=tls_cert_file,
                server_api=server_api.ServerApi('1'),
                serverSelectionTimeoutMS=10000  # 10 second timeout
            )
        else:
            print("WARNING: Connecting without TLS certificate.")
            client = MongoClient(
                mongodb_uri,
                server_api=server_api.ServerApi('1'),
                serverSelectionTimeoutMS=10000  # 10 second timeout
            )

        # Ping the MongoDB server to verify connection
        print("Sending ping command to verify connection...")
        client.admin.command('ping')

        # Get server info
        server_info = client.server_info()
        print("Connection successful!")
        print_separator()
        print("MongoDB Server Information:")
        print(f"  Version: {server_info.get('version', 'Unknown')}")
        print(f"  Uptime: {server_info.get('uptime', 'Unknown')} seconds")

        # List available databases
        print_separator()
        print("Available Databases:")
        databases = client.list_database_names()
        for db in databases:
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

if __name__ == "__main__":
    success = test_mongodb_connection()
    print_separator()
    if success:
        print("MongoDB connection test PASSED!")
        sys.exit(0)
    else:
        print("MongoDB connection test FAILED!")
        sys.exit(1)
