# 01 - Capture & sanitize script

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Role:** Scraping Expert → Security Auditor (sanitizer review)

## Requirements

- `scripts/capture_fixtures.py` (a dev tool; outside the coverage scope, but its pure sanitizer functions live in
  `src/bookscraper/scraping/sanitize.py` and are unit-tested):

  ```text
  uv run python scripts/capture_fixtures.py --site amazon --kind detail \
      --url https://www.amazon.com/dp/1098131029 --name python_crash_course_3e
  uv run python scripts/capture_fixtures.py --site leanpub --kind api \
      --url "https://leanpub.com/api/v1/cache/books/<slug>.json?include=accepted_authors" --name <slug>
  ```

  - `--kind {detail,search,api,robots}`. `detail` / `search` use headless Chromium with the **same** context
    factory and `route_handler` as the scraper (so the captured DOM equals what production sees). It waits for
    `domcontentloaded` plus the site's title selector (bounded), then takes `page.content()`. `api` / `robots` use
    httpx.
  - Writes `tests/fixtures/{html|json|robots}/<site>/<name>.<ext>` and `<name>.meta.json`:
    `{"url", "site", "kind", "captured_at" (UTC), "chromium_version", "bookscraper_version", "sha256",
    "bytes_raw", "bytes_sanitized"}`.
  - Refuses to overwrite an existing fixture without `--force`.
- `sanitize.py` (pure functions):
  - `strip_scripts_and_styles(html)`: removes `<script>` (except `type="application/ld+json"`, which may hold
    metadata worth keeping), `<style>`, `<noscript>`, `<iframe>`, inline `on*=` handlers.
  - `strip_tracking(html)`: removes `<img>`/`<link>` to known tracker hosts and query params `ref=`, `tag=`,
    `pd_rd_*`, `pf_rd_*`, `qid=`, `sr=`, `crid=`.
  - `strip_secrets(html)`: removes `<input type="hidden">` values, `csrf`/`token`/`session` named attributes and
    meta tags, email addresses (regex → `user@example.invalid`), and `data-*` attributes over 200 chars (opaque
    blobs).
  - `shrink(html, keep_selectors: list[str])`: optional. Drops large subtrees (reviews, recommendations carousels)
    that contain none of `keep_selectors`, so the file fits the size budget.
  - JSON: `sanitize_json(obj)` redacts keys matching `/(email|token|session|password)/i`.
- A final guard, `assert_clean(text)`: fails the capture if any of the secret-scan patterns from
  `.claude/skills/secret-scan/references/pattern-catalog.md` still match.

## Files

- Create `scripts/capture_fixtures.py`, `src/bookscraper/scraping/sanitize.py`.
- Create `tests/unit/scraping/test_sanitize.py`.

## Tests

- `test_scripts_removed_but_ld_json_kept`
- `test_inline_event_handlers_removed`
- `test_tracking_query_params_stripped_from_links`
- `test_hidden_input_values_and_csrf_meta_removed`
- `test_emails_replaced_with_invalid_domain`
- `test_shrink_keeps_subtrees_containing_selectors`
- `test_sanitize_json_redacts_sensitive_keys`
- `test_assert_clean_rejects_known_secret_patterns`

## Success criteria

- [ ] `/secret-scan tests/fixtures/` is clean after every capture.
- [ ] Security-auditor sign-off on the sanitizer rules.
