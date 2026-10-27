# 03 - Field extraction engine

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md)
**Status:** ✅ Complete
**Depends on:** [02-shared-ai-prompt-infrastructure.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/02-shared-ai-prompt-infrastructure.md),
03.1 subtasks [02](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/02-isbn-normalization.md) /
[03](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/03-date-normalization.md)
**Role:** Security Auditor → Python Expert

## Requirements

- New module `src/bookscraper/scraping/ai_extraction.py` with:
  ```python
  async def extract_missing_fields(
      html: str, site: str, fields: list[str], *, client: Optional[Any], replay: Optional[dict[str, str]],
      record: Optional[dict[str, str]], usage: UsageAccumulator,
  ) -> dict[str, ExtractedValue]
  ```
  - `ExtractedValue` is a frozen dataclass `(value, evidence)`.
  - It returns only the fields that passed validation; a dropped field is simply absent.
- **One request per page.** All missing eligible fields go into one prompt. The prompt uses the `ai_common`
  `<page_html>` fence, the untrusted-data framing, and the token budget. Output contract (strict JSON):
  ```json
  {"fields": {"ISBN13": {"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"},
              "DESCRIPTION": {"value": null, "evidence": null}}}
  ```
  `value: null` is the explicit "not on this page" answer, and the prompt instructs it over guessing.
- **Deterministic validation.** Each check drops the field on failure; nothing raises, and the drop is logged with
  a reason code.
  1. **Shape.** Unknown field keys are dropped, and a non-string `value`/`evidence` is dropped.
  2. **Evidence.** `evidence` must be a verbatim substring of the page's visible text, whitespace-normalized and
     case-sensitive. An absent quote counts as a hallucination.
  3. **Value check.** The value must be derivable from its evidence: a normalized ISBN or date must parse *from the
     evidence string*, and a description must be a substring of the page text.
  4. **03.1 normalizers.**
     - ISBN checksum (`isbn10`/`isbn13`);
     - date parse into `YYYY[-MM[-DD]]`;
     - authors: list of non-empty strings, each ≤ 200 chars, ≤ 20 entries;
     - description ≤ 10,000 chars.
  5. **Sanitizing.** `sanitize_untrusted()` runs on every stored string.
- **Error policy (differs from `detect-schema` on purpose).**
  - A malformed or non-JSON response, a refusal, or a `max_tokens` stop: log a warning and return `{}`. The book is
    saved with deterministic data only.
  - Configuration errors still fail loudly at **start-up** (subtask 05): a missing key with `--ai-fallback` set, or
    a bad model ID.
  - Rationale: one hostile page must not abort a scrape run.
- `ANTHROPIC_API_KEY`, raw prompts, and page HTML never reach a log line or the result. SDK errors report the class
  name and HTTP status only, as in 04.0/05.

## Files

- Create `src/bookscraper/scraping/ai_extraction.py`.
- Create `tests/unit/scraping/test_ai_extraction.py` (validation, pure).
- Create `tests/mock/scraping/test_ai_extraction.py` (client mocked or replay).

## Tests

- `test_valid_isbn_with_verbatim_evidence_is_kept`
- `test_value_without_evidence_on_page_is_dropped` (hallucination)
- `test_isbn_failing_checksum_is_dropped`
- `test_value_not_derivable_from_evidence_is_dropped` (evidence and value disagree)
- `test_unknown_field_key_is_dropped`
- `test_injected_instruction_in_page_cannot_add_title_field` (`title` is never eligible)
- `test_malformed_response_returns_empty_not_raise`
- `test_one_request_for_multiple_missing_fields`
- `test_api_key_and_html_never_logged` (caplog sweep)
- `test_replay_mode_constructs_no_client`

## Success criteria

- [ ] Every HIGH finding from the subtask 01 threat model has a named test above or in subtask 05.
