from __future__ import annotations

# User-agent policy for Playwright browser contexts (see scraping/browser.py). Two honest modes
# only - no spoofed browser string is used to evade blocking (the task 03.2 "identify and back
# off, never disguise" rule):
#   "native" (default) - DO NOT override the user-agent at all. Playwright's bundled Chromium
#                         advertises an internally consistent identity (navigator.userAgent, the
#                         Sec-CH-UA client hints, and the request header all agree). This is the
#                         honest default and what the scraper uses unless explicitly changed.
#   "rotate"           - pick a user-agent from ``USER_AGENT_TEMPLATES`` filled with the INSTALLED
#                         Chromium major version (derived at runtime from ``browser.version``), so
#                         the header still matches the real engine. Used only when a run must vary
#                         the presented UA; it never advertises a different browser than the one
#                         actually driving the page.
USER_AGENT_MODE = "native"

# Rotate-mode user-agent templates. Each carries a single ``{major}`` placeholder filled at runtime
# with the installed Chromium major version (``scraping/browser.build_user_agents``), so a rotated
# header never claims a Chrome version the bundled engine is not. Chromium-family strings only -
# a Firefox/Safari string would contradict the Chromium engine actually rendering the page. The
# minor/build/patch segments are reduced to ``0.0.0`` (the modern reduced-UA convention).
USER_AGENT_TEMPLATES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36",
]

# Fallback Chromium major used to fill a rotate-mode template when ``browser.version`` cannot be
# parsed into a leading integer. A conservative recent major; the engine still agrees in practice
# because rotate-mode UAs are Chromium-family regardless of the exact number.
DEFAULT_CHROMIUM_MAJOR = 120

# Default to headless: headed mode is a debugging choice (opt in with --no-headless or
# BOOKSCRAPER_HEADLESS=0), and a scheduled or CI run must never try to open a window. The CLI's
# --headless/--no-headless flag and the BOOKSCRAPER_HEADLESS env var both override this default via
# runtime.runtime_options().
HEADLESS_BROWSER = True

# Per-domain request rate limits consumed by the politeness layer (scraping/politeness.py).
# Each value is ``(requests_per_second, burst)``: the steady-state per-host request rate and the
# number of requests allowed back-to-back before throttling kicks in. Keyed on the registrable
# host with any leading ``www.`` stripped (see ``DomainRateLimiters.for_url``). Conservative by
# design - being slow is always acceptable, being blocked is not. Edit these (not the limiter
# code) to re-tune a site's politeness.
RATE_LIMITS: dict[str, tuple[float, int]] = {
    "amazon.com": (0.5, 1),
    "packtpub.com": (1.0, 2),
    "oreilly.com": (1.0, 2),
    "leanpub.com": (2.0, 4),
}

# Fallback rate for any host not listed in ``RATE_LIMITS`` - the most conservative entry, so an
# unrecognized host is throttled hardest rather than hammered.
DEFAULT_RATE_LIMIT: tuple[float, int] = (0.5, 1)

# SEARCH_* keys in a ``site_constants`` entry that are NOT CSS selectors: a base search
# URL / API endpoint, a page-count integer, or an attribute NAME (``data-asin``). Every
# other ``SEARCH_*`` value is a CSS selector - including ``SEARCH_NO_RESULTS_TEXT`` /
# ``SEARCH_NO_BOOKS_FOUND``, which despite their names select the "no results" marker
# element. This is the single source of truth shared by ``schema_detection`` (the
# ``detect-schema --page-type search`` field set) and ``scripts/capture_fixtures.py`` (the
# search-fixture keep-selector set) so the two classifications can never drift apart.
NON_SELECTOR_SEARCH_KEYS = frozenset(
    {"SEARCH_BASE_URL", "SEARCH_BASE_API", "SEARCH_MAXIMUM_PAGES", "SEARCH_ASIN_ATTRIBUTE"}
)

# Selector keys that may legitimately match NO element on a healthy page - interaction/expander
# affordances, auxiliary blocks the record can be built without, and conditionally-present search
# markers. ``detect-schema``'s drift check (``--check-current-only``) reports these as
# ``absent_optional`` rather than ``broken`` and never exits 4 on them, so a scheduled CI gate is
# not tripped by a selector that is optional by design. Anything the scraper NEEDS to extract a
# record (title, authors, ISBN-13, publication date, description, the search card + its title /
# detail-link) is deliberately NOT here and stays gating. Kept minimal and checked against every
# site's ``site_constants`` entry below.
OPTIONAL_SELECTOR_KEYS = frozenset(
    {
        # Detail-page interaction/expander affordances: clicking them only reveals content that is
        # already in the DOM (or available via a fallback), so their absence is not record drift.
        "DETAILS_BUTTON",  # amazon "Product details" expander button
        "READ_MORE_LINK",  # packtpub/oreilly description "read more" expander
        # Auxiliary amazon block used only as one way to locate the ASIN; the ASIN also comes from
        # the page URL / search ``data-asin``, so the detail-page block is not required.
        "ASIN_DETAIL_PAGE_SELECTOR",
        # Search pagination: a listing's last (or only) page legitimately has no "next" control, so
        # a non-match is an expected terminal condition, not drift. Pagination is navigational, not
        # part of extracting any single record.
        "SEARCH_NEXT_PAGE_SELECTOR",
        # Empty-results markers: present ONLY on a zero-results search page, so on a populated
        # sample page they correctly match nothing.
        "SEARCH_NO_RESULTS_TEXT",
        "SEARCH_NO_BOOKS_FOUND",
        # Search-card rating/review-count: a book with no reviews carries neither, so a card (or a
        # whole page) matching nothing is expected, not drift.
        "SEARCH_RATING",
        "SEARCH_RATING_COUNT",
    }
)

# Ensure these match the keys in site_constants.
# SITES_TO_SCRAPE = ["amazon", "leanpub", "packtpub", "oreilly"]
# SITES_TO_SCRAPE = ["amazon", "leanpub"]
SITES_TO_SCRAPE = ["leanpub"]
# SITES_TO_SCRAPE = ["amazon"]

# Define search queries. Each query can specify its own max_search_pages.
# Note: At time of latest update, amazon only allowed 75 search result pages
#       To possibly get more overall searches, one can change the order of the results (Featured, by publication date, Price, etc.)
SEARCH_QUERIES = [
    "Python",
    "Data Science with Python",
    # "Playwright Python",
    # Add more queries as needed
]

# Filters to apply during the scraping process.
SCRAPE_FILTERS = {
    "enable_rating_filter": True,
    "min_rating": 3.5,  # Minimum rating for books to be considered
    "include_new_books_without_rating": True,  # If True, books with no rating (0.0) will be included
    "min_isbns_required": 1,  # Minimum number of ISBNs to consider a book's ISBNs "complete"
}

# Generic bot-wall / anti-bot interstitial detection rules applied to EVERY site (subtask
# 03.2/04). Each rule is either ``{"title_contains": str}`` (a case-insensitive page-title
# substring) or ``{"selector": str}`` (a CSS selector that matches an element only on the
# interstitial). This generic set catches the Cloudflare "challenge" page every host can serve;
# per-site ``BOT_WALL`` lists below extend it with that site's own CAPTCHA markers. A positive
# match makes ``scrape_details.scrape_book`` return ``ScrapeStatus.BLOCKED`` with no retry - the
# project's "identify and back off, never disguise or bypass" rule - and trips the per-domain
# circuit breaker. Edit these (not the detection code) to tune bot-wall recognition.
GENERIC_BOT_WALL_RULES: list[dict[str, str]] = [
    {"selector": "#challenge-form, #cf-challenge-running"},
    # Newer Cloudflare "managed challenge" variant (task 03.3/03 investigation): it titles itself
    # "Just a moment..." and loads the challenge-platform runtime script, but exposes NEITHER of the
    # older #challenge-form / #cf-challenge-running elements above (both matched 0 elements against
    # the live Packtpub wall). Either marker - the title or the script tag - identifies it.
    {"title_contains": "Just a moment"},
    {"selector": "script[src*='challenge-platform']"},
]

# Selectors and constants for each site
site_constants = {
    "amazon": {
        "BASE_URL": "https://www.amazon.com",
        # Amazon's "Robot Check" CAPTCHA interstitial: a 200 page with no #productTitle. Matched
        # by its title and its captcha form, plus the shared Cloudflare rule.
        "BOT_WALL": [
            {"title_contains": "Robot Check"},
            {"selector": 'form[action*="validateCaptcha"]'},
            *GENERIC_BOT_WALL_RULES,
        ],
        ### SEARCH SELECTORS
        "SEARCH_BASE_URL": "https://www.amazon.com/s?i=stripbooks-intl-ship&k=[k]&page=[p]",
        "SEARCH_BOOK_CARD": '[data-component-type="s-search-result"]',
        "SEARCH_TITLE": "h2.a-size-medium.a-spacing-none.a-color-base.a-text-normal > span",
        "SEARCH_AUTHORS": "div.a-row.a-size-base.a-color-secondary div.a-row > a.a-link-normal",
        "SEARCH_PUBLICATION_DATE": "div.a-row.a-size-base.a-color-secondary div.a-row > span.a-size-base.a-color-secondary.a-text-normal",
        "SEARCH_ASIN_ATTRIBUTE": "data-asin",
        "SEARCH_BOOK_DETAIL_LINK": "a.a-link-normal.s-line-clamp-2.s-link-style.a-text-normal",
        # Search-card rating (optional - a book with no reviews has none). The rating text is
        # Amazon's "4.6 out of 5 stars" icon-alt; the count is the sibling underline span.
        "SEARCH_RATING": "span.a-icon-alt",
        "SEARCH_RATING_COUNT": "span.s-underline-text",
        "SEARCH_NEXT_PAGE_SELECTOR": ".s-pagination-next",
        "SEARCH_NO_RESULTS_TEXT": "div.s-no-results-box h1.a-size-large",
        # Amazon documents a hard limit of 75 search-result pages per query; the search provider
        # (task 03.3/02) clamps any requested --max-search-pages to this and logs when it does.
        "SEARCH_MAXIMUM_PAGES": 75,
        ### BOOK DETAIL PAGE SELECTORS
        "404_PAGE_TITLE": "page not found",  # Part of title to check for 404 pages
        "BOOK_TITLE": "#productTitle",
        "AUTHORS": ".author a.a-link-normal",
        "AUTHORS_ALT": "#bylineInfo .a-link-normal",  # Alternative for less structured author lines
        "ASIN_DETAIL_PAGE_SELECTOR": "#regulatoryDeposit_feature_div",
        "ISBN10": "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span",
        # Usually inside #detailBullets_feature_div or #productDetails_techSpec_section_1
        "ISBN13": "#rpi-attribute-book_details-isbn13 .rpi-attribute-value span",
        # Usually inside #detailBullets_feature_div or #productDetails_techSpec_section_1
        "PUBLICATION_DATE": "#rpi-attribute-book_details-publication_date .rpi-attribute-value span, #rpi-attribute-audiobook_details-release-date .rpi-attribute-value span",
        "DESCRIPTION": "#bookDescription_feature_div .a-expander-content",  # Main description block
        "DESCRIPTION_ALT": "#productDescription p",  # Fallback for simpler descriptions
        "TAGS": "#detailBulletsWrapper_feature_div ul.zg_hrsr a",
        # Often found in 'Customers who bought this item also bought' or similar sections for categories
        "DETAILS_BUTTON": "a.a-expander-header:has-text('Product details'), a.a-expander-header:has-text('See more product details'), a.a-expander-header:has-text('Product information')",
    },
    "leanpub": {
        "BASE_URL": "https://leanpub.com",
        # Leanpub has no site-specific CAPTCHA marker; only the shared Cloudflare rule applies.
        "BOT_WALL": [*GENERIC_BOT_WALL_RULES],
        ### SEARCH SELECTORS
        "SEARCH_BASE_API": "https://leanpub.com/api/v1/cache/simple_books.json",
        "SEARCH_BASE_URL": "https://leanpub.com/bookstore?search=",
        "SEARCH_NO_BOOKS_FOUND": "div.BookstoreContent__NoResults",
        "SEARCH_BOOK_CARD": "li.BookListItem.ListItem",
        "SEARCH_BOOK_DETAIL_LINK": "a.ListItem__Text",
        "SEARCH_TITLE": "h3.ListItem__Title",
        "SEARCH_AUTHORS": "div.names",
        "SEARCH_NEXT_PAGE_SELECTOR": "section.pagination-wrapper button.btn--plain",
        ### BOOK DETAIL PAGE SELECTORS
        # Single Book API Endpoint: https://leanpub.com/api/v1/cache/books/mastering_modern_time_series_forecasting.json?include=accepted_authors
        "SINGLE_BOOK_API": "https://leanpub.com/api/v1/cache/books/[slug].json",
        "404_PAGE_TITLE": "page not found",
        "BOOK_TITLE": ".book-hero__title.ltr",
        "AUTHORS_META": 'meta[name="author"]',
        "AUTHORS": ".avatar-with-name__name",
        "AUTHORS_ALT": ".book-authors span",  # If not linked
        "ISBN10": None,  # Leanpub often doesn't prominently display ISBNs
        "ISBN13": None,  # Needs investigation, might be in a meta tag or specific details section
        "PUBLICATION_DATE_META": 'meta[name="DCTERMS.date"]',
        "PUBLICATION_DATE": ".last-updated",
        "DESCRIPTION": ".about-book__content",
        "TAGS": ".tag-list a",  # Or similar for topics/tags
    },
    "packtpub": {
        # No site-specific CAPTCHA marker captured yet; only the shared Cloudflare rule applies.
        "BOT_WALL": [*GENERIC_BOT_WALL_RULES],
        "SEARCH_BASE_URL": "https://www.packtpub.com/search?query=",
        "SEARCH_RESULT_ITEM_SELECTOR": ".product-result-item",  # Each product in search results
        "SEARCH_BOOK_LINK_SELECTOR": "a.product-result-info-link",
        "SEARCH_TITLE_SELECTOR": "div.product-result-title",
        "SEARCH_AUTHOR_SELECTOR": ".product-result-author",
        "SEARCH_RATING_SELECTOR": ".rating-value",  # Or similar, might be a number
        "SEARCH_NEXT_PAGE_SELECTOR": "a.next-page-link",  # Or similar pagination
        "404_PAGE_TITLE": "page not found",
        "BOOK_TITLE": "h1.product-title",
        "AUTHORS": "div.authors span",
        "AUTHORS_ALT": ".product-page-author a",
        "ISBN10": None,  # Packtpub usually only has ISBN-13
        "ISBN13": "div.product-page-rhs.desktop span.product-details-section-key:has-text('ISBN-13 :') + span.product-details-section-value",
        "TAGS": "div.product-page-rhs.desktop a",  # Example: tags under "Related Titles" or "Subjects"
        "PUBLICATION_DATE": "div.product-page-rhs.desktop span.product-details-section-key:has-text('Publication date :') + span.product-details-section-value",
        "DESCRIPTION": ".product-book-content-details h2:has-text('Description') + div.content-text",
        "READ_MORE_LINK": "",  # Usually no expander
    },
    "oreilly": {
        # No site-specific CAPTCHA marker captured yet; only the shared Cloudflare rule applies.
        "BOT_WALL": [*GENERIC_BOT_WALL_RULES],
        "SEARCH_BASE_URL": "https://www.oreilly.com/search/?q=",
        "SEARCH_RESULT_ITEM_SELECTOR": "article.product-card",  # Each product card
        "SEARCH_BOOK_LINK_SELECTOR": "a.product-card__content-link",
        "SEARCH_TITLE_SELECTOR": ".product-card__title",
        "SEARCH_AUTHOR_SELECTOR": ".product-card__author",
        "SEARCH_RATING_SELECTOR": ".star-rating__average",  # Numeric rating
        "SEARCH_NEXT_PAGE_SELECTOR": ".pagination__next a",
        "404_PAGE_TITLE": "page not found",  # Needs confirmation for O'Reilly
        "BOOK_TITLE": "h1.t-title",
        "AUTHORS": "a.author-name",
        "AUTHORS_ALT": "a[data-toggle='tooltip']",  # If author links have tooltips
        # "ISBN": 'div.t-isbn',
        "ISBN10": None,
        "ISBN13": 'li:has-text("ISBN:") > span.value.t-isbn',
        "PUBLICATION_DATE": 'li:has-text("Release date:") > span.value',
        "DESCRIPTION": 'h2.t-description-heading:has-text("Book description") + span',
        "DESCRIPTION_ALT": "div.read-more-wrapper",
        "TAGS": ".topic-link",  # Example: topics listed on the page
        "READ_MORE_LINK": "a.read-more-link",  # For description expanders
    },
}
