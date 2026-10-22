"""Unit tests for bookscraper.scraping.filters.

The two filter stages are pure functions over a SearchCandidate (search stage, rating) and a
normalized detail document (detail stage, ISBN count). These tests pin the rating rules, the
ISBN-count rule, and the per-site override mechanism without any network or disk I/O.
"""

from bookscraper.scraping.filters import (
    passes_detail_filters,
    passes_search_filters,
    resolve_site_filters,
)
from bookscraper.scraping.search_providers import SearchCandidate

#: A compact base SCRAPE_FILTERS-shaped mapping used across the rating tests.
BASE_FILTERS = {
    "enable_rating_filter": True,
    "min_rating": 3.5,
    "include_new_books_without_rating": True,
    "min_isbns_required": 1,
}


def _candidate(*, site: str = "amazon", rating=None) -> SearchCandidate:
    """Build a minimal SearchCandidate carrying only the fields the filters inspect.

    :param site: The candidate's site key (drives per-site override resolution).
    :param rating: The candidate's rating, or ``None`` for an unrated book.
    :returns: The populated :class:`~bookscraper.scraping.search_providers.SearchCandidate`.
    """
    return SearchCandidate(
        site=site,
        title="Some Book",
        url="https://example.com/b",
        detail_ref="https://example.com/b",
        authors=[],
        rating=rating,
    )


def test_rating_below_threshold_rejected_with_reason():
    """
    [Unit] filters: verifies a rated candidate below min_rating is rejected with a reason string.

    Scenario:
        Given rating-filtering enabled with min_rating 3.5 and a candidate rated 3.1
        When passes_search_filters is applied
        Then it returns (False, reason) and the reason names the rating and the threshold.

    Boundaries:
        - Rating comparison only; ISBN counting is the detail stage's job.

    On failure, first check:
        - The ``candidate.rating < min_rating`` branch and its reason f-string in passes_search_filters.
    """
    keep, reason = passes_search_filters(_candidate(rating=3.1), BASE_FILTERS)
    assert keep is False
    assert reason == "rating 3.1 < 3.5"


def test_unrated_kept_when_include_new_books_true():
    """
    [Unit] filters: verifies an unrated candidate is kept when include_new_books_without_rating is True.

    Scenario:
        Given rating-filtering enabled and include_new_books_without_rating True and a candidate with
        rating None
        When passes_search_filters is applied
        Then it returns (True, None).

    Boundaries:
        - Covers the unrated keep path; the reject path is a separate test.

    On failure, first check:
        - The ``candidate.rating is None`` branch in passes_search_filters.
    """
    keep, reason = passes_search_filters(_candidate(rating=None), BASE_FILTERS)
    assert keep is True
    assert reason is None


def test_unrated_rejected_when_include_new_books_false():
    """
    [Unit] filters: verifies an unrated candidate is rejected when include_new_books_without_rating is False.

    Scenario:
        Given rating-filtering enabled and include_new_books_without_rating False and a candidate with
        rating None
        When passes_search_filters is applied
        Then it returns (False, reason).

    Boundaries:
        - Only the unrated path depends on include_new_books_without_rating.

    On failure, first check:
        - The ``not include_new_books_without_rating`` reject branch in passes_search_filters.
    """
    filters = {**BASE_FILTERS, "include_new_books_without_rating": False}
    keep, reason = passes_search_filters(_candidate(rating=None), filters)
    assert keep is False
    assert reason == "unrated book excluded"


def test_rating_filter_disabled_keeps_everything():
    """
    [Unit] filters: verifies a disabled rating filter keeps both low-rated and unrated candidates.

    Scenario:
        Given enable_rating_filter False
        When passes_search_filters is applied to a 1.0-rated candidate and an unrated candidate
        Then both are kept with no reason.

    Boundaries:
        - Disabling the filter short-circuits before any rating comparison.

    On failure, first check:
        - The ``not site_filters.get("enable_rating_filter")`` short-circuit in passes_search_filters.
    """
    filters = {**BASE_FILTERS, "enable_rating_filter": False}
    assert passes_search_filters(_candidate(rating=1.0), filters) == (True, None)
    assert passes_search_filters(_candidate(rating=None), filters) == (True, None)


def test_min_isbns_required_counts_non_null_isbns():
    """
    [Unit] filters: verifies passes_detail_filters counts non-null isbn10/isbn13 against the threshold.

    Scenario:
        Given min_isbns_required 1 and a site with no per-site override
        When a document with both ISBNs null, one non-null, and both non-null is checked
        Then only the all-null document is rejected (below 1) and the others are kept.

    Boundaries:
        - Counts non-null isbn10/isbn13 keys only; ISBN checksum validity is the model's concern.

    On failure, first check:
        - The ``sum(... if doc.get(key))`` count and the ``< min_isbns`` comparison in passes_detail_filters.
    """
    filters = {**BASE_FILTERS, "per_site": {}}
    none_doc = {"site": "amazon", "isbn10": None, "isbn13": None}
    one_doc = {"site": "amazon", "isbn10": "0306406152", "isbn13": None}
    both_doc = {"site": "amazon", "isbn10": "0306406152", "isbn13": "9780306406157"}

    keep_none, reason_none = passes_detail_filters(none_doc, filters)
    assert keep_none is False
    assert reason_none == "0 ISBN(s) < 1 required"
    assert passes_detail_filters(one_doc, filters) == (True, None)
    assert passes_detail_filters(both_doc, filters) == (True, None)


def test_per_site_override_applies_to_leanpub():
    """
    [Unit] filters: verifies the per-site override relaxes min_isbns_required for Leanpub only.

    Scenario:
        Given SCRAPE_FILTERS with per_site leanpub min_isbns_required 0 and base min_isbns_required 1
        When a Leanpub document with no ISBNs and an Amazon document with no ISBNs are checked
        Then the Leanpub document is kept (override 0) and the Amazon document is rejected (base 1).

    Boundaries:
        - resolve_site_filters merges only the selected site's override; other sites keep the base.

    On failure, first check:
        - resolve_site_filters' merge of filters["per_site"][site] onto the base keys.
    """
    filters = {**BASE_FILTERS, "per_site": {"leanpub": {"min_isbns_required": 0}}}
    leanpub_doc = {"site": "leanpub", "isbn10": None, "isbn13": None}
    amazon_doc = {"site": "amazon", "isbn10": None, "isbn13": None}
    assert passes_detail_filters(leanpub_doc, filters) == (True, None)
    assert passes_detail_filters(amazon_doc, filters)[0] is False


def test_resolve_site_filters_excludes_per_site_key():
    """
    [Unit] filters: verifies resolve_site_filters never leaks the per_site block into the merged result.

    Scenario:
        Given filters carrying a per_site block and an override for leanpub
        When resolve_site_filters is called for leanpub and for a site with no override
        Then neither result contains a "per_site" key, leanpub gets the override, the other site does not.

    Boundaries:
        - Merge semantics only; the returned dict is a fresh copy of the base keys.

    On failure, first check:
        - The ``if key != "per_site"`` comprehension and the ``merged.update`` call in resolve_site_filters.
    """
    filters = {"min_isbns_required": 1, "per_site": {"leanpub": {"min_isbns_required": 0}}}
    leanpub = resolve_site_filters(filters, "leanpub")
    amazon = resolve_site_filters(filters, "amazon")
    assert "per_site" not in leanpub
    assert "per_site" not in amazon
    assert leanpub["min_isbns_required"] == 0
    assert amazon["min_isbns_required"] == 1
