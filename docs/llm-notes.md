# Claude API notes for this codebase (read before writing any LLM code)

Source: Anthropic's current SDK reference as of 2026-10 (the `claude-api` skill). These rules are
binding for `backend/cashcow_studio/llm/`.

## Models and prices (first-party API, per million tokens in / out)

| Use | Model id | Price | Notes |
|---|---|---|---|
| Titles, scripts | `claude-opus-5-5` | $4 / $20 | default; thinking always on; effort default `medium`, set `high` for scripts |
| Storyboard, summaries, classification, image QA (vision) | `claude-sonnet-5-5` | $2 / $10 | effort default `high`; `medium` is fine |
| Bulk cheap tasks | `claude-haiku-4-5` | $1 / $5 | uses `thinking: {type: "enabled", budget_tokens: N}` if thinking is wanted |

Use the ids exactly as written. Never append dates. Keep the price table in `config/llm.yaml`
and compute `cost_usd` from `response.usage` (`input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`).

## Request shape

```python
import anthropic
client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from the environment (we load .env first)

# Long outputs: always stream, then take the final message
with client.messages.stream(
    model="claude-opus-5-5",
    max_tokens=16000,
    system=[{"type": "text", "text": FRAMEWORK_TEXT, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": TASK_INSTRUCTIONS}],
    messages=[{"role": "user", "content": USER_INPUT}],
    output_config={"effort": "high"},
) as stream:
    message = stream.get_final_message()
```

- Thinking: on Opus 5.5 and Sonnet 5.5 omit `thinking` (adaptive is the default) or send
  `{"type": "adaptive"}`. Never send `budget_tokens` or `{"type": "disabled"}` to these models.
- Effort goes inside `output_config`: `{"effort": "low"|"medium"|"high"}`.
- No assistant prefill (returns 400). Steer format with instructions or structured outputs.
- No `temperature`/`top_p` on Sonnet 5.5 (non-default values return 400); do not set them anywhere.
- `max_tokens`: 16000 for normal calls, 64000 when streaming a long script.

## Structured outputs (preferred for every JSON result)

```python
from pydantic import BaseModel

class TitleVariants(BaseModel):
    variants: list[Variant]
    recommended_index: int

response = client.messages.parse(
    model="claude-opus-5-5",
    max_tokens=16000,
    system=...,
    messages=[...],
    output_format=TitleVariants,
)
result = response.parsed_output      # validated Pydantic instance
```

The schema is compiled on first use (a little latency) and cached for 24 h. Changing the schema
invalidates the prompt cache, so keep schemas stable. Keep `response.usage` for cost logging.

## Prompt caching

Caching is a prefix match. Put the stable, large text first (framework text, style guide, task
instructions) with `cache_control` on the last stable block; put volatile text (the title, the
candidate, timestamps) after it. Never put `datetime.now()` or random ids in the system prompt.
Verify with `response.usage.cache_read_input_tokens > 0` on the second call.

## Refusals and fallbacks

Current models carry safety classifiers. A declined request returns HTTP 200 with
`stop_reason == "refusal"` and `response.stop_details.category`. Always check `stop_reason`
before reading content. Opt into server-side fallback by default:

```python
response = client.beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
    messages=[...],
)
```

If the whole chain refuses: retry once with a reworded prompt (remove anything that reads as
advice-giving or sensitive framing), then fail the stage with the category in the error. If
`messages.parse` cannot be combined with the beta fallback parameters in the installed SDK
version, use `beta.messages.create` with `output_config={"format": {"type": "json_schema",
"schema": Model.model_json_schema()}}` and validate with Pydantic yourself.

## Errors

Catch most specific first: `anthropic.RateLimitError` (honour `retry-after`), then
`anthropic.APIStatusError` (>= 500 retry with backoff; 4xx do not retry), then
`anthropic.APIConnectionError`. Log `response._request_id` on failures. Never log prompts that
contain the user's key or full framework text at INFO level.

## Vision (image QA, thumbnail template extraction)

Pass images as base64 content blocks before the text block:
`{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}`.
Use Sonnet 5.5 for QA at effort `low`/`medium`.

## Mock provider

`CCS_LLM_PROVIDER=mock` must produce schema-valid outputs without network access. Every stage
test runs with the mock. A live smoke test (marked `@pytest.mark.live`, skipped by default) may
call the real API when `ANTHROPIC_API_KEY` is set.
