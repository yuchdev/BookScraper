# 02 - Staging document contract

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/README.md)
**Status:** ✅ Complete
**Depends on:** [01-adr-staged-review-pipeline.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/01-adr-staged-review-pipeline.md)
**Role:** App Architect → Python Expert

## Context

Today the only contract for a stored book is "whatever `scrape_book()` returned, plus `hash`". Once a human edits the
file and `push` sends it to Mongo, the tool needs a written-down notion of which fields are required, which are
reserved, and what each reserved field means.

## Requirements

- A reviewer-facing spec at `docs/dev/staging-file-format.md`: file shape (one JSON array), required book fields,
  optional fields, reserved keys, and a worked example (one freshly scraped and one pulled document).
- **Required for push:** `url` (non-empty string), `title` (non-empty string), `authors` (list of strings or a
  string, matching what `hash_book` accepts today), `site`. Confirm the list against the per-site output of
  `scrape_details.py` and record any site that cannot satisfy it.
- **Reserved `_id`:** absent on new documents; on pulled documents a 24-character lowercase hex string (an
  `ObjectId` rendered with `str()`). Never `{"$oid": ...}` Extended JSON - keep the file plain JSON.
- **Reserved `_staging`:** optional object; keys `status` (`"pending"` | `"approved"` | `"rejected"`, default
  `"pending"` when absent), `scraped_at` (ISO-8601 UTC string), `source` (`"scrape-urls"` | `"search"` | `"pull"`),
  `note` (free text for the reviewer). Unknown `_staging` keys are allowed (reviewer scratch space) and also stripped.
- No other key may start with `_`, start with `$`, or contain `.` at any nesting depth (Mongo field-name safety).
- Constants in code: `RESERVED_ID_KEY = "_id"`, `STAGING_META_KEY = "_staging"`, `REVIEW_STATUSES` - defined once in
  `backends/local/staging.py` (subtask 03) and imported elsewhere.

## Files

- Create `docs/dev/staging-file-format.md`.

## Tests

None directly; the rules become executable in subtask 04's validator tests.

## Success criteria

- [x] Spec lists required fields, reserved keys, forbidden key patterns, and one example per document origin.
- [x] Required-field list verified against all four sites' scrape output (noted in the PR).
