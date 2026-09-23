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
   existing field names, with a one-line rationale each.
3. **Validates every proposal against each sample page's live DOM** - reporting per
   field/per URL whether it matched, how many elements, and a text snippet, and flagging any
   field that matches on some sample URLs but not others rather than silently picking one.
4. **Emits a diff-style report** (to stdout, and to `--output <path>` if given) comparing
   the proposals to the current `site_constants[site]` values.

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

- `ANTHROPIC_API_KEY` is not set (see Configuration below).
- A sample page fails to load (timeout or navigation error).
- Claude returns a malformed or non-JSON response.

### Configuration

The tool needs an Anthropic API key, read directly from the environment (or `.env` at the
repo root):

```dotenv
ANTHROPIC_API_KEY="<your-anthropic-api-key>"
```

Optionally, `ANTHROPIC_MODEL` pins/overrides the model used. Unlike the MongoDB
credentials, this key is **not** read through `~/.bookscrapper/settings.json` - it is a
standalone dev-tool credential, not a runtime auth path.

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

Flags: `--site {amazon,packtpub,leanpub,oreilly}` (required), `--url URL` (required,
repeatable - pass it more than once to validate proposals across multiple pages),
`--output <path>` (optional), and `--log-severity {debug,info,warning,error}` like the
other subcommands.

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
