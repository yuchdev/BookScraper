# Book Scraper Roadmap

Planning and progress tracking for Book Scraper, organised as a three-tier
hierarchy: **Milestone → Task → Subtask**.

## Hierarchy & vocabulary

| Tier          | Meaning                                                                                                       | Lives in                                                |
|---------------|---------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| **Milestone** | A large, strategic initiative / development direction (e.g. the generic implementation effort).               | A folder `docs/roadmap/{NNNN}-{milestone-slug}/`.       |
| **Task**      | One deliverable unit of a milestone (e.g. "AWS Bedrock Backend"). Listed in the milestone's `## Tasks` table. | A subfolder `…/{TT.t}-{task-slug}/` with a `README.md`. |
| **Subtask**   | An atomic, implementable spec (one file, one set of classes/tests). Listed in the task's `## Subtasks` table. | A file `…/{TT.t}-{task-slug}/{NN}-{subtask-slug}.md`.   |

A *subtask* is part of a *task*; a *task* is part of a *milestone*.

## File & folder convention

```
docs/roadmap/
  README.md                              ← this index + convention
  {NNNN}-{milestone-slug}/               ← MILESTONE
    plan.md                              ← milestone spec; opens with a `## Tasks` table
    status.md                            ← progress tracker (optional); `## Current status` table
    {TT.t}-{task-slug}/                  ← TASK
      README.md                          ← task spec; opens with a `## Subtasks` table
      {NN}-{subtask-slug}.md             ← SUBTASK spec
```

- `{NNNN}` - zero-padded milestone number (`0001`, `0002`, …).
- `{TT.t}` - task number, carried from the milestone's `## Tasks` table (e.g. `03.2`, `06.1`, `08.0`).
- `{NN}` - zero-padded subtask order (`01`, `02`, …).
- Slugs are kebab-case.

### Heading vocabulary

- A milestone's `plan.md` lists its tasks under a `## Tasks` heading (table column: `Task`).
- A task's `README.md` lists its subtasks under a `## Subtasks` heading.

### Linking convention

When a document references another **specific** document, use an
absolute-from-repo-root Markdown link:

```
[docs/roadmap/0001-generic-implementation/plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
```

- Always a leading `/` (repo root), never relative `../../` chains.
- **Template** paths (containing `{` / `}`, e.g. `` `{NN}-{subtask-slug}.md` ``) stay as
  backtick code spans, not links.
- Run `python scripts/check_doc_links.py docs/` (or the `/link-check` skill) to verify every
  link target and `#heading-anchor` resolves. The `doc_link_check` hook runs it on edits.

## Milestones

| #    | Milestone                                                        | Spec                                                          | Status                                                               |
|------|------------------------------------------------------------------|---------------------------------------------------------------|----------------------------------------------------------------------|
| 0001 | Generic implementation (backends, X509, CLI, Playwright, …)      | [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)  | [status.md](/docs/roadmap/0001-generic-implementation/status.md)     |
