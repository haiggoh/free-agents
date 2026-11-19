# Measurement — per-provider quota feasibility for FULL SESSIONS (2026-11-20)

Open question from the 2026-11-19 handoff: "for full sessions, per-provider quota feasibility
is NOT established yet (open question, don't guess)". Measured, not guessed, 2026-11-20 ~00:11 UTC.

## Method

One bounded generation per keyed provider (POST `{base}{chat_path}`, `max_tokens=4`,
`Content-Type: application/json`, credential via header built at runtime — never in argv, logs
or output). Provider endpoints, tiers and auth come from the repo's own
`bin/remote_provider_registry.py`; the model id is the first non-broken roster alias per
provider (`config/remote-agents.sh` + `config/broken-nvidia-models.json`), falling back to a
GET of the provider's own catalog for SELECT providers. Raw record:
`docs/measurements/quota-probe-20261119T231118Z.json` (statuses, latency, rate-limit headers,
body heads — no credentials anywhere in it).

A full session is a sustained consumer (many turns over minutes to hours), so the evidence that
matters is (a) the provider's own quota surface (headers / documented caps) and (b) whether a
single session can run for hours — NOT whether one dispatch succeeds.

## Measured results (2026-11-20 ~00:11 UTC)

| provider | roster tier | probe (4 tokens) | quota surface (measured) | full-session verdict |
|---|---|---|---|---|
| nvidia | no daily cap / 40 per min | 200 OK | no daily cap; 429 only when the ~11 live proxies share the key in a burst; no rate-limit headers on success | **feasible** — plus in-session evidence: this very handover session ran 10+ h on nvidia-mistral-large4 without a quota kill |
| groq | renewing_free | 200 OK | `x-ratelimit`: 1000 req/min, 8000 tok/min, resets per minute (999/7924 remaining) | feasible for one sustained session; per-MINUTE caps burst-limit |
| mistral | free tier | 200 OK | per-minute caps: 2 req/min, 40 000 tok/min on codestral | feasible sustained, burst-limited; per-minute only |
| openrouter | free router | 200 OK (routed to a `:free` model) | no cap headers; model availability varies per `:free` route | feasible but model-dependent; round-trip verify each id |
| zai | unknown | 429 `1113 insufficient balance` | key has no balance/resource package | **not feasible now** (paid) |
| sambanova | unknown | 429 "high demand" | capacity-limited at probe time | not reliably feasible right now |
| gemini | renewing_free | curl timeout after 40 s, 0 bytes | plus the 2026-11-19 incident (quota burned in minutes inside a full session) | **not feasible for full sessions — dispatch-only** (matches the maintainer split; pinned by test) |
| cerebras | trial | 402 payment_required | trial allowance exhausted | not free anymore |
| kilo, llm7, siliconflow, huggingface | — | 402 insufficient balance / credits | paid | not free |
| vercel | — | 403 requires credit card on file | paid | not free |
| streamlake | — | 400 user not allowed to access ChatCompletions | key not permissioned | not feasible with this key |
| github | retired | (service retired; ignore) | — | n/a |

## Consequence for the session ladder (`config/session-preferences.json`)

Measured evidence supports **nvidia leading full sessions** (no daily cap + 10 h in-session
evidence) with groq/mistral/openrouter as burst-limited per-minute fallbacks. Gemini stays
dispatch-only. No provider was added to or moved in the session ladder on anything but this
measurement. Re-measure before changing the ladder again — quota state decays (several providers
flipped 402/429 within days).

Follow-up: ship the probe as `bin/quota-probe.py` with `--help` and tests if periodic
re-measurement is wanted (currently a one-shot script; method above).
