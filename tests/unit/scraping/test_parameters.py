"""Sanity/regression guards for bookscraper.scraping.parameters (pure data).

Not deep testing - just a tripwire against accidental corruption of the data that
drives every scrape: a truncated site_constants, an emptied USER_AGENTS, or a
flipped HEADLESS_BROWSER all silently change scraper behavior with no crash.
"""

from bookscraper.scraping import parameters


def test_headless_browser_is_false() -> None:
    """
    [Unit] parameters: verifies headless browser is false.

    Scenario:
        Given test inputs and fixtures for headless browser is false
        When test_headless_browser_is_false executes the target flow
        Then the expected outcome for headless browser is false is confirmed.

    Boundaries:
        - Focus: headless browser is false
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to headless browser is false
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by parameters
    """
    assert parameters.HEADLESS_BROWSER is False


def test_sites_to_scrape_is_leanpub_only() -> None:
    """
    [Unit] parameters: verifies sites to scrape is leanpub only.

    Scenario:
        Given test inputs and fixtures for sites to scrape is leanpub only
        When test_sites_to_scrape_is_leanpub_only executes the target flow
        Then the expected outcome for sites to scrape is leanpub only is confirmed.

    Boundaries:
        - Focus: sites to scrape is leanpub only
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to sites to scrape is leanpub only
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by parameters
    """
    assert parameters.SITES_TO_SCRAPE == ["leanpub"]


def test_site_constants_has_all_four_sites() -> None:
    """
    [Unit] parameters: verifies site constants has all four sites.

    Scenario:
        Given test inputs and fixtures for site constants has all four sites
        When test_site_constants_has_all_four_sites executes the target flow
        Then the expected outcome for site constants has all four sites is confirmed.

    Boundaries:
        - Focus: site constants has all four sites
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to site constants has all four sites
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by parameters
    """
    assert set(parameters.site_constants) == {"amazon", "leanpub", "packtpub", "oreilly"}


def test_user_agents_non_empty_strings() -> None:
    """
    [Unit] parameters: verifies user agents non empty strings.

    Scenario:
        Given test inputs and fixtures for user agents non empty strings
        When test_user_agents_non_empty_strings executes the target flow
        Then the expected outcome for user agents non empty strings is confirmed.

    Boundaries:
        - Focus: user agents non empty strings
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to user agents non empty strings
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by parameters
    """
    assert len(parameters.USER_AGENTS) > 0
    assert all(isinstance(ua, str) and ua for ua in parameters.USER_AGENTS)


def test_min_rating_is_float() -> None:
    """
    [Unit] parameters: verifies min rating is float.

    Scenario:
        Given test inputs and fixtures for min rating is float
        When test_min_rating_is_float executes the target flow
        Then the expected outcome for min rating is float is confirmed.

    Boundaries:
        - Focus: min rating is float
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to min rating is float
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by parameters
    """
    assert isinstance(parameters.SCRAPE_FILTERS["min_rating"], float)
