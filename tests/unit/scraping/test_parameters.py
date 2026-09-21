"""Sanity/regression guards for bookscraper.scraping.parameters (pure data).

Not deep testing - just a tripwire against accidental corruption of the data that
drives every scrape: a truncated site_constants, an emptied USER_AGENTS, or a
flipped HEADLESS_BROWSER all silently change scraper behavior with no crash.
"""

from bookscraper.scraping import parameters


def test_headless_browser_is_false() -> None:
    assert parameters.HEADLESS_BROWSER is False


def test_sites_to_scrape_is_leanpub_only() -> None:
    assert parameters.SITES_TO_SCRAPE == ["leanpub"]


def test_site_constants_has_all_four_sites() -> None:
    assert set(parameters.site_constants) == {"amazon", "leanpub", "packtpub", "oreilly"}


def test_user_agents_non_empty_strings() -> None:
    assert len(parameters.USER_AGENTS) > 0
    assert all(isinstance(ua, str) and ua for ua in parameters.USER_AGENTS)


def test_min_rating_is_float() -> None:
    assert isinstance(parameters.SCRAPE_FILTERS["min_rating"], float)
