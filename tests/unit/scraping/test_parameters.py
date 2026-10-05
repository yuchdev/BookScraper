"""Sanity/regression guards for bookscraper.scraping.parameters (pure data).

Not deep testing - just a tripwire against accidental corruption of the data that
drives every scrape: a truncated site_constants, an emptied USER_AGENTS, or a
flipped HEADLESS_BROWSER all silently change scraper behavior with no crash.
"""

from bookscraper.scraping import parameters, schema_detection, scrape_details

# Detail-page selector keys that ``detail_selector_fields`` returns but ``scrape_book`` does
# not read by their canonical name, each with the reason it is a deliberate exception rather
# than dead config (success criterion of subtask 03.0/04).
_CONSUMED_ALLOW_LIST = {
    # Read via the dedicated meta-tag author branch; the canonical AUTHORS key represents the
    # author field in CONSUMED_DETAIL_KEYS.
    "AUTHORS_META": "read via the meta-author branch, tracked under AUTHORS",
    # Read via the dedicated meta-tag date branch; the canonical PUBLICATION_DATE key represents
    # the publication-date field in CONSUMED_DETAIL_KEYS.
    "PUBLICATION_DATE_META": "read via the meta-date branch, tracked under PUBLICATION_DATE",
    # The ASIN is taken from the page URL; this detail block is an unused alternate locator and
    # is listed as OPTIONAL_SELECTOR_KEYS in parameters.py.
    "ASIN_DETAIL_PAGE_SELECTOR": "ASIN comes from the URL, not this detail block",
}


def test_headless_browser_is_false():
    """
    [Unit] parameters: verifies HEADLESS_BROWSER is False (headed mode).

    Scenario:
        Given the parameters module is imported
        When HEADLESS_BROWSER is read
        Then its value is exactly False (Playwright runs headed by default)

    Boundaries:
        - Single boolean constant; no scraper or browser logic exercised
        - Does not assert anything about Playwright launch behaviour

    On failure, first check:
        - parameters.HEADLESS_BROWSER was accidentally changed to True
    """
    assert parameters.HEADLESS_BROWSER is False


def test_sites_to_scrape_is_leanpub_only():
    """
    [Unit] parameters: verifies SITES_TO_SCRAPE is exactly ["leanpub"].

    Scenario:
        Given the parameters module is imported
        When SITES_TO_SCRAPE is read
        Then it equals ["leanpub"] with no other site

    Boundaries:
        - Single list constant; no search workflow logic exercised
        - Tripwire: a new site entry would need test and workflow updates

    On failure, first check:
        - parameters.SITES_TO_SCRAPE was extended or changed unexpectedly
    """
    assert parameters.SITES_TO_SCRAPE == ["leanpub"]


def test_site_constants_has_all_four_sites():
    """
    [Unit] parameters: verifies site_constants contains exactly the four supported sites.

    Scenario:
        Given the parameters module is imported
        When the key set of site_constants is compared to the expected set
        Then it equals {amazon, leanpub, packtpub, oreilly} with no additions or omissions

    Boundaries:
        - Key-set equality only; per-site selector values are not inspected
        - Does not assert that each site's selectors are non-empty or valid CSS

    On failure, first check:
        - A site was added to or removed from site_constants without updating this tripwire
    """
    assert set(parameters.site_constants) == {"amazon", "leanpub", "packtpub", "oreilly"}


def test_user_agents_non_empty_strings():
    """
    [Unit] parameters: verifies USER_AGENTS is a non-empty list of non-empty strings.

    Scenario:
        Given the parameters module is imported
        When USER_AGENTS is inspected
        Then the list is non-empty and every element is a non-empty str

    Boundaries:
        - Shape check only; does not validate that strings are valid HTTP User-Agent headers
        - No network or browser call

    On failure, first check:
        - USER_AGENTS was emptied or a blank/non-string value was appended
    """
    assert len(parameters.USER_AGENTS) > 0
    assert all(isinstance(ua, str) and ua for ua in parameters.USER_AGENTS)


def test_min_rating_is_float():
    """
    [Unit] parameters: verifies SCRAPE_FILTERS["min_rating"] is a float.

    Scenario:
        Given the parameters module is imported
        When SCRAPE_FILTERS["min_rating"] is inspected
        Then it is an instance of float

    Boundaries:
        - Type check only; the numeric value is not asserted
        - Does not test that the threshold is within a sensible range

    On failure, first check:
        - SCRAPE_FILTERS["min_rating"] was changed to an int or string literal
    """
    assert isinstance(parameters.SCRAPE_FILTERS["min_rating"], float)


def test_every_detail_selector_key_is_consumed():
    """
    [Unit] parameters: verifies every detail-page selector key is read by scrape_book.

    Scenario:
        Given the union of detail-page selector keys across every site_constants entry (via
        schema_detection.detail_selector_fields)
        When compared with scrape_details.CONSUMED_DETAIL_KEYS
        Then every key is either consumed or a documented allow-list exception, and
        CONSUMED_DETAIL_KEYS names no key that is not a real detail selector

    Boundaries:
        - Pure set comparison over static data; no scrape, browser, or network
        - The only tolerated unconsumed keys are the three documented in _CONSUMED_ALLOW_LIST

    On failure, first check:
        - A selector was added to parameters.py but never wired into scrape_book (add it to the
          relevant branch and to CONSUMED_DETAIL_KEYS), or a key was removed from one side only
    """
    all_detail_keys: set[str] = set()
    for entry in parameters.site_constants.values():
        all_detail_keys |= set(schema_detection.detail_selector_fields(entry))

    consumed = set(scrape_details.CONSUMED_DETAIL_KEYS)
    # Nothing in the consumed list is stale (every entry is a real detail selector key).
    assert consumed <= all_detail_keys
    # Everything defined is either consumed or a documented, reasoned exception.
    unconsumed = all_detail_keys - consumed
    assert unconsumed == set(_CONSUMED_ALLOW_LIST)
    # Each allow-list entry carries a one-line reason.
    assert all(reason for reason in _CONSUMED_ALLOW_LIST.values())
