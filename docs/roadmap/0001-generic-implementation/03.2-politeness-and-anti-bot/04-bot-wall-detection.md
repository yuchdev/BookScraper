# 04 - Bot-wall detection & circuit breaker

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Depends on:** [03-shared-backoff-and-retry-after.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/03-shared-backoff-and-retry-after.md)
**Role:** Scraping Expert

## Context

When Amazon serves its "Robot Check" interstitial, `scrape_book()` sees a 200 page with no `#productTitle`. Before
task 03.0 that meant three retries and `FAILED`. After 03.0 it means `PARSE_ERROR`, which is still wrong: it hides
the fact that the site is now blocking the whole run.

## Requirements

- `site_constants[site]["BOT_WALL"]`: a list of detection rules per site. Each rule is
  `{"title_contains": str}` or `{"selector": str}`. Seed values (verify against captured fixtures from 06.0):
  - amazon: title contains `"Robot Check"`; selector `form[action*="validateCaptcha"]`.
  - all sites: selector `#challenge-form, #cf-challenge-running` (generic Cloudflare interstitial).
- `scrape_details._detect_bot_wall(page, site) -> bool`, checked right after navigation and before the 404 check.
  Positive → `ScrapeResult(BLOCKED)`, **no retry**.
- `politeness.CircuitBreaker(threshold=3, cooldown_s=900)` per domain: after `threshold` consecutive `BLOCKED`
  results, it opens, and every further URL for that domain returns `BLOCKED` immediately
  (`error="circuit open"`) without a request, for the rest of the run (cooldown longer than any realistic run).
  A `SUCCESS` resets the counter.
- When a breaker opens, print one `print_log(..., "warning")` naming the domain and how many URLs will be skipped.
- `detect-schema` (task 04.0) must also refuse to send a bot-wall page to Claude, raising `SchemaDetectionError`
  with "bot wall detected".

## Files

- Modify `src/bookscraper/scraping/parameters.py`, `scrape_details.py`, `politeness.py`, `schema_detection.py`.
- Fixtures: `tests/fixtures/html/amazon/_bot_wall.html` (captured and sanitized; see task 06.0).
- Tests: `tests/unit/scraping/test_politeness.py`, `tests/mock/scraping/test_scrape_details.py`,
  `tests/mock/scraping/test_schema_detection.py`, `tests/unit/scraping/test_parameters.py`.

## Tests

- `test_amazon_robot_check_page_returns_blocked_without_retry`
- `test_cloudflare_challenge_detected_on_any_site`
- `test_circuit_opens_after_three_consecutive_blocks`
- `test_circuit_open_skips_requests_entirely`
- `test_success_resets_block_counter`
- `test_detect_schema_refuses_bot_wall_page`
- `test_every_site_has_bot_wall_rules` (parameters sanity)

## Success criteria

- [ ] A bot-wall page can never be saved as a book, or sent to the Anthropic API.
