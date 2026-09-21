USER_AGENTS = [
    # Chrome (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36",

    # Firefox (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:92.0) Gecko/20100101 Firefox/92.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:93.0) Gecko/20100101 Firefox/93.0",

    # Edge (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36 Edg/92.0.902.55",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36 Edg/93.0.961.44",

    # Safari (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Safari/605.1.15",

    # Chrome (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",

    # Linux (Chrome/Firefox)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0",
]

HEADLESS_BROWSER = False

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

# Selectors and constants for each site
site_constants = {
    "amazon": {
        "BASE_URL": "https://www.amazon.com",
        ### SEARCH SELECTORS
        "SEARCH_BASE_URL": "https://www.amazon.com/s?i=stripbooks-intl-ship&k=[k]&page=[p]",
        "SEARCH_BOOK_CARD": '[data-component-type="s-search-result"]',
        "SEARCH_TITLE": 'h2.a-size-medium.a-spacing-none.a-color-base.a-text-normal > span',
        "SEARCH_AUTHORS": 'div.a-row.a-size-base.a-color-secondary div.a-row > a.a-link-normal',
        "SEARCH_PUBLICATION_DATE": 'div.a-row.a-size-base.a-color-secondary div.a-row > span.a-size-base.a-color-secondary.a-text-normal',
        "SEARCH_ASIN_ATTRIBUTE": 'data-asin',
        "SEARCH_BOOK_DETAIL_LINK": 'a.a-link-normal.s-line-clamp-2.s-link-style.a-text-normal',
        "SEARCH_NEXT_PAGE_SELECTOR": ".s-pagination-next",
        "SEARCH_NO_RESULTS_TEXT": 'div.s-no-results-box h1.a-size-large',
        "SEARCH_MAXIMUM_PAGES": 2,

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
    }
}
