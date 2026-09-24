# 02 - Anthropic client & response validation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ✅ Complete
**Role:** Python Expert

## Requirements

- `call_anthropic(prompt, api_key, model=DEFAULT_MODEL) -> str`: one `messages.create` call,
  `max_tokens=DEFAULT_MAX_TOKENS` (2048). SDK errors and empty content → `SchemaDetectionError`.
- `ANTHROPIC_API_KEY` is read from the environment / `.env`, and `ANTHROPIC_MODEL` optionally overrides the model.
- `validate_ai_response(raw_text, field_names)`: strips code fences, requires a JSON object, ignores unknown keys,
  requires a `selector` key whose value is a string or null per proposal, and rejects an empty proposal set.

## Tests

`tests/unit/scraping/test_schema_detection.py`: `test_strip_code_fences_json_fence`, `test_strip_code_fences_bare_fence`,
`test_validate_ai_response_happy_path`, `test_validate_ai_response_accepts_null_selector`,
`test_validate_ai_response_ignores_unknown_keys`, `test_validate_ai_response_rejects_non_json`,
`test_validate_ai_response_rejects_non_object_top_level`,
`test_validate_ai_response_rejects_proposal_without_selector_key`,
`test_validate_ai_response_rejects_non_string_selector`, `test_validate_ai_response_rejects_empty_proposal_set`.
`tests/mock/scraping/test_schema_detection.py`: `test_call_anthropic_returns_reply_text`,
`test_call_anthropic_wraps_sdk_error`, `test_call_anthropic_raises_on_empty_content`.

## Success criteria

- [x] A malformed AI response can never produce partial output.
