# Catalog facts — 2026-11-22 (Task 4, provider-buckets plan)

One `GET <base>/models` per keyed provider, `User-Agent: free-agents-model-facts/1`
(raw JSON: `2026-11-22-catalog-facts.json`):

| provider | status | models | context field present |
|---|---|---|---|
| nvidia | 200 | 33 | sparse (context_window) — HF config.json used for the rest |
| mistral | 200 | 51 | 51/51 max_context_length |
| groq | 200 | 11 | the 403 without a real UA (spec C10) was a UA block; with the UA it answers |
| gemini | 200 | 65 | 65/65 inputTokenLimit + outputTokenLimit |
| openrouter | 200 | 441 | 441/441 context_length + top_provider.max_completion_tokens |

Merged into `config/provider-limits.json` (633 model facts, 504 with a context value;
curated/non-catalog sources are never overwritten by catalog values).
