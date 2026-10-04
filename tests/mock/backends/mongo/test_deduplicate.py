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
    def test_book_id_only_builds_id_only_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies book id only builds id only query.

        Scenario:
            Given test inputs and fixtures for book id only builds id only query
            When test_book_id_only_builds_id_only_query executes the target flow
            Then the expected outcome for book id only builds id only query is confirmed.

        Boundaries:
            - Focus: book id only builds id only query
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to book id only builds id only query
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", None, collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}]})

    def test_book_slug_only_builds_slug_only_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies book slug only builds slug only query.

        Scenario:
            Given test inputs and fixtures for book slug only builds slug only query
            When test_book_slug_only_builds_slug_only_query executes the target flow
            Then the expected outcome for book slug only builds slug only query is confirmed.

        Boundaries:
            - Focus: book slug only builds slug only query
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to book slug only builds slug only query
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate(None, "slug-1", collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"slug": "slug-1"}]})

    def test_both_identifiers_build_combined_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies both identifiers build combined query.

        Scenario:
            Given test inputs and fixtures for both identifiers build combined query
            When test_both_identifiers_build_combined_query executes the target flow
            Then the expected outcome for both identifiers build combined query is confirmed.

        Boundaries:
            - Focus: both identifiers build combined query
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to both identifiers build combined query
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}, {"slug": "slug-1"}]})

    def test_no_identifiers_returns_false_without_query(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies no identifiers returns false without query.

        Scenario:
            Given test inputs and fixtures for no identifiers returns false without query
            When test_no_identifiers_returns_false_without_query executes the target flow
            Then the expected outcome for no identifiers returns false without query is confirmed.

        Boundaries:
            - Focus: no identifiers returns false without query
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to no identifiers returns false without query
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        result = deduplicate.leanpub_prescrape_deduplicate(None, None, collection)
        assert result is False
        collection.find_one.assert_not_called()

    def test_found_returns_true(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies found returns true.

        Scenario:
            Given test inputs and fixtures for found returns true
            When test_found_returns_true executes the target flow
            Then the expected outcome for found returns true is confirmed.

        Boundaries:
            - Focus: found returns true
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to found returns true
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = {"book_id": "id-1", "slug": "slug-1"}
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is True

    def test_not_found_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies not found returns false.

        Scenario:
            Given test inputs and fixtures for not found returns false
            When test_not_found_returns_false executes the target flow
            Then the expected outcome for not found returns false is confirmed.

        Boundaries:
            - Focus: not found returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not found returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False

    def test_none_collection_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies none collection returns false.

        Scenario:
            Given test inputs and fixtures for none collection returns false
            When test_none_collection_returns_false executes the target flow
            Then the expected outcome for none collection returns false is confirmed.

        Boundaries:
            - Focus: none collection returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none collection returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        with patch.object(deduplicate, "get_mongo_collection", Mock(return_value=None)):
            result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", None)
        assert result is False

    def test_find_one_exception_returns_false(self) -> None:
        """
        [Local] leanpub prescrape deduplicate: verifies find one exception returns false.

        Scenario:
            Given test inputs and fixtures for find one exception returns false
            When test_find_one_exception_returns_false executes the target flow
            Then the expected outcome for find one exception returns false is confirmed.

        Boundaries:
            - Focus: find one exception returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to find one exception returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by leanpub prescrape deduplicate
        """
        collection = Mock()
        collection.find_one.side_effect = PyMongoError("boom")
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False
