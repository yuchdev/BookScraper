# Scrapegraph-ai evaluation and proxy-rotation analysis - 2026-10-04

## Scope

- **Subject:** [Scrapegraph-ai](https://github.com/ScrapeGraphAI/Scrapegraph-ai) **v2.3.0** (commit `15ff7ec9`), a
- **Questions:**
  1. Is Scrapegraph-ai useful for `bookscraper`, or does Playwright plus what we already have cover our needs?
  2. Does either codebase, built or planned, rotate proxy servers while scraping?
- **Method:** a static read of the library source (paths below are relative to the checkout) and of our `src/`,
  roadmap and agent guidelines. Nothing was installed or run against live sites, and no LLM call was made.

## Summary

| Question | Answer |
|---|---|
| Adopt Scrapegraph-ai? | **No.** It solves a different problem: an LLM extracts the data from every page. Its fetching layer is Playwright, which we already use, and its extras are stealth/evasion. Claude support is stale, telemetry uploads page content by default, and it brings in a large LangChain dependency tree. |
| Anything worth reusing? | **Ideas, not code.** (a) An LLM extraction fallback for a single drifted field. (b) Markdown conversion to shrink prompts. Both can be built on our existing `anthropic`-based `detect-schema` stack. |
| Does our code rotate proxies? | **No.** There is no proxy support at all, and the roadmap explicitly excludes proxy rotation (task 03.2). |
| Does Scrapegraph-ai rotate proxies? | **Not by itself.** Despite the module name `proxy_rotation.py`, it uses **one** proxy per loader for the whole run, either configured or taken at random from a free public list. Real per-request rotation exists only by delegating to the paid **Scrape.do** service, and that integration has security defects. |

## 1. What Scrapegraph-ai is

An MIT-licensed Python library that describes a scrape as a graph of nodes (fetch → parse → LLM answer), built on
LangChain. It ships ~30 prebuilt graphs (`scrapegraphai/graphs/`), including:

- **`SmartScraperGraph`:** give it a URL and a natural-language prompt, and an LLM returns structured JSON.
- **Multi-page and search variants:** `SmartScraperMultiGraph`, `SearchGraph`, `DepthSearchGraph`.
- **`ScriptCreatorGraph`:** the LLM *writes* a scraper script.
- **Other inputs:** document, CSV, XML, JSON, screenshot and speech graphs.

Requires Python `>=3.12,<4.0`, which is compatible with ours (`>=3.13`).

## 2. Fit with our needs

**Our problem:** structured book metadata from four *known* sites (Amazon, Packtpub, O'Reilly, Leanpub) using
deterministic CSS selectors (`scraping/parameters.py`), hash-based dedup and two storage backends. The LLM is used
only to *maintain* selectors (`bookscraper detect-schema`, propose-only per ADR 0002), never to extract records.

**Scrapegraph-ai's model is the inverse:** the LLM extracts data from every page, every run.

| Concern | Scrapegraph-ai | `bookscraper` today | Assessment |
|---|---|---|---|
| Page fetching | Playwright via `docloaders/chromium.py` (`ChromiumLoader`); optional Selenium + `undetected_chromedriver` | Playwright (`scrape_details.py`, `search_utils.py`) and `httpx` for Leanpub | **No gain**: same engine. |
| Getting past bot walls | `undetected-playwright` stealth: `Malenia.apply_stealth(context)` (`chromium.py` ~L364) and an undetected Chrome driver | None by design. 03.2: *"no CAPTCHA solving, no fingerprint spoofing, no proxy rotation"* | **Conflicts** with our no-evasion policy (task 03.2, scraping-expert guidelines). It also would not legitimately fix the walls we hit (Amazon CAPTCHA, Packt Cloudflare, O'Reilly JS gate; see the 06.0 notes in `status.md`). |
| Extraction | An LLM per page (`GenerateAnswerNode`) | CSS selectors; an LLM only proposes selector fixes | Per-page LLM cost and non-deterministic output for data that selectors return for free and reproducibly. |
| Selector maintenance | `ScriptCreatorGraph` generates code | `detect-schema`: propose-only, cross-sample DOM validation, offline replay goldens, drift exit code | Ours is narrower and safer. Generating code contradicts ADR 0002. |
| Claude support | Through LangChain `init_chat_model(model_provider="anthropic")` (`graphs/abstract_graph.py` ~L159-253) | The official `anthropic` SDK 1.8.0, `claude-sonnet-5` | **Weak** (below). |
| Prompt-injection hardening | None found | `<page_html>` fence, selector rejection rules, bounded evaluation, sanitized reports (04.0/09) | Ours is stronger. |
| Offline / CI | Not designed for it | `--html-file`, `--replay`, `--check-current-only` with no key and no network (04.0/07-08) | Ours fits CI. |

### Claude support gaps

- `langchain-anthropic` is **not** among its dependencies (`pyproject.toml`), so the `anthropic` provider fails
  until it is installed separately.
- The token-limit table (`helpers/models_tokens.py` ~L248-289) stops at Claude 4 (`claude-sonnet-4-20250514`). An
  unknown model such as `claude-sonnet-5` falls back to an **8,192-token** window with only a log warning
  (`abstract_graph.py`, `model_tokens_defaulted`), so long pages are truncated unless `model_tokens` is set by
  hand.
- It is unverified whether the LangChain path sends `temperature`. Sonnet 5 rejects sampling parameters with a 400
  (per the `claude-api` reference used in 04.0/05).

### Telemetry: on by default, and it uploads scraped content

`telemetry/telemetry.py`:

- `g_telemetry_enabled = _check_config_and_environ_for_telemetry_flag(True, config)` makes telemetry **on by
  default**.
- `_build_telemetry_payload` sends `user_prompt`, `json_schema`, **`website_content`**, `llm_response`,
  `llm_model` and `url` to `https://sgai-oss-tracing.onrender.com/v1/telemetry` (`TRACK_URL`), up to 1,000 events
  per session.
- Opting out requires `SCRAPEGRAPHAI_TELEMETRY_ENABLED=false` or `telemetry_enabled = false` in its config file.

This would send third-party page content, which our corpus policy goes to lengths to sanitize, to an external
service by default. That alone disqualifies drop-in adoption.

### Dependency weight

The direct dependencies include `langchain`, `langchain-classic`, `langchain-openai`, `langchain-mistralai`,
`langchain-community`, `langchain-aws`, `langchain-ollama`, `tiktoken`, `ddgs`, `free-proxy`,
`undetected-playwright`, `semchunk`, `simpleeval` and `scrapegraph-py` (the client for its commercial API). For
comparison, our only AI dependency is `anthropic`, which already passes `/dep-audit`. Adopting it would need a full
dependency and license audit, and it would greatly enlarge the supply-chain surface.

### What is worth borrowing (as ideas)

1. **LLM extraction fallback for one drifted field.** When a *needed* selector breaks (e.g. the live Amazon
   `DESCRIPTION` now matches an empty element, a 04.0/08 follow-up), extract only that field with Claude. Build it
   on the existing `detect-schema` stack (sanitizer, `<page_html>` fence, token budget, offline replays), and mark
   records produced that way so they can be told apart. **Planned as task
   [04.1](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md), subtasks 01-03 and 05.**
2. **Markdown conversion before prompting** (`utils/convert_to_md.py`) could shrink prompts compared with our HTML
   pruning. It fits value extraction only: `detect-schema` proposes CSS selectors and needs the HTML structure.
   **Planned as a measured spike,
   [04.1/04](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/04-markdown-snapshot-spike.md).**

## 3. Proxy rotation

### `bookscraper`, built

- `grep -rni proxy src/` finds **nothing**. There is no proxy option for Playwright or httpx.
- One incidental behaviour: the Leanpub calls use `httpx.AsyncClient()` with its defaults (`trust_env=True`), so an
  `HTTP(S)_PROXY` environment variable would be honoured *implicitly*. That is not a supported feature. The fixture
  capture tool explicitly sets `trust_env=False`.
- The only rotation that exists is **User-Agent** rotation from the static `USER_AGENTS` list in
  `scraping/parameters.py`.

### `bookscraper`, planned

- **Task 03.2 (politeness & anti-bot), key constraints:** *"No evasion: this task makes the scraper identify and
  back off, never disguise or bypass (no CAPTCHA solving, no fingerprint spoofing, **no proxy rotation**)."*
- **03.2/05** narrows User-Agent handling: by default the browser's own UA (`USER_AGENT_MODE = "native"`), and an
  optional `"rotate"` limited to UAs matching the installed Chromium major version.
- **05.0/03** (browser and concurrency flags) has no proxy option today. It is the natural home for a *single,
  configured* outbound proxy, if one is wanted (see Recommendations).

**Verdict:** no proxy rotation, built or planned, and that is deliberate.

### Scrapegraph-ai

Three mechanisms, none of which rotates per request on its own:

1. **Static configured proxy.** `utils/proxy_rotation.py` → `parse_or_search_proxy(proxy)` validates a
   `{"server", "bypass", "username", "password"}` dict into a Playwright proxy setting. `ChromiumLoader.__init__`
   resolves it **once** (`chromium.py` L91: `self.proxy = parse_or_search_proxy(proxy) if proxy else None`) and
   passes the same `proxy=` to every `chromium.launch(...)`. That gives one proxy for the loader's lifetime.
2. **"Broker" mode (free public proxies).** With `server: "broker"`, `_search_proxy` calls
   `search_proxy_servers(max_shape=1)`. That uses the `free-proxy` package (`FreeProxy`) to scrape public
   open-proxy lists, liveness-checks the candidates, and returns **one** server, again fixed for the loader's
   lifetime. Despite the module name, there is no per-request or per-failure rotation.
   - **Risk:** public open proxies are run by unknown parties. They can read or alter plain-HTTP traffic, log every
     request, inject content, and they are unreliable. Not acceptable for us.
3. **Scrape.do (paid third-party service).** `docloaders/scrape_do.py` → `scrape_do_fetch(token, target_url,
   use_proxy, geoCode, super_proxy)`, wired through `nodes/fetch_node.py` (~L343-354). This is the **only** real
   rotation, and it happens on Scrape.do's side (its residential/mobile pool via `super_proxy`, optionally
   geo-pinned). Defects found:
   - **TLS verification disabled** in proxy mode: `requests.get(..., proxies=..., verify=False, ...)`. That allows
     man-in-the-middle interception of every proxied request.
   - **API token in plaintext:** proxy mode builds `http://{token}:@proxy.scrape.do:8080`, and API mode sends
     `http://api.scrape.do?token={token}&url=...`. Both put the credential in cleartext HTTP, so it shows up in
     logs and on the wire.
   - **A `super_proxy` bug:** the `super` parameter is sent only when `geoCode` is set (`params = {...} if geoCode
     else {}`), so `super_proxy=True` without a geo code is silently ignored.

**Verdict:** Scrapegraph-ai offers proxy *configuration* and a free-proxy *picker*, not rotation. True rotation is
outsourced to a paid service, and that integration weakens transport security.

## Findings

| # | Severity | Finding | Evidence |
|---|---|---|---|
| F-1 | HIGH | Telemetry on by default uploads scraped page content, prompts, schema, LLM output and URL to a third-party endpoint | `telemetry/telemetry.py` (`TRACK_URL`, `_build_telemetry_payload`, default `True`) |
| F-2 | HIGH | Scrape.do proxy mode disables TLS verification (`verify=False`) | `docloaders/scrape_do.py` `scrape_do_fetch` |
| F-3 | MEDIUM | The Scrape.do API token is sent over cleartext HTTP, in the URL or in proxy credentials | `docloaders/scrape_do.py` |
| F-4 | MEDIUM | Broker mode sources proxies from free public lists (untrusted operators) | `utils/proxy_rotation.py` `search_proxy_servers` |
| F-5 | MEDIUM | Stealth/undetected browsers contradict our no-evasion policy | `docloaders/chromium.py` (`Malenia.apply_stealth`, `undetected_chromedriver`) |
| F-6 | LOW | Stale Claude model table, with a silent fallback to an 8,192-token window; `langchain-anthropic` not a dependency | `helpers/models_tokens.py`, `graphs/abstract_graph.py`, `pyproject.toml` |
| F-7 | LOW | `super_proxy` is ignored unless `geoCode` is set | `docloaders/scrape_do.py` |
| F-8 | INFO | "Proxy rotation" is a misnomer: one proxy per loader | `chromium.py` L91, `proxy_rotation.py` |

These findings are about Scrapegraph-ai. None is a defect in `bookscraper`.

## Verdict

**Do not adopt Scrapegraph-ai** as a dependency. Playwright plus the existing `bookscraper` stack (deterministic
selectors, the real-HTML corpus and drift check, and the propose-only `detect-schema`) covers our needs.
Scrapegraph-ai's distinctive features are either outside our model (per-page LLM extraction), against our policy
(stealth/evasion), or unsafe by default (telemetry, Scrape.do transport).

## Recommended actions

1. **Do not add `scrapegraphai` to `pyproject.toml`.** If it is ever evaluated hands-on, set
   `SCRAPEGRAPHAI_TELEMETRY_ENABLED=false` first and run it against fixtures only.
2. **Decision for the user:** optionally add a **single, fixed outbound proxy** setting (e.g. `--proxy` /
   `BOOKSCRAPER_PROXY`, passed to Playwright's `launch(proxy=...)` and to `httpx` with `trust_env=False` and an
   explicit proxy) in **05.0/03**, next to the planned Chromium executable-path option. This serves network
   routing, a corporate egress or region-specific content. It is not block evasion, and it keeps TLS verification
   on and credentials out of logs (consistent with the redaction rules in `docs/security/`).
3. **Keep rotating proxies out of scope**, per 03.2, unless the policy is deliberately revisited. Rotation to get
   past blocks conflicts with the sites' terms and the project's dual-use boundary.
4. **LLM extraction fallback** for a single drifted field, built on the existing hardened `detect-schema` stack:
   planned as roadmap task
   [04.1](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md).
5. **Housekeeping:**
   - add `/Scrapegraph-ai` (and `restore-atlas.txt`) to `.gitignore`, so the local symlink can't be committed;
   - pytest collection is already limited to `tests/` (`testpaths`, commit `96099eb`), after the symlink caused 44
     collection errors.
