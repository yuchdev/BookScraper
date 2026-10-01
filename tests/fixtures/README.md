# Test fixture corpus

A versioned corpus of **real, captured, sanitized** pages used to run the unmodified
scraper against known bytes in a fully offline test harness (task 06.0). HTML fixtures
feed the Playwright detail/search paths; JSON fixtures feed the Leanpub `httpx` API
path. Golden records (`*.expected.json`, added in subtask 04) capture the expected
parser output.

## Purpose

These files exist **solely for interoperability testing** of `bookscraper` against the
minimized page/response structure its selectors and parsers depend on. They are **not a
redistribution** of the source sites' content. Every fixture is reduced to the minimum
structure a selector needs; long free-form prose is neutralized (below).

This is the recorded answer to the threat model's FIX-12 licensing question
(`docs/security/2026-09-30-real-html-test-corpus.md`).

## Layout

```text
tests/fixtures/
  README.md                        this policy
  html/<site>/<name>.html          sanitized DOM snapshots (Playwright sites)
  html/<site>/<name>.meta.json     provenance + integrity metadata
  html/<site>/<name>.expected.json golden record (subtask 04)
  json/leanpub/<name>.json         Leanpub API responses (+ .meta.json, .expected.json)
  robots/<site>.txt                captured robots.txt (subtasks 01/07)
  schema_detection/…               task 04.0 subtask 07
```

**Naming** is `snake_case`. Detail pages are named after the book; search pages are
`search_<query>_p<N>`; edge cases get a leading underscore (`_404`, `_bot_wall`).

## Budgets (enforced by `tests/unit/test_fixture_corpus.py`)

- **Per file: 400 KB.** Keep each fixture to the minimum structure the selectors touch.
- **Whole corpus: 15 MB.**

Both are also enforced at capture time by `scripts/capture_fixtures.py` (FIX-11).

## Integrity & provenance

- Every fixture has a sibling `.meta.json`; a fixture without one, or a `.meta.json` /
  `.expected.json` without its fixture, fails a corpus test.
- `.meta.json` records `url`, `site`, `kind`, `captured_at`, `chromium_version`,
  `bookscraper_version`, `sha256` (of the sanitized bytes), `bytes_raw`,
  `bytes_sanitized`, and the audit fields `logged_out: true` and `sanitizer_version`
  (FIX-14). `test_meta_sha256_matches_file` fails if a fixture is hand-edited after
  capture - this is the compensating control for the `-diff` gitattribute on `*.html`
  (FIX-13), together with the secret-scan gate and the diffable goldens.

## Ratified policy decisions (2026-09-30, `yuchdev/BookScraper` is a PUBLIC repo)

1. **Prose neutralization.** Fixtures keep the real markup and structure the selectors
   need, and keep factual metadata (title, authors, ISBN, publication date, publisher,
   categories). Long free-form prose - descriptions, reviews, blurbs, author bios,
   `about_the_book` - is replaced with a neutral placeholder
   (`Neutralized prose omitted from the public test corpus.`) by
   `src/bookscraper/scraping/sanitize.py`. No copyrighted long-form text reaches the
   public repo.
2. **robots.txt during capture.** The one-off capture of a handful of pages was done
   **regardless of `robots.txt`**, by the user's explicit decision. It was still run
   headless, logged out, cookie-less, and rate-limited (one request at a time, a fixed
   delay between requests to the same site). This supersedes the threat model's FIX-7
   "robots honoured during capture" clause for this capture only.

## How to capture, sanitize, and refresh

Capture routes every byte through the fail-closed sanitizer + `assert_clean` gate
(`docs/security/2026-09-30-real-html-test-corpus.md`, FIX-1/2/5/7/11/14):

```bash
uv run python scripts/capture_fixtures.py --site amazon --kind detail \
    --url https://www.amazon.com/dp/0321714113 --name cpp_primer_5e
uv run python scripts/capture_fixtures.py --site leanpub --kind api \
    --url "https://leanpub.com/api/v1/cache/books/<slug>.json?include=accepted_authors" \
    --name <slug>
```

See subtask 01 (`docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/01-capture-and-sanitize-script.md`)
for the capture/sanitize tool and subtask 07 (…/07-fixture-refresh-workflow.md) for the
refresh/drift workflow.

**macOS 13 platform note.** Playwright 1.63 refuses to download or launch its bundled
Chromium on macOS 13, so on such a machine point the capture at a locally installed
Chromium via `executable_path` (e.g. `/Applications/Chromium.app/Contents/MacOS/Chromium`).
This is a local platform workaround only - it is not UA spoofing, a stealth plugin, or a
proxy, and every other property (fresh cookie-less logged-out context, rate limiting,
the sanitize + `assert_clean` gate) is unchanged.

**Headed capture (user decision, 2026-10-01).** The production scraper runs headed
(`HEADLESS_BROWSER = False`), so a live capture attempt is made once per site with a
visible Chromium window to match what production sees. A live fixture records
`"headed": true` in its `.meta.json`. Everything else holds: logged out, fresh
cookie-less context (FIX-1), >=10 s between requests, one attempt per site, no evasion.

## Hand-built fixtures

When a site actively blocks the logged-out headless capture (CAPTCHA, a Cloudflare
challenge, a JS-gated empty page, a 403), or an API endpoint is gone, **do not attempt
evasion** (no stealth plugins, UA spoofing, or proxies): stop capturing that site and
hand-build its fixture from the site's public structure exactly as the current
`parameters.py` selectors (or the Leanpub parser) expect it. A hand-built fixture:

- is routed through the **same** `sanitize` + `assert_clean` pipeline as a live capture
  (FIX-6) - it is never a raw paste;
- carries `"hand_built": true` and a `"reason"` in its `.meta.json`, plus the same
  `logged_out: true` and `sanitizer_version` audit fields;
- uses only neutral, non-copyrighted placeholder prose and factual metadata.

`test_meta_sha256_matches_file` guards hand-built fixtures against post-build edits just
like captured ones.

> **A hand-built fixture does NOT detect real markup drift.** It is built to match the
> *current* `parameters.py` selectors, so it will keep passing even after the live site
> changes its markup - the exact failure this corpus exists to catch for the sites that
> could be captured live. Hand-built fixtures verify the parser's control flow and the
> sanitize pipeline against realistic structure, nothing more. When a walled site later
> becomes capturable, replace its hand-built fixtures with live captures.

## Provenance

Every fixture in the initial **detail** corpus is **hand-built**: the one live headed
attempt per site (2026-10-01) was blocked on all three Playwright detail pages, and the
Leanpub JSON:API is gone, so no detail fixture carries `"headed": true`. The **search**
tier below is different - Amazon search is not walled, so its four pages are live headed
captures (`"headed": true`).

A later, single Amazon **detail** attempt for subtask 06 (2026-10-01, system Chromium,
logged out) loaded a **normal** product page rather than the CAPTCHA wall, so that one
detail fixture - `html/amazon/a_tour_of_cpp_3e.html` - **is** a live headed capture
(`"headed": true`); its subresources are stripped like the search fixtures so it serves
cleanly in the offline default-deny harness. See the edge-case table below.

| Fixture | Source | Reason | Bytes |
|---|---|---|---|
| `html/amazon/cpp_primer_5e.html` | hand-built | Amazon served a CAPTCHA bot-wall (headed + headless, logged out) | 1235 |
| `html/amazon/programming_principles_and_practice_3e.html` | hand-built | same Amazon CAPTCHA wall | 1131 |
| `html/amazon/the_cpp_programming_language_4e.html` | hand-built | same Amazon CAPTCHA wall | 1093 |
| `html/packtpub/mastering_the_cpp17_stl.html` | hand-built | Packtpub Cloudflare "Just a moment..." challenge | 775 |
| `html/packtpub/software_architecture_with_cpp.html` | hand-built | same Packt Cloudflare challenge | 816 |
| `html/packtpub/cpp_windows_programming.html` | hand-built | same Packt Cloudflare challenge | 778 |
| `html/oreilly/practical_cpp_metaprogramming.html` | hand-built | O'Reilly JS-gated near-empty page (~344 B, no structure) | 814 |
| `html/oreilly/effective_modern_cpp.html` | hand-built | same O'Reilly JS gate | 739 |
| `html/oreilly/cpp_in_a_nutshell.html` | hand-built | same O'Reilly JS gate | 728 |
| `json/leanpub/cpp17_in_detail.json` | hand-built | Leanpub `api/v1/cache` JSON:API now 404; book `.json` 401 | 733 |
| `json/leanpub/cpp_lambda_story.json` | hand-built | same Leanpub API gone | 730 |
| `json/leanpub/cpp_best_practices.json` | hand-built | same Leanpub API gone | 733 |

### Search-page & API fixtures (subtask 05, 2026-10-01)

Amazon search is **not** walled (unlike its detail pages), so these four are **live** headed
captures (system Chromium, logged out, cookie-less); `headed: true` in each `.meta.json`. Each
search snapshot is trimmed to the first 8 `SEARCH_BOOK_CARD` matches and has its image/stylesheet
subresources stripped (budget + offline harness), recorded as `trimmed` in meta. The live pages
already show selector drift: `SEARCH_BOOK_DETAIL_LINK` matches zero live cards (see the amazon
search golden's `_known_issues`). The Leanpub search API (`api/v1/cache/simple_books.json`) returns
**HTTP 404** (verified logged-out, 2026-10-01), so its JSON fixtures are hand-built to the JSON:API
shape; `search_python_p1.json` holds a full 100-item page so pagination continues to the short
`search_python_last.json`. `robots/` covers all four sites: amazon/leanpub fetched live; packtpub
and oreilly returned HTTP 403 to the logged-out fetch and are hand-built to a neutral shape.

| Fixture | Source | Reason | Bytes |
|---|---|---|---|
| `html/amazon/search_python_p1.html` | live (headed) | Amazon search not walled; page 1, trimmed to 8 cards, subresources stripped | 39693 |
| `html/amazon/search_python_p2.html` | live (headed) | page 2, trimmed to 8 cards | 34135 |
| `html/amazon/search_python_last_page.html` | live (headed) | result-exhausted page (no result cards) | 9468 |
| `html/amazon/search_no_results.html` | live (headed) | no-results page (no result cards survive sanitize) | 15 |
| `html/amazon/search_sponsored.html` | hand-built | no captured page had an `/sspa/click` card inside a `SEARCH_BOOK_CARD` | 985 |
| `json/leanpub/search_python_p1.json` | hand-built | Leanpub search API 404; full 100-item page (pagination continues) | 36250 |
| `json/leanpub/search_python_last.json` | hand-built | same API gone; short 7-item page (pagination stops) | 3142 |
| `json/leanpub/search_empty.json` | hand-built | same API gone; empty `data` array | 34 |
| `robots/amazon.txt` | live | fetched logged-out via httpx | 7887 |
| `robots/leanpub.txt` | live | fetched logged-out via httpx | 648 |
| `robots/packtpub.txt` | hand-built | live fetch returned HTTP 403 (bot-walled) | 143 |
| `robots/oreilly.txt` | hand-built | live fetch returned HTTP 403 (bot-walled) | 158 |

### Edge-case & live detail fixtures (subtask 06, 2026-10-01)

Each row of task 06's edge-case table gets a committed fixture and a golden recording
**today's** behavior, with `_known_issues` naming the fixing roadmap subtask. All derived
fixtures carry `"derived_from"` + `"hand_built": true` in their `.meta.json` and are built
by re-running the named source fixture's markup (minimally changed) through the same
`sanitize` + `assert_clean` pipeline (FIX-6). `_bot_wall` is hand-built from the known
Amazon CAPTCHA structure; `a_tour_of_cpp_3e` is the one **live** headed detail capture (see
above). The Packtpub/O'Reilly edge goldens characterize `FAILED` because `site_constants`
lacks a `BASE_URL` key for those sites (real bug, roadmap 03.0/03), which raises before any
selector runs; `_read_more_expander`'s "full description" is the neutralization placeholder
by corpus policy, not real prose.

| Fixture | Source | Reason | Bytes |
|---|---|---|---|
| `html/amazon/_404.html` | derived: `amazon/cpp_primer_5e` | `<title>` set to the 404_PAGE_TITLE so the 404 branch fires | 1251 |
| `html/amazon/_bot_wall.html` | hand-built | Amazon CAPTCHA ("Robot Check") structure; no `#productTitle` so title times out, 3 retries | 316 |
| `html/amazon/_no_isbn.html` | derived: `amazon/cpp_primer_5e` | Kindle-only: ISBN-10/13 rows dropped so both resolve to `"N/A"` | 1007 |
| `html/amazon/_audiobook.html` | derived: `amazon/cpp_primer_5e` | publication-date row swapped for the audiobook release-date row (PUBLICATION_DATE branch) | 1251 |
| `html/amazon/_non_ascii_title.html` | derived: `amazon/cpp_primer_5e` | NFC non-ASCII `#productTitle` (title/hash stability under Unicode) | 1295 |
| `html/amazon/_missing_title.html` | derived: `amazon/cpp_primer_5e` | `#productTitle` dropped so BOOK_TITLE times out, 3 retries | 1194 |
| `html/amazon/a_tour_of_cpp_3e.html` | live (headed) | one 2026-10-01 Amazon detail attempt loaded normally (no bot wall); subresources stripped | 37740 |
| `html/packtpub/_404.html` | derived: `packtpub/mastering_the_cpp17_stl` | `<title>` set to a 404 title (FAILED via missing BASE_URL before the 404 branch) | 801 |
| `html/packtpub/_many_authors.html` | derived: `packtpub/mastering_the_cpp17_stl` | 6 `div.authors span` authors incl one duplicate, order preserved | 935 |
| `html/oreilly/_404.html` | derived: `oreilly/effective_modern_cpp` | `<title>` set to a 404 title (FAILED via missing BASE_URL before the 404 branch) | 753 |
| `html/oreilly/_read_more_expander.html` | derived: `oreilly/effective_modern_cpp` | DESCRIPTION_ALT read-more-wrapper + READ_MORE_LINK expander (description neutralized) | 895 |
| `html/oreilly/_many_authors.html` | derived: `oreilly/effective_modern_cpp` | 6 `a.author-name` authors incl one duplicate, order preserved | 1030 |
| `json/leanpub/_404.json` | derived: `leanpub/cpp17_in_detail` | `data: null` not-found response (no-data branch -> returns None) | 18 |
| `json/leanpub/_leanpub_null_about.json` | derived: `leanpub/cpp_lambda_story` | `about_the_book: null` -> `unescape(None)` TypeError -> returns None | 620 |
