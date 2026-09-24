# 05 - Leanpub API detail hardening

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-typed-scrape-result.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/02-typed-scrape-result.md)
**Role:** Python Expert

## Context

`get_leanpub_book_details()` is the entire detail path for the only site `search` supports end-to-end, and it is
fragile:

- `unescape(attributes.get("about_the_book"))` raises `TypeError` when the field is absent or `null`, and the
  catch-all turns that into "unexpected error" and a lost book.
- The `HTTPStatusError` branch prints `e.response.text` (the full response body) to stdout.
- The `JSONDecodeError` branch references `response`, which is unbound if the failure happened before assignment.
- A new `httpx.AsyncClient` per book defeats connection reuse across hundreds of concurrent calls.
- HTML-to-text conversion is five ad hoc regexes, duplicated nowhere else, and not unit-testable in isolation.

## Requirements

- Extract `html_to_text(html: Optional[str]) -> str` into `scraping/text.py` (pure, unit-tested). It handles
  `None` → `""`, `<br>` → newline, `</p>` → blank line, `</li>` → newline, strips remaining tags, unescapes entities
  **after** stripping tags, and collapses whitespace. It is reused later by 03.1 for Playwright descriptions.
- `get_leanpub_book_details(url, client: Optional[httpx.AsyncClient] = None)`: when a client is passed, reuse it.
  `commands/search.py` creates **one** `AsyncClient` per run (with `timeout=httpx.Timeout(30.0)`,
  `limits=httpx.Limits(max_connections=10)`) and passes it in.
- Error reporting: HTTP status → `ScrapeResult(FAILED, error="HTTP <code>")`, body never included; transport error →
  `error="<ExcType>"`; JSON decode → `error="invalid JSON"`. All go through `module_logger` / `print_log`.
- A missing `data` key → `PARSE_ERROR`. A missing `title` → `PARSE_ERROR`.

## Files

- Create `src/bookscraper/scraping/text.py`.
- Modify `src/bookscraper/scraping/scrape_details.py`, `src/bookscraper/commands/search.py`.
- Create `tests/unit/scraping/test_text.py`; modify `tests/mock/scraping/test_scrape_details.py`,
  `tests/mock/commands/test_search.py`.

## Tests

- `test_html_to_text_none_is_empty`, `test_html_to_text_paragraphs_and_lists`, `test_html_to_text_unescapes_after_strip`
- `test_leanpub_missing_about_the_book_still_succeeds`
- `test_leanpub_http_error_reports_status_without_body`
- `test_leanpub_invalid_json_is_failed_not_unbound_error`
- `test_leanpub_missing_data_is_parse_error`
- `test_search_reuses_single_async_client` (patch `httpx.AsyncClient`; constructed once for N books)

## Success criteria

- [ ] No Leanpub API response body can reach stdout or the log.
- [ ] 06.0 Leanpub JSON fixtures pass unchanged, except `about_the_book` whitespace, which is explained in the PR.
