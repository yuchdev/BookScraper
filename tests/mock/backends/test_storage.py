"""Mock-tier tests for bookscraper.backends.storage - the mongo/json backend
adapters and resolve_store_backend()'s pre-flight-check-then-construct logic.

Every collaborator (database, deduplicate, store, check_local_write_permission) is
patched at its point of use inside the storage module, so no real MongoDB or
filesystem access occurs. The assertions pin both the delegated call args and that
each adapter returns exactly what its collaborator returned.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bookscraper.backends import storage
from bookscraper.backends.local import store
from bookscraper.backends.mongo.config import ConfigError
from bookscraper.backends.storage import JsonBackend, MongoBackend, SaveSummary, resolve_store_backend

_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_KEY_PEM = _TEST_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _write_combined_cert(path, expiry: datetime):
    """Write a throwaway combined key+cert PEM expiring at ``expiry``; return the path."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bookscraper-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(_TEST_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(expiry - timedelta(days=365))
        .not_valid_after(expiry)
        .sign(_TEST_KEY, hashes.SHA256())
    )
    path.write_bytes(_TEST_KEY_PEM + cert.public_bytes(serialization.Encoding.PEM))
    return path


class TestSaveSummary:
    """Tests for the SaveSummary dataclass."""
    def test_total_sums_the_three_fields(self) -> None:
        """
        [Local] save summary: verifies total sums the three fields.
        
        Scenario:
            total is the aggregate of inserted + duplicates + errors.
        
        Boundaries:
            - mixed non-zero counts.
        
        On failure, first check:
            - a caller cannot trust total to equal the batch size.
        """
        summary = SaveSummary(inserted=3, duplicates=2, errors=1)
        assert summary.total == 6

    def test_is_frozen(self) -> None:
        """
        [Local] save summary: verifies is frozen.
        
        Scenario:
            SaveSummary is an immutable value object.
        
        Boundaries:
            - attribute assignment after construction.
        
        On failure, first check:
            - a summary could be mutated after the fact, hiding the real outcome.
        """
        summary = SaveSummary(inserted=1, duplicates=0, errors=0)
        with pytest.raises(AttributeError):
            summary.inserted = 5  # type: ignore[misc]


class TestMongoBackend:
    """Tests for storage.MongoBackend."""
    def test_save_books_delegates_to_database(self) -> None:
        """
        [Local] mongo backend: verifies save books delegates to database.

        Scenario:
            Given test inputs and fixtures for save books delegates to database
            When test_save_books_delegates_to_database executes the target flow
            Then the expected outcome for save books delegates to database is confirmed.

        Boundaries:
            - Focus: save books delegates to database
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to save books delegates to database
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by mongo backend
        """
        collection = Mock(name="collection")
        backend = MongoBackend(collection)
        books = [{"title": "A"}, {"title": "B"}]
        expected = SaveSummary(inserted=2, duplicates=0, errors=0)

        with patch.object(storage, "database") as mock_db:
            mock_db.save_books_to_mongodb.return_value = expected
            result = backend.save_books(books)

        mock_db.save_books_to_mongodb.assert_called_once_with(books, collection)
        # save_books now returns the SaveSummary its collaborator produced.
        assert result is expected

    def test_book_exists_by_hash_returns_delegated_value(self) -> None:
        """
        [Local] mongo backend: verifies book exists by hash returns delegated value.

        Scenario:
            Given test inputs and fixtures for book exists by hash returns delegated value
            When test_book_exists_by_hash_returns_delegated_value executes the target flow
            Then the expected outcome for book exists by hash returns delegated value is confirmed.

        Boundaries:
            - Focus: book exists by hash returns delegated value
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to book exists by hash returns delegated value
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by mongo backend
        """
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            mock_db.check_book_exists_in_db.return_value = True
            result = backend.book_exists_by_hash("deadbeef")

        mock_db.check_book_exists_in_db.assert_called_once_with("deadbeef", collection)
        assert result is True

    def test_book_exists_by_asin_returns_delegated_value(self) -> None:
        """
        [Local] mongo backend: verifies book exists by asin returns delegated value.

        Scenario:
            Given test inputs and fixtures for book exists by asin returns delegated value
            When test_book_exists_by_asin_returns_delegated_value executes the target flow
            Then the expected outcome for book exists by asin returns delegated value is confirmed.

        Boundaries:
            - Focus: book exists by asin returns delegated value
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to book exists by asin returns delegated value
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by mongo backend
        """
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            mock_db.check_amazon_asin_exists_in_db.return_value = False
            result = backend.book_exists_by_asin("B00XYZ")

        mock_db.check_amazon_asin_exists_in_db.assert_called_once_with("B00XYZ", collection)
        assert result is False

    def test_leanpub_book_exists_delegates_to_deduplicate(self) -> None:
        """
        [Local] mongo backend: verifies leanpub book exists delegates to deduplicate.

        Scenario:
            Given test inputs and fixtures for leanpub book exists delegates to deduplicate
            When test_leanpub_book_exists_delegates_to_deduplicate executes the target flow
            Then the expected outcome for leanpub book exists delegates to deduplicate is confirmed.

        Boundaries:
            - Focus: leanpub book exists delegates to deduplicate
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to leanpub book exists delegates to deduplicate
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by mongo backend
        """
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "deduplicate") as mock_dedup:
            mock_dedup.leanpub_prescrape_deduplicate.return_value = True
            result = backend.leanpub_book_exists("id-1", "slug-1")

        mock_dedup.leanpub_prescrape_deduplicate.assert_called_once_with("id-1", "slug-1", collection)
        assert result is True

    def test_close_delegates_to_database(self) -> None:
        """
        [Local] mongo backend: verifies close delegates to database.

        Scenario:
            Given test inputs and fixtures for close delegates to database
            When test_close_delegates_to_database executes the target flow
            Then the expected outcome for close delegates to database is confirmed.

        Boundaries:
            - Focus: close delegates to database
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to close delegates to database
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by mongo backend
        """
        collection = Mock(name="collection")
        backend = MongoBackend(collection)

        with patch.object(storage, "database") as mock_db:
            backend.close()

        mock_db.close_mongo_connection.assert_called_once_with()


class TestJsonBackend:
    """Tests for storage.JsonBackend."""
    def test_save_books_delegates_to_store(self) -> None:
        """
        [Local] json backend: verifies save books delegates to store.

        Scenario:
            Given test inputs and fixtures for save books delegates to store
            When test_save_books_delegates_to_store executes the target flow
            Then the expected outcome for save books delegates to store is confirmed.

        Boundaries:
            - Focus: save books delegates to store
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to save books delegates to store
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by json backend
        """
        backend = JsonBackend()
        books = [{"title": "A"}]
        expected = SaveSummary(inserted=1, duplicates=0, errors=0)

        with patch.object(storage, "store") as mock_store:
            mock_store.save_books.return_value = expected
            result = backend.save_books(books)

        # JsonBackend passes the (call-time) store path explicitly, so isolation via
        # monkeypatching store.LOCAL_STORE_FILE holds.
        mock_store.save_books.assert_called_once_with(books, mock_store.LOCAL_STORE_FILE)
        assert result is expected

    def test_lookups_read_from_the_cached_index(self, isolated_local_store) -> None:
        """
        [Local] json backend: verifies lookups read from the cached index.
        
        Scenario:
            hash/asin/book_id/slug lookups resolve against the in-memory index built from the file, returning the same answers the old per-call file reads did.
        
        Boundaries:
            - one stored book queried by each identifier, plus a miss and an empty key.
        
        On failure, first check:
            - a lookup reads a field the index never captured, so genuine duplicates slip through or new books are wrongly rejected.
        """
        store.save_books(
            [{"hash": "h1", "asin": "B001", "book_id": "42", "slug": "some-book"}],
            isolated_local_store,
        )
        backend = JsonBackend()

        assert backend.book_exists_by_hash("h1") is True
        assert backend.book_exists_by_hash("nope") is False
        assert backend.book_exists_by_hash("") is False
        assert backend.book_exists_by_asin("B001") is True
        assert backend.book_exists_by_asin("B999") is False
        assert backend.book_exists_by_asin("") is False
        assert backend.leanpub_book_exists("42", None) is True
        assert backend.leanpub_book_exists(None, "some-book") is True
        assert backend.leanpub_book_exists("99", "other") is False
        assert backend.leanpub_book_exists(None, None) is False

    def test_json_backend_reads_file_once_for_many_lookups(self, isolated_local_store) -> None:
        """
        [Local] json backend: verifies json backend reads file once for many lookups.
        
        Scenario:
            the index is built once and reused, so N dedup lookups parse the file once (the success criterion: 1,000 lookups against a 5,000-book store parse once).
        
        Boundaries:
            - many mixed lookups after a single populated store, spying on _load_books.
        
        On failure, first check:
            - each lookup re-reads and re-parses the whole file - O(candidates x size), the exact cost this cache exists to remove.
        """
        store.save_books([{"hash": "h1", "asin": "B001"}], isolated_local_store)
        backend = JsonBackend()

        with patch.object(store, "_load_books", wraps=store._load_books) as spy:
            for _ in range(50):
                backend.book_exists_by_hash("h1")
                backend.book_exists_by_asin("B001")
                backend.leanpub_book_exists("x", "y")

        spy.assert_called_once()

    def test_json_backend_cache_sees_books_saved_in_same_process(self, isolated_local_store) -> None:
        """
        [Local] json backend: verifies json backend cache sees books saved in same process.
        
        Scenario:
            save_books keeps the in-memory index current, so a book saved after the index was built is immediately visible to lookups without re-reading the file.
        
        Boundaries:
            - build the index (a lookup) before saving, then save and re-query.
        
        On failure, first check:
            - the cache goes stale within a run and a just-saved book is re-scraped or re-inserted as if new.
        """
        backend = JsonBackend()
        assert backend.book_exists_by_hash("h1") is False  # builds an empty index

        # A non-dict element and a hash-less book are ignored by the cache update, exactly
        # as store.save_books ignores them on the write side.
        backend.save_books(
            ["not-a-dict", {"title": "no hash"}, {"hash": "h1", "asin": "B001", "book_id": "42", "slug": "s"}]
        )

        assert backend.book_exists_by_hash("h1") is True
        assert backend.book_exists_by_asin("B001") is True
        assert backend.leanpub_book_exists("42", None) is True

    def test_close_is_noop_default(self) -> None:
        """
        [Local] json backend: verifies close is noop default.
        
        Scenario:
            JsonBackend inherits the ABC's no-op close(); it must not raise.
        
        Boundaries:
            - see test body
        
        On failure, first check:
            - see test body
        """
        backend = JsonBackend()
        assert backend.close() is None


def test_mongo_and_json_backends_return_save_summary() -> None:
    """
    [Local] storage: verifies mongo and json backends return save summary.
    
    Scenario:
        both adapters propagate a SaveSummary from their collaborator, so a caller can treat the two backends interchangeably on the write path.
    
    Boundaries:
        - distinct summaries per backend; the adapters must not coerce or drop them.
    
    On failure, first check:
        - one backend returns None (or a different type) and the exit-code contract that reads summary.errors breaks for that backend only.
    """
    mongo_summary = SaveSummary(inserted=5, duplicates=1, errors=2)
    json_summary = SaveSummary(inserted=1, duplicates=0, errors=0)

    with patch.object(storage, "database") as mock_db:
        mock_db.save_books_to_mongodb.return_value = mongo_summary
        mongo_result = MongoBackend(Mock(name="collection")).save_books([{"hash": "h"}])

    with patch.object(storage, "store") as mock_store:
        mock_store.save_books.return_value = json_summary
        json_result = JsonBackend().save_books([{"hash": "h"}])

    assert mongo_result is mongo_summary
    assert json_result is json_summary
    assert isinstance(mongo_result, SaveSummary)
    assert isinstance(json_result, SaveSummary)


class TestResolveStoreBackend:
    """Tests for storage.resolve_store_backend."""
    def test_mongo_with_live_collection_returns_mongo_backend(self) -> None:
        """
        [Local] resolve store backend: verifies mongo with live collection returns mongo backend.

        Scenario:
            Given test inputs and fixtures for mongo with live collection returns mongo backend
            When test_mongo_with_live_collection_returns_mongo_backend executes the target flow
            Then the expected outcome for mongo with live collection returns mongo backend is confirmed.

        Boundaries:
            - Focus: mongo with live collection returns mongo backend
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to mongo with live collection returns mongo backend
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve store backend
        """
        fake_collection = Mock(name="collection")

        with (
            patch.object(storage, "_warn_if_cert_expiring"),
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = fake_collection
            backend = resolve_store_backend("mongo")

        assert isinstance(backend, MongoBackend)
        assert backend._collection is fake_collection
        mock_db.get_mongo_collection.assert_called_once_with()

    def test_mongo_with_no_collection_exits(self) -> None:
        """
        [Local] resolve store backend: verifies mongo with no collection exits.

        Scenario:
            Given test inputs and fixtures for mongo with no collection exits
            When test_mongo_with_no_collection_exits executes the target flow
            Then the expected outcome for mongo with no collection exits is confirmed.

        Boundaries:
            - Focus: mongo with no collection exits
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to mongo with no collection exits
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve store backend
        """
        with (
            patch.object(storage, "_warn_if_cert_expiring"),
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("mongo")

        assert exc_info.value.code == 1

    def test_json_with_write_permission_returns_json_backend(self, isolated_local_store) -> None:
        """
        [Local] resolve store backend: verifies json with write permission returns json backend.

        Scenario:
            Given test inputs and fixtures for json with write permission returns json backend
            When test_json_with_write_permission_returns_json_backend executes the target flow
            Then the expected outcome for json with write permission returns json backend is confirmed.

        Boundaries:
            - Focus: json with write permission returns json backend
            - Fixtures/params: isolated_local_store
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to json with write permission returns json backend
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve store backend
        """
        with patch.object(storage, "check_local_write_permission", return_value=True) as mock_check:
            backend = resolve_store_backend("json")

        assert isinstance(backend, JsonBackend)
        mock_check.assert_called_once_with()

    def test_preflight_exits_on_corrupt_store(self, isolated_local_store) -> None:
        """
        [Local] resolve store backend: verifies preflight exits on corrupt store.
        
        Scenario:
            a corrupt store is caught at pre-flight, before any scraping starts, so a run aborts loudly instead of failing at the first save (or clobbering the store).
        
        Boundaries:
            - write permission granted, but the existing store file is invalid JSON.
        
        On failure, first check:
            - the process proceeds to scrape and only trips at save time, or worse, overwrites the corrupt store.
        """
        isolated_local_store.write_text("{not valid json", encoding="utf-8")

        with patch.object(storage, "check_local_write_permission", return_value=True):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("json")

        assert exc_info.value.code == 1

    def test_json_without_write_permission_exits(self) -> None:
        """
        [Local] resolve store backend: verifies json without write permission exits.

        Scenario:
            Given test inputs and fixtures for json without write permission exits
            When test_json_without_write_permission_exits executes the target flow
            Then the expected outcome for json without write permission exits is confirmed.

        Boundaries:
            - Focus: json without write permission exits
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to json without write permission exits
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve store backend
        """
        with patch.object(storage, "check_local_write_permission", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("json")

        assert exc_info.value.code == 1

    def test_unknown_backend_exits_without_running_checks(self) -> None:
        """
        [Local] resolve store backend: verifies unknown backend exits without running checks.

        Scenario:
            Given test inputs and fixtures for unknown backend exits without running checks
            When test_unknown_backend_exits_without_running_checks executes the target flow
            Then the expected outcome for unknown backend exits without running checks is confirmed.

        Boundaries:
            - Focus: unknown backend exits without running checks
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to unknown backend exits without running checks
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve store backend
        """
        with (
            patch.object(storage, "database") as mock_db,
            patch.object(storage, "check_local_write_permission") as mock_check,
        ):
            with pytest.raises(SystemExit) as exc_info:
                resolve_store_backend("csv")

        assert exc_info.value.code == 1
        mock_db.get_mongo_collection.assert_not_called()
        mock_check.assert_not_called()


class TestCertExpiryPreflight:
    """Tests for the cert-expiry pre-flight check in resolve_store_backend."""
    def test_preflight_prints_warning_for_soon_expiring_cert(self, tmp_path) -> None:
        """
        [Local] cert expiry preflight: verifies preflight prints warning for soon expiring cert.
        
        Scenario:
            the mongo pre-flight prints a renewal warning when the active cert is close to expiry, before it connects, and still returns a working backend.
        
        Boundaries:
            - cert ~10 days out; resolver and collection are mocked so no real I/O.
        
        On failure, first check:
            - an about-to-expire identity is discovered only when a scrape run fails.
        """
        cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) + timedelta(days=10))

        with (
            patch.object(storage.config, "resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
            patch.object(storage, "print_log") as mock_print,
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            backend = resolve_store_backend("mongo")

        assert isinstance(backend, MongoBackend)
        warnings = [c.args for c in mock_print.call_args_list if c.args[1] == "warning"]
        assert any("expires on" in msg for msg, _ in warnings)

    def test_preflight_parse_failure_is_warning_not_crash(self, tmp_path) -> None:
        """
        [Local] cert expiry preflight: verifies preflight parse failure is warning not crash.
        
        Scenario:
            an unparseable/key-only cert on the pre-flight path yields a warning, not a crash, and the connection still proceeds (ROT-17).
        
        Boundaries:
            - a key-only PEM (no CERTIFICATE block) as the resolved cert.
        
        On failure, first check:
            - a bad cert aborts the whole run at pre-flight instead of warning.
        """
        key_only = tmp_path / "key-only.pem"
        key_only.write_bytes(_TEST_KEY_PEM)

        with (
            patch.object(storage.config, "resolve_mongo_connection", return_value=("mongodb+srv://x", str(key_only))),
            patch.object(storage, "print_log") as mock_print,
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            backend = resolve_store_backend("mongo")

        assert isinstance(backend, MongoBackend)
        warnings = [c.args[0] for c in mock_print.call_args_list if c.args[1] == "warning"]
        assert any(msg.startswith("WARNING: could not read TLS client certificate expiry") for msg in warnings)
        # The private key body never appears in any logged line (ROT-3).
        assert all("PRIVATE KEY" not in c.args[0] for c in mock_print.call_args_list)

    def test_preflight_no_warning_when_cert_far_from_expiry(self, tmp_path) -> None:
        """
        [Local] cert expiry preflight: verifies preflight no warning when cert far from expiry.
        
        Scenario:
            a healthy, far-from-expiry cert produces no pre-flight warning.
        
        Boundaries:
            - cert ~200 days out.
        
        On failure, first check:
            - healthy certs generate a warning on every run, training users to ignore it.
        """
        cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) + timedelta(days=200))

        with (
            patch.object(storage.config, "resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
            patch.object(storage, "print_log") as mock_print,
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            resolve_store_backend("mongo")

        assert not [c for c in mock_print.call_args_list if c.args[1] == "warning"]

    def test_preflight_no_cert_configured_is_silent(self) -> None:
        """
        [Local] cert expiry preflight: verifies preflight no cert configured is silent.
        
        Scenario:
            with no x509 cert configured the pre-flight is silent (nothing to warn about).
        
        Boundaries:
            - resolver returns cert=None.
        
        On failure, first check:
            - a password-auth setup gets a spurious cert warning.
        """
        with (
            patch.object(storage.config, "resolve_mongo_connection", return_value=("mongodb+srv://x", None)),
            patch.object(storage, "print_log") as mock_print,
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            resolve_store_backend("mongo")

        assert not [c for c in mock_print.call_args_list if c.args[1] == "warning"]

    def test_preflight_never_leaks_key_material_to_log_or_console(self, tmp_path, caplog, capsys) -> None:
        """
        [Local] cert expiry preflight: verifies preflight never leaks key material to log or console.
        
        Scenario:
            with print_log running for real, a key-only cert on the pre-flight path leaks no private-key bytes to the captured log records or to stdout (ROT-3, CWE-532/CWE-312).
        
        Boundaries:
            - a key-only PEM; print_log NOT mocked so both sinks are exercised.
        
        On failure, first check:
            - the private-key PEM body reaches ~/.bookscrapper/logs or the console.
        """
        import logging

        key_only = tmp_path / "key-only.pem"
        key_only.write_bytes(_TEST_KEY_PEM)
        key_body = _TEST_KEY_PEM.decode()

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(storage.config, "resolve_mongo_connection", return_value=("mongodb+srv://x", str(key_only))),
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            resolve_store_backend("mongo")

        captured = capsys.readouterr()
        assert "PRIVATE KEY" not in captured.out
        assert key_body not in captured.out
        assert all("PRIVATE KEY" not in rec.getMessage() for rec in caplog.records)
        assert all(key_body not in rec.getMessage() for rec in caplog.records)

    def test_preflight_config_error_is_swallowed(self) -> None:
        """
        [Local] cert expiry preflight: verifies preflight config error is swallowed.
        
        Scenario:
            a resolver ConfigError in the advisory pre-flight is swallowed, leaving the real connection attempt to surface it properly.
        
        Boundaries:
            - resolve_mongo_connection raises ConfigError.
        
        On failure, first check:
            - an advisory warning path crashes the run before the real error is reported.
        """
        with (
            patch.object(storage.config, "resolve_mongo_connection", side_effect=ConfigError("bad")),
            patch.object(storage, "print_log"),
            patch.object(storage, "database") as mock_db,
        ):
            mock_db.get_mongo_collection.return_value = Mock(name="collection")
            backend = resolve_store_backend("mongo")

        assert isinstance(backend, MongoBackend)
