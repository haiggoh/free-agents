# NVIDIA NIM serving notes — measured behavior per deployment

Last measured: **2026-10-13** (GLM-lane remote session; direct API probes paced inside
the machine-wide 40 RPM budget; four or more samples per claim unless noted).
**Serving behavior changes silently** — these are observations of NVIDIA's deployments,
not properties of the models. Re-test with the recipe at the bottom before trusting
any number below.

Provenance: trigger + latency decomposition from the GLM-lane session 2026-10-11..13;
the broken-models triage in `config/broken-nvidia-models.json` from the concurrent
Nemotron session 2026-10-13 — which this document partially revises (see the probe
confound section).

## glm-5.3 full (`z-ai/glm-5.3`) — the slow lane, and why

- **Door charge: ~53–72s before the first token** (4 direct samples). Payload-
  independent — a 19-token prompt pays it. Generation itself runs **~15–40 tok/s once
  flowing** (45 tokens arrive in ~1–3s after the wait), so this is deployment-side
  scheduling, not model compute. Verified with nothing of ours in the request path
  (direct curl, no proxy, no limiter). Not configurable. Whether it is deliberate
  throttling, capacity queueing, or third-party-model priority is not observable from
  outside — operationally irrelevant, take it as given.
- **Thinking is always on; chat-template kwargs do NOT control it.** Tested inert
  (reasoning still present, identical usage): `chat_template_kwargs` with
  `{"enable_thinking": false}`, `{"thinking": {"type": "disabled"}}` (a non-empty dict
  is *truthy* to the template — a wrong shape can affirmatively ENABLE what it was
  meant to disable), and `{"thinking": false}`.
- **`reasoning_effort` (API-layer field) IS honored**: `"low"` → 0 reasoning tokens,
  content immediate; `"high"` (the session generator's current default) → reasoning
  volume that dominates session turn time on complex turns.
- **Session math**: turn ≈ door charge (~1 min, fixed) + prefill of an uncached
  ~100k-token context (rate unmeasured) + reasoning and output at ~15–40 tok/s.
  No configuration makes this deployment sub-minute. Use it for deliberate deep work;
  see the roster notes in `config/remote-agents.sh` for the fast alternatives.

## glm-5.3-flash (`z-ai/glm-5.3-flash`) — fast, healthy, also always-thinking

0.6–3.9s on trivial prompts, non-streaming fine for small outputs, thinking included
(32–80 reasoning tokens even on "Reply OK") — and still fast. The earlier "requires
streaming" label was an artifact (below).

## Probe-instrument confound — affects every "empty" verdict in the history

GLM-class models spend their entire `max_tokens` budget on reasoning BEFORE content.
`remote-probe-log.py` sends `max_tokens: 24` — below GLM's minimum reasoning spend —
so `finish_reason: length`, content empty, probe records status "empty".
Mechanically reproduced 2026-10-13 (flash, max_tokens 24: 24/24 tokens reasoning,
content `''`, `finish=length`).

Consequences for `config/broken-nvidia-models.json` (re-audit pending):

- `nvidia-glm53` flash: 11/11 "empty" = artifact. **Not broken.**
- **`gpt-oss-20b` (5/5 "empty") is a known reasoning model — same artifact, same
  shape. Strongest candidate for a false "broken" verdict.**
- `muse-glimmer` (2/5 empty) suspect. `kimi-k3` / `deepseek-v4` "timeouts" may be
  the same story in a different mask (non-streaming + thinking + door-charge-style
  queue ⇒ probe timeout).
- 404 "not provisioned for account" verdicts (kimi-k26, nano3, nemotron4, minitron)
  are unaffected by this confound.

Probe tool needs (pending fix): `max_tokens >= 512` for reasoning models; record
`finish_reason`, reasoning length, and elapsed; route through the shared rate
limiter in `bin/rate_limiter.py` (it currently self-paces *outside* the machine-wide
bucket — uncounted calls make the shared ledger lie to every co-resident process).

## Working set (measured)

- **nemotron-ultra / nemotron3 / lightning**: 19/19 probes ok at ~1s, including the
  `-thinking` variants. The fast interactive lanes.
- **laguna**: capacity-constrained, NOT fast-lane — ~half of probes 503
  ("worker local total request limit reached 66/32").

## Caching

`cached_tokens: 0` on repeated identical 19-token prompts (2026-10-13). Session-scale
prefill rate and prefix caching at ~100k tokens: **unmeasured** — the remaining
unknown in the 5-minute-turn decomposition.

## Re-test recipe (stay inside the shared 40 RPM budget)

One-off sanity check, paced by hand — for anything scripted use the shared limiter
(`bin/rate_limiter.py`; `bin/remote_http.py`'s `stream_chat` already routes through it):

    key=$(sed -n 1p "${LA_API_KEYS_DIR:-$HOME/.api_keys}/nvidia")
    time curl -s https://integrate.api.nvidia.com/v1/chat/completions \
      -H "Content-Type: application/json" -H "Authorization: Bearer $key" \
      -d '{"model":"z-ai/glm-5.3","max_tokens":512,"temperature":0,
           "messages":[{"role":"user","content":"Reply with the single word: OK"}]}'

Read the WHOLE response, not just `content`: `reasoning_content`, `usage`
(`completion_tokens_details.reasoning_tokens`), and `finish_reason`. For a streaming
decomposition, time the first chunk separately from the total — the gap is the door
charge. A reasoning model needs `max_tokens >= 512`, or you measure truncation, not
the model.
