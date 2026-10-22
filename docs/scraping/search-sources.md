# Search sources per site

This document records, per scrape target, **where the `bookscraper search` pipeline gets its
search-result candidates** - a public JSON endpoint called directly with `httpx`, or DOM scraping
of the rendered search page with Playwright - together with the evidence behind that choice and
whether `robots.txt` permits it.

It is the companion to the per-field selector reference in
[`schema-detection.md`](/docs/scraping/schema-detection.md): that doc covers *how* each field is
located once a page is in hand; this one covers *how the pipeline reaches a search page at all* and
why each site's `SEARCH_*` entry in `src/bookscraper/scraping/parameters.py` is shaped the way it
is.

Investigation method (per
[task 03.3 subtask 03](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/03-packtpub-and-oreilly-search.md)):
load a live search-results page with the project's own headless Chromium (the honest native
user-agent - no spoofing, per the task 03.2 "identify and back off, never disguise or bypass"
rule), log every response via `page.on("response")`, and check whether results are populated by a
visible JSON/XHR endpoint. The project's rate limiting and robots compliance
(`src/bookscraper/scraping/politeness.py`) stay in force throughout. Where a site actively blocks
automated access, that block is treated as the answer, not an obstacle to route around.

## Summary

| Site     | Source          | Reachable by automation? | robots.txt            | Status                         |
|----------|-----------------|--------------------------|-----------------------|--------------------------------|
| Leanpub  | JSON:API (httpx) | Yes                     | Allowed               | Live (`LeanpubApiProvider`)    |
| Amazon   | DOM (Playwright) | Yes                     | Allowed               | Live (`AmazonPlaywrightProvider`) |
| Packtpub | *undetermined*   | **No - Cloudflare wall** | Allowed (`Content-Signal: search=yes`) | **Blocked - no provider** |
| O'Reilly | *undetermined*   | **No - Akamai 403 wall** | **Unfetchable (403)** | **Blocked - no provider** |

## Leanpub - JSON:API (`httpx`, `needs_browser=False`)

Pre-existing and unchanged by this investigation; recorded here for completeness.

- **Endpoint:** `https://leanpub.com/api/v1/cache/simple_books.json`
  (`site_constants["leanpub"]["SEARCH_BASE_API"]`).
- **Parameters:** `search=<query>`, `page=<n>`, `page_size=100`, `sort=bestsellers_last_week`,
  `language=eng`, `include=accepted_authors`, plus the bookstore filter flags - see
  `get_leanpub_search_results_via_api` in `src/bookscraper/scraping/search_utils.py`.
- **Pagination:** 1-based `page`; stops when a page returns fewer than `page_size` results.
- **robots.txt:** allowed.

## Amazon - DOM scraping (Playwright, `needs_browser=True`)

Pre-existing (task 03.3 subtask 02); recorded here for completeness. Amazon search is not walled to
the project's headless Chromium, and its `SEARCH_*` selectors are fixture-validated. See
`AmazonPlaywrightProvider` / `get_search_results_via_playwright`.

## Packtpub - BLOCKED (Cloudflare managed challenge)

**Finding: no source could be validated. Every request to `www.packtpub.com` (and the candidate
API host `services.packtpub.com`) is intercepted by a Cloudflare "managed challenge" and returns
HTTP 403 before any application content or JSON is served.** This holds for the project's headless
Chromium and for a plain `httpx` client alike.

Evidence (captured 2026-10-16 from this environment):

- `GET https://www.packtpub.com/en-us/search?query=python` (headless Chromium, native UA) →
  **HTTP 403**, redirected to a `...&__cf_chl_rt_tk=<token>` URL, page `<title>` = `Just a
  moment...`, body ~29 KB consisting of the Cloudflare challenge runtime. `networkidle` never
  settles; the only XHR/`fetch` traffic is to `challenges.cloudflare.com` /
  `.../cdn-cgi/challenge-platform/...`, never a Packtpub application endpoint.
- The same challenge is served for the **home page** (`https://www.packtpub.com/`), a **product
  page**, and the plausible JSON host
  `https://services.packtpub.com/products-v1/search?query=python` - all HTTP 403 `Just a
  moment...`. The whole origin is walled, not just `/search`, so there is no un-challenged JSON API
  to fall back to. `static.packt-cdn.com` returns an S3 `AccessDenied`.
- The challenge DOM is the **newer** Cloudflare variant: it loads
  `script[src*='challenge-platform']` and titles itself `Just a moment...`, but does **not** expose
  the older `#challenge-form` / `#cf-challenge-running` elements that the project's current
  `GENERIC_BOT_WALL_RULES` in `parameters.py` match on (both matched 0 elements). See the follow-up
  note below.

**robots.txt:** `https://www.packtpub.com/robots.txt` is reachable (HTTP 200) and for `User-agent:
*` declares `Content-Signal: search=yes,ai-train=no,use=reference` with `Allow: /` and no
`Disallow: /search`. So robots *policy* permits fetching the search pages for a catalog/search use
(we are not an AI-training crawler, and are not one of the named training bots - `ClaudeBot`,
`GPTBot`, `anthropic-ai`, etc. - which are each `Disallow: /`). The block is enforced at the
Cloudflare edge regardless of robots policy.

Note: the committed offline fixture `tests/fixtures/robots/packtpub.txt` was hand-built on
2026-10-01 (live fetch 403 at the time) and carries a now-inaccurate `Disallow: /search`; the live
robots.txt is now reachable and allows `/`. Refreshing that fixture is a small, reachable follow-up
but is not required while no Packtpub provider exists.

## O'Reilly - BLOCKED (Akamai "Access Denied", plus subscription paywall)

**Finding: no source could be validated. Every request to `www.oreilly.com` returns an Akamai edge
HTTP 403 "Access Denied" with a ~300-byte error body - no challenge to pass, a hard refusal.**

Evidence (captured 2026-10-16 from this environment):

- `GET https://www.oreilly.com/search/?q=python` (headless Chromium, native UA) → **HTTP 403**,
  page `<title>` = `Access Denied`, 301-byte body, **zero** XHR/`fetch`/JSON responses.
- `GET https://www.oreilly.com/robots.txt` → **HTTP 403** Akamai "Access Denied" (reproduced via
  `curl` and `httpx`). The `robots.txt` itself is unfetchable, so under the project's conservative
  fail-closed `RobotsCache` policy (`politeness.py`: any non-2xx-non-404 robots response disallows
  the host for the run) O'Reilly would be disallowed on the robots gate alone, before any search
  request.
- Independently, `oreilly.com` is now a **paid subscription learning platform**: the
  `/library/view/<slug>/<isbn13>/` detail URLs the spec references sit behind a login/paywall.
  Reaching them for scraping would require credential/session use, which the project forbids.

**robots.txt:** unfetchable (403). Treated as disallow (fail closed).

## Decision

Both Packtpub and O'Reilly **actively block automated access** to the project's honest headless
Chromium:

- Packtpub via a Cloudflare managed challenge across the whole origin;
- O'Reilly via an Akamai hard 403 across the whole origin, compounded by a subscription paywall on
  the detail pages and an unfetchable `robots.txt`.

Passing either wall would require anti-bot evasion (solving/bypassing the Cloudflare challenge,
rotating/spoofing identity, or sharing a logged-in O'Reilly session), which the project's rules and
this role's charter forbid: *if a target actively blocks scraping, that block is the answer.*

Consequently **no `PacktpubProvider` or `OreillyProvider` is implemented**, the speculative
`SEARCH_*` selectors for these sites in `parameters.py` remain **unvalidated** (they could not be
checked against a page that never renders), and `SITES_TO_SCRAPE` is **not** extended to include
them - enabling them would only trip the per-domain circuit breaker on every run. See the subtask's
return notes for the recommended next step.

## Follow-up (`GENERIC_BOT_WALL_RULES` coverage)

The live Packtpub challenge is the newer Cloudflare variant (`script[src*='challenge-platform']` +
`<title>Just a moment...`), which the current generic bot-wall rule
(`#challenge-form, #cf-challenge-running`) does **not** detect. If/when any Cloudflare-fronted site
is scraped again, that rule should be widened to catch this variant (e.g. a `title_contains: "just
a moment"` rule). Recorded here so the gap is not lost; out of scope for this (blocked) subtask.
