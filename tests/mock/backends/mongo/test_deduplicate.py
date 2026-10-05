"""Mock-tier tests for bookscraper.backends.mongo.deduplicate.leanpub_prescrape_deduplicate.

Verifies the $or query is built from exactly the identifiers provided, that a truthy
find_one result means "duplicate" and None means "new", and that the no-identifier,
no-collection, and find_one-error branches all short-circuit to False without
propagating. The collection is a plain Mock - no real MongoDB involved.
"""

from unittest.mock import Mock, patch

from pymongo.errors import PyMongoError

from bookscraper.backends.mongo import deduplicate


class TestLeanpubPrescrapeDeduplicate:
    """Tests for deduplicate.leanpub_prescrape_deduplicate."""

    def test_book_id_only_builds_id_only_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a book_id-only call builds a single-clause $or query.

        Scenario:
            Given a Mock collection whose find_one returns None and a book_id "id-1" with no slug
            When leanpub_prescrape_deduplicate("id-1", None, collection) is called
            Then find_one is called with {"$or": [{"book_id": "id-1"}]} and the function returns False.

        Boundaries:
            - Only book_id is passed; slug is None so no slug clause should appear
            - find_one returning None means "not found" -> False

        On failure, first check:
            - The $or-clause construction logic in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", None, collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}]})

    def test_book_slug_only_builds_slug_only_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a slug-only call builds a single-clause $or query.

        Scenario:
            Given a Mock collection whose find_one returns None and a slug "slug-1" with no book_id
            When leanpub_prescrape_deduplicate(None, "slug-1", collection) is called
            Then find_one is called with {"$or": [{"slug": "slug-1"}]} and the function returns False.

        Boundaries:
            - Only slug is passed; book_id is None so no book_id clause should appear
            - find_one returning None means "not found" -> False

        On failure, first check:
            - The $or-clause construction logic in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate(None, "slug-1", collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"slug": "slug-1"}]})

    def test_both_identifiers_build_combined_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that providing both identifiers builds a two-clause $or query.

        Scenario:
            Given a Mock collection and both book_id "id-1" and slug "slug-1"
            When leanpub_prescrape_deduplicate("id-1", "slug-1", collection) is called
            Then find_one is called with {"$or": [{"book_id": "id-1"}, {"slug": "slug-1"}]}.

        Boundaries:
            - Both identifiers present; neither is omitted from the query
            - Order of clauses: book_id before slug

        On failure, first check:
            - The $or-clause construction logic in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}, {"slug": "slug-1"}]})

    def test_no_identifiers_returns_false_without_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that passing neither identifier short-circuits without querying.

        Scenario:
            Given a Mock collection and both book_id and slug set to None
            When leanpub_prescrape_deduplicate(None, None, collection) is called
            Then the function returns False and find_one is never called.

        Boundaries:
            - The short-circuit path when no identifiers are provided
            - No MongoDB query is issued

        On failure, first check:
            - The early-return guard in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        result = deduplicate.leanpub_prescrape_deduplicate(None, None, collection)
        assert result is False
        collection.find_one.assert_not_called()

    def test_found_returns_true(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a truthy find_one result causes the function to return True.

        Scenario:
            Given a Mock collection whose find_one returns an existing document
            When leanpub_prescrape_deduplicate("id-1", "slug-1", collection) is called
            Then the function returns True (duplicate found).

        Boundaries:
            - The "duplicate detected" branch: any truthy find_one return value
            - Does not verify query structure (covered by other tests)

        On failure, first check:
            - The bool-conversion of the find_one return value in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = {"book_id": "id-1", "slug": "slug-1"}
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is True

    def test_not_found_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a None find_one result causes the function to return False.

        Scenario:
            Given a Mock collection whose find_one returns None
            When leanpub_prescrape_deduplicate("id-1", "slug-1", collection) is called
            Then the function returns False (no duplicate).

        Boundaries:
            - The "not found" branch: find_one returning None

        On failure, first check:
            - The bool-conversion of the find_one return value in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False

    def test_none_collection_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a None collection short-circuits to False.

        Scenario:
            Given get_mongo_collection is patched to return None and collection argument is also None
            When leanpub_prescrape_deduplicate("id-1", "slug-1", None) is called
            Then the function returns False without attempting any query.

        Boundaries:
            - No collection available (unavailable MongoDB); tests the None-collection guard

        On failure, first check:
            - The collection-availability guard in leanpub_prescrape_deduplicate
        """
        with patch.object(deduplicate, "get_mongo_collection", Mock(return_value=None)):
            result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", None)
        assert result is False

    def test_find_one_exception_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies that a PyMongoError from find_one is caught and returns False.

        Scenario:
            Given a Mock collection whose find_one raises PyMongoError("boom")
            When leanpub_prescrape_deduplicate("id-1", "slug-1", collection) is called
            Then the exception is swallowed and the function returns False.

        Boundaries:
            - Exception handling path: only PyMongoError is exercised here
            - Does not verify logging or error message content

        On failure, first check:
            - The try/except around find_one in leanpub_prescrape_deduplicate
        """
        collection = Mock()
        collection.find_one.side_effect = PyMongoError("boom")
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False
