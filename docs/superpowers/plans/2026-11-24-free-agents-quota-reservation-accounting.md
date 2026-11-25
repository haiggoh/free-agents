# free-agents: quota reservation accounting — exact receipts and request-lifetime bookings — Implementation Plan

**Status:** draft

> **Lasting Plans:** when every task below is done, set the Status line to `completed` by running `lasting-plans complete "~/.claude/plans/2026-11-24-free-agents-quota-reservation-accounting.md"` as the closing step.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (NATIVE: implement every task yourself, then ONE final whole-branch review). Steps use `- [ ]` checkboxes.

**Goal:** A token reservation is reconciled against exactly the reservation it was booked as — in its own TPM stamp and its own UTC day/week — and a booking lives exactly as long as its request, so slow queues and long streams no longer lose their over-estimate refund.

**Architecture:** `QuotaLedger.book()` returns a receipt; `QuotaLedger.settle(receipt, used)` reconciles that receipt only (idempotent). The proxy hook books AFTER the limiter grants, keeps the receipt keyed by `litellm_call_id` until the success or failure callback, and prunes only entries older than the hard upper bound of a live request. One LiteLLM HTTP contract test proves the chain end to end on a token-limited route.

**Tech Stack:** Python 3 (LiteLLM venv for proxy tests — interpreter verified at Phase 0), pytest, fcntl-locked JSON sidecar.

**Spec:** `~/.claude/plans/free-agents next patch — reservation lifecycle and quota reconciliation (Claude Code plan).md` (JoyIA review, 2026-11-24). This plan keeps its genuine findings and narrows its scope; the "Judgement" section says what was kept, changed and deferred, and why.

---

## Opening rules

1. **Highest authority granted: push the feature branch `fix/quota-reservation-receipts`.** Merge, tag, release, `claude plugin update`, and restarting ANY live proxy each need a separate explicit user go. A go for one boundary never implies the next.
2. Work only in `~/ClaudeWorkspace/.wt/fa-quota-receipts`, created from current `origin/main`. Never switch the canonical checkout's branch. Never touch `.wt/fa-nicegui` (another session owns it).
3. Preserve 0.63.1: no `_la_*` key (or any bookkeeping) in outbound kwargs; the existing no-`_la_`-keys regression stays green. No NVIDIA-only drop list.
4. Tests use fake clocks, temp ledger/limiter state (`FA_LIMITER_DIR` or the module's own override — verify the name at Phase 0), loopback stub upstreams and synthetic keys. No real provider call, no edit of the live limiter state under `~/.local/state` or wherever Phase 0 finds it.
5. Never kill, restart or reconfigure a proxy on 4141–4151 / 8000–8010. Old proxies keep running OLD code until the user restarts them; this plan must be safe for that mixed state (Task 1 policy U).
6. TDD; every defect gets a known-bad discriminant (red on the old code, green on the new). Never weaken an assertion, never skip a required test silently (`pytest -rs` and read the skip list).
7. Stage by path; commit per task; push at M1 and M2. Commit trailer = the running model's line from `~/.claude/projects/-Users-bra0002h/memory/commit-attribution-by-model.md`.
8. Out of scope: dependency upgrades, limiter-fairness changes, cooldown/fallback behaviour, the NiceGUI front end, mid-stream 500 handling (separate waypoint `free-agents-server-error-mid`).

## Judgement of the JoyIA plan (decisions)

| JoyIA item | Verdict | Evidence / reason |
|---|---|---|
| A. booking can expire before reconciliation | **KEEP** | confirmed live 2026-11-24 on 0.63.2: `bin/la_proxy_hooks.py:500` `_RECORD_TTL = 600.0`; `_book_call` at `:852` / `:865` runs BEFORE `await bucket.aacquire`; `_take_booked` at `:968` returns 0 for a pruned entry. `rate_limiter.py` allows a stretched wait up to 1800 s and `stream_timeout` is 300 s (0.63.2). |
| B. refund hits the newest stamp | **KEEP** | confirmed live: `bin/quota_ledger.py:176-191` `refund(n, now)` deducts from `stamps[-1]` and calls `_rollover(st, now)` first, so a refund after UTC midnight subtracts from the NEW day. Callers: `rate_limiter.py:919` and `:1365` (both limiter modes). |
| Impact claim (TPM providers only) | **KEEP, quantified** | `config/provider-limits.json`: mistral `tpm=40000`, groq `tpm=8000`; nvidia/gemini/cerebras `tpm=None`. NVIDIA is unaffected by default; Mistral and Groq rolling windows over-count. |
| C. HTTP contract proof | **KEEP, narrowed** | one contract test on a TPM-limited route: non-stream + stream + two overlapping requests completing out of order. Tool-use continuation is covered by existing contracts; not repeated. |
| "Separate request identity from upstream attempt identity" (retries/fallback) | **DEFER** | already a documented, open gap: waypoint `out-of-scope-items-from-the` ("booking SDK hidden retries that win", contract test 8b xfail). This plan PINS current behaviour for a fallback (Task 2 test) and must not regress it; redesign stays with that waypoint. |
| "Startup orphan recovery that distinguishes abandoned from slow" | **REPLACE** with a derived bound | a live request cannot outlive `MAX_WAIT (1800 s) + stream_timeout (300 s)`. An entry is removed by its success OR failure callback; a safety prune drops only entries older than `2 × (1800 + 300) = 4200 s` (named constant computed from the two sources, not a literal). No refund is ever invented for a pruned entry. This is not "just a bigger TTL": lifetime ends at the callback; the bound only caps memory for callbacks that never fire. |
| "Detect incompatible old sidecar writers" | **KEEP as policy U** | old proxies rewrite the sidecar wholesale and drop receipt ids. Policy U (Task 1) makes an unknown receipt safe; plus a one-line launch-time warning (waypoint `free-agents-rate-limiter`) — Task 3. |
| Candidate version 0.63.2 | **CHANGE** | 0.63.2 shipped 2026-11-24 (`3368b20`). Next free patch resolved at Phase 0 against `git ls-remote --tags origin` (tags are a shared namespace). |
| Plan location `docs/superpowers/plans/` in the repo | **CHANGE** | keep this plan in `~/.claude/plans` (concurrent sessions in the repo); the executor copies it into the worktree's `docs/superpowers/plans/` in its first commit so the repo carries it, and keeps THIS file as the authoritative copy, updating it with receipts. |

## Contract (exact interfaces)

- `QuotaLedger.book(n: int, now: float) -> Receipt` (was: returns None). `Receipt` = frozen dataclass `{id: str (uuid4 hex), n: int, at: float, day: str ("YYYY-MM-DD" UTC), week: str (ISO "YYYY-Www")}`, JSON round-trippable via `Receipt.to_json() / Receipt.from_json(d)`. The TPM stamp written by `book` carries `"id": receipt.id`.
- `QuotaLedger.settle(receipt: Receipt, used: int | None, now: float) -> str` returns exactly one of `"settled"`, `"already"`, `"unknown"`, `"over"`, `"no-usage"`:
  - `used is None` (usage absent/malformed) → `"no-usage"`, nothing refunded (never treat absent as zero).
  - `used > receipt.n` → book the difference as extra usage on the CURRENT period and stamp, return `"over"` (actual usage above estimate is real spend).
  - otherwise refund `receipt.n - used`: from the stamp whose `id == receipt.id` (if still inside the TPM window), from `day` only if `receipt.day` is the ledger's current day, from `week` only if `receipt.week` is current; mark the receipt settled (bounded set of settled ids in the sidecar, pruned with the TPM window + 1 day). Return `"settled"`; a second call with the same receipt → `"already"`, no change.
  - **Policy U (unknown receipt):** no stamp with that id (pruned by an OLD writer, or expired) → refund only the period counters that still match, never touch another stamp; return `"unknown"`.
- `QuotaLedger.refund(n, now)` is REMOVED from production callers (no fallback path refunds the newest stamp). Keep the method only if a test or external caller needs it, marked deprecated, raising if called from `rate_limiter`.
- `rate_limiter` `note_success(tokens_used, booked)` becomes `note_success(tokens_used: int | None, receipt: Receipt | None)` in BOTH limiter classes (sliding window ≈`:919`, smooth bucket ≈`:1365`); `receipt is None` → no ledger write.
- `la_proxy_hooks`: `_CALL_BOOKED: dict[str, Receipt]` keyed by `litellm_call_id`; `_book_call(kwargs, receipt)` called only AFTER `aacquire` returns a grant; `_take_booked(kwargs) -> Receipt | None` pops it; the FAILURE callback pops it too (no settle: a failed call's reservation stays booked — the provider may have counted it; this matches current behaviour, pinned by a test). `_RECORD_TTL` is replaced by `_RECORD_BOUND = 2 * (MAX_WAIT_S + STREAM_TIMEOUT_S)`, both imported/derived from their real sources.

## Phase 0 — forced preflight (read-only)

- [ ] Write three sentences connecting receipts, request-lifetime bookings and the HTTP proof to the goal.
- [ ] `git -C ~/ClaudeWorkspace/free-agents fetch origin --tags`; record `origin/main` sha + version; pick the next free patch version (no tag, no open branch uses it).
- [ ] `git worktree add -b fix/quota-reservation-receipts ~/ClaudeWorkspace/.wt/fa-quota-receipts origin/main`; `git status --porcelain` empty.
- [ ] Re-verify the line facts in the Judgement table BY CONTENT in this checkout; any already fixed → drop only that part, record it.
- [ ] Find every caller of `book(` / `refund(` / `note_success(` (`git grep -n`), including `bin/free-agent-tool.py` and any dispatch path; list them — the contract must update ALL.
- [ ] Verify the LiteLLM interpreter (`~/.local/pipx/venvs/litellm/bin/python -c "import litellm, pytest; print(litellm.__version__)"` or the path `.rtc/pre-flight.sh` uses); record versions. Read `rtc-ship.json` `test_cmd` and `.rtc/pre-flight.sh` gates.
- [ ] Find the names of `MAX_WAIT` (stretched-wait cap, `rate_limiter.py` ≈1656) and the stream timeout source (`LA_STREAM_TIMEOUT`, 0.63.2) so `_RECORD_BOUND` derives from them.
- [ ] Baseline: `tests/test_quota_ledger.py tests/test_provider_buckets.py tests/test_quota_wiring.py tests/test_la_proxy_hooks.py` under the verified interpreter, plus `tests/test_litellm_contract.py tests/test_bridge_shim.py`; output to a file, read `EXIT:` and `-rs` skips. Then the full `test_cmd`. Record counts.
- [ ] Receipt: commands, versions, counts, EXIT codes, chosen version, the caller list.

### Task 1: Exact receipts in the ledger

**Files:** Modify `bin/quota_ledger.py`; Test `tests/test_quota_receipts.py` (NEW file — split playbook; do not grow `test_quota_ledger.py`).

- [ ] **Step 1 — failing tests** (memory-only/temp IO, fake `now`):
  - `test_refund_targets_original_reservation`: A books 1000 @100, B books 2000 @120, A settles used=125 @140 → at @150 TPM used = 125+2000 = 2125; at @161 (A out of the 60 s window) used = 2000 (old code: 1125).
  - `test_settle_is_idempotent`: second `settle` → `"already"`, counters unchanged.
  - `test_expired_receipt_never_touches_live_stamp`: A @0, B @70, A settles @71 → B's stamp unchanged; returns `"unknown"` for the stamp part, day/week refunded.
  - `test_settle_keeps_original_utc_period`: A books 23:59:50 UTC day 1, B books 00:00:10 day 2, A settles 00:00:20 → day-2 tokens unchanged; day-1 counter is already rolled away (no negative, no cross-day subtraction).
  - `test_unknown_receipt_policy_u`: sidecar rewritten by an old-style writer (stamp without `id`) → `"unknown"`, no other stamp changed.
  - `test_receipts_survive_reload_and_interleaved_writers`: two `QuotaLedger` instances on one temp file, reversed completion order → both `"settled"`, totals exact.
  - `test_no_usage_refunds_nothing` and `test_usage_above_estimate_books_extra` (`"over"`).
- [ ] **Step 2:** run → FAIL (old `book` returns None / wrong stamp).
- [ ] **Step 3:** implement the contract above; keep the existing lock order (main limiter lock before sidecar lock) and file format additive (old readers ignore `id` and the settled-id set).
- [ ] **Step 4:** new file + full `tests/test_quota_ledger.py` → PASS.
- [ ] **Step 5 (discriminant):** in a temp copy of the module, restore `stamps[-1]` targeting → `test_refund_targets_original_reservation` RED; record; discard the copy.
- [ ] **Step 6:** commit `fix(quota): settle the exact reservation in its own stamp and period`.

### Task 2: Book after the grant; the booking lives as long as the request

**Files:** Modify `bin/rate_limiter.py`, `bin/la_proxy_hooks.py`, every caller from Phase 0; Test `tests/test_reservation_lifetime.py` (NEW).

- [ ] **Step 1 — failing tests** (fake clock, stub bucket whose `aacquire` blocks on an event):
  - `test_booking_created_only_after_grant`: while `aacquire` is pending, `_CALL_BOOKED` has no entry for the call; after the grant it holds a Receipt; a timeout/denial leaves none.
  - `test_booking_survives_long_queue_with_interleaved_request`: A waits 1900 s (fake), B books and completes meanwhile, A succeeds used=125 → A's own receipt `"settled"` with refund 875.
  - `test_booking_survives_long_stream`: A granted, 2000 s fake stream, success → `"settled"`.
  - `test_failure_callback_removes_only_owned_entry`: A fails, B in flight → A's entry gone, B's intact, nothing refunded for A.
  - `test_bound_prunes_only_dead_entries`: an entry older than `_RECORD_BOUND` is pruned on the next booking; one at `_RECORD_BOUND - 1` survives; `_RECORD_BOUND == 2*(MAX_WAIT_S+STREAM_TIMEOUT_S)` asserted against the real constants.
  - `test_ineligible_estimate_books_zero_receipt`: estimate-ineligible path → zero-token Receipt after the grant, settle is a no-op `"settled"`.
  - `test_fallback_attempt_behaviour_pinned`: a fallback to another provider → each provider's own receipt settles once; if LiteLLM reuses `litellm_call_id` across the fallback, the test documents it and asserts no WRONG-provider refund (this is the boundary of the deferred waypoint; the xfail 8b stays xfail).
  - Parameterize the success/settle tests over BOTH limiter classes; keep the existing no-`_la_`-keys assertion in each.
- [ ] **Step 2:** run → FAIL (booking happens before the grant; 600 s prune).
- [ ] **Step 3:** implement per the Contract; the grant path that hands a queued waiter its grant (another poller grants the winner) must create the receipt for the WINNER's estimate/provider — locate that path at Phase 0 and cover it with one test (`test_winner_gets_its_own_receipt`).
- [ ] **Step 4:** new file + `tests/test_la_proxy_hooks.py tests/test_provider_buckets.py tests/test_quota_wiring.py` → PASS.
- [ ] **Step 5 (discriminants):** temp copy with booking moved back before `aacquire` → `test_booking_created_only_after_grant` RED; temp copy with `_RECORD_BOUND = 600` → the long-queue test RED. Record both; discard copies.
- [ ] **Step 6:** commit `fix(proxy): book after the grant; booking lifetime = request lifetime`. **M1:** push the branch; one independent stateless dispatch review (`python3 bin/free-agent-tool.py`, inputs via `"files"`: the diff + this plan's Contract) asking which Contract clause has no test — verify each finding against the code.

### Task 3: HTTP contract proof + legacy-code warning

**Files:** Modify `tests/test_litellm_contract.py` (extend its existing stub-upstream fixture — do not build a second harness); Modify the proxy startup path found at Phase 0 (warning); Test `tests/test_legacy_limiter_warning.py` (NEW).

- [ ] **Step 1 — failing tests:**
  - `test_tpm_route_settles_exact_receipt_over_http`: through the real LiteLLM `/v1/messages` route with the registered callbacks, a TPM-limited fake provider (e.g. `tpm=8000` in a temp limits file): one non-stream and one streamed completion with known usage; the sidecar ends with exactly booked − refunded per receipt. Fails if no upstream request was captured, a callback never ran, usage was missing, or the case was skipped.
  - `test_two_overlapping_requests_out_of_order_over_http`: A long (delayed stub), B short; B finishes first → sidecar TPM/day exact after advancing the fake clock past the window.
  - `test_no_private_keys_upstream_over_http`: captured upstream payload has no `_la_` key (existing assertion reused on these routes).
  - `test_legacy_limiter_warning`: a proxy process whose LOADED `rate_limiter`/`quota_ledger` lacks the receipt API while the on-disk source has it → one stderr/log line `free-agents: this proxy runs pre-<version> limiter code — restart it to get exact quota receipts`; no warning when they match.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement the warning (compare a module-level `RECEIPTS_API = 1` constant in the loaded module with the on-disk file, read once at startup; never auto-restart). **Step 4:** PASS under the LiteLLM interpreter.
- [ ] **Step 5 (discriminant):** stub upstream that drops `usage` → the over-HTTP settle test fails with a named message (not a silent pass).
- [ ] **Step 6:** commit `test(proxy): exact quota receipts proven over the real LiteLLM route; legacy-code warning`.

### Task 4: Gate, docs, version (stop at push)

- [ ] Full `test_cmd` + `.rtc/pre-flight.sh` gates (respect their interpreters); output to files, read `EXIT:`; failure set compared to the Phase 0 baseline — no new reds; `-rs` skip list read.
- [ ] CHANGELOG (new version entry; 0.63.1 entry untouched): "quota refunds now settle the exact reservation in its own TPM window and UTC day — affects TPM-limited providers (Mistral 40k, Groq 8k); NVIDIA is RPM-only by default and unaffected. Bookings now last as long as the request (slow queues, long streams). Old proxies keep the old accounting until restarted; a startup warning says so."
- [ ] Version on every surface (`bin/rtc-ship.sh --versions-only`), `scripts/build-portable.py` then `--check`.
- [ ] `redact-secret.py --scan-only` over the branch diff; commit; push. **M2** = final whole-branch review (executing-plans) against the Contract and Judgement table.
- [ ] **GATED (separate user go each):** G1 merge, G2 tag+release via `bin/rtc-ship.sh` from the worktree, G3 `claude plugin update free-agents`, G4 restart of NAMED proxies only (exact ports/pids listed and approved), G5 dogfood: one real short generation on a Mistral or Groq route, sidecar receipt settled.

## Side effects

| Step | Side effect | Routine route |
|---|---|---|
| ledger writes | shared limiter sidecar used by every live proxy | temp state dir in all tests; live only after G4 |
| proxy restart (G4) | interrupts the sessions on that port | never in this plan without a named, approved list |
| HTTP contract test | starts a local LiteLLM on a scratch loopback port | scratch port outside 4141–4151/8000–8010, stub upstream |

## Adversarial review

- Bump `_RECORD_TTL` to 4200 and call it fixed → booking still made before the grant, false receipts on timeout → `test_booking_created_only_after_grant`.
- Settle by "newest stamp with enough tokens" heuristics → wrong recipient under interleaving → receipt ids + `test_refund_targets_original_reservation`.
- Refund after midnight from today's counter → negative/under-counted new day → `test_settle_keeps_original_utc_period`.
- Old proxy wipes ids → new code refunds a random stamp → policy U + `test_unknown_receipt_policy_u`.
- Treat missing usage as 0 → full refund of real spend → `"no-usage"` + test.
- Unit tests seed `_CALL_BOOKED` by hand → green while the real callback order differs → Task 3 over-HTTP tests that fail when no request/callback was observed.
- One limiter class updated, the other not → parameterized tests over both.
- A pipeline hides the gate's exit → redirect to a file and read `EXIT:`.
- Restart "all proxies" to deploy → kills live sessions → rule 5, G4 lists exact ports.

## Final delivery questions

1. Which Contract clauses and Judgement rows are implemented and proven by a named test? 2. Which receipts ran (EXIT codes, counts), which were skipped? 3. Canonical checkout and other worktrees untouched? 4. Stopped at the push boundary; which of G1–G5 remain? 5. Close with `lasting-plans complete "~/.claude/plans/2026-11-24-free-agents-quota-reservation-accounting.md"` only when G1–G5 are done; until then **Status:** `in-progress`.
