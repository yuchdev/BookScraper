---
name: implement-milestone
description: Drives an entire milestone to completion. Cold-starts with a deep research phase - reads the milestone plan.md and status.md, reconciles the spec against as-built code, and authors specs for any decomposition gaps - then executes every task in dependency order through the implement-subtasks iteration algorithm, one subtask per iteration. Updates status.md after every completed subtask and appends a per-task detail record at each task close. Self-terminates when every task row in status.md shows ✅ Complete (or a ratified deferral) and the milestone exit gates pass.
invoke: /loop implement-milestone <milestone>
terminates-when: Every task row in the milestone status.md shows ✅ Complete (or a ratified deferral) AND the milestone exit gates have passed
---

# implement-milestone - milestone-level execution loop

This loop drives **one whole milestone** (e.g. `docs/roadmap/0002-notification-delivery/`) to
completion. It is the level above [implement-subtasks.md](implement-subtasks.md): that
loop builds one task; this one researches the milestone, sequences its tasks by their
dependency graph, and executes each task *through* the implement-subtasks algorithm -
still exactly **one subtask per iteration**, because the subtask is the atomic unit of
verified progress at every level.

The navigation chain, one level up from implement-subtasks:

```
docs/roadmap/{NNNN}-{milestone-slug}/plan.md      → tasks, dependency graph, shared contracts
        └─ status.md                               → per-task state gate + decisions record
              └─ {TT.t}-{task-slug}/README.md      → the task's subtask queue
                    └─ {NN}-{subtask-slug}.md       → the spec for THIS iteration
```

---

## Argument

`<milestone>` identifies which milestone to drive. Accepted forms (most specific wins):

- `0002` - milestone number; resolves `docs/roadmap/0002-*/`.
- `0002-notification-delivery` or `notification-delivery` - the folder slug (full or suffix).
- `"Notification Delivery Pipeline"` - the milestone title, matched (case-insensitive,
  substring) against each `plan.md`'s H1.

These are illustrative - a fictional example milestone, not one this preset ships. The
shapes below (multi-task dependency graph, a security-sensitive task, shared contracts)
are what a real milestone looks like; substitute your own project's actual milestone
argument forms once one exists.

If the argument matches zero or more than one milestone, **stop** and ask the user to
disambiguate - do not guess. If the folder exists but has no `plan.md`, **stop**: this
loop executes a specified milestone; writing the specification is `app-architect`'s
job, not an execution loop's.

---

## Composition contract with implement-subtasks

**This loop embeds the implement-subtasks algorithm; it never spawns it.** A loop owns
its own `ScheduleWakeup`: if this loop rescheduled with `/loop implement-subtasks
<task>`, control would pass to the task loop permanently - implement-subtasks
terminate by *not* rescheduling, so there is no wakeup left to return to the
milestone. Instead, each iteration of this loop executes **Steps 2-6 of
[implement-subtasks.md](implement-subtasks.md) verbatim, by reference** (pick subtask →
delegate to the fleet → stop-and-ask on forks → verification gate → `/verify-subtask` +
quality gates), and overrides only the boundary steps that implement-subtasks defines
for a *single-task* run:

| implement-subtasks step  | Milestone-run override                                                                                       |
|--------------------------|--------------------------------------------------------------------------------------------------------------|
| Step 1 (cursor)          | The cursor is milestone-level (below); the task-level fields implement-subtasks needs are embedded in it     |
| Step 7, "task complete"  | Do **not** stop: run the task-close gate, record the task, advance the task queue (Step M4 below)            |
| Step 7.1-7.2 (recording) | Additionally update `status.md` after **every** subtask, not only at task close (Step M3 below)              |
| Step 8 (reschedule)      | Reschedule with `/loop implement-milestone <milestone>` - always the milestone prompt, never the task prompt |

Everything else in implement-subtasks - the fleet routing table, briefing discipline
(paths and anchors, never pasted bodies), return discipline, stop-and-ask rules, the
verification and spec-compliance gates - applies unchanged and is **not** duplicated
here. If the two files ever disagree about a per-subtask mechanic, implement-subtasks
win; if they disagree about task sequencing or milestone state, this file wins.

---

## Milestone cursor (token-economy core)

Resolve once, then never re-read `plan.md`/`status.md` wholesale in the main loop:

```
.claude/state/implement-milestone-{milestone-slug}.json
```
```json
{
  "milestone_path": "docs/roadmap/0002-notification-delivery",
  "milestone_title": "Notification Delivery Pipeline",
  "phase": "execute",
  "task_queue": [
    {"tt": "01.0", "slug": "delivery-queue-foundation", "status": "complete",
     "depends_on": [], "security_sensitive": false, "subtasks_done": 4, "subtasks_total": 4},
    {"tt": "02.0", "slug": "retry-and-backoff-policy", "status": "in_progress",
     "depends_on": ["01.0"], "security_sensitive": false, "subtasks_done": 1, "subtasks_total": 3},
    {"tt": "03.0", "slug": "webhook-signing", "status": "not_started",
     "depends_on": ["01.0"], "security_sensitive": true, "subtasks_done": 0, "subtasks_total": 4},
    {"tt": "04.0", "slug": "delivery-dashboard", "status": "not_started",
     "depends_on": ["02.0", "03.0"], "security_sensitive": false, "subtasks_done": 0, "subtasks_total": 3}
  ],
  "current_task": {
    "task_folder": "docs/roadmap/0002-notification-delivery/02.0-retry-and-backoff-policy",
    "subtask_queue": [
      {"nn": "01", "slug": "backoff-schedule-model", "status": "complete"},
      {"nn": "02", "slug": "...", "status": "not_started"}
    ],
    "next_index": 1
  },
  "research_digest": {
    "contracts_anchor": "plan.md#shared-contracts-authoritative",
    "contracts": ["C1 delivery-attempt schema", "C2 idempotency-key format"],
    "exit_gates": ["full regression", "security-auditor pass (Task 03.0 surface)"],
    "gap_dispositions": ["Task 04.0 implied a dashboard-auth subtask the README lacked -> authored 04.0/03-dashboard-auth.md"]
  },
  "decisions": ["03.0 signing algorithm ratified as HMAC-SHA256 2026-02-03 (see status.md Notes & decisions)"]
}
```

The cursor is a **derived cache**; `plan.md`, `status.md`, and the task READMEs remain
the source of truth. `current_task` is the embedded implement-subtasks cursor for the
in-flight task. `research_digest` holds one-line pointers (anchor and label), never
copied contract text - agents are briefed with the anchors and read the plan
themselves. Refresh the cursor only at subtask close (M3) and task close (M4).

## Phase R - cold-start research (once per run)

Runs only when no cursor exists. This phase is what makes the loop safe on a large
piece of functionality: it establishes what the plan promises, what already exists,
and what is missing - **before** any implementation. All heavy reading happens
**inside subagents** that return structured summaries; the file bodies never enter
the persistent loop context.

### R1 - digest the plan

Spawn an `Explore` agent to read `{milestone_path}/plan.md` in full and return JSON
only: the `## Tasks` table rows; the dependency graph (explicit section if present,
else numeric order); the shared-contracts section's anchor plus a one-line label per
contract; any per-task security/risk markers (e.g. "security-sensitive - requires a
`security-auditor` pass"); and the milestone's own exit gates if the plan or its
closing task defines them.

### R2 - classify milestone state from status.md

Read `status.md` (or note its absence). Classify:

- **NEW** - no status.md, or every row `⬜ Not started` → full run ahead. If the file
  is missing, create it now from the skeleton shape (`## Current status` table with
  one row per task, all `⬜`, plus the legend) so every later update is a row edit.
- **IN PROGRESS** - a mix of `✅`/`🔶`/`⬜` → resume. Trust `✅` rows *provisionally*,
  pending R3.
- **DIVERGED** - the `## Notes & decisions` section records supersession or
  ratified redesigns → read those notes into `decisions` before anything else; they
  override the plan's per-task specs where they conflict.
- **COMPLETE** - every row `✅` → do **not** re-implement. Spot-verify (R3 probes on a
  sample), report the milestone's standing, and **stop** without rescheduling.

### R3 - as-built reconnaissance (code is the truth, status is a cache)

For every task not marked `✅`, spawn a cheap probe: do the artifacts named in the
task's `Output` column already exist in the codebase? Three outcomes per task:

- **absent** → normal pending task.
- **present and matching the spec** → status.md is stale; mark the task for a
  `subtask-verifier` pass instead of implementation (verify, record, don't rebuild).
- **present but shaped differently** → a live divergence (someone implemented the
  idea another way - the most dangerous state, because blind execution would build a
  duplicate). Route to the divergence protocol below **before** the task is queued.

### R4 - decomposition audit and gap planning

Cross-check the plan against the decomposition on disk: every task in the `## Tasks`
table has a folder, a `README.md` with a subtask table, and one spec file per subtask
row; every shared contract and exit gate is exercised by at least one subtask. For
each gap:

- **Missing decomposition for promised scope** (a task folder or subtask spec that
  the plan clearly implies) → delegate `app-architect` to author the missing spec
  file(s) into the task folder, matching the sibling specs' format. This is the
  "detailed plan for facts not covered in tasks and subtasks" - it is written down
  as real spec files, never held only in loop memory.
- **Contract-level ambiguity or genuinely new scope** → `AskUserQuestion`. An
  execution loop fills in missing *decomposition*; it never quietly extends the
  milestone's *scope*.

Record every gap and its disposition in `status.md` under `## Notes & decisions`
(create the section if absent) - one line each, so the audit trail survives the run.

### R5 - write the cursor and enter the execute phase

Build `task_queue` (dependency-ordered), `research_digest`, and `decisions`; write
the cursor; set `phase: "execute"`; proceed to Step M1 in the same iteration if
budget allows, else reschedule.

**Milestone exit gates: this project defines none beyond "full suite green +
`/pr-review`" - do not invent any.** There is no CI configuration, no coverage
threshold, no lint config, and no integration or benchmark suite in the repository;
`tests/bookscraper_test.py` is an empty `unittest` placeholder and
`tests/connection_test_password.py` / `tests/connection_test_tls.py` / `tests/connection_test_settings.py` are
standalone MongoDB connectivity scripts that pytest does not collect. When a milestone's own `plan.md` names gates, those are the gates;
otherwise R1 records an empty `exit_gates` list.

## Iteration algorithm (execute phase)

### Step M1 - load the cursor

Warm path only reads the cursor (~400 tokens). Never re-read `plan.md` or `status.md`
wholesale; a single necessary field is a `grep` of one row.

### Step M2 - select the active task

If `current_task` is in flight, continue it. Otherwise, pick the first `task_queue`
entry whose status is pending and whose `depends_on` are all `complete` - plan order
within a parallel-eligible group (this loop is one conversation, so "parallel" tasks
still execute serially; parallelism lives *inside* a subtask, at the agent level, per
implement-subtasks Step 3). If no task is eligible but pending tasks remain, the
dependency graph is cyclic or blocked on a deferral - **stop** and surface it.

On first entering a task: build `current_task` from the task README (via a cheap
subagent, as implement-subtasks Step 1 does); and if the task is flagged
`security_sensitive`, spawn `security-auditor` on the task's spec **before the first
subtask** - its threat model becomes a standing input for every coder briefing in
this task (implement-subtasks' per-subtask trigger still applies on top).

### Step M3 - execute ONE subtask

Run implement-subtasks **Steps 2-6** against `current_task`, with one milestone-run
addition to the briefing: include the plan's shared-contracts anchor
(`research_digest.contracts_anchor`) plus the names of the contracts this task
touches, so every agent reads the authoritative contract instead of re-deriving it.

Then close the subtask with implement-subtasks Step 7.1-7.2 (task README row edit and
cursor refresh) **plus the milestone addendum - a `status.md` update after every
completed subtask** (this is a hard rule of this loop, not an option): a targeted
edit of the task's row in `## Current status`, e.g. Status cell
`🔶 In progress (3/6 subtasks)`. One row, one edit; never rewrite the table.

### Step M4 - task close

When the task's completion condition holds (implement-subtasks Step 7's rule: core
feature delivered and green; 1-2 *minor* subtasks may be explicitly deferred), run
that step's task-complete actions - full suite once, `/pr-review` to LGTM, targeted
`status.md` row flip to `✅ Complete` - but **do not stop**. Additionally:

1. Append a per-task detail section to `status.md` (`### Task {TT.t} - {Name} (✅
   {date})`): a **Delivered** list and a **Tests / gate** summary, written from the
   task cursor and agent return summaries - never by re-reading diffs.
2. If the as-built shape diverged from the task's spec (superset, redesign, dropped
   subtask), append a **Reconciliation note** recording the disposition of each
   affected subtask spec: *consumed* / *superseded by <what>* / *not applicable*.
   Future readers of the decomposition must be able to tell which spec files still
   describe reality.
3. Mark the task `complete` in `task_queue`, clear `current_task`, refresh the cursor.

### Step M5 - milestone close

When every `task_queue` entry is `complete` (or carries a ratified deferral recorded
in `## Notes & decisions`):

1. Run the milestone exit gates from `research_digest.exit_gates`. If the plan
   defines a closing/integration task, its subtasks *are* the gates and have already
   run as Step M3/M4 - do not run them twice; run only whatever the digest lists
   beyond them.
2. Run `/link-check docs/roadmap/` - the run has edited status.md, READMEs, and
   possibly authored new spec files.
3. Final `status.md` pass: gate-status line under the table (which gates ran, verdict,
   date), and flip the roadmap index (`docs/roadmap/README.md` / `roadmap.md`) if it
   tracks milestone state.
4. **Delete the cursor** and **stop** (do NOT reschedule). Print a closing summary:
   tasks delivered, gates passed, deferrals, and decisions ratified during the run.

### Step M6 - reschedule

Call `ScheduleWakeup` with:

- `prompt`: the literal `/loop implement-milestone <milestone>` (the **same** argument).
- `delaySeconds`: `270`.
- `reason`: "milestone {NNNN}: advancing to subtask {NN} of task {TT.t} ({done}/{total}
  tasks complete)".

---

## Divergence & decision protocol

The hardest real-world state (drawn from a production milestone run): a task's design
already exists in the codebase **in a different shape** than its spec - a superset, a
refactor, a rename. Blind execution builds a duplicate; blind acceptance silently
abandons the plan. When R3 or an in-flight subtask discovers a material departure:

1. **Pause the affected work** (finish nothing against the stale spec).
2. Present the fork with `AskUserQuestion`: follow the spec as written (rework the
   as-built code) vs. ratify the as-built shape (rework the spec's remaining
   promises onto it), with one line on the consequence of each.
3. Record the ruling in `status.md` `## Notes & decisions` - dated, with the
   per-subtask disposition list (consumed / superseded / n-a) - and add it to the
   cursor's `decisions` so later tasks brief against the ratified reality.
4. Re-audit downstream `task_queue` entries whose specs referenced the superseded
   design; author spec amendments via `app-architect` where the ruling changed them.

Minor deviations (a field default, a file path) stay at the implement-subtasks level:
its `/verify-subtask` PARTIAL flow already logs them. This protocol is for
*contract-level* departures only.

Ratified divergence decisions live in the milestone `status.md` `## Notes & decisions`
section, exactly as step 3 describes. This project defines no separate sign-off
authority and no ADR requirement of its own - it ships no `docs/` tree, no design
document, and no `AGENTS.md` outside this kit - so step 3 stands unchanged. Treat a
change to either book-document shape, to `book_utils.hash_book`'s identity inputs, or
to the `site_constants` key contract as contract-level rather than minor: every dedup
and storage path in `src/bookscraper/` keys off one of those three.

---

## Token-economy invariants

All five implement-subtasks invariants hold per subtask. At milestone level, three
more compounds across the (much longer) run:

1. **Research once, digest forever.** Phase R is the only wholesale read of the plan,
   and it happens inside subagents. After R5 the main loop touches only the cursor,
   single grepped rows, and the *current* subtask spec.
2. **Write records from the state you already hold.** Per-task detail sections (M4) come
   from the task cursor and agent summaries; never re-read diffs or re-run gates to
   write prose.
3. **`/compact` at task boundaries.** A milestone run spans dozens of iterations; the
   accumulated narration tail is the dominant compounding cost. Task close (M4) is
   the natural compact point - everything worth keeping is already in status.md and
   the cursor.

## Termination conditions

| Condition                                            | Action                                                                           |
|------------------------------------------------------|----------------------------------------------------------------------------------|
| All tasks `✅` + exit gates pass (M5)                | Final status.md pass, delete cursor, do NOT reschedule                           |
| Milestone already COMPLETE at Phase R                | Spot-verify, report standing, do NOT reschedule                                  |
| Argument matches zero or >1 milestone / no plan.md   | Stop, ask the user                                                               |
| Dependency cycle, or pending tasks all blocked       | Stop, surface the graph                                                          |
| Divergence fork the user declines to resolve         | Stop; record the open fork in Notes & decisions                                  |
| Any implement-subtasks stop condition fires mid-task | Stop the whole loop, surface it - never skip to the next task over a failed gate |
| Exit gate fails at M5                                | Stop, surface the failing gate; do not flip status.md to complete                |

An implement-subtasks-level failure (verification red after retries, `/verify-subtask`
FAIL, CRITICAL security finding, `/pr-review` unresolved) stops the **milestone**
loop, not just the task: a milestone must never advance past a task that could not
pass its own gates.

This project defines no stricter bar than the `implement-subtasks.md` defaults - but note that "full unit suite green" is currently vacuous: there is no runnable pytest suite (`test/bookscraper_test.py` is an empty `unittest` placeholder), no coverage configuration, and no `uv` lockfile, since dependencies are pinned in `requirements.txt` and installed with pip. Until a subtask lands a real suite, the effective bar at M4/M5 is `/pr-review` LGTM plus the tests the subtask itself introduces, and any `uv run pytest` instruction in an agent brief needs its runner adjusted to the venv actually in use.

---

## Loop ↔ loop ↔ skill relationship

Three levels, same vocabulary: this loop *sequences tasks*;
[implement-subtasks.md](implement-subtasks.md) *builds one task* (and remains
independently invocable for single-task work - this loop reuses its algorithm rather
than wrapping its invocation); the skills - `/verify-subtask`, `/test-gap`,
`/dep-audit`, `/secret-scan`, `/link-check`, `/pr-review` - are the per-checkpoint
gates both loops call. Implementation is delegated to the dev-fleet agents in
`agent-orchestrator.md`'s roster; `app-architect` additionally serves this loop in
Phase R (gap-spec authoring) and the divergence protocol (spec amendments).
