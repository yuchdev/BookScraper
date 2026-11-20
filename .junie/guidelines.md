# BookScrapper Junie Guidelines

This repository was ported from a Claude-oriented kit into Junie-native repo guidance, custom agents, skills, and commands. Use the files under `.junie/agents/`, `.junie/skills/`, and `.junie/commands/` as the active Junie configuration; treat `.claude/` as historical/source material only.

## Shared repo expectations

- Prefer small, scoped edits and targeted validation before broad sweeps.
- Use the project's actual Python environment and runner; do not assume `uv` is authoritative if the repo is being run through a venv plus `pip`.
- Keep Python formatting and linting clean after edits. The baseline expectation is `ruff format` plus `ruff check --fix` where safe, then a clean lint/test pass.
- Follow the repo's documented Python conventions when present, including `Optional[T]` rather than `T | None` where the existing standards require it.
- Never weaken tests to get green: no silent assertion reduction, no disabling, no fake passing paths.

## Runtime and data-shape invariants

- There are two distinct entry-point flows: `src/scrape_existing_books.py` for existing-URL scraping and `src/bookscraper/search_and_scrape.py` for discovery scraping.
- The target registry lives in `src/bookscraper/parameters.py`; when adding or changing a target, keep `site_constants` and the website-identification flow aligned.
- Treat the stored book `hash` as the canonical identity. Changes to title/authors/year normalization, hash inputs, or dedup queries are contract-level changes.
- Every processed input URL should land in exactly one output bucket such as the successful-books, failed-books, or other-links outputs.
- Be alert for existing contract mismatches between scraper outputs, dedup logic, JSON document/report shape, and persistence paths; do not assume all current behavior is already internally consistent.

## Testing and verification

- Prefer saved fixtures for parser and scraper tests over live network calls.
- Report the exact validation you ran and what it covered.
- For non-trivial code changes, validate lint plus the narrowest meaningful tests first, then expand only when risk justifies it.
- If documentation, interfaces, or roadmap artifacts change, also run the relevant doc-maintenance command flow.

## Documentation and design records

- Keep `docs/README.md` aligned with added, renamed, or removed documentation files.
- Treat documentation as a graph: if a file path, heading, or public API name changes, update linked Markdown, docstrings, and comments in the same change.
- ADRs belong under `docs/adr/` with zero-padded filenames such as `0001-some-decision.md`.
- Security review notes belong under `docs/security/` when a task warrants a written threat model or audit trail.

## Secret and safety policy

- Never hardcode secrets, tokens, passwords, certificate material, or live connection strings.
- Never print full secret values in analysis, logs, docs, diffs, or reports. Redact to type plus short excerpt only.
- Treat environment variables, local secret files, TLS certificates, logs, JSON URL-list inputs, external HTML/JSON, and attacker-influenced URLs as potentially sensitive or hostile inputs.
- For security-sensitive changes or files, use the `secret-scan` skill and the `security-auditor` agent.

## Native Junie port of Claude hook behavior

Junie does not expose a direct repo-local equivalent of Claude's `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, or MCP-call hook lifecycle here. Do not invent fake hook files for Junie. Instead, preserve the intent as follows:

| Claude hook | Intent | Junie-native equivalent |
|---|---|---|
| `_common.py` | Shared helpers for hook scripts | Split across these repo guidelines plus the `link-check`, `doc-xref`, `dep-audit`, and `secret-scan` workflows |
| `dep_audit.py` | Advisory audit when dependency manifests change | Run the `dep-audit` command when dependency manifests are edited |
| `doc_link_check.py` | Fast outbound Markdown link/anchor check after edits and at session end | Use the `link-check` and `update-docs` commands whenever docs or anchors change |
| `github_audit.py` | Durable audit log for GitHub tool usage | No direct hook equivalent; if GitHub tooling is used, summarize the actions in the final report |
| `guard_bash.py` | Block destructive or production-targeting shell commands | Rely on Junie's built-in command safety rules and require explicit user confirmation for destructive or production actions |
| `post_edit_format.py` | Auto-format edited Python files and log edits | Manually run formatting/lint cleanup for touched Python files before finishing |
| `run_tests.py` | Stop-hook gate on lint plus tests | Junie Code-mode validation plus this repo's verification rules; run meaningful lint/tests before submit |
| `secret_scan.py` | Pre-write secret blocking plus manual scan mode | Use the `secret-scan` skill proactively on changed files and before finishing sensitive work |
| `session_start.py` | Auto-inject branch, recent commits, and priority issues into the session | No direct automatic equivalent; inspect that context manually when it matters to the task |
| `style_fixes.py` | Auto-fix or check repo-specific Python style rules | Apply the style rule manually during code work and keep the affected files lint-clean |

## Native Junie port of Claude loop behavior

- Junie does not expose Claude's `ScheduleWakeup` self-rescheduling loop primitive in this repo configuration.
- The former Claude loops are ported as human- or agent-invoked commands under `.junie/commands/`.
- When a workflow spans many iterations, persist durable progress in the repo's authoritative docs (`status.md`, task `README.md`, ADRs, review docs) and in concise Junie status updates, not in hidden repo-local loop state files.