# LLM extraction fallback (`--ai-fallback`)

**See also:** [ADR 0003](../adr/0003-llm-extraction-fallback.md) (design rationale),
[threat model](../security/2026-10-23-llm-extraction-fallback.md),
[CLI reference](../cli/cli.md) (flags).

`--ai-fallback` (task 04.1) is an **opt-in** safety net on the `scrape-urls` / `search` detail
path. When a deterministic selector leaves an *eligible* field empty — the known silent case is
Amazon's live `DESCRIPTION` matching an empty element — the scraper asks Claude for just that one
field from the page HTML already in hand, validates the answer deterministically, and stores it
only if it passes.

This deliberately crosses the line ADR 0002 drew (AI *values*, not just proposed selectors, now
enter stored records). Every value is gated and marked. It is **off by default**: without the
flag no Anthropic call is made, `ANTHROPIC_API_KEY` is never read, and every `detect-schema`
golden is byte-identical.

---

## Flags

Declared on the shared scrape-runtime parent parser, so both `scrape-urls` and `search` carry
them:

- `--ai-fallback` — enable the fallback (default off)
- `--ai-fallback-max-pages N` — hard per-run cap on fallback **requests** (default `20`, range
  `1..200`)
- `--model <id>` — model precedence: `--model` flag > `ANTHROPIC_MODEL` env > `claude-sonnet-5`
  (same resolution as `detect-schema`)

With `--ai-fallback`, a missing `ANTHROPIC_API_KEY` or empty model ID fails loudly at
**start-up** (`print_log` error, exit `1`) before any scraping. During a run, a
malformed/refused/`max_tokens` response or any SDK error logs a warning and yields `{}` — one
hostile or pathological page must never abort a scrape of thousands (threat model EXT-7).

---

## Eligible fields

`DESCRIPTION`, `PUBLICATION_DATE`, `ISBN13`, `ISBN10`, `AUTHORS`.

**`title` is never eligible** — it feeds the dedup `hash`, so an AI-guessed title could silently
merge or split records (EXT-2); a missing title stays `PARSE_ERROR`. `AUTHORS` also feeds the
hash and remains eligible as an accepted trade-off (re-scrapeable, provenance-marked) recorded in
ADR 0003 (EXT-3).

---

## Implementation: reusing the hardened stack (EXT-5)

`scraping/ai_extraction.py` (the engine) and `scraping/ai_common.py` (the shared stack) supply:
- the `<page_html>` fence + untrusted-data framing
- `sanitize_untrusted` (strips ANSI/C0/C1/DEL and U+2028/U+2029)
- the HTML pruner + pre-flight token budget
- model/key resolution
- `UsageAccumulator`
- `sha256(prompt)` replay/record I/O

`extract_missing_fields()` batches **all** missing eligible fields into **one** request per page
(EXT-4) and validates each returned `{value, evidence}` pair through five deterministic gates:

1. Response shape (the expected JSON contract)
2. Verbatim evidence present in the page's visible text
3. Value derivable from that evidence
4. The 03.1 normalizer (ISBN checksum / date parse / authors shape / description length)
5. `sanitize_untrusted`

Any field failing a gate is dropped with a body-free reason code (never logged with page content).

---

## Hook point in the scraper

Inside `scraping/scrape_details.py`'s `scrape_book()`, *after* the deterministic selectors run
and *before* the `BookRecord` is built, `_apply_ai_fallback()`:

1. Computes still-empty eligible fields.
2. If any exist and the shared per-run cap grants a slot, calls
   `extract_missing_fields(await page.content(), …)`.
3. Merges each surviving value **into the raw `book_details` dict** so
   `normalize.from_playwright` canonicalizes it exactly like a deterministic value (an AI-filled
   `authors` list therefore re-derives the dedup hash).

Leanpub's API path is **not** hooked — it has no CSS selectors that can drift.

The concurrency-safe per-run cap is `AICallCap` (a synchronous check-and-increment with no
intervening `await`, so a 10-way-concurrent batch can never overshoot it).
`AIFallbackRuntime` bundles the client/model/usage/cap and is built once at start-up by
`create_ai_fallback()`, threaded directly into `scrape_book` by `scrape-urls` and via
`SearchContext → scrape_candidate` by `search`.

---

## Provenance (EXT-9)

Every field merged by the fallback is listed in `BookRecord.ai_extracted_fields` (sorted field
names; `[]` by default — reserved in schema-2, so this is **not** a schema bump), written by
both backends.

A later deterministic scrape never overwrites good data with AI data: dedup by `hash` keeps the
first record, and the run report flags AI-filled records for re-scrape instead.

One end-of-run `print_log` summary line reports the requests made, fields filled/dropped, whether
the cap was hit, and the summed token usage — **no prices, no key** (EXT-6).

---

## Security contracts

- The pruned page HTML is fenced as untrusted data (`<page_html>…</page_html>`, AIS-1), same as
  `detect-schema`.
- A proposed value's **only** effect is being merged into `book_details` after passing the five
  gates — it is never executed, shelled out, or written anywhere else (AIS-4).
- `sanitize_untrusted` strips control characters and Unicode line separators from every
  AI-returned string before it is validated or stored (AIS-13).
- SDK errors surface only the exception class and HTTP status, **never the response body** (which
  could contain reflected page content).

---

## Tests (never call the real API)

`tests/fixtures/ai_extraction/<site>.replay.json` is hand-authored (`value`/`evidence` verbatim
visible text of a committed fixture, carrying `_provenance`), its key generated by an extension
of `scripts/generate_schema_replays.py`. It is exempt from the corpus `.meta.json`/sha256
integrity checks like `schema_detection/`.

The Chromium golden `tests/mock/scraping/html/test_ai_fallback_goldens.py` runs the unmodified
`scrape_book` against the `cpp_primer_5e` amazon fixture with `DESCRIPTION`/`DESCRIPTION_ALT`
patched to a non-matching selector, recovers the description through replay, and asserts
`ai_extracted_fields == ["description"]` — offline, skipping cleanly without a browser
(`BOOKSCRAPER_CHROMIUM_PATH`).
