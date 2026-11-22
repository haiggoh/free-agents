# Provider buckets, limiter fixes, swap pre-flight, effort defaults, bridge errors — Implementation Plan

**Status:** draft
> **Lasting Plans:** when every task below is done, set the Status line to `completed` by running `lasting-plans complete "docs/superpowers/plans/2026-11-22-provider-buckets-and-limiter-fixes.md"` as the closing step.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (NATIVE execution — you implement every task yourself, then ONE final whole-branch review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every free provider gets its own rate bucket built from one structured limits file; the limiter stops failing open, stops booking its own errors and honours per-model cooldowns; clients get retriable 429s instead of 500s; swap pre-flights go through the bucket and prove the target; omitted effort means the saved lane default everywhere; the ladders and the picker know each provider's limits; a default-on "large context only" filter.

**Architecture:** `config/provider-limits.json` (loader `bin/provider_limits.py`) is the single source of limits/model facts. `bin/rate_limiter.py` gains a `provider` dimension; new dimensions (tokens/min, day, week) live in a sidecar `bin/quota_ledger.py` file so live 0.61.0 writers of the NVIDIA file cannot erase them. `bin/la_proxy_hooks.py` maps errors and installs a self-retiring LiteLLM bridge shim. Effort resolves through one choke point `bin/effort_resolve.py`.

**Tech Stack:** Python 3.14 (stdlib only in bin/), bash 3.2-compatible scripts, LiteLLM 1.110.x proxy (pipx venv), pytest + xdist, Textual picker.

**Spec:** `docs/superpowers/specs/2026-11-22-provider-buckets-and-limiter-fixes-design.md` — read it fully first; section numbers below (§1.1b, C4, …) refer to it. Evidence rig: `docs/measurements/2026-11-22-bridge-experiment/`.

**Binding playbooks:** `~/.claude/plans/_PLAYBOOKS/Playbook-milestone-qc-offload-test-proof.md` (QC milestones below) and `~/.claude/plans/_PLAYBOOKS/Playbook-split-monolithic-test-suites.md` (test-file layout below). Read both before Task 1.

## 🚨 System instructions for the executor — read first

1. This plan is self-contained; do not depend on any chat. Revalidate every "confirmed" fact in Phase 0 before relying on it.
2. Before any file edit, write exactly three strategy sentences, then do Phase 0 (read-only).
3. Work ONLY in the worktree `~/ClaudeWorkspace/.wt/fa-provider-buckets` on branch `fix/limiter-provider-buckets`. Never switch the branch of `~/ClaudeWorkspace/free-agents` (launchers resolve through it).
4. Prefer native Read/Grep/Edit; shell for git, tests, processes. Edit anchors come from a FRESH read, whole lines, exact indentation. Never rewrite a file from fragments.
5. TDD every change: the test fails for the RIGHT reason first, then passes. Mutation-check every new guard (temporary edit, suite RED, restore, `git diff` empty for the mutation). Never weaken a test.
6. **Highest granted boundary: commit + push of `fix/limiter-provider-buckets`.** Merge, tag, release, `claude plugin update`, version bumps that ship, and editing any installed copy (`~/.claude/plugins/cache/…`, `~/.local/pipx/venvs/…`) are PROHIBITED.
7. NEVER kill, restart or reconfigure anything listening on 4141–4151 or the swap scratch band; never run `remote-session.sh --stop`; never start a remote session. Your own test proxies use ports you allocate dynamically outside 4141–4199, and you stop them by their own pid only.
8. NEVER touch the live limiter state (`~/.claude/free-agents/.nvidia_throttle_state` or anything under `~/.claude/free-agents/limiter/`): every test and probe sets `LA_NVIDIA_THROTTLE_STATE` / `FA_LIMITER_DIR` to a temp dir. A test that can read the real path is a bug.
9. Commit every task, staged BY PATH (never `git add -A`), message ending with the running model's co-author trailer (look it up in memory `commit-attribution-by-model` — do not derive it) plus `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
10. At every QC milestone, follow the QC playbook verbatim (dispatch brief, mutations RED, `-rs` 0 unexpected skips, receipt). Do not start the next task with an open finding.
11. Append one progress line per receipt to `## Progress log` at the bottom of THIS file, in the same commit.
12. No secrets in receipts, logs or commits. Never print `~/.api_keys/*`, process argv, or `$KEYS --env` output.

## 📌 Context and objectives

- **Source:** git repo `~/ClaudeWorkspace/free-agents`, branch base `origin/main` @ `75d1801` (0.61.0) — *confirmed live 2026-11-22*.
- **Running reality:** live proxies on 4141–4151 run 0.61.0 code (symlinked from the plugin cache) under LiteLLM 1.110.2 — *dated 2026-11-22*. They keep writing the NVIDIA state file during your whole run (mixed-writer constraint, spec C2).
- **Current → required behaviour:** spec §1 (findings, defects L1–L8) → spec §3 C1–C10 and §4 invariants 1–11.
- **Decisions already made by the user:** spec §2 table. Do not re-open them.
- **Unknowns to inspect (do not guess):** Groq `/models` with a real User-Agent (spec C10); Gemini's documented daily quota for the free tier (cite a source URL or record `null` + reason); NVIDIA per-model context/max_output (model cards, one URL per value).
- **Non-goals:** consolidating the two effort→wire mappers; booking SDK hidden retries that win; any cost-tracker change; pinning LiteLLM.

## 🔒 Authority matrix

| Operation | Status | Gate / evidence |
|---|---|---|
| Read-only inspection, local tests, temp dirs | allowed now | — |
| Edit files in the worktree, local commits | allowed now | per-task receipt |
| Push `fix/limiter-provider-buckets` | allowed after Task 12 gate | full gate green + `-rs` clean |
| Start own scratch LiteLLM proxies on dynamic ports > 4199 with fake upstreams | allowed now | sandbox state env, own pid cleanup |
| Real provider calls | allowed ONLY in Task 4 (one catalog GET per provider) and Task 10 Step 6 (≤ 2 round-trips per new dispatch entry, through the new buckets) | logged count in receipt |
| Merge / tag / release / plugin update / version bump | PROHIBITED (needs separate user permission) | — |
| Kill/restart live proxies, edit live limiter state, edit installed copies | PROHIBITED | — |
| Change `~/.claude/settings.json`, LaunchAgents, pipx venvs | PROHIBITED | — |

## Global Constraints

- Large-context threshold: **262 144** tokens; filter hides `context <= 262144`; default **ON** for the remote lane; persisted.
- Session eligibility: `context >= session_min_context`, never `dispatch_only` providers (Gemini); a ≤128k model such as Groq gpt-oss is never session-eligible. **The value is PENDING user confirmation.** Rule: after Task 4 has curated contexts, if EVERY model in the current `config/session-preferences.json` order whose provider is NVIDIA has `context > 262144`, use **262 145** (same line as the filter); otherwise use **200 000** and add a waypoint asking the user to choose, naming the models between 200k and 256k. The filter threshold 262 144 itself is decided (user) and does not change.
- Effort precedence: explicit > SAVED lane default (present in `config/session-menu.local.json`) > per-alias last-launch (`bin/last-effort.py`) > provider default. Lanes: remote session/handover/remote dispatch → `remote_api_session`; local session/local dispatch → `local_session`.
- Limits semantics: `null` = no known limit; `0` = forbidden; missing key = schema error.
- NVIDIA main state file: path and key set UNCHANGED (`tokens,last_refill,capacity,rate,cooldown_until,consecutive_429s,last_429_time,models,owners,waiters,deficit,grants`); every new dimension goes to the sidecar `<state>.quota`. Lock order main → sidecar.
- Self-raised hook exceptions carry attribute `la_self_raised = True`.
- Client mapping: chain ended on a rate limit ⇒ HTTP 429 `rate_limit_error` + `Retry-After`; never 500.
- bin/ scripts: stdlib only, `--help` that never executes, unknown flag ⇒ exit 2 with usage on stderr; set-but-invalid env/flag aborts naming it.

## Review Focus

1. **A provider with no limits entry** (a newly added provider): must behave exactly as today (unthrottled, "limits unknown" in the UI) — pinned in Task 5 (`test_unknown_provider_is_unlimited_and_labelled`).
2. **A corrupt or truncated limits file / state file**: picker shows a loud error, proxy refuses to start with a named message; state fails SAFE — Task 3 + Task 4 tests.
3. **Clock skew between processes / a laptop waking from sleep**: day/week counters keyed by UTC from `time.time()`, never monotonic; a 9-hour gap must not grant a burst beyond capacity — Task 5 (`test_long_sleep_refills_to_capacity_not_beyond`).
4. **A request larger than a provider's TPM** (e.g. 9 000 tokens on Groq's 8 000/min): must be ineligible at once, not queue forever — Task 5 (`test_request_larger_than_tpm_is_ineligible`).
5. **Old 0.61.0 proxy writing the NVIDIA file while new code runs**: neither side loses state — Task 5 mixed-writer test against a frozen copy of the 0.61.0 writer.

## Test-file layout (split-suites playbook, decided at plan time)

- `tests/test_rate_limiter.py` is 74 s / 72 tests serial today; pytest-xdist `--dist load` already schedules per test, so it is NOT split. New limiter work goes into NEW themed files (each must stay < 30 s serial): `tests/test_limiter_cooldown.py` (Task 3), `tests/test_limiter_state_io.py` (Task 3), `tests/test_quota_ledger.py` (Task 5), `tests/test_provider_buckets.py` (Task 5), `tests/test_limiter_mixed_writer_v2.py` (Task 5).
- `tests/test_swap_model.py` is 424 s / 12 tests (~35 s each, real waits). New swap tests go in `tests/test_swap_preflight_bucket.py` and use a tests-only knob `LA_SWAP_TEST_FAST=1` that shrinks `LA_SWAP_TERM_GRACE`/readiness polls (production default unchanged; the knob is asserted to be ignored unless `PYTEST_CURRENT_TEST` is set).
- Real-LiteLLM delivery tests (Tasks 1–2, 6) live in `tests/live_proxy/` and run ONLY in the new pre-flight gate (Task 1); they carry NO `load_sensitive` marker. Shared rig in `tests/live_proxy/conftest.py` (not matched by the test glob as a test).
- Measure per-file serial time after Tasks 3, 5, 7 and record it in the receipt; any new file > 30 s gets split per the playbook procedure.

## QC milestones (playbook: milestone-qc-offload-test-proof)

| # | After | Requirements under review (spec wording) | Test files |
|---|---|---|---|
| QC1 | Task 2 | Invariants 4, 9, 10; C4 shim self-retirement | tests/live_proxy/*, tests/test_bridge_shim.py |
| QC2 | Task 3 | Invariants 2, 3; L3/L5/L7 | test_limiter_cooldown.py, test_limiter_state_io.py |
| QC3 | Task 5 | Invariants 1, 8; Review Focus 1, 3, 4, 5 | test_quota_ledger.py, test_provider_buckets.py, test_limiter_mixed_writer_v2.py |
| QC4 | Task 7 | Invariant 5; progress-based readiness | test_swap_preflight_bucket.py |
| QC5 | Task 11 | Invariants 7, 11; C8 rendered infoboxes (screenshots) | test_picker_limits_infobox.py, test_large_context_filter.py, test_model_facts_complete.py |

Standing rule (verbatim from the playbook): *"At each milestone, run /offload (free-agents:offload) — a stateless dispatch to an out-of-family reviewer — with the brief below. Treat its findings as hypotheses: run every proposed mutation; fix or rebut each finding with evidence. Do not start the next task with an open finding."* Use the playbook's dispatch brief unchanged; attach spec excerpt + tests + fixtures + implementation via `"files"`; reviewer out-of-family from YOUR model (`bin/free-agent-tool.py --list`, `"avoid_family"`).

---

## 🛑 Phase 0 — forced preflight (read-only)

- [ ] **Step 1:** Write three sentences: how Task 1's rig enables every later delivery proof; why the NVIDIA file schema must not change; where the run stops (push of the branch, nothing further).
- [ ] **Step 2:** `git -C ~/ClaudeWorkspace/.wt/fa-provider-buckets status --porcelain` → expect only this plan + spec + `docs/measurements/2026-11-22-bridge-experiment/` committed and nothing dirty; `git log --oneline -3`.
- [ ] **Step 3:** Confirm facts: `grep -n 'os.read(fd, 65536)' bin/rate_limiter.py` (2 hits), `grep -n 'LA_PROXY_HOOKS_RATE_LIMIT=0' bin/remote-session.sh` (1 hit), `grep -n 'raise litellm.RateLimitError' bin/la_proxy_hooks.py` (1 hit), `~/.local/pipx/venvs/litellm/bin/python -c "import litellm,importlib.metadata as m;print(m.version('litellm'))"`. Any mismatch ⇒ STOP and record.
- [ ] **Step 4:** Baseline: `pyt="$HOME/.local/bin/pytest"; "$pyt" -q tests -p no:cacheprovider -n 12 -m 'not load_sensitive' -rs > /tmp/fa-base.out 2>&1; echo EXIT:$?` and `bash .rtc/pre-flight.sh; echo EXIT:$?`. Record pass/skip/fail counts (the 2026-11-22 serial facts are in spec §4b). **Known baseline (2026-11-22, main @ 75d1801, machine loaded by the investigation's scratch proxies):** `2 failed, 1856 passed, 50 skipped in 1892 s` — `tests/test_rate_limiter_mixed.py::MixedWriterProcessTests::test_old_and_new_processes_share_one_file` (passes alone in 0.8 s) and `tests/test_prefill_measure.py::TimeoutCleanupTests::test_timeout_kills_the_whole_process_group`. If either still fails on YOUR baseline, diagnose it (superpowers:systematic-debugging) and fix it as Task 0b before Task 1 — no release ships with a known red test; record the diagnosis in the progress log.

**🧪 Receipt 0:** the three sentences, porcelain output, the four fact checks, both baseline result lines with exit codes.

---

### Task 1: Live-proxy test rig + dedicated pre-flight gate

**Files:**
- Create: `tests/live_proxy/fake_openai_upstream.py` (from `docs/measurements/2026-11-22-bridge-experiment/fake_openai.py`; modes `ok, die, die0, die1, dier, r429`; adds `--port`, a control file `<log>.mode` re-read per request so one fake can switch modes without restart, and records `{"path","stream","mode","model"}` per hit — never headers)
- Create: `tests/live_proxy/conftest.py` (fixtures `fake_upstream(mode) -> FakeUpstream(port, hits(), set_mode())`, `live_proxy(config_yaml, env) -> LiveProxy(port, log_text(), stop())`)
- Create: `tests/live_proxy/test_rig_selfcheck.py`
- Modify: `.rtc/pre-flight.sh` (add `LIVE_PROXY_TESTS` gate)

**Interfaces:**
- Produces: `live_proxy(cfg: str, env: dict) -> LiveProxy` — starts `$LITELLM_PY bin/litellm-with-trust.py --config <tmp>/cfg.yaml --port <free port > 4199>` with `cwd=<tmp>`, env merged with `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LA_NVIDIA_THROTTLE_STATE=<tmp>/state`, `FA_LIMITER_DIR=<tmp>/limiter`, `LA_SESSION_MENU_CONFIG_DIR=<tmp>/menu`, `PYTHONPATH=<repo>/bin`; symlinks `<repo>/bin/la_proxy_hooks.py` into `<tmp>`; readiness is PROGRESS-based (alive AND (log grew OR `/health/liveliness` 200) within each 60 s; hard cap 420 s); `stop()` kills only its own pid and asserts the port is free.
- Produces: `anthropic_stream(port, model="claude-opus-5-5") -> (http_status: int, events: list[dict], headers: dict)`.
- Produces: fixtures config helper `two_tier_config(primary_port, fallback_port, hooks=True) -> str` using `model: nvidia_nim/fake-primary` / `nvidia_nim/fake-fallback` (production prefix — spec §1.1b route fact).

- [ ] **Step 1: failing self-check tests** in `test_rig_selfcheck.py`:
  - `test_ok_stream_round_trips`: fallback-less config, mode ok ⇒ status 200, concatenated text == "pong-ok", fake hits == 1 with path `/v1/chat/completions`.
  - `test_rig_never_uses_live_state`: inside a running `live_proxy`, the child's env has `LA_NVIDIA_THROTTLE_STATE` under the tmp dir (read `/proc`-free: the rig writes its env to `<tmp>/env.json` before exec; assert no value contains `.claude/free-agents`).
  - `test_rig_port_outside_live_band`: port > 4199.
- [ ] **Step 2:** run under the LiteLLM interpreter: `~/.local/pipx/venvs/litellm/bin/python -m pytest -q tests/live_proxy -p no:cacheprovider` → FAIL (fixtures missing).
- [ ] **Step 3:** implement the fake, the conftest fixtures and `two_tier_config`.
- [ ] **Step 4:** rerun → 3 passed. Record the cold-start seconds the rig measured.
- [ ] **Step 5:** add the gate to `.rtc/pre-flight.sh`: `LIVE_PROXY_TESTS="tests/live_proxy"` run with `$LITELLM_PY -m pytest -q tests/live_proxy -p no:cacheprovider --timeout=1800` — NO `-m` filter, serial (no `-n`), and fail when the summary line says `no tests ran` or the collected count is 0. Update its `--help` text. Verify: `bash .rtc/pre-flight.sh --help` (prints, runs nothing), then `bash .rtc/pre-flight.sh; echo EXIT:$?`.
- [ ] **Step 6: commit** `tests/live_proxy/ .rtc/pre-flight.sh` — `test: live-proxy rig (real LiteLLM + fake OpenAI upstream) + dedicated pre-flight gate`.

**Discriminant:** temporarily set the gate's path to an empty dir ⇒ pre-flight exits non-zero with "covers nothing"; restore.
**🧪 Receipt 1:** the RED run line, the GREEN run line, gate exit codes (good + empty-dir), cold-start seconds.

### Task 2: Hook error mapping, L8 fix, bridge shim (C4)

**Files:**
- Modify: `bin/la_proxy_hooks.py` (fast-fail raise ~:401-406; `async_log_failure_event` ~:421-451; `async_post_call_failure_hook` ~:475-506; new `install_bridge_shim()` + call at import, after `proxy_hooks` creation ~:563)
- Create: `tests/test_bridge_shim.py` (unit, LiteLLM venv gate)
- Create: `tests/live_proxy/test_error_mapping.py`

**Interfaces:**
- Produces: `SELF_RAISED_ATTR = "la_self_raised"`; `install_bridge_shim() -> str` returning exactly one of `"installed"`, `"upstream-guard-present"`, `"adapter-absent"`; module global `BRIDGE_SHIM_STATE: str`.
- Produces (for Task 6): `_retry_after_for_chain(exc) -> float | None` — shortest remaining cooldown named by the exception chain, else None.

- [ ] **Step 1: failing live tests** (`test_error_mapping.py`, all via the Task 1 rig, hooks on, sandbox state):
  - `test_fallback_429_reaches_client_as_429`: primary mode `die1`, fallback mode `r429` ⇒ `http_status == 429`, an `error` event with `type == "rate_limit_error"`, `Retry-After` header present and > 0. (Today: 500 — spec §1.1b row 5.)
  - `test_cooling_fallback_reaches_client_as_429`: sandbox state pre-seeds `models["nvidia_nim/fake-fallback"]` and `models["fake-fallback"]` cooling 3600 s, env `LA_PROXY_FALLBACK_NAMES=la-fallback-1` ⇒ 429, fallback hits == 0.
  - `test_self_raised_fast_fail_books_nothing`: same setup ⇒ after the request, sandbox state `consecutive_429s` unchanged, `models["fake-primary"]` absent unless the primary itself returned 429, and the hook log has NO `"NVIDIA 429 with"` line attributable to the fast-fail. Bucket bookings == fake hits (invariant 9).
  - `test_empty_unterminated_stream_is_retriable`: primary `die0`, no fallback ⇒ NOT (status 200 with empty text and no error); expect 529/`overloaded_error` or a fallback answer. If Step 3's probe shows LiteLLM offers no hook point for this on /v1/messages, convert this test to `xfail(strict=True, reason="<probe evidence>")` and record it in the receipt — never delete it.
- [ ] **Step 2: failing unit tests** (`test_bridge_shim.py`): `install_bridge_shim()` on the installed LiteLLM returns `"installed"`; afterwards `AnthropicAdapter().translate_completion_output_params_streaming(None, model="m")` raises `litellm.APIConnectionError` with `.llm_provider == "anthropic"`; a real async iterator still returns an `AnthropicSSEStream`; against a monkeypatched adapter whose source contains `completion_stream is None` it returns `"upstream-guard-present"` and does not wrap; calling it twice does not double-wrap.
- [ ] **Step 3:** run both → RED for the expected reasons (500 observed; shim absent).
- [ ] **Step 4: implement**: (a) the fast-fail sets `la_self_raised = True` on the exception before raising; (b) `async_log_failure_event` returns early for a marked exception (and for any exception whose `__cause__`/`__context__` chain contains a marked one); (c) **PROVEN MECHANISM (2026-11-22 probe, `docs/measurements/2026-11-22-bridge-experiment/la_proxy_hooks.EXPERIMENT-PATCH.py.txt`, response `resp-e.txt`): on streaming /v1/messages in LiteLLM 1.110.2 `async_post_call_failure_hook` is NOT reached with the rate-limit cause — the surfacing exception is the PRIMARY's `APIConnectionError` and the client gets 500. What works: implement `async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data)` as an async generator that re-yields every chunk; on an exception BEFORE the first chunk was yielded, if THIS call's record (a dict keyed by `request_data["litellm_call_id"]`, written by `async_log_failure_event` when it sees an upstream 429 OR a marked self-raised fast-fail for that call id; entries expire after 600 s) says the chain ended on a rate limit, raise `litellm.RateLimitError(...)` marked `la_self_raised` with the shortest known remaining cooldown as Retry-After → client receives HTTP 429 `rate_limit_error` (observed); otherwise re-raise the original. After ≥1 chunk was yielded, never change the error (headers are sent). Then `async_post_call_failure_hook` IS called with the 429 (observed) — keep its account-error branch unchanged and make sure it does not rewrite a 429 to 402.** Attach `Retry-After` from `_retry_after_for_chain`; if LiteLLM drops the header on this path, record that in the receipt and assert only status + type; (d) `install_bridge_shim()` wraps the funnel exactly per upstream PR #53341 semantics (raise `APIConnectionError(message=..., llm_provider="anthropic", model=model)` when `completion_stream is None`) and the non-stream `translate_openai_response_to_anthropic` when `response is None or response.choices is None`; detection of an upstream guard = `"completion_stream is None" in inspect.getsource(fn)`. `--self-test` prints `bridge shim: <state>`.
- [ ] **Step 5:** rerun → GREEN; run `.rtc/pre-flight.sh` → all gates green.
- [ ] **Step 6: commit** — `fix(hooks): 429 not 500 when a chain ends on a rate limit; never book self-raised errors; LiteLLM #53340 bridge shim`.

**Discriminant:** revert (a) alone ⇒ `test_self_raised_fast_fail_books_nothing` RED; revert (c) alone ⇒ both 429 tests RED (500); make the shim's detection always report `upstream-guard-present` ⇒ shim unit test RED.
**🧪 Receipt 2:** RED lines, GREEN lines, each discriminant's RED line, `--self-test` output. **Then QC1.**

### Task 3: Limiter correctness — L1, L2, L3/L7, L4, L5, L6

**Files:**
- Modify: `bin/rate_limiter.py` (`SmoothTokenBucket.try_acquire` :550-663, `_load_state` :668-704, `note_429` :733-785; the SlidingWindowLimiter equivalents :895-1260)
- Modify: `bin/la_proxy_hooks.py:415` (pass `model=`), `:393-419` (catch `RateLimitTimeout`)
- Modify: `bin/free-agent-tool.py:735-745` (`nvidia_load_detected` — tolerant torn-read: retry once, then False)
- Create: `tests/test_limiter_cooldown.py`, `tests/test_limiter_state_io.py`

**Interfaces:**
- Produces: `try_acquire(..., model: str | None)` returns the model's remaining cooldown (> 0) when `models[model].until > now` — the request WAITS in the queue (registered waiter, heartbeats continue); it never consumes a token while its model cools.
- Produces: `aacquire(..., model=...)` unchanged signature; the hook now passes `model=str(kwargs.get("model") or "")`.
- Produces: `_read_all(fd) -> bytes` (loop `os.read` until `b""`), used by both `_load_state` and `_read`.
- Produces: `_fail_safe_state(now) -> dict` — tokens 0.0, `cooldown_until = now + base_cooldown`, everything else empty; written back and logged to stderr as `rate_limiter: state unreadable (<reason>) -> failing SAFE`.
- Produces: `in_window` in `note_429` = number of `grants` with `now - t < 60` (smooth) / stamps in window (sliding); the legacy peek path appends to `grants` too.

- [ ] **Step 1: failing tests** (`test_limiter_cooldown.py`): `test_cooling_model_gets_no_token` (seed models[m].until=now+30 with a fake clock; `try_acquire(token=t, model=m)` > 0 and tokens unchanged; another model m2 still gets a token); `test_chainless_proxy_waits_out_cooldown` (fake clock advanced past until ⇒ 0.0); `test_steady_load_model_429_is_model_throttle` (40 grants in the last 60 s spread evenly, tokens≈0, one model 429 ⇒ kind `model_throttle` only when < limit/2 grants in window, else `rpm_overrun` — assert BOTH sides with two fixtures); `test_own_owner_serves_winner_not_steals_age` (L5: two tokens of one owner, the older keeps its `arrived`).
  (`test_limiter_state_io.py`): `test_state_larger_than_64k_is_read_whole` (write 200 KB of valid waiters ⇒ parsed, no reset); `test_corrupt_state_fails_safe` (garbage bytes ⇒ next `try_acquire` returns > 0 and tokens == 0; stderr names "failing SAFE"); `test_torn_read_in_dispatch_peek_retries` (free-agent-tool peek on a half-written file then a whole one).
  Hook side (`tests/test_la_proxy_hooks.py`, LiteLLM venv): `test_hook_passes_model_to_aacquire`; `test_rate_limit_timeout_maps_to_rate_limit_error` (aacquire raising `RateLimitTimeout` ⇒ hook raises `litellm.RateLimitError` marked `la_self_raised`).
- [ ] **Step 2:** run → RED (each for its own reason; quote them).
- [ ] **Step 3:** implement in both limiter classes (they share `_cool_model`/`_cooling`; add one shared helper `_model_wait(models, model, now) -> float` used by both).
- [ ] **Step 4:** rerun targeted → GREEN; then full main suite `-n 12 -rs` and `.rtc/pre-flight.sh`.
- [ ] **Step 5:** extend `tests/live_proxy/test_error_mapping.py` with `test_chainless_cooling_model_gets_zero_upstream_hits` (no `LA_PROXY_FALLBACK_NAMES`, primary seeded cooling 20 s ⇒ fake hits == 0 during the cooldown, request completes after it).
- [ ] **Step 6: commit** — `fix(limiter): per-model cooldown enforced in the grant path; read state whole and fail SAFE; real 60 s window in the 429 classifier; RateLimitTimeout -> 429`.

**Discriminant:** delete the `_model_wait` call in one class ⇒ that class's cooldown test RED; restore `os.read(fd, 65536)` ⇒ 64k test RED; replace fail-safe with the old reset ⇒ corrupt-state test RED.
**🧪 Receipt 3:** RED/GREEN lines, discriminant RED lines, per-file serial seconds for the two new files. **Then QC2.**

### Task 4: `config/provider-limits.json` + loader + catalog facts (C1, C10 data)

**Files:**
- Create: `config/provider-limits.json`, `bin/provider_limits.py`, `bin/model-facts.py`, `tests/test_provider_limits.py`, `tests/test_model_facts_complete.py`
- Create: `docs/measurements/2026-11-2x-catalog-facts.md` (date of your run)

**Interfaces:**
- Produces: `provider_limits.load(path: Path | None = None) -> Limits`; `Limits.provider(pid: str) -> ProviderLimits | None`; `Limits.model(pid: str, model_id: str) -> ModelFacts | None`; `Limits.session_min_context: int`; `Limits.large_context_threshold: int` (262144); `class LimitsError(ValueError)`.
- `ProviderLimits` (frozen dataclass): `rpm, tpm, rpd, rpw, tpd: int | None`, `dispatch_only: bool`, `reserves_on_request: bool`, `measured_at: str`, `source: str`.
- `ModelFacts` (frozen): `context: int | None`, `max_output: int | None`, `released: str | None`, `broken: bool | None`, `source: str`, `reason: str | None` (required when a field is null).
- Path resolution: `FA_PROVIDER_LIMITS` if set (must exist, else `LimitsError` naming it) → `<repo>/config/provider-limits.json`.
- CLI `bin/provider_limits.py --help | validate [PATH] | show PROVIDER`.
- CLI `bin/model-facts.py --help | fetch [--provider P ...] [--out FILE] | merge --into config/provider-limits.json FILE` — `fetch` does ONE `GET <base>/models` per keyed provider with `User-Agent: free-agents-model-facts/1` (keys read via `bin/` key helper inside Python, never printed); `merge` never overwrites an entry whose `source` does not start with `catalog `.

- [ ] **Step 1: failing tests**: loader rejects unknown keys / wrong types / missing `reason` for null facts / `FA_PROVIDER_LIMITS` pointing nowhere (each its own test, message names the key/var); `test_threshold_is_256k` (262144); `test_unknown_provider_returns_none`; `test_model_facts_complete` — for every alias in `remote-session.sh --inventory` whose provider has a key file name in `bin/` provider table, `Limits.model()` returns facts where every filter field (`context`, `broken`, `released`) is a value or null-with-reason (a MISSING entry fails, naming the alias).
- [ ] **Step 2:** RED.
- [ ] **Step 3:** implement loader + CLI; write `provider-limits.json`: providers from spec §1.4 measurement (nvidia rpm 40; mistral rpm 2, tpm 40000; groq rpm 1000, tpm 8000; gemini `dispatch_only: true`, rpd = documented free-tier value with URL or null+reason; cerebras `reserves_on_request: true`), each `measured_at`/`source` citing `docs/measurements/2026-11-20-remote-quota-probe.md` or a URL.
- [ ] **Step 4:** implement `model-facts.py`; run `fetch` ONCE (real calls: mistral, openrouter, gemini, groq with UA, nvidia), save the raw output under `docs/measurements/`, `merge` it. NVIDIA models lacking catalog context: curate from each model card (URL per value). Groq: record what the UA retry returned.
- [ ] **Step 5:** rerun → GREEN (`test_model_facts_complete` lists 0 missing).
- [ ] **Step 6: commit** — `feat: provider-limits.json (single source of limits + model facts), loader, catalog ingestion`.

**Discriminant:** delete one roster model's entry ⇒ completeness test RED naming it; set `FA_PROVIDER_LIMITS=/nonexistent` ⇒ `LimitsError` naming the var.
**🧪 Receipt 4:** RED/GREEN lines, the real-call count (must equal the number of providers fetched), the completeness count.

### Task 5: Per-provider buckets + quota sidecar (C2)

**Files:**
- Create: `bin/quota_ledger.py`, `tests/test_quota_ledger.py`, `tests/test_provider_buckets.py`, `tests/test_limiter_mixed_writer_v2.py`, `tests/fixtures/rate_limiter_0_61_0.py` (byte copy of `git show 75d1801:bin/rate_limiter.py` — the frozen old writer)
- Modify: `bin/rate_limiter.py` (`get_limiter` :1398-1405; both classes' `__init__`, `try_acquire`, `note_429`, `note_success`)
- Modify: `bin/la_proxy_hooks.py` (`_is_nvidia` gating ~:168, :390-392 → `_provider_of(kwargs)`; bucket per provider)

**Interfaces:**
- Produces: `get_limiter(path=None, rpm=None, clock=time.time, cooldown=None, provider: str = "nvidia")` — `provider="nvidia"` keeps today's path/env/settings exactly; other providers: state `FA_LIMITER_DIR` (default `~/.claude/free-agents/limiter`) `/<provider>.state`, rpm from `Limits.provider(p).rpm`; a provider with no rpm/tpm/rpd/rpw/tpd ⇒ `None` (no limiter — Review Focus 1).
- Produces: `try_acquire(..., model=None, tokens: int = 0)` / `aacquire(..., tokens=0)` — a grant also books `tokens` in the sidecar; returns wait > 0 while any sidecar dimension lacks room; returns `float("inf")` when the request can NEVER fit (tokens > tpm, exhausted until next reset beyond `max_wait`) and `aacquire` raises `RateLimitTimeout("ineligible: <dimension>")` immediately.
- Produces: `note_success(model=None, tokens_used: int | None = None, booked: int = 0)` — refunds `booked - tokens_used` when known; `note_quota_headers(headers: Mapping[str, str])`; `note_quota_refusal(dimension: str, reset_at: float)`.
- Produces: `bin/quota_ledger.py` `QuotaLedger(path, limits: ProviderLimits, clock)` with `room(tokens, now) -> float` (seconds until it fits, inf if never), `book(tokens, now) -> None`, `refund(n, now)`, `adopt_headers(headers, now)`, `mark_exhausted(dimension, until)`, `snapshot(now) -> dict` (used by Tasks 6, 10, 11). Counters keyed `day: YYYY-MM-DD`, `week: YYYY-Www` (UTC). Lock order: caller holds the main lock, ledger takes the sidecar lock.
- Produces: `provider_headroom(provider: str) -> float` in `rate_limiter` (0.0–1.0 = min over dimensions of remaining/limit; 1.0 for unlimited) — read-only.

- [ ] **Step 1: failing tests**: `test_quota_ledger.py` (tpm window rolls; day/week rollover at UTC boundaries with a fake clock; header adoption overrides local remaining; refusal marks exhausted until reset; `test_long_sleep_refills_to_capacity_not_beyond`; `test_request_larger_than_tpm_is_ineligible`). `test_provider_buckets.py` (`test_unknown_provider_is_unlimited_and_labelled`; mistral rpm 2 ⇒ third request in 60 s waits ≈ 30 s on a fake clock; buckets of two providers are independent; hook routes `nvidia_nim/*` → nvidia, `mistral/*` → mistral, `groq/*` → groq). `test_limiter_mixed_writer_v2.py` — multiprocess: one process runs the FROZEN 0.61.0 writer against the NVIDIA main file, another the new code with sidecar bookings, 200 interleaved acquisitions ⇒ (a) total grants in any 60 s ≤ 40, (b) sidecar counters equal the new process's bookings, (c) the main file still parses with exactly the 0.61.0 key set.
- [ ] **Step 2:** RED. **Step 3:** implement. **Step 4:** GREEN + full suite `-rs` + pre-flight.
- [ ] **Step 5:** live delivery test `tests/live_proxy/test_bucket_counts.py::test_rpm_never_exceeded_across_two_proxies` — two `live_proxy` instances sharing one sandbox limiter dir, provider limits overridden to rpm 6 via `FA_PROVIDER_LIMITS`, 20 concurrent requests ⇒ fake hits in any 60 s ≤ 6 (measured on the fake's timestamps).
- [ ] **Step 6: commit** — `feat(limiter): one bucket per provider (rpm/tpm/day/week) with a sidecar ledger; NVIDIA file schema unchanged`.

**Discriminant:** drop the sidecar lock ⇒ mixed-writer (b) RED; make the new code write a new key into the main file ⇒ mixed-writer (c) RED; skip `room()` ⇒ tpm test RED.
**🧪 Receipt 5:** RED/GREEN lines, mixed-writer counts, live 60 s max-hits figure, per-file serial seconds. **Then QC3.**

### Task 6: Load-stretched waits (C3)

**Files:** Modify `bin/rate_limiter.py` (new `stretched_wait`), `bin/la_proxy_hooks.py` (aacquire `max_wait=`), `bin/free-agent-tool.py:724-745` (replace `RATE_LIMITED_PROVIDERS={"nvidia"}` + `nvidia_load_detected` by provider-generic `load_detected(provider)`). Test: `tests/test_stretched_wait.py`.

**Interfaces:**
- Produces: `stretched_wait(provider: str, base: float, *, cap: float = 1800.0) -> float` = `min(cap, base * (1 + queued_waiters / max(1, rpm)) + remaining_cooldown)`; `load_detected(provider: str, env) -> bool` (cooldown active OR consecutive 429s OR queued waiters ≥ rpm/2).
- [ ] **Step 1: failing tests**: base 300 with 0 waiters ⇒ 300; 40 waiters at rpm 40 ⇒ 600; active cooldown 120 ⇒ +120; cap honoured; dispatch tool picks `patient` for mistral when its bucket is loaded (was nvidia-only).
- [ ] **Step 2–4:** RED → implement → GREEN (+ full suite).
- [ ] **Step 5: commit** — `feat: load-stretched waits for every bucketed provider (proxy queue + dispatch tiers)`.
**Discriminant:** hard-code `RATE_LIMITED_PROVIDERS={"nvidia"}` back ⇒ mistral patient test RED.
**🧪 Receipt 6.**

### Task 7: Swap pre-flight through the bucket, target-only (C5)

**Files:** Modify `bin/remote-session.sh` (`swap_model` :1440-1530, `_swap_probe` :1371-1383, `_wait_proxy_ready` :1300-1313 gains a progress mode); Create `tests/test_swap_preflight_bucket.py`.

**Interfaces:**
- Produces: `write_proxy_config` gains a mode `LA_CFG_TARGET_ONLY=1` (no fallback chain, `num_retries: 0`, no `la-fallback-*`).
- Produces: `_wait_proxy_ready <port> <pid> <secs> [progress]` — with `progress`, `<secs>` is the no-progress window (default 60) and the hard cap is `LA_PROXY_READY_CAP` (default 420).
- Produces: `_swap_probe` sends ONE streaming request; verdict lines `PREFLIGHT ok|refused <code>|throttled|timeout`.
- [ ] **Step 1: failing tests** (stub-litellm harness of `tests/test_swap_model.py`, extended: the stub records whether it was started with `LA_PROXY_HOOKS_RATE_LIMIT=0` and its config's model_list names): `test_preflight_runs_with_the_limiter` (env var absent); `test_preflight_config_has_no_fallbacks`; `test_preflight_200_from_target_proceeds`; `test_preflight_410_aborts_nothing_changed`; `test_preflight_429_aborts_throttled_unless_force`; `test_slow_but_progressing_start_is_not_aborted` (stub sleeps 150 s while logging every 10 s, with `LA_SWAP_TEST_FAST=1` scaling to 15 s / 1 s); `test_dead_route_may_target_paid_only` (current route stubbed 402 ⇒ paid_only alias accepted with a label line); `test_skip_override_env_is_gone` (`LA_SWAP_SKIP_PREFLIGHT=1` has no effect — the superseded branch's knob never ships).
- [ ] **Step 2–4:** RED → implement → GREEN; `bash -n bin/remote-session.sh`; `/bin/bash` 3.2 compatibility (no `${var,,}`, no `mapfile`).
- [ ] **Step 5: commit** — `fix(swap): pre-flight books the provider bucket, probes the target alone, progress-based readiness`.
**Discriminant:** re-add `LA_PROXY_HOOKS_RATE_LIMIT=0` ⇒ first test RED; re-add the fallback block ⇒ second RED.
**🧪 Receipt 7** (+ per-file serial seconds; split per playbook if > 30 s). **Then QC4.**

### Task 8: Effort resolver (C6)

**Files:** Create `bin/effort_resolve.py`, `tests/test_effort_resolve.py`; Modify `bin/session_menu_state.py` (expose `saved_effort(cfg_dir, lane) -> str | None` — None when the lane key is absent from the FILE, regardless of `DEFAULT_EFFORT`), `bin/remote-session.sh:2059-2066`, `bin/launch-claude-agent.sh:288-291`, `bin/free-agent-tool.py` (dispatch effort), `skills/free-session-handover/SKILL.md` (the two "pass --effort explicitly" lines → "omitting --effort uses the saved lane default").

**Interfaces:**
- Produces: `resolve(lane: str, alias: str | None = None, explicit: str | None = None, *, config_dir: Path | None = None) -> tuple[str | None, str]` — `(level, provenance)` with provenance ∈ `explicit | saved-lane | last-launch | provider-default`; invalid explicit ⇒ `ValueError` naming it.
- CLI: `effort_resolve.py resolve --lane L [--alias A] [--explicit E]` prints `<level-or-empty> <provenance>`; `--help`.
- [ ] **Step 1: failing tests** — the precedence table (4 rows × 3 lanes), unsaved lane falls through to last-launch, and delivery: `remote-session.sh --dry-run <alias>` with a temp menu dir saying `remote_api_session: max` and a last-launch of `low` prints effort `max`; `launch-claude-agent.sh` dry path picks `local_session`; `free-agent-tool.py` dispatch envelope records the resolved effort (`"effort": "max", "effort_provenance": "saved-lane"`) for a remote entry.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5: commit** — `feat: omitted --effort resolves to the SAVED lane default on every launch, handover and dispatch path`.
**Discriminant:** swap the saved-lane and last-launch layers ⇒ the dry-run test RED.
**🧪 Receipt 8.**

### Task 9: Session ladder — ordered + spill (C7, sessions)

**Files:** Create `bin/session_ladder.py`, `tests/test_session_ladder.py`; Modify consumers `bin/fa-takeover`, `bin/fa_capture.py`, `bin/budget-wall-capture.py` (where they read `config/session-preferences.json`), and `remote-session.sh --swap-model` target suggestion text.

**Interfaces:**
- Produces: `pick(order: list[str], *, need_context: int, limits: Limits, headroom: Callable[[str], float], predicted_wait: Callable[[str], float], timeout: Callable[[str], float]) -> tuple[str | None, list[tuple[str, str]]]` — returns the first ELIGIBLE alias whose predicted wait ≤ its stretched timeout, plus a reasons list `(alias, "skipped: <why>")` for every skipped one.
- Eligibility = key present, not exhausted, `context >= session_min_context`, not `dispatch_only`, not broken.
- [ ] **Step 1: failing tests**: Gemini never picked; a 131 072-context Groq model never picked; NVIDIA first when it has room; NVIDIA predicted wait > timeout ⇒ next eligible picked with reason `skipped: nvidia predicted wait 900s > 600s`; unknown context ⇒ skipped with reason `context unknown` (sessions must not gamble).
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5: commit** — `feat: session/handover ladder spills past a saturated provider and never picks small-context or dispatch-only models`.
**🧪 Receipt 9.**

### Task 10: Dispatch ladder — capacity-weighted spread + new entries (C7, dispatch)

**Files:** Modify `bin/free-agent-tool.py` (ladder ordering ~:800-840), `config/dispatch-preferences.json` (`order` + new entries); Create `tests/test_dispatch_spread.py`.

**Interfaces:**
- Produces: `rank_entries(entries: list[dict], *, need_context: int, need_output: int, limits: Limits, headroom: Callable[[str], float], rng: random.Random) -> list[dict]` — eligible entries ordered by weighted random draw without replacement, weight = `pref_weight(rank) * headroom(provider)` with `pref_weight(r) = 1 / (1 + r)`; ineligible entries dropped; a seeded `rng` makes it deterministic in tests; headroom 0 ⇒ entry moves to the END (still reachable, queues), never dropped.
- [ ] **Step 1: failing tests**: with all headrooms 1.0 the distribution over 10 000 seeded draws matches the weights ±3%; an entry whose model `max_output < need_output` is dropped; Gemini with `rpd` exhausted moves to the end; headroom 0 for NVIDIA ⇒ NVIDIA entries last, not removed.
- [ ] **Step 2–4:** RED → implement → GREEN.
- [ ] **Step 5:** add entries for mistral (a codestral/medium model), groq (a qwen model whose catalog context qualifies, else gpt-oss for dispatch only), openrouter (a `:free` model) — each `smoke_tested: false` first.
- [ ] **Step 6 (real calls, ≤ 2 per new entry, THROUGH the new buckets):** `FA_DISPATCH_INCLUDE_UNTESTED=1` one tiny dispatch per new entry; flip `smoke_tested: true` only on a 200 whose content is non-empty. Record the count.
- [ ] **Step 7: commit** — `feat: offload dispatch spreads by capacity across free providers; mistral/groq/openrouter entries`.
**Discriminant:** set weight to `pref_weight` only (ignore headroom) ⇒ the headroom-0 test RED.
**🧪 Receipt 10** (real-call count).

### Task 11: Infoboxes + large-context filter + completeness gate (C8, C9, C10 UI)

**Files:** Modify `bin/session_picker_model.py` (`RemoteAgent` :211-226 gains `context: int | None`, `max_output: int | None`; `PROVIDER_INFOS` :586-593 limit sentences derived from `Limits`; `_agent_remote_filters` :1584-1597; `RemoteScreen.info_text` :1989-; `APIKeysScreen.info_text` :3251-3270; filter toggles near :2228-2234), `bin/session_menu_state.py` (`SCHEMA_VERSION` 10 → 11, new section `remote_filters` with `large_context_only: true` default, migration of v10 files, `READABLE_SCHEMAS` includes 10 and 11); Create `tests/test_picker_limits_infobox.py`, `tests/test_large_context_filter.py`.

**Interfaces:**
- Produces: `limits_lines(provider: str, model_id: str | None, snapshot: dict | None) -> list[str]` (one shared renderer for both screens): `Requests/min: 40`, `Tokens/min: —`, `Daily quota: none known`, `Weekly quota: none known`, `Today: 312 used`, `Max output: 16 384`, `Context: 1 000 000`, `Full sessions: yes` / `no — context 131 072 ≤ 256k`, `Measured 2026-11-20 — <source>`; unknown provider ⇒ `Limits: unknown (no entry in provider-limits.json)`.
- Filter: `large_context_only` hides `agent.context is not None and agent.context <= 262144`; unknown context ⇒ shown with tag `context unknown`.
- [ ] **Step 1: failing tests** (PTY/Textual pilot — these run in the picker-venv gate; add the files to `PICKER_TESTS` in `.rtc/pre-flight.sh`): rendered Remote list with the filter ON (default from a fresh v11 file AND from a migrated v10 file) does NOT contain a fixture 131 072-context row and DOES contain a 1 000 000 one — assert on rendered rows, not the predicate; toggling OFF shows it; the setting persists across a restart of the screen; infobox text for an NVIDIA group row, a Mistral model row and an unknown provider matches `limits_lines`; APIKeys infobox shows the same lines for the provider.
- [ ] **Step 2–4:** RED → implement → GREEN; save screenshots of the Remote screen (filter ON/OFF) and the API keys screen (`save_screenshot` → `rsvg-convert` PNG) into `docs/measurements/2026-11-2x-picker-screens/` and LOOK at them.
- [ ] **Step 5: commit** — `feat(picker): limits/quota/max-output infoboxes from provider-limits.json; "Large context only" filter, default ON`.
**Discriminant:** default `large_context_only` to false ⇒ fresh-file rendered test RED; remove the migration ⇒ v10 test RED.
**🧪 Receipt 11** (+ screenshot paths). **Then QC5.**

### Task 12: Docs, waypoints, final gate, push

- [ ] **Step 1:** CHANGELOG `## Unreleased` entry (no version bump — prohibited); README/docs sections that describe the limiter, swap pre-flight and effort defaults updated; `docs/ROADMAP.md` line.
- [ ] **Step 2:** Full gate: `rtc-ship.json test_cmd` equivalent with `-rs` + `bash .rtc/pre-flight.sh`. Record counts; 0 unexpected skips; compare with Receipt 0.
- [ ] **Step 3:** Final whole-branch review: one dispatch to the strongest out-of-family free reviewer with the spec + `git diff origin/main...HEAD`; verify every finding against the code; fix or rebut.
- [ ] **Step 4:** Waypoints via `waypoints.py` (never hand-edit): `done` for `litellm-proxy-crashes-on-v1`, `litellm-proxy-500s-on`, `swap-model-preflight-rate` with `--evidence` naming commits + test counts ONLY if their every point is covered (else `edit --point` what remains); `add` waypoints for the out-of-scope items in spec §5 and for the reviewer-#4 hidden-retry gap if not already tracked.
- [ ] **Step 5:** Secret scan the diff's files (`audit-loose-ends` `redact-secret.py --scan-only`), then `git push -u origin fix/limiter-provider-buckets`; verify with `git ls-remote origin fix/limiter-provider-buckets` (quote the sha).
- [ ] **Step 6:** STOP. The run's authority ends here. Report: implemented vs planned, receipts that ran, tests that did not, the branch sha; say plainly that merge/tag/release await the user. Note for the user: branch `fix/swap-skip-preflight` (worktree `.wt/fa-swap-override`) is superseded by Task 7 and can be deleted by them.
- [ ] **Step 7:** set `**Status:** completed` on a `docs/plan-provider-buckets-complete` branch commit and run `lasting-plans complete "docs/superpowers/plans/2026-11-22-provider-buckets-and-limiter-fixes.md"`.

## 🧨 Adversarial review

| Tempting shortcut | Plausible false success | Harm | Rule | Detector |
|---|---|---|---|---|
| Mark real-LiteLLM tests `load_sensitive` "because they are slow" | main gate + pre-flight green | the delivery proofs never run | live tests live in `tests/live_proxy/`, own gate, no marker | Task 1 empty-dir discriminant; gate prints collected count |
| Use `openai/` prefix in fixtures | tests pass | exercises the Responses bridge, not NIM's chat/completions | production prefix only | `test_ok_stream_round_trips` asserts path `/v1/chat/completions` |
| Point a test at the real limiter state | passes locally | throttles/corrupts live sessions | sandbox env always | `test_rig_never_uses_live_state` |
| Add new keys to the NVIDIA main file | unit tests green | live 0.61.0 proxies erase them every write | sidecar only | mixed-writer (c) |
| Count bookings from the hook's own counters | invariant 9 "holds" | self-booking bugs invisible | count on the fake's hit log | Task 2/5 live tests |
| Fix 500→429 only for the non-stream path | non-stream test green | streaming (the real traffic) still 500 | streaming tests are the primary | `test_fallback_429_reaches_client_as_429` uses stream |
| Edit the pipx LiteLLM to apply #53341 | works today | reverted silently by the nightly upgrade | runtime shim in la_proxy_hooks only | shim unit tests; installed files untouched (`git`-free check: `pip show -f` hash unchanged) |
| Swap test waits real 150 s | green but 7-minute files | gate floor balloons | `LA_SWAP_TEST_FAST` knob, test-only | per-file timing in Receipt 7 |
| Treat `DEFAULT_EFFORT` as "saved" | precedence test passes on defaults | last-launch layer dead | `saved_effort` reads the FILE | Task 8 unsaved-lane test |
| Filter predicate test only | green | rendered list unchanged (dead selector class of bug) | assert rendered rows | Task 11 pilot tests + screenshots |
| Catalog merge overwrites curated NVIDIA facts | completeness green | wrong context, model hidden/shown wrongly | merge only `source: catalog …` | Task 4 merge test |
| Real provider calls in routine tests | green | spends quota, flakes | real calls only at the two named steps | receipts carry the counts |
| `git add -A` | commit "clean" | stages unrelated work | stage by path | `git diff --cached --stat` in each receipt |
| Reading "push allowed" as "merge allowed" | branch merged | unreviewed release path | authority matrix | Task 12 Step 6 stop |

Risks that cannot be tested safely: real NVIDIA 429 behaviour under load (only the fakes); real Groq/Mistral quota exhaustion (only header-adoption unit tests).

## 🏁 Final review and delivery

Answer in the Task 12 report: what is implemented vs only planned; which receipts ran; which tests did not run and why; that no live proxy, live state, installed copy or user config was touched; that the run stopped at the push; the plan file reread after the last edit.

## Progress log

- 2026-11-22 — spec + plan written by the investigating session (Claude Opus 5.5); evidence rig committed; execution not started.
