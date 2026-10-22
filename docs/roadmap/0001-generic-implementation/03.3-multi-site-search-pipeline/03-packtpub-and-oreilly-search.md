# 03 - Packtpub & O'Reilly search providers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ✅ Complete (descoped - see Investigation outcome; 2026-10-22, user-ratified)
**Depends on:** [01-search-provider-registry.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/01-search-provider-registry.md)
**Role:** Scraping Expert

## Context

The `SEARCH_*` selectors for Packtpub and O'Reilly carry comments like `# Or similar` and `# Example`. They were
never validated. Both sites render search results client-side, and may expose a JSON endpoint that is cheaper and
more stable than DOM scraping.

## Requirements

1. **Investigate first** (time-boxed, 1 day per site), recorded in `docs/scraping/search-sources.md`:
   - Does the search page call a public JSON endpoint (visible in the Playwright network log)? Record the URL
     pattern, parameters, pagination, and whether `robots.txt` allows it.
   - If yes → implement an httpx provider like Leanpub (`needs_browser=False`).
   - If no → Playwright provider. Validate every `SEARCH_*` selector with `detect-schema` (requires task 04.0
     subtask 06's search-page support) against at least two query result pages.
2. `PacktpubProvider` / `OreillyProvider` emitting `SearchCandidate`s with title, authors, detail URL, rating
   where available, and ISBN-13 when the listing exposes it (O'Reilly URLs embed it:
   `/library/view/<slug>/<isbn13>/`).
3. `already_stored`: no site-native ID exists, so check by `url` via a new contract method
   `StorageBackend.book_exists_by_url(url) -> bool`, implemented in both backends, with a sparse index in Mongo
   (extends task 01.0 subtask 06) and parity scenarios (extends task 01.0 subtask 08).
4. Update `parameters.py` with the verified selectors or endpoints and delete the speculative comments.

## Files

- Create `docs/scraping/search-sources.md`.
- Modify `src/bookscraper/scraping/search_providers.py`, `search_utils.py`, `parameters.py`,
  `src/bookscraper/backends/storage.py`, `backends/mongo/database.py`, `backends/local/store.py`.
- Fixtures: `tests/fixtures/{html,json}/{packtpub,oreilly}/search_*.{html,json}`.
- Tests: `tests/mock/scraping/test_search_providers.py`, `tests/mock/backends/test_backend_parity.py`.

## Tests

- `test_packtpub_search_parses_fixture_page`
- `test_oreilly_search_parses_fixture_page`
- `test_oreilly_candidate_extracts_isbn13_from_url`
- `test_pagination_stops_at_last_page` (per provider)
- `test_book_exists_by_url_parity` (both backends)

## Investigation outcome (2026-10-22, user-ratified)

Step 1's live investigation (see [`docs/scraping/search-sources.md`](/docs/scraping/search-sources.md)) found
**both sites hard-block automated access**, independent of `robots.txt`:

- **Packtpub:** every request - including the plausible `services.packtpub.com` API host - is intercepted by a
  Cloudflare managed challenge (`HTTP 403`, `<title>Just a moment...</title>`), from both the project's headless
  Chromium and a plain `httpx` client. `robots.txt` itself *allows* search (`Content-Signal: search=yes`); the
  block is enforced at the Cloudflare edge regardless.
- **O'Reilly:** a hard `HTTP 403` (Akamai) on the search page itself, and `robots.txt` also returns `403` -
  unfetchable, so the project's fail-closed `RobotsCache` policy disallows it on the robots gate alone. The site is
  now a paywalled subscription platform.

Passing either wall would require solving/bypassing a Cloudflare challenge, identity spoofing, or a logged-in
session - all excluded by the project's "identify and back off, never disguise or bypass" rule (task 03.2). No
selector was fabricated, no endpoint was guessed, and no fixture was captured behind either wall.

**Decision: descope both providers.** `PacktpubProvider`/`OreillyProvider` are not implemented.
`SITES_TO_SCRAPE` stays `["leanpub", "amazon"]` (the original success criterion below is superseded and will not
be met). Their existing `SEARCH_*` entries in `parameters.py` stay as the pre-existing speculative placeholders -
`site_constants` still carries them for a future reattempt, but nothing reads them. Detail-page scraping for
Packtpub/O'Reilly via `scrape-urls` (task 03.0) is unaffected; only *search discovery* for these two sites is
closed off.

Three wall-independent pieces still land under this subtask, since they are needed regardless of how (or whether)
Packtpub/O'Reilly search is ever reattempted:
1. `StorageBackend.book_exists_by_url(url) -> bool` on both backends (a sparse `url` index in mongo, the json
   in-memory index), with parity test coverage. Pure infra for any future URL-keyed provider.
2. Widen `GENERIC_BOT_WALL_RULES` to also recognize this newer Cloudflare "Just a moment..." challenge variant
   (title text + the `challenge-platform` script), since any site could serve it, with a fixture.
3. Refresh the stale `tests/fixtures/robots/packtpub.txt` fixture: the committed copy has `Disallow: /search`,
   which the live file no longer has.

## Success criteria (superseded - see Investigation outcome above)

- [x] `docs/scraping/search-sources.md` documents the chosen source for each site, with evidence.
- [ ] ~~`SITES_TO_SCRAPE` default becomes `["leanpub", "amazon", "packtpub", "oreilly"]`~~ - not achievable without
      evasion; `SITES_TO_SCRAPE` stays `["leanpub", "amazon"]`.
