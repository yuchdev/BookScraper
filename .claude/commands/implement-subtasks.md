---
name: implement-subtasks
description: Drives one task to completion, implementing exactly one subtask per iteration. Resolves the task from a milestone's plan.md, gates on status.md, then walks the task README's subtask queue - implementing, verifying, and running quality gates per subtask. Updates the task README after each subtask and status.md when the task lands. Self-terminates when the task reaches ✅ Complete.
invoke: /loop implement-subtasks <task>
terminates-when: The target task's row in the milestone status.md shows ✅ Complete
---

# implement-subtasks - per-subtask implementation loop for one task

This loop takes **one task** (e.g. "Scraping Engine Hardening", task `3.0`) and drives it to
completion. Each iteration implements **exactly one** pending subtask, verifies it against
its spec, runs the relevant quality-gate skills, and records progress. The loop
re-schedules itself until the whole task is ✅ Complete.

The navigation chain it follows every iteration:

```
docs/roadmap/{NNNN}-{milestone-slug}/plan.md          → find the task in ## Tasks
        └─ status.md                                   → gate on ## Current status
              └─ {TT.t}-{task-slug}/README.md          → read ## Subtasks queue
                    └─ {NN}-{subtask-slug}.md           → the spec for THIS iteration
```

---

## Argument

`<task>` identifies which task to drive. Accepted forms (most specific wins):

- `0001/1.0` or `0001 1.0` - explicit milestone + task number.
- `1.0` - task number only; resolve the milestone by scanning every
  `docs/roadmap/{NNNN}-*/plan.md` `## Tasks` table for that number.
- `"Scraping Engine Hardening"` - task name; matched (case-insensitive, substring) against the
  `Name` column of the same tables.

If the argument matches zero or more than one task, **stop** and ask the user to
disambiguate - do not guess.

**Number → folder mapping:** a task number `T.t` maps to the folder prefix `{TT.t}`
zero-padded to two digits before the dot: `3.2 → 03.2`, `6.1 → 06.1`, `8 → 08.0`. The
matching task folder is `docs/roadmap/{NNNN}-{milestone-slug}/{TT.t}-{task-slug}/`.

---

## Per-run state cursor (token-economy core)

This loop re-fires into the **same growing conversation**, so anything the main loop reads
directly stays in context for *every* subsequent iteration. The milestone path, task folder,
and subtask queue do **not** change within a run - reparsing the ~500-line `plan.md` and
~150-line `status.md` each wakeup would add ~10k tokens of pure rediscovery per iteration.
Instead, resolve once and persist a small cursor:

```
.claude/state/implement-subtasks-{task-slug}.json
```
```json
{
  "milestone_path": "docs/roadmap/0001-generic-implementation",
  "task_number": "3.0",
  "task_name": "Scraping Engine Hardening",
  "task_folder": "docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening",
  "subtask_heading": "## Subtasks",
  "subtask_queue": [
    {"nn": "01", "slug": "page-lifecycle-and-leaks", "status": "complete"},
    {"nn": "02", "slug": "typed-scrape-result", "status": "not_started"}
  ],
  "next_index": 1
}
```

The cursor is a **derived cache**, not the source of truth: the task `README.md` and
`status.md` remain authoritative. Refresh the cursor only when a subtask closes (Step 7).
`{task-slug}` is a filesystem-safe slug of the `<task>` argument.

## Iteration algorithm

### Step 1 - load or build the run cursor

Check for the cursor file.

- **Warm path - cursor exists:** read it (~300 tokens) and go straight to Step 3. Do **not**
  re-read `plan.md`, `status.md`, or the README - they are already distilled into the cursor.
- **Cold path - no cursor (first iteration, or it was deleted):** do the discovery **inside a
  cheap subagent**, not in the main loop, so the large file bodies never enter the persistent
  conversation context. Spawn an `Explore` agent (Haiku-tier) with this instruction:

  > Resolve task `<task>` for the implement-subtasks loop. (1) In each
  > `docs/roadmap/{NNNN}-*/plan.md` `## Tasks` table, find the row matching the task number or
  > name; record milestone path, task number `{TT.t}`, and name. Number→folder: pad to two
  > digits before the dot (`3.2 → 03.2`); resolve `…/{TT.t}-{task-slug}/`. (2) In that
  > milestone's `status.md` `## Current status` table, read the task row's Status cell. (3) In
  > the task `README.md`, find the subtask table (heading `## Subtasks`, or legacy
  > `## Subtask overview`) and list each subtask's `{NN}`, slug, and status.
  > **Return ONLY** a JSON object: `{milestone_path, task_number, task_name, task_folder,
  > subtask_heading, status_gate, subtask_queue:[{nn,slug,status}]}`. No prose, no file bodies.

  Branch on the returned `status_gate`:
  - `⬜ Not started` / `🔶 In progress` → write the cursor (`next_index` = first pending
    subtask) and continue to Step 3.
  - `✅ Complete` → do **not** re-implement. Spawn `subtask-verifier` against the task folder,
    report its verdict, and **stop** (do NOT reschedule). A completed task is verified, not rebuilt.
  - Argument matched zero or >1 task → **stop** and ask the user to disambiguate.
  - No decomposed folder / no README → **stop**: this loop drives a decomposed task.
  - Missing `status.md` (milestones 0002-0004) → the agent returns `status_gate: "absent"`;
    treat as `⬜ Not started` and proceed (the table is created in Step 7).

If the main loop ever needs a single field it doesn't have, prefer a narrow `grep` of one row
(e.g. `grep -n "^| 3.2 " <status.md>`) over a full `Read` of the file.

### Step 2 - pick the next pending subtask

From the cursor's `subtask_queue`, choose the **first** subtask whose status is `🔶`/Partial,
else the first `⬜`/Not started. Partial is prioritised: unfinished work carries higher
regression risk than new work. Record its `{NN}`, slug, and spec path
`{task_folder}/{NN}-{subtask-slug}.md`.

If every subtask is `✅ Complete` (or deferred per the Step 7 completion rule), jump straight
to Step 7 to close the task.

Read **only the chosen subtask spec** in full - it is the authoritative brief for this
iteration (Files, symbols/fields, validators, tests, success criteria, constraints). This is
a *different* file each iteration, so it is necessary cost, not repeated cost.

### Step 3 - implement via the dev-fleet agents

Delegate the subtask spec to the appropriate fleet agent. Independent units within one
subtask may run concurrently; units with stated ordering dependencies run sequentially:

| Spec role keyword  | Fleet agent                             | Agentic action               |
|--------------------|-----------------------------------------|------------------------------|
| `Architect`        | `app-architect`                         | evaluate design              |
| `Python Expert`    | `python-expert`                         | subtask implementation       |
| `Testing Expert`   | `testing-expert` then `python-expert`   | QA then fix regressions      |
| `Security Auditor` | `security-auditor` then `python-expert` | advisory then implementation |
| `Docs Writer`      | `docs-writer` or `docs-updater`         | write or update docs         |

Brief each coder/QA agent with **paths and section references, not pasted file bodies** -
pasting a spec bills it twice (once in your context, once in theirs):

- The subtask spec **path** (`{task_folder}/{NN}-{subtask-slug}.md`) - the agent reads it itself. Add a one-line scope note, not the spec text.
- The cross-cutting rules in `@docs/dev/python_coding_standard.md` - full annotations, ruff,
  RAII, this project's log-redaction mechanism (if it has one), no bare `except:`, unit +
  mocked-integration tests.
  Also name the repo-root `CLAUDE.md` and its key sections:
  - **"Known inconsistencies to watch for"**: `init_template.py` and `release_package.py` are
    cookiecutter leftovers outside the scraping workflow; run output artifacts (`books.json`,
    `failed_books.csv`, etc.) are gitignored and written to the repo root — don't add new default
    output filenames without gitignoring them.
  - **"Running"**: `--store-backend {mongo|json}` is required; invocation is `uv run bookscraper
    <subcommand>`; six subcommands are `scrape-urls`, `search`, `rotate-cert`, `detect-schema`,
    `migrate`, `doctor` — see `docs/cli/cli.md` for the full reference.
  - **"Testing"**: the three-tier suite (`tests/unit/`, `tests/mock/`, `tests/integration/`) is the
    real test infrastructure; integration tests are excluded by default (`-m "not integration"`);
    the canonical full-suite command is `uv run pytest --cov`; coverage must stay ≥ 90%.
  - **"Architecture"**: import direction is one-way — `commands/` → `scraping/` + `backends/`;
    `backends/` → `book_utils.py`; nothing under `backends/` or `scraping/` ever imports from
    `commands/`.
  If the milestone's `plan.md` carries its own cross-cutting guidelines section, name it for the
  agent to read.

**Return discipline (keeps the parent context lean):** instruct every spawned agent to return
a **compact structured summary** - files changed, symbols added, pass/fail, coverage delta -
and to **never echo diffs, file contents, or full test logs**. A subagent's internal reads and
reasoning are discarded with its context; only its final message persists in the loop, so that
message is the only thing you pay to carry forward.

**Security-sensitive subtasks** (auth middleware, anything parsing untrusted or
attacker-influenced input, external-service credentials or tokens): spawn `security-auditor`
**before** coding begins. It reads the spec and writes a threat model to
`docs/security/<date>-<topic>.md`; the coder picks that up as an additional input. A CRITICAL
finding blocks merge - stop the loop and require human sign-off.

### Step 4 - stop-and-ask when implementation needs a decision

During implementation the loop **may pause to ask the user** whenever the spec is ambiguous,
a design choice materially shapes the final implementation, or a deviation from the spec
looks warranted. Use `AskUserQuestion` with concrete options, apply the answer, and
continue the same iteration. Prefer asking to guessing for anything that is expensive to
reverse. (Routine, unambiguous implementation does **not** pause - only genuine forks.)

### Step 5 - verification gate

Run **only this subtask's** verification, not the whole suite: scope `pytest` to the
subtask's own test file(s) and use `-q` (capture the summary line, not per-test `-v` output),
plus `python .claude/hooks/style_fixes.py --check <path>`. Do **not** pipe full pytest output into context -
a green run is one summary line; on failure, tail only the failing assertions. The full suite
runs once at task completion (Step 7), and the Stop hook gates the session end regardless.

On failure:

- Diagnose, delegate the fix to `python-expert`, re-run. Repeat until green or until the
  failure is clearly a spec ambiguity needing human input - in that case stop the loop and
  surface the blocker.

### Step 6 - spec-compliance gate (`/verify-subtask`)

With verification green, run `/verify-subtask {subtask-path}` where `{subtask-path}` is the
spec path from Step 2 (relative to `docs/roadmap/`, without the `.md` extension). It spawns
`subtask-verifier` for a compliance matrix + verdict:

- **PASS** → record a **single line** (`✓ verify-subtask PASS`), not the full matrix - the
  matrix only earns its tokens when there is something to act on. Continue to Step 7.
- **PARTIAL** → log the deviation list (this is worth carrying); continue, and feed those
  deviations into the `/pr-review` context so the reviewer sees them.
- **FAIL** → delegate the blocking gaps back to `python-expert` (max 1 retry), re-run
  Step 5 then this step. If still FAIL after one retry, surface the gaps and stop the loop.

Then run the applicable quality-gate skills. **Skip any gate whose trigger condition is false -
do not spawn an agent that can only find nothing.** Each gate is module-scoped, never tree-wide:

| Condition                                                | Skill                                                                                                                                |
|----------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| Always                                                   | `/test-gap src/bookscraper/<subtask-module>/` - report coverage delta; if < 90% delegate missing tests to `testing-expert`. |
| `pyproject.toml` or any `requirements*.txt` changed      | `/dep-audit`                                                                                                                         |
| Any file under an API route, auth, or middleware path changed, or anywhere handling credentials/tokens | `/secret-scan src/bookscraper/<changed-module>/` |
| Subtask touches docs / public API                        | `/link-check docs/` - inbound references still resolve.                                                                              |

### Step 7 - record the subtask, then check the task

**After each subtask completes:**

1. Edit the task README `{task_folder}/README.md` - set **only this subtask's** row Status
   cell to `✅ Complete` (or `🔶`/deferred with a one-line note if only partially done). A
   targeted row edit, not a rewrite.
2. **Refresh the cursor** (`.claude/state/implement-subtasks-{task-slug}.json`): update
   this subtask's `status` and advance `next_index`. This keeps the next iteration on the warm
   path - it never has to re-read the README.

**Then test the task-completion condition** over the cursor's `subtask_queue`:

> The task is **✅ Complete** when the feature is overall created and working - even if 1-2
> **minor** subtasks are explicitly deferred. Do not block completion on optional or
> enhancement subtasks (e.g. a caching optimization) once the core feature is delivered and
> green. A *major* subtask still `⬜`/`🔶` means the task is **🔶 In progress**, not complete.

- **Task not yet complete** → go to Step 8 (reschedule for the next subtask).
- **Task complete** → this is the **one** place the whole suite runs: execute
  `uv run pytest --cov` once (the canonical command; summary line only), then run
  `/pr-review` (must reach LGTM or have REQUEST_CHANGES resolved). Then update
  `{milestone_path}/status.md` `## Current status` with a **targeted row edit**:
  - Set the task row's Status cell to `✅ Complete` (note any deferred subtasks inline,
    e.g. "✅ Complete (subtask 11 deferred)").
  - Update the Tests cell with the test files introduced.
  - Append/extend the `## Task N - <Name>` summary paragraph: what was delivered, key
    implementation decisions, coverage numbers, and any deferred items.
  - Run `/link-check docs/roadmap/` to confirm the edits keep every link/anchor resolving.
  - **Delete the cursor** `.claude/state/implement-subtasks-{task-slug}.json` (the run
    is over; a stale cursor would mislead a future invocation).
  - **Stop** (do NOT reschedule) - the task is done.

### Step 8 - reschedule

Call `ScheduleWakeup` with:

- `prompt`: the literal `/loop implement-subtasks <task>` (the **same** `<task>` argument).
- `delaySeconds`: `270` - under the 300 s prompt-cache TTL, so the cached prefix (system
  prompt + this loop file + `CLAUDE.md`) is reused at ~10× lower cost on the next wakeup.
- `reason`: "advancing to next pending subtask of task {TT.t} <Name> after completing
  subtask {NN}".

> Cache caveat: a heavy `python-expert` step that runs longer than ~5 min blows the TTL
> regardless, so the warm cache mainly benefits the fast gate/verify iterations. The cursor
> (not the cache) is what guarantees cheap rediscovery; the cache is a bonus on top.

---

## Task-specific notes

Most tasks need nothing here - Steps 3-6 already cover the general case. Add a
`### Task {TT.t} - <Name>` subsection only when a specific task carries a real gotcha future
iterations must not rediscover from scratch: a non-obvious ordering dependency between its
subtasks, a security-sensitive subsystem that always needs `security-auditor` before coding
starts, an artifact category (generated code, vendored fixtures, build output) that should
skip the usual lint/typing gate, or a dependency that must land before a subtask can pass.
Keep each note to what the next iteration needs to act correctly on - not a running
commentary on the task.

---

## Token-economy invariants

Because the loop re-fires into one growing conversation, the cost that compounds is whatever
**permanently lands in the main-loop context**. Hold these every iteration:

1. **Resolve once, cache forever (per run).** Steps 1-2 read the cursor (~300 tokens), never
   re-parse `plan.md`/`status.md`/README. Cold-start discovery happens *inside a subagent* so
   the file bodies never enter the persistent context.
2. **Read narrow.** Grep single rows / `offset`+`limit` to a table; never full-`Read` a large
   doc from the main loop. Full reads of the *current subtask spec* are the only exception
   (necessary, and a different file each time).
3. **Delegate, then demand terse returns.** Push reads, analysis, and reasoning into subagents
   (discarded with their context); require compact structured summaries back - no diffs, file
   bodies, or full test logs. `verify-subtask` PASS = one line.
4. **Run the minimum gate.** Subtask-scoped tests with `-q`; full suite once at task close;
   skip any quality gate whose trigger is false; module-scope the ones that fire.
5. **Keep narration terse and lean on `/compact`.** A status line, not a recap - the main
   loop's "thinking out loud" persists; a subagent's does not. On a long task, `/compact`
   between iterations resets the accumulated tail.

## Termination conditions

| Condition                                           | Action                                       |
|-------------------------------------------------------|--------------------------------------------------|
| Target task reaches `✅ Complete` (Step 7)          | Update status.md, print summary, do NOT reschedule |
| Task already `✅ Complete` at Step 1                | Run `subtask-verifier`, report, do NOT reschedule  |
| Argument matches zero or >1 task                    | Stop, ask the user to disambiguate           |
| Verification fails after the fix retries (Step 5)   | Surface the blocker, stop the loop           |
| `subtask-verifier` FAIL after one retry (Step 6)    | Surface blocking gaps, stop the loop         |
| `security-auditor` issues a CRITICAL finding        | Stop the loop, require human sign-off        |
| `/pr-review` returns REQUEST_CHANGES after 2 rounds | Stop the loop, surface the diff              |

---

## Loop ↔ skill relationship

This loop is the **sentence**; the skills are its **vocabulary**. It calls `/verify-subtask`
(spec compliance), `/test-gap` (coverage), `/dep-audit`, `/secret-scan`, `/link-check`, and
`/pr-review` at the right checkpoints, and delegates implementation to the dev-fleet agents
listed in `agent-orchestrator.md`'s roster. One level up,
[implement-milestone.md](implement-milestone.md) drives a whole milestone by executing this
loop's Steps 2-6 per iteration with milestone-scoped overrides (see its composition
contract); this file remains the authority on all per-subtask mechanics.
