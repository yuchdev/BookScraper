# HTML fixture corpus: refresh & drift workflow

How to keep the real-HTML mock corpus (`tests/fixtures/`) current when a scrape target
changes its markup. The corpus and its policy are described in
[`tests/fixtures/README.md`](../../tests/fixtures/README.md); this page is the operational
runbook for **detecting drift and refreshing** fixtures, driven by
[`scripts/capture_fixtures.py`](../../scripts/capture_fixtures.py). It implements subtask
[07 — fixture refresh & drift workflow](../roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/07-fixture-refresh-workflow.md)
and is bound by [FIX-7 / FIX-10](../security/2026-09-30-real-html-test-corpus.md) in the
threat model.

## When to refresh

Refresh a site's fixtures when any of these happen:

- **A drift report flags it.** A `--refresh` run (below) prints `SELECTOR DRIFT` or
  `GOLDEN DRIFT` for a fixture.
- **A live smoke test fails.** The real scraper starts returning `FAILED`/empty fields for
  a site that used to work — usually a selector that no longer matches the live markup.
- **Quarterly**, as routine hygiene, even with no observed failure: sites change silently.

Hand-built and derived fixtures (see Provenance below) do **not** detect live markup
drift — they are built to match the *current* `parameters.py`, so they keep passing after
the site changes. Only live-captured fixtures catch real drift. The refresh tool still runs
a useful in-place selector check against hand-built/derived fixtures after a `parameters.py`
edit (it flags a selector you just changed that no longer matches the committed fixture).

## The refresh command

```bash
# Report only: re-capture every fixture of a site into a TEMP dir and print a drift report.
# Writes NOTHING into tests/fixtures.
uv run python scripts/capture_fixtures.py --refresh --site amazon

# Apply: the sole in-tree write path. Rewrites the fixture bytes AND its .meta.json,
# re-running sanitize + assert_clean + the size budgets on everything it writes (FIX-10).
uv run python scripts/capture_fixtures.py --refresh --site amazon --apply
```

`--refresh` takes no `--kind`/`--url`/`--name` (it iterates the committed fixtures of the
site). `--apply` is only valid with `--refresh`.

What each fixture row reports:

- **`sha256` CHANGED / unchanged** — a changed hash is expected; sites change constantly.
- **`SELECTOR DRIFT`** — for every current `site_constants[site]` detail (or `SEARCH_*`)
  selector, its match count in the old snapshot vs the fresh one. A `>=1 -> 0` transition
  (the selector used to match, now matches nothing) is flagged. This is the signal that
  tells you *which* selector to fix.
- **`GOLDEN DRIFT`** — the unmodified parser (`scrape_book` for HTML, `get_leanpub_book_details`
  for Leanpub JSON) is run against the fresh snapshot and diffed field-by-field against the
  fixture's `.expected.json`. Shown only for fixtures that have a sibling golden; the HTML
  leg needs a browser and degrades to "could not evaluate" when none is available.
- **`WALL detected`** — the fresh capture looks like a bot-wall/CAPTCHA (a known marker, or
  the site's title selector is absent).

### Safety properties (FIX-7 / FIX-10)

- `--refresh` fetches are **logged out, cookie-less, no user proxy or credentials** — it
  reuses the capture tool's `_capture_html` / `_fetch_body` code paths (FIX-7). Requests are
  rate-limited to **>= 10 s between requests to the same site**; one attempt per fixture; no
  evasion.
- `--refresh` alone writes **only to a temp dir** (printed at the top of the run). `--apply`
  is the **only** path that writes into `tests/fixtures/`, and every write re-runs the
  sanitizer, `assert_clean`, and the per-file/whole-corpus budgets — there is no
  silent-green write (FIX-10). `--apply` **refuses to overwrite a hand-built fixture with a
  walled/CAPTCHA page**: if a hand-built fixture's recapture looks walled, it is left as-is.

### Selector-drift counting is offline (no browser)

Selector match counts come from the sanitizer's own bounded `_select`
(`src/bookscraper/scraping/sanitize.py`), not from Chromium `set_content`. The fixtures are
already-sanitized static DOM snapshots and the selectors are plain CSS that `_select` fully
supports, so counting stays offline, deterministic, and Chromium-free (it runs in the
no-Chromium CI baseline and matches how the capture pipeline already counts result cards in
`_trim_repeated_blocks`). The pure comparison helper is
`compare_selector_matches(old_counts, new_counts)`, unit-tested in
`tests/unit/scraping/test_fixture_drift.py`.

## Step-by-step: from "the site changed" to an updated selector + golden

1. **Refresh (report only).**
   `uv run python scripts/capture_fixtures.py --refresh --site <site>` and read the
   `SELECTOR DRIFT` / `GOLDEN DRIFT` lines. The temp snapshots it wrote are named in the
   output — inspect them to see the new markup.

2. **Inspect the new snapshot** for the drifted field and work out the new selector.

3. **Propose selectors.** The intended tool is
   `detect-schema --html-file <snapshot>` (the offline, no-network mode of the
   [schema-detection command](../scraping/schema-detection.md)). **This flag is planned, not
   yet implemented** — it is subtask
   [04.0 / 07 — offline mode & golden tests](../roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/07-offline-mode-and-golden-tests.md).
   **Interim manual step until it lands:** open the temp snapshot in a browser / editor,
   find the element the field needs, and derive the CSS selector by hand (the "manual loop"
   in the [schema-detection guide](../scraping/schema-detection.md)); or point the existing
   `detect-schema --url <live-url>` at the live page if the site is reachable.

4. **Update `parameters.py` by hand.** Automation proposes; a human applies. Edit the
   `site_constants[site]` entry with the new selector.

5. **Regenerate goldens.** `uv run pytest --update-goldens` rewrites the affected
   `.expected.json` files and then *fails* the run so the diff is never silently green
   (subtask 04). Review the diff.

6. **Apply the refreshed fixtures** (optional, when the capture is live and clean):
   `uv run python scripts/capture_fixtures.py --refresh --site <site> --apply`. For walled
   sites this is refused for hand-built fixtures — keep hand-building instead (below).

7. **Review and commit** the `parameters.py`, fixture, meta, and golden changes together.

## Sanitize & size rules (unchanged by refresh)

Every byte written — live, hand-built, derived, or refreshed — goes through the identical
sanitizer + `assert_clean` gate (prose neutralization, secret/token stripping) and the size
budgets (**400 KB/file, 15 MB/corpus**). See the policy and budgets sections of
[`tests/fixtures/README.md`](../../tests/fixtures/README.md). `--apply` enforces all of
these before writing.

## Walled sites & hand-built / derived provenance

Three of the four sites actively wall the logged-out capture (Amazon CAPTCHA, Packtpub
Cloudflare challenge, O'Reilly JS gate), and the Leanpub JSON:API is gone. Those fixtures
are **hand-built** from each site's public structure as the current `parameters.py`
selectors expect it — never a raw paste, and never via evasion (no stealth plugins, UA
spoofing, or proxies): a wall is the answer, not an obstacle to route around. Edge cases are
**derived** (`derived_from`) from a base fixture by minimally editing its markup and
re-running the sanitize pipeline.

Consequences for refresh:

- **Derived fixtures** (`derived_from` in meta) have no real page to re-capture — refresh
  reports them as `hand-built: no live source` and makes no capture attempt. It still runs
  the in-place selector check (step 1's value after a `parameters.py` edit).
- **Base hand-built fixtures** whose `meta.url` is a real page may get one capture attempt,
  reported as live or walled. If the recapture is walled, `--apply` refuses to overwrite the
  hand-built fixture. When a walled site later becomes capturable, replace its hand-built
  fixtures with live captures.

The provenance tables in [`tests/fixtures/README.md`](../../tests/fixtures/README.md) record,
per fixture, whether it is live, hand-built, or derived and why.

## Success criterion

A maintainer can go from "Amazon changed its markup" to an updated selector and golden by
following the step-by-step above.

---

## Test harness reference

This section documents how the offline fixture corpus integrates with pytest for developers
adding or modifying tests.

### `corpus_router` and `httpx_corpus`

`tests/mock/scraping/html/conftest.py` builds the corpus mapping (fixture URL → bytes) from
each fixture's `.meta.json` `url` field. Two helpers serve this mapping:

- **`corpus_router(browser, mapping)`** — wraps a real headless Chromium so every page load
  serves the mapped corpus bytes. Any request that is **not** in the mapping is aborted,
  recorded, and **fails the test** (threat model FIX-8). Corpus routes are installed *after*
  the production `route_handler` so they take precedence and an unmapped document is never
  `route.continue_()`d to the network (FIX-9).
- **`httpx_corpus(mapping)`** — does the same for the Leanpub `httpx` path by injecting an
  `httpx.MockTransport`. It is a same-signature `httpx.AsyncClient` factory, requiring no
  production code change.

### Chromium marker

Tests that need a real browser carry `@pytest.mark.chromium` (registered in `pyproject.toml`
and **included in the default run** — offline and fast). They pick their browser via the
`BOOKSCRAPER_CHROMIUM_PATH` env var (a system Chromium, for macOS 13 where Playwright's bundled
Chromium will not launch), falling back to the bundled browser and **skipping cleanly** (never
failing) when no usable browser is found, so CI can install one.

### Detail-page golden tests (`test_detail_goldens.py`)

`tests/mock/scraping/html/test_detail_goldens.py` runs the unmodified `scrape_book()` /
`get_leanpub_book_details()` against every non-edge-case fixture and compares the result
field-by-field against a committed `tests/fixtures/**/<name>.expected.json`.

Goldens record what the code does **today**, including known-wrong output; each such value
carries a `"_known_issues"` list naming the subtask that will fix it (the key is ignored by the
comparison). It is split into:

- `test_detail_golden` — Playwright sites, `chromium`-marked
- `test_detail_golden_leanpub` — the Leanpub JSON path via `httpx_corpus`, unmarked so it
  runs offline without Chromium

### Search-page golden tests (`test_search_goldens.py`)

`tests/mock/scraping/html/test_search_goldens.py` runs `get_search_results_via_playwright`
against live-captured Amazon search snapshots (Amazon search is not walled) in real headless
Chromium (`chromium`-marked, skips without a usable browser) and
`get_leanpub_search_results_via_api` over `httpx_corpus` (unmarked, offline). Both goldens live
in `tests/mock/scraping/html/goldens/` (outside `tests/fixtures/`, so diffable and not subject
to the corpus orphan check) and carry `_known_issues` notes.

### Shared fixtures (`tests/conftest.py`)

- `isolated_config` — monkeypatches `CONFIG_DIR`/`SETTINGS_FILE` to `tmp_path`; a test can
  never read or write the real `~/.bookscrapper/settings.json`.
- `isolated_local_store` — monkeypatches `LOCAL_STORE_FILE` to `tmp_path`; a test can never
  read or write the repo-root `books.json`.
- `no_real_env` — strips MongoDB env vars for a test's duration so a prior `load_dotenv()` can
  never leak real values in.
- `--update-goldens` / `update_goldens` fixture — rewrites `.expected.json` files and then
  **fails** the run with `"goldens updated - review the diff"`, so regeneration is never
  silently green.

### Regenerating goldens

```bash
uv run pytest --update-goldens
```

This rewrites the affected `.expected.json` files and intentionally fails the run. Review the
diff before committing.
