# Design — per-provider rate buckets, limiter fixes, swap pre-flight, effort defaults, LiteLLM bridge shim

Status: SPEC (2026-11-22). Written by the investigating session (Claude Opus 5.5) after a two-day
investigation; every "verified" fact below cites the command/file it came from. The plan built on
this spec is `docs/superpowers/plans/2026-11-22-provider-buckets-and-limiter-fixes.md`
(lasting-plans:plan-airtight, binding playbooks: milestone-QC-offload-test-proof and
split-monolithic-test-suites).

## 0. Why this exists (the user's request, verbatim intent)

1. "check the new free-agents update for the new nvidia rate limiter system if it's working as
   intended, if its logic is sound and its codebase free of bugs. I noticed an increase in transient
   api errors since the update."
2. Waypoints `litellm-proxy-crashes-on-v1`, `litellm-proxy-500s-on`, `swap-model-preflight-rate` and
   the branch `fix/swap-skip-preflight` (worktree `.wt/fa-swap-override`, commit 9925766): "if the
   preflight for model swaps goes outside of the rate limiter, it will always overshoot the overall
   rate limit … that was always a logic error in the design."
3. "adapt our existing nvidia rate limiter to create a separate bucket for each of those providers
   that knows their specific rate limits and handles them in the same way. their known max token
   limits also have to be accounted for … the infoboxes in both api key setup and the model selector
   will have to give information about rate limit, max tokens and daily quota or weekly quota …
   those facts have to be known by the offload model selection priority ladder as well, so it can
   fall back outside of nvidia if needed but knows the limitations in doing so."
4. "default setting for handovers should be whatever is saved as the current default for launching
   a session on that lane … offloads should also follow the same setting."

## 1. Findings (evidence)

### 1.1 The transient errors are NOT caused by the 0.61.0 limiter (verified)
- Proxy logs `$TMPDIR/local-agents-remote/proxy-41*.log`: **0** `RateLimitTimeout` ("no NVIDIA slot")
  lines; **466** queued calls, longest wait **37.6 s** (max_wait 300).
- The error increase is concentrated on port **4151** (Mistral Large 4 on NIM): 37×500 vs 5×200 on
  /v1/messages; 244 `__aiter__` errors, 52/139/62 per hour 01–03 h on 2026-11-21. Every chain
  STARTS with `'NoneType' object has no attribute '__aiter__'` before any limiter event; only 4
  limiter lines in that log (fallback-model 429s). GLM 5.3 Flash ports 4141–4144: 103–770×200,
  0–10×500 each.
- Root cause = upstream bug **BerriAI/litellm#53340** (open, filed 2026-11-21, reproduces on
  1.109.1): when an upstream stream dies mid-flight the /v1/messages→completions bridge receives
  `completion_stream=None`, defers it, and raises a NON-retriable `AttributeError` at iteration → the
  router cannot fall back cleanly → 500. Fix PR **#53341** (open, unmerged) adds an 11-line None
  guard in `AnthropicAdapter.translate_completion_output_params_streaming` raising
  `APIConnectionError`. Latest PyPI release **1.110.2** (2026-11-20) does NOT contain it (wheel
  inspected). The non-stream sibling is the `'choices'` crash in
  `translate_openai_response_to_anthropic` (`response=None`), waypoint `litellm-proxy-500s-on`.
- A separate 6-minute burst at 15:46–15:52 on 4142+4143 simultaneously = `Nvidia_nimException -
  Connection error` (upstream/network; not ours).
- Correction to waypoint `litellm-proxy-crashes-on-v1`: NOT model-independent — GLM lanes were
  healthy under the same in-memory LiteLLM; the trigger is mid-flight stream death, frequent on
  Mistral Large 4 NIM streams.

### 1.1b Bridge experiment — real LiteLLM 1.110.2 vs a loopback fake OpenAI upstream (verified 2026-11-22)
Rig: `docs/measurements/2026-11-22-bridge-experiment/` (fake_openai.py modes ok/die/die0/die1/dier/
r429, cfg.yaml, captured responses). Scratch proxy on :4199 (outside the live band), started via
`bin/litellm-with-trust.py` with `LITELLM_LOCAL_MODEL_COST_MAP=True`; the hooked run used a
SANDBOX `LA_NVIDIA_THROTTLE_STATE`. Streaming `POST /v1/messages`, primary `claude-opus-5-5` →
fallback `la-fallback-1`:

| primary behaviour | fallback | client sees | verdict |
|---|---|---|---|
| content, then socket close | ok | HTTP 200 + `error` event "Stream ended before a finish_reason" | correct (text already sent ⇒ no fallback) |
| close before any chunk (`die0`) | ok | **HTTP 200, empty text, NO error** — fallback never tried | DEFECT (silent empty answer) |
| role-only chunk, then close (`die1`) | ok | fallback answers ("pong-ok") | correct |
| reasoning-only chunk, then close (`dier`) | ok | 200 + error event, no fallback | upstream #49795 (known) |
| role-only, then close | upstream **429** | **HTTP 500** naming the PRIMARY's stream error; fallback hit 3× (1 + 2 SDK retries) | DEFECT: rate-limit cause masked as 500 |
| role-only, then close | **cooling** (hook fast-fail) | **HTTP 500**; fallback upstream hits = 0 | DEFECT (500) — fast-fail itself works |

Plus, in the hooked run: **our own fast-fail RateLimitError was booked by `async_log_failure_event`
as an upstream NVIDIA 429 against the PRIMARY** ("NVIDIA 429 with 3/40 … fake-die cools 30s") with
2 phantom hidden-retry slots — 1 real upstream request, 3 booked. A self-amplifying loop on live
proxies (cooldown ⇒ fast-fails ⇒ phantom 429s ⇒ more cooldowns, drained slots). New defect **L8**.
- Route fact: with the `openai/` prefix LiteLLM 1.110 bridges /v1/messages to the **Responses API**
  (`/v1/responses`); `nvidia_nim/` uses chat/completions. Test fixtures MUST use the production
  provider prefix or they exercise a different bridge.
- Startup fact: a cold proxy start took **≈180–200 s** here (fresh 1.110.2 install, niced Bash-tool
  process), vs ≈8 s for warm live starts. The swap pre-flight's fixed 90 s readiness bound
  (remote-session.sh:1488) aborts such starts — a likely cause of the "not ready in 90 s" aborts.
- The `None`-stream crash (#53340) itself did not reproduce with these shapes; C4 still mirrors the
  upstream guard. The client-visible harm (500 instead of a retriable 429) IS reproduced, twice.

### 1.2 Limiter defects (verified against `bin/rate_limiter.py` @ 75d1801 unless noted)
Independent review: dispatch to Nemotron 3 Ultra (NVIDIA route), every claim re-checked here.

| # | Defect | Evidence | Severity |
|---|---|---|---|
| L1 | Per-model cooldown never enforced in the grant path: `try_acquire(model=…)` only stores `model` in the waiter record; `self._models` is consulted only by `model_cooling()`, which `la_proxy_hooks.py:396` calls ONLY when `LA_PROXY_FALLBACK_NAMES` is set. A chain-less proxy keeps sending to a throttled model (log: "545. in a row"). Also `la_proxy_hooks.py:415` calls `aacquire()` without `model`. | rate_limiter.py:550-663, la_proxy_hooks.py:393-419 | critical (pre-existing, 0.40.0) |
| L2 | State read is ONE `os.read(fd, 65536)`; any parse failure resets to PERMISSIVE state (tokens=capacity, cooldown 0, models/waiters cleared) — fails OPEN, a burst right after a 429. Heavy queueing (now the desired normal) grows the file. | :671, :689-699, :922 (+ sliding equivalent) | important |
| L3 | Smooth-bucket 429 classifier: `in_window = capacity - tokens` (burst depletion, ≤ capacity=10) is logged as "N/40 requests in the last 60 s" and `tokens > capacity/2 ⇒ model_throttle`; under steady load (tokens≈0) a single model's 429 is classified `rpm_overrun` and pauses EVERY NVIDIA proxy. The live mode is smooth_bucket (user's saved setting). | :753-762; config/session-menu.local.json rate_limiter.mode | important |
| L4 | `RateLimitTimeout` from `aacquire` is not caught in the hook → surfaces as 500, not a retriable 429. (0 occurrences so far — latent.) | la_proxy_hooks.py:415 | important (latent) |
| L5 | "Own owner outranks the queue" pops the WINNER's token instead of serving it; the same owner's other request loses its queue age. | :644-652 | minor |
| L6 | `free-agent-tool.nvidia_load_detected` reads the state unlocked while writers ftruncate+write → torn read → silently False. | free-agent-tool.py:735-745 | minor |
| L7 | Legacy peek path (token=None) does not append to `grants`, so the window count L3 needs would miss those grants. | :597-607 | minor (prerequisite of L3 fix) |
| L8 | Our own fast-fail `RateLimitError` (la_proxy_hooks.py:404) reaches `async_log_failure_event` → `_is_429` true by type name → `note_429(hidden_attempts=2, model=<primary>)`: a request that never left the machine books a 429 against the wrong model plus 2 phantom slots. | 1.1b hooked run | critical |
| known | SDK hidden retries that WIN are unbooked (documented gap, contract test 8b xfail). Reviewer #4 restates it. | la_proxy_hooks.py:358-366 | documented, unchanged |
| rejected | Reviewer #7 "migration race": `_load()` migration is in-memory only; `_save()` has no callers (all runtime writes go through the flocked fd). Not a defect. | grep `_save(` = 0 callers | — |

### 1.3 Swap pre-flight bypasses the limiter AND over-fires (verified, remote-session.sh @ 75d1801)
- Scratch proxy spawned with `LA_PROXY_HOOKS_RATE_LIMIT=0` (:1486) — no bucket at all.
- The scratch config comes from the SAME `write_proxy_config`, so it carries the session's
  same-provider fallback chain (`la-fallback-1..3`, :1080-1100) and `num_retries: 3` on the primary.
  With `RateLimitErrorRetries: 0` plus `SDK_HIDDEN_RETRIES = 2`, one `ping` during a 429 window can
  fire ≈12 unthrottled NIM calls (assumed: arithmetic, not measured), more on other errors.
- A 200 can come from a FALLBACK model → "pre-flight passed" does not prove the target serves.
- The probe uses /v1/messages (non-stream) → also exposed to the 1.1 bridge bug.
- `fix/swap-skip-preflight` (9925766, unmerged) skips the pre-flight entirely; superseded by C5.

### 1.4 Limits data today (verified)
- NVIDIA "40/min" exists 3× as prose/constants: registry `tier` string, `rate_limiter.py` rpm
  default, `session_picker_model.py:577-593` `PROVIDER_INFOS`. No structured rpm/tpm/rpd/rpw/
  max_output/context for ANY remote model; `model-catalogue-context-list.yaml` covers local models
  only; registry entries carry only `autocompact_default`.
- Max tokens: dispatch entries hold hand-entered `max_tokens` (8 entries); sessions only learn an
  output cap reactively from a 400 into in-memory `_LEARNED_CAPS` (la_proxy_hooks.py:206-245) — lost
  on every proxy restart.
- Measured quotas: `docs/measurements/2026-11-20-remote-quota-probe.md` (+ raw JSON): mistral 2 req/
  min + 40 000 tok/min (codestral); groq 1000 req/min + 8000 tok/min (headers); nvidia 40/min, no
  daily cap; gemini daily-quota-bound, dispatch-only; openrouter no headers, per-`:free` model; zai/
  sambanova/cerebras/kilo/llm7/siliconflow/huggingface/vercel/streamlake not free today.
- Cerebras reserves TPM from the REQUESTED max_tokens (remote-session.sh:2068) → TPM accounting must
  book an estimate at grant and reconcile from usage.

### 1.5 Ladders today (verified)
- In-proxy fallback chain is SAME-PROVIDER by design (remote-session.sh:1062-1067, contract test 3):
  a cross-provider member would drain another account and paper over a key-wide refusal. KEEP.
- Dispatch ladder `config/dispatch-preferences.json` `order`: gemini-3.8-flash, 4× nvidia, 3× local
  — no mistral/groq/openrouter entries. Load-stretch: `RATE_LIMITED_PROVIDERS = {"nvidia"}` +
  `nvidia_load_detected` → patient tier (free-agent-tool.py:724-745).
- Session ladder `config/session-preferences.json` (Mistral Large 4 first; gemini excluded).

### 1.6 Effort defaults (verified)
- Saved lane defaults: `config/session-menu.local.json` `effort.{local_session, remote_api_session,
  lowkey}`; `session_menu_state.DEFAULT_EFFORT` fills unsaved lanes.
- Picker path honours it (csl:54). Direct paths do NOT: remote-session.sh:2059-2066 (per-alias
  last-launch via `last-effort.py`, else provider default); launch-claude-agent.sh:290 (alias config
  `LA_CUR_EFFORT`); free-agent-tool.py (per-entry `reasoning_effort`, e.g. gemini "medium").
- Effort→wire mapping lives twice (bash remote-session.sh:943-967, python resolve_thinking
  free-agent-tool.py:129-160) — NOT consolidated by this spec (scope); recorded as a waypoint.

### 1.7 Test-gate facts that shape the plan (verified)
- Main gate (`rtc-ship.json test_cmd`) runs `-m 'not load_sensitive'`; `.rtc/pre-flight.sh`'s
  litellm gate (:76) ALSO deselects `load_sensitive`. A real-LiteLLM test carrying that marker is
  silently NOT run by any gate.
- No existing test runs real LiteLLM against a fake upstream; `tests/fixtures/fake_upstream.py` is
  an ANTHROPIC-shaped fake; swap tests use a stub `litellm` (LA_LITELLM_CMD).

## 2. Decisions (user, 2026-11-21/22)

| Topic | Decision |
|---|---|
| Limits data home | NEW `config/provider-limits.json` — the single structured source |
| Full bucket | Queue is the usual outcome; bounded wait with GENEROUS, load-stretched timeouts (generalise the existing NVIDIA stretch); fail over only past it. Balanced use of free resources > fast turnover |
| Ladder — sessions & handovers | Ordered + spill when saturated / cannot fit |
| Ladder — offload dispatches | Capacity-weighted spread across eligible providers |
| Session eligibility | Small-context models (≤128k, e.g. Groq gpt-oss) not used for full sessions |
| Quota knowledge | Local counters per provider per UTC day + ISO week, adopting x-ratelimit headers when sent; a quota refusal marks exhausted until reset |
| LiteLLM | Map errors only (keep auto-upgrading); no pin |
| Effort precedence (sessions, handovers, dispatch) | explicit > SAVED lane default > per-alias last-launch > provider default |

## 3. Components

### C1 — `config/provider-limits.json` + loader `bin/provider_limits.py`
Schema (versioned; every number carries provenance):
```
{"schema_version": 1,
 "session_min_context": 262145,   # PENDING USER CONFIRMATION: = the C9 filter line (>256k);
                                  # alternative 200000 keeps ~200k models (check GLM 5.3 Flash's
                                  # documented context first — NVIDIA's catalog does not publish it)
 "providers": {
   "nvidia":  {"rpm": 40, "tpm": null, "rpd": null, "rpw": null, "tpd": null,
               "dispatch_only": false, "measured_at": "2026-11-20", "source": "docs/measurements/2026-11-20-remote-quota-probe.md"},
   "mistral": {"rpm": 2, "tpm": 40000, ...},
   "groq":    {"rpm": 1000, "tpm": 8000, ...},
   "gemini":  {"rpd": <measured or documented>, "dispatch_only": true, ...}},
 "models": {"nvidia:mistralai/mistral-large-4-1t-a52b": {"context": 1000000, "max_output": <n>, "source": "..."}}}
```
- `null` = "no known limit" (distinct from 0 = forbidden); an unknown provider has NO bucket
  dimensions → behaves exactly as today (no limiting) and is labelled "limits unknown".
- Loader: strict validation (types, ranges, unknown keys rejected), explicit `--help`; an invalid
  file is a LOUD error in the picker and at proxy start (override-fails-loudly rule), never a silent
  fallback to "no limits". Env override `FA_PROVIDER_LIMITS` must exist or abort.
- `bin/quota-probe.py` (ships the one-shot method of 3b942a4): one bounded generation per keyed
  provider THROUGH that provider's bucket (C2), records headers, writes a dated measurement file and
  proposes (never silently applies) changes to provider-limits.json (`--apply` writes only fields
  whose `source` is a measurement, never hand-pinned ones).
- The registry `tier` display strings and `PROVIDER_INFOS` limit sentences are DERIVED from C1 (one
  constant per shared label); advisory prose (e.g. "dispatch only") stays in notes.

### C2 — Per-provider buckets (generalised limiter)
- `get_limiter(provider="nvidia", ...)`; one bucket per provider KEY (same key ⇒ same account ⇒
  same limits). Dimensions per bucket from C1: requests/min (existing smooth/sliding machinery,
  fairness queue unchanged), tokens/min, requests+tokens per UTC day and ISO week.
- **Mixed-writer safety (hard constraint):** live 0.61.0 proxies rewrite the NVIDIA main file with a
  FIXED key set (:707-722) and drop unknown keys. Therefore: NVIDIA main file path + schema stay
  byte-compatible; ALL new dimensions for every provider live in a sidecar
  `<state>.quota` file that old code never opens. Lock order is always main → sidecar. Non-NVIDIA
  providers use `~/.claude/free-agents/limiter/<provider>.state` (+ sidecar), no legacy writers.
- TPM booking: at grant, book `estimate = prompt_tokens_estimate + requested_max_tokens` (clamped
  to the model's max_output); on success, reconcile to actual usage (refund the difference); on
  failure, keep the booking only for providers known to reserve on request (cerebras-style flag in
  C1), else refund. A request whose estimate alone exceeds a provider's tpm or the model's
  context/max_output is ineligible for that provider (fast, no queue).
- Daily/weekly: counters keyed `YYYY-MM-DD` / `YYYY-Www` (UTC); x-ratelimit-* headers (when present)
  overwrite the local remaining figures; a quota refusal (429/402 whose body says quota/limit
  exhausted) sets `exhausted_until = next reset` for that dimension.
- Fixes folded in: L1 (try_acquire honours `models[model].until`; the hook passes `model`; a
  chain-less proxy WAITS out the cooldown inside the queue instead of firing), L2 (read to EOF;
  parse failure fails SAFE: tokens=0, cooldown=base, loud log, file rewritten), L3 (classifier uses
  the 60 s grant count, L7 makes every grant appear in it; log prints real counts), L5, L6 (read
  under a shared lock or tolerate torn reads by retry).

### C3 — Load-stretched waits (generalised)
- One function `stretched_wait(provider, base)` used by BOTH the proxy hook (`aacquire(max_wait=…)`,
  today flat 300 s) and the dispatch tool (today `RATE_LIMITED_PROVIDERS={"nvidia"}` → patient).
  Stretch = f(queue depth / rate, active cooldown, recent 429s), capped by the patient tier.
  RATE_LIMITED_PROVIDERS becomes "every provider with a bucket dimension in C1".
- L4: a `RateLimitTimeout` in the hook is mapped to `litellm.RateLimitError` with `Retry-After`
  (client retries) — never a 500.

### C4 — LiteLLM bridge shim (user: map errors only)
- In `la_proxy_hooks` at import: if `AnthropicAdapter.translate_completion_output_params_streaming`
  exists and its source has NO `completion_stream is None` guard, wrap it to raise
  `litellm.APIConnectionError` when the stream is None (exact upstream PR #53341 semantics).
  Same for the non-stream path (`translate_openai_response_to_anthropic` with `response None` /
  `choices None`). Self-detecting retirement: when an installed LiteLLM already guards, the shim
  logs "upstream guard present — shim off" and does nothing. Never edits the pipx venv.
- `--self-test` reports shim state; the proxy start log prints it once.
- **Client-facing error mapping** (the harm actually reproduced in 1.1b):
  - a request whose fallback chain ends on a rate limit (upstream 429 OR our fast-fail) reaches the
    client as **429 + Retry-After** (the shortest remaining cooldown among the chain), never 500 —
    set on the exception in `async_post_call_failure_hook` (1.105+ builds the reply from the
    exception's own status_code/type; verify on 1.110.2 in the delivery test);
  - an empty, unterminated stream (no chunk, no finish_reason — `die0`) surfaces as a retriable
    `overloaded_error`/529 instead of an empty 200 (stream-iterator hook; if LiteLLM offers no hook
    point on /v1/messages, record that and keep the shim-level guard only);
  - **L8 fix:** every exception the hooks raise themselves carries a marker
    (`la_self_raised=True`); `async_log_failure_event` never books a marked exception, and books
    upstream 429s against the deployment that actually answered (`kwargs["model"]` of THAT attempt,
    asserted against the fake's hit log).

### C5 — Swap pre-flight through the bucket, probing the TARGET only
- Scratch proxy keeps the limiter (drop `LA_PROXY_HOOKS_RATE_LIMIT=0`), books on the target's
  provider bucket like any request (kind interactive, normal class).
- Scratch config: target model only — NO fallback chain, `num_retries: 0`, so a 200 proves the
  target. Probe = one tiny streaming request (also exercises the C4 path).
- Verdict: 200 ⇒ proceed; hard refusal (402 account/quota, 410 EOL, connection refused) ⇒ abort;
  429/timeout ⇒ abort with "target throttled — try another or --force". The fixed 90 s
  readiness bound is replaced by a PROGRESS-based one: wait while the proxy process is alive and
  its log is growing or CPU-active, abort after N s of no progress or a hard cap (cold starts
  measured at ≈180–200 s, 1.1b); the probe's own wait uses C3.
- Dead-route preference (waypoint point 3): when the CURRENT route is dead (402/410/hours-long 429)
  `--swap-model` may target `paid_only` aliases, labelled as such.
- `fix/swap-skip-preflight` is superseded — not merged; its branch is left for the user to delete.

### C6 — Effort resolver (single choke point)
- `bin/effort_resolve.py resolve --lane <local_session|remote_api_session|lowkey> [--alias A]
  [--explicit E]` → level + its provenance (explicit / saved-lane / last-launch / provider-default).
  "Saved" means PRESENT in session-menu.local.json — `DEFAULT_EFFORT` must not mask the last-launch
  layer (session_menu_state must expose saved-vs-defaulted).
- Callers: remote-session.sh (replaces :2059-2066), launch-remote-window.sh, launch-claude-agent.sh
  (local lane), free-agent-tool.py (remote dispatch → remote_api_session, local dispatch →
  local_session; the entry's `reasoning_effort` becomes the provider-default layer), the
  free-session-handover SKILL text (drop "pass --effort explicitly").
- Model constraints still apply AFTER resolution (existing mappings: GLM-on-NIM low=off, Nemotron no
  reasoning_effort) — the resolver chooses the LEVEL, the existing mapper translates it.

### C7 — Ladders know the limits
- Eligibility (both ladders): key present, provider not exhausted for today/week, model context ≥
  request need (sessions additionally ≥ `session_min_context`), max_output ≥ requested, not
  `dispatch_only` for sessions.
- Sessions/handovers (`session-preferences.json` consumers: launcher auto-pick, `--swap-model`
  target ranking, handover successor choice): ORDERED; take the first eligible whose predicted wait
  ≤ its stretched timeout; else spill to the next. Never mid-request (in-proxy chains stay
  same-provider).
- Dispatch: CAPACITY-WEIGHTED among eligible entries — weight = preference weight × headroom
  (min over the provider's dimensions of remaining/limit); deterministic tie-break by order. Adds
  smoke-tested mistral/groq/openrouter entries (each must pass a real round-trip before
  `smoke_tested: true`).

### C8 — Infoboxes
- API keys screen (`APIKeysScreen.info_text`) and model selector (`RemoteScreen.info_text` group +
  model rows) render from C1: requests/min, tokens/min, daily/weekly quota (with "today used N /
  remaining M" when the counters know), max output, context, "session-eligible: yes/no (why)",
  "measured <date> — <source>". Unknown → "limits unknown" (never blank, never invented).

### C9 — "Only show large context models" filter (user 2026-11-22)
- New Remote-screen filter beside trial / local_capable / broken / old
  (`_agent_remote_filters`, session_picker_model.py:1584): hides every model whose context window
  is ≤ 262 144 tokens (256k). **Default ON for the remote lane** and PERSISTED (unlike the
  in-memory show_broken/show_old) — a session_menu_state schema bump with migration; toggle shows
  "Large context only: ON/OFF".
- Unknown context: the model is SHOWN with a visible "context unknown" tag (a filter must never
  hide what it cannot judge), and C10's completeness test makes "unknown" a release-blocking
  defect for every curated/registry model, so it stays rare.
- Applies wherever the Remote-screen filters apply (lowkey's opt-in `lowkey_remote_filters` too).

### C10 — Model/provider data completeness so every filter actually applies (user 2026-11-22)
Measured 2026-11-22 (one catalog GET per provider, no generation):
mistral 51/51 models carry `max_context_length`; openrouter 441/441 `context_length` +
`top_provider.max_completion_tokens`; gemini 65/65 `inputTokenLimit` + `outputTokenLimit`;
**nvidia only 1/33 carry `context_window`**; groq `/models` answered 403 (assumed: User-Agent
block — re-test with a real UA).
- Catalog ingestion: `quota-probe.py --catalog` (or a sibling `bin/model-facts.py`) reads each
  keyed provider's `/models` with a proper User-Agent and writes context/max_output/release-date
  into provider-limits.json `models` with `source: "catalog <url> <date>"`. Curated entries
  (`source` = doc/measurement) are never overwritten by catalog values.
- NVIDIA (and any provider whose catalog lacks the fields) is curated by hand from the model cards,
  each value citing its source URL.
- Every filter's input becomes data-complete per keyed provider: `broken` generalises from
  `broken-nvidia-models.json` to a per-provider broken list (NVIDIA file migrated, not renamed
  in place for old readers), `old` reads release dates from the catalog facts, `local_capable`
  unchanged, `large_context` reads `context`.
- **Completeness test (release gate):** for every registry/roster alias of a keyed provider, each
  filter field is either a value or an explicit `null` with a `reason` — a MISSING key fails. Plus a
  delivery-level picker test: with the filter ON, a fixture ≤256k model is absent from the RENDERED
  list and a >256k one present (declared ≠ delivered: assert the rendered rows, not the predicate).

## 4. Invariants (each gets a delivery-level test + a known-bad mutation)
1. No provider bucket ever grants more than its rpm in any 60 s window across processes, including
   swap pre-flights (count upstream hits on the fake).
2. A model in cooldown receives ZERO upstream requests from any proxy, chain or no chain.
3. A corrupt/oversized state file never yields a burst (fails safe).
4. A mid-stream upstream death on /v1/messages yields a fallback answer or a clean retriable error
   to the client — never a 500 with AttributeError.
5. Swap pre-flight: a 200 can only come from the target model; it books exactly one bucket slot.
6. Effort: omitting --effort on every launch/dispatch path yields the SAVED lane default.
7. A ≤128k-context model is never chosen for a full session; Gemini never for a session.
8. Old 0.61.0 writers and new writers on the NVIDIA file coexist: neither loses the other's state.
9. Bucket bookings equal real upstream hits: a self-raised error books nothing (L8); measured on the
   fake's hit log, never on the hook's own counters.
10. A fallback chain ending on a rate limit reaches the client as 429 + Retry-After, never 500.
11. With "large context only" ON (the remote default), no ≤256k model appears in the RENDERED list;
    every keyed provider's roster model has a context value or an explicit null+reason.

## 4b. Test-rig rules (from 1.7 / 1.1b; binding for the plan)
- Delivery tests that need real LiteLLM run in a NEW `.rtc/pre-flight.sh` gate WITHOUT the
  `load_sensitive` deselection, serial, generous timeouts, failing when it collects 0 tests.
- Fixtures use the production prefix (`nvidia_nim/…`) and the real hook module + trust wrapper,
  a sandbox `LA_NVIDIA_THROTTLE_STATE`, a scratch port outside 4141–4151 and outside the swap
  scratch band, `LITELLM_LOCAL_MODEL_COST_MAP=True`; proxy readiness waits are progress-based
  (cold starts ≈3 min).
- Measured durations (serial, 2026-11-22): test_rate_limiter.py 74 s / 72 tests;
  test_swap_model.py 424 s / 12 tests (~35 s each); test_la_proxy_hooks.py SKIPS entirely under
  the pipx pytest (37 skipped) — its receipts must come from the litellm-venv gate.

## 5. Out of scope (waypoints)
- Consolidating the two effort→wire mappers (1.6).
- Booking SDK hidden retries that win (documented gap).
- cost-tracker issues (concurrent session owns them).
