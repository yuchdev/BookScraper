# Determining and refreshing CSS-selector schemas

Every field `bookscraper` extracts from a book detail page - title, authors, ISBN-10/13,
publication date, description, tags, plus the search-result selectors - is located by a
CSS selector stored in `site_constants[site]` in
`src/bookscraper/scraping/parameters.py`. Those selectors are the scraper's most fragile
surface: each of the four sites (Amazon, Packtpub, Leanpub, O'Reilly) changes its markup
on its own schedule, and when a site drifts, a selector silently starts matching nothing
(or the wrong element). The scrape still "succeeds" but writes `N/A` ISBNs and empty
descriptions - **schema drift**.

This document covers two things:

- **Part 1** - the manual loop a developer follows to determine or refresh a site's
  selectors, written up as it is actually done today.
- **Part 2** - the `bookscraper detect-schema` tool that automates most of that loop, its
  hard "propose, never apply" boundary, usage, and when to reach for it versus the manual
  loop.

See also [ADR 0002](../adr/0002-ai-assisted-schema-detection.md) for the design rationale
behind the automated tool.

---

## Part 1 - The manual loop

This is the process the comments in `parameters.py` are artifacts of (e.g.
`# Usually inside #detailBullets_feature_div...`, `# Needs investigation, might be in a
meta tag...`). It repeats whenever a site's markup drifts.

### 1. Open a sample detail page in an inspectable browser

Pick a representative detail-page URL for the site and load it in a mode you can inspect:

- **Recorder:** `playwright codegen <url>` opens the page with the Playwright recorder, so
  you can click elements and watch it emit locators.
- **Inspector:** in an existing Playwright script, drop `await page.pause()` to open the
  Playwright Inspector with its element picker and a live console.
- **Claude Code / MCP browser:** since selector work is often done inside Claude Code,
  drive the page directly through the browser/Playwright MCP tools and read the
  accessibility tree / DOM without leaving the session.

### 2. Locate each field and propose a robust selector

For each required field, find the DOM element(s) using devtools "Inspect" or the
Inspector's element picker, then propose a CSS selector - by hand or by pasting the
relevant HTML / accessibility snapshot to an AI assistant and asking it to suggest one.
Aim for:

- **Stable anchors first.** Prefer `id` and `data-*` attributes over long auto-generated
  class chains (`div.a-row.a-size-base.a-color-secondary > ...`), which break on the next
  redesign.
- **Match the file's existing style.** Follow the naming already in `site_constants[site]`
  (`BOOK_TITLE`, `AUTHORS`/`AUTHORS_ALT`, `ISBN10`/`ISBN13`, `PUBLICATION_DATE`,
  `DESCRIPTION`/`DESCRIPTION_ALT`, `TAGS`, `404_PAGE_TITLE`, ...). Note the key set differs
  per site - Amazon carries `AUTHORS_ALT` and a `DETAILS_BUTTON` expander; Leanpub uses
  meta-tag selectors (`AUTHORS_META`, `PUBLICATION_DATE_META`) and leaves `ISBN10`/`ISBN13`
  as `None`.
- **Add a fallback where the primary might miss.** Provide an `_ALT` selector when a field
  has alternate markup (a collapsed "read more" expander, audiobook-vs-book layouts, a less
  structured author line, etc.).

### 3. Validate every candidate against the *live* DOM, across multiple pages

A selector that matches one page's markup can be a false positive. Validate each candidate
live:

- In the Inspector console: `await page.locator(sel).count()` and inspect
  `.first.textContent()`.
- In devtools: `document.querySelectorAll(sel)`.

Do this across **several** sample URLs for the site, not just one - two different books can
render differently (hardcover vs. Kindle, book vs. audiobook), and a selector that only
works on your first sample is a latent bug.

### 4. Record the finalized selectors

Write the validated selectors into `site_constants[site]` in `parameters.py`, following the
existing naming convention, with a short comment for anything non-obvious (mirroring the
comments already in the file).

### 5. Re-run the scraper end-to-end

Isolated selector matches are not proof the pipeline produces correct data. Run a handful
of known URLs through the real scraper and inspect the output:

```bash
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json
```

Check `books.json` for correct titles/authors/ISBNs/dates and `failed_books.csv` for URLs
that failed - a selector can match yet still extract the wrong text.

### 6. Repeat on drift

This loop runs again whenever a site changes its markup. That recurring cost is exactly
what Part 2 automates.

---

## Part 2 - The `detect-schema` tool

`bookscraper detect-schema` automates steps 1-3 of the manual loop and produces a report
for you to apply in step 4. It is a **developer-maintenance tool**, not part of a scheduled
scrape, and it does **not** take `--store-backend` - it never touches book storage.

### What it does

For a given `--site` and one or more sample `--url`s, the tool
(`src/bookscraper/scraping/schema_detection.py`):

1. **Loads each URL with Playwright**, reusing `scrape_details.route_handler` so only the
   document is fetched, and extracts a pruned HTML snapshot (scripts/styles/SVGs/comments
   stripped, whitespace collapsed, truncated to a size budget) fit to hand to an LLM.
2. **Asks Claude** (via the official `anthropic` SDK) to propose a CSS selector for each
   field already present in `site_constants[site]`, returned as JSON keyed by those
   existing field names, with a one-line rationale each. One request is sent per sample
   URL (sharing a cacheable static instruction block), each fitted to a pre-flight
   input-token budget; the per-page proposal sets are then merged (first non-null selector
   per field wins).
3. **Validates every proposal against each sample page's live DOM** - reporting per
   field/per URL whether it matched, how many elements, and a text snippet, and flagging any
   field that matches on some sample URLs but not others rather than silently picking one.
4. **Emits a diff-style report** (to stdout, and to `--output <path>` if given) comparing
   the proposals to the current `site_constants[site]` values, followed by a one-line
   **usage summary**: the model, the summed input / cache-read / cache-creation / output
   tokens across every request, and the request count. It carries no prices (they change,
   and a stale figure would mislead) and never the API key.

### The hard boundary: propose, never apply

**The tool never writes to `parameters.py`.** It only prints a report; you apply the
changes you agree with by hand. This is deliberate - a wrong selector does not crash, it
silently corrupts scraped data, so the human review is the safety mechanism. It mirrors the
project's existing "automation proposes, a human/deterministic step decides" convention
(the Atlas cert-rotation flow only rewrites `settings.json` for a value it deterministically
owns) and the config resolver's "fail loudly rather than silently guess" philosophy.

### Fail-loud behavior

Each of these exits non-zero with a clear error and produces **no** partial or guessed
output:

- More than 5 sample `--url`s (or `--html-file`s) are passed, or both kinds are passed together, or
  neither is (rejected before any network or API call).
- `ANTHROPIC_API_KEY` is not set (live runs only; not required with `--replay`), or the resolved
  model ID is empty (see Configuration below).
- A sample page fails to load (timeout or navigation error), or an `--html-file` is missing,
  oversized (> 400 KB), or not valid UTF-8.
- The built prompt exceeds `--max-input-tokens` even after pruning to the 10000-character floor
  (live runs only).
- A `--replay` file is missing/unreadable/not a JSON object, or a run's prompt is not recorded in it.
- Claude returns a malformed or non-JSON response, or stops with `max_tokens` (truncated) or `refusal`.

### Configuration

The tool needs an Anthropic API key, read directly from the environment (or `.env` at the
repo root):

```dotenv
ANTHROPIC_API_KEY="<your-anthropic-api-key>"
```

The model defaults to **`claude-sonnet-5`**, resolved with precedence `--model` flag >
`ANTHROPIC_MODEL` env var > default (an empty ID is rejected). Unlike the MongoDB
credentials, the key is **not** read through `~/.bookscrapper/settings.json` - it is a
standalone dev-tool credential, not a runtime auth path.

Sonnet 5 runs adaptive thinking by default and those thinking tokens count against
`max_tokens`, so the answer budget is raised to 16000 and a `max_tokens` (truncated) or
`refusal` stop reason is treated as a hard failure rather than parsed. No sampling
parameters or `budget_tokens` are sent (Sonnet 5 rejects them).

**Cost controls.** A run is capped at **5 sample URLs** (rejected before any network or
API call) and makes **one request per URL**, each sharing a cacheable static instruction
block. Before each request a pre-flight token count enforces `--max-input-tokens`
(default 40000): an over-budget prompt is re-pruned toward a 10000-character HTML floor
and, if it still will not fit, abandoned with an error rather than sent. A transient API
error is retried at most once (the SDK's own retries are set to match, so retries never
stack). SDK errors surface only the exception class and HTTP status, never the response
body.

### Usage

```bash
# Propose selectors for Amazon, validated across two sample detail pages:
uv run bookscraper detect-schema \
  --site amazon \
  --url "https://www.amazon.com/dp/1098131029" \
  --url "https://www.amazon.com/dp/1492051365"

# Single sample page, also write the report to a file:
uv run bookscraper detect-schema \
  --site leanpub \
  --url "https://leanpub.com/some-book" \
  --output schema-report.txt

# Without `uv run`, inside an activated venv:
python -m bookscraper detect-schema --site oreilly --url "https://www.oreilly.com/library/view/.../"
```

Flags: `--site {amazon,packtpub,leanpub,oreilly}` (required), `--page-type {detail,search}`
(optional, default `detail`), **exactly one of `--url URL` or `--html-file PATH`** (required,
both repeatable - pass more than once to validate across multiple pages, up to 5), `--model <id>`
(optional, overrides `ANTHROPIC_MODEL` and the `claude-sonnet-5` default), `--max-input-tokens
<n>` (optional, default 40000), `--replay <path>` / `--record <path>` (optional, see Offline
modes below), `--output <path>` (optional), and `--log-severity {debug,info,warning,error}` like
the other subcommands.

### Offline modes: `--html-file`, `--replay`, `--record`

The tool can run **fully offline - no network and no API key** - which is how it runs in CI (the
golden tests in `tests/mock/scraping/html/test_schema_detection_goldens.py`). CI never makes a
live Anthropic call; the automation is offline-only.

- **`--html-file PATH`** (repeatable, mutually exclusive with `--url`) uses a local HTML file as a
  sample page instead of fetching a URL. The file is read with a size cap (400 KB, aligned with
  the fixture-corpus per-file budget) as UTF-8 - a missing, oversized, or non-UTF-8 path fails
  loudly - and loaded into Chromium via `page.set_content()` with **every** request aborted
  (default-deny), so nothing is fetched. The pruned snapshot is derived from the file bytes, so
  it (and the replay key below) is deterministic and independent of the Chromium build.
- **`--replay PATH`** serves the model step from a recorded JSON file of
  `{sha256(prompt): raw_model_response}` instead of calling Claude. In replay mode **no Anthropic
  client is constructed and `ANTHROPIC_API_KEY` is not required**; the pre-flight token-count is
  skipped (there is no client to count with) and the usage line reports zero tokens and the
  replayed count. A prompt missing from the file is a hard error ("no recorded response") - never
  a silent fall-through to a live call. Keys beginning with `_` (e.g. a `_provenance` note) are
  ignored by the loader.
- **`--record PATH`** writes a live run's responses (keyed by `sha256(prompt)`) back out for a
  maintainer to curate into a replay file. It is ignored in replay mode and **never** contains the
  API key.

The committed replay recordings under `tests/fixtures/schema_detection/<site>.replay.json` are
**hand-authored** (plausible model JSON following the exact `validate_ai_response` output
contract, carrying a `_provenance: "hand-authored; not a recorded live model response"` note);
their keys are generated from the real prompt builders by
`scripts/generate_schema_replays.py` (never hand-computed). The `.report.txt` goldens beside them
are regenerated with `uv run pytest --update-goldens` (which rewrites then fails so the diff is
reviewed). Both are derived artifacts, not captured pages, so they are exempt from the
captured-corpus `.meta.json`/sha256 integrity checks (see `tests/fixtures/README.md`).

```bash
# Fully offline: local sample files + a recorded replay, no key, no network.
uv run bookscraper detect-schema --site amazon \
  --html-file tests/fixtures/html/amazon/cpp_primer_5e.html \
  --html-file tests/fixtures/html/amazon/a_tour_of_cpp_3e.html \
  --replay tests/fixtures/schema_detection/amazon.replay.json

# Maintainer refresh: a live run that records its responses for a future --replay.
uv run bookscraper detect-schema --site amazon \
  --url "https://www.amazon.com/dp/0136816487" \
  --record tests/fixtures/schema_detection/amazon.recorded.json
```

### Detail pages vs. search pages (`--page-type`)

By default the tool proposes selectors for a book **detail** page (`BOOK_TITLE`, `AUTHORS`,
`ISBN10`/`ISBN13`, `PUBLICATION_DATE`, `DESCRIPTION`, `TAGS`, ...). Pass `--page-type search`
with a search-results URL to propose and validate the `SEARCH_*` selectors instead - the
least-verified part of `parameters.py`:

```bash
uv run bookscraper detect-schema --site amazon --page-type search \
  --url "https://www.amazon.com/s?k=python"
```

A search-results page is a list of repeated book **cards**. The tool treats the
card-container field (`SEARCH_BOOK_CARD` / `SEARCH_RESULT_ITEM_SELECTOR`) and the
pagination / no-results fields (`SEARCH_NEXT_PAGE_SELECTOR`, `SEARCH_NO_RESULTS_TEXT`,
`SEARCH_NO_BOOKS_FOUND`) as **page-level**, and every other `SEARCH_*` field (title,
authors, detail link, publication date, rating) as **card-relative** - evaluated inside a
single card. The prompt tells Claude the same, so it proposes card-relative selectors for
those fields. The non-selector `SEARCH_*` constants (the base search URL/API, the page-count
integer, and the `data-asin` attribute NAME) are excluded. Validation enumerates the cards on
each sample page and reports, per card-relative selector, the card count and the fraction of
cards the selector matched; a selector matching **fewer than 80%** of the cards on any sample
page is flagged `!! PARTIAL`. Everything else - the propose-never-apply boundary, the usage
line, the fail-loud behavior - is identical to a detail run.

### Reading the report

For each field the report shows the current value versus the proposed selector (a `-`/`+`
diff when they differ, `=` when identical), the proposal's rationale, and the per-URL
live-DOM validation (`[MATCH]`/`[ no ]` with element counts and a text snippet). A field
that matched inconsistently across sample URLs is flagged with `!! INCONSISTENT` and summed
up in a closing `WARNING:` line - treat those as needing a closer look before you apply
them.

### When to use the tool vs. the manual loop

- **Reach for `detect-schema`** when you want to check one or all four sites for drift
  quickly, or want a validated first draft of selectors across several sample pages without
  a manual browser session. It is the fast, repeatable path.
- **Reach for the manual loop** for a one-off quick fix to a single known-broken selector,
  when you have no API key handy, or when a field needs human judgement the tool cannot
  give - e.g. deciding between two equally-matching selectors based on which is more likely
  to survive the next redesign, or wiring up expander/`_ALT` interactions that require
  actually clicking through the page.

In practice the two compose: run `detect-schema` to get a validated draft, then use the
manual loop's step 5 (`scrape-urls` end-to-end against `--store-backend json`) to confirm
the selectors you applied produce correct data in `books.json`.

### Driving selector updates from the fixture corpus

When drift is detected by the real-HTML mock corpus rather than a live run, the
[HTML fixture refresh & drift workflow](../test/html-fixtures.md) is the companion runbook:
`scripts/capture_fixtures.py --refresh` re-captures a site's fixtures and reports which
selectors drifted, and its step-by-step feeds those snapshots into `detect-schema`
(via the `--html-file`/`--replay` offline mode above) and then `pytest --update-goldens`.
