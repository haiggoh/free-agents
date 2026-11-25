# NVIDIA rate-limiter fairness implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in this session (native inline execution is the standing user decision, memory plans-execute-native). No implementer/reviewer subagents: the machine is saturated with concurrent NVIDIA sessions and subagent dispatches on that lane die at the 600 s stream watchdog — the executor runs the QC playbook's effect checks itself (playbook rule 6 fallback). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the machine-wide NVIDIA rate limiter grant slots by policy instead of by polling luck: a visible waiter queue with FIFO + aging tiers always on (deficit ranker built, opt-in), on-demand manual priority lanes bounded by a share cap instead of a capacity partition, dispatch/background requests boosted only within tier 0, every new threshold user-settable in the csl rate-limiter submenu, fully integrated with both limiter modes, install-safe while old-code sessions still run — and zero idle capacity (scheduling reorders demand, never withholds supply).

**Architecture:** One shared grant core in `bin/rate_limiter.py`, evaluated inside `try_acquire()` under the existing `fcntl.flock`: prune stale waiters → machine-wide cooldown blocks grants → per-owner `rpm_scale` eligibility filter → lane share cap → rank by the active policy → book the slot under the WINNER's owner and mark the winner's waiter entry `granted` (the winner acks on its next poll and returns success; the evaluating caller registers itself and gets a short poll interval). Arrivals register whenever the queue holds another waiter (no barging); an empty queue is byte-for-byte today's behavior. New state keys (`waiters`, smooth's `grants`, `deficit`) are append-only so old code keeps parsing and accounting exactly; old code's wholesale rewrites drop the new keys and new code re-writes them on every poll (≤ `waiter_poll_seconds`), so the registry self-heals. `policy: legacy` hot-reloads to today's semantics as the kill-switch.

**Tech Stack:** Python 3 stdlib only (fcntl, json, os, time, asyncio, argparse); tests with pytest + pytest-xdist (`~/.local/bin/pytest`), unittest.mock, multiprocessing; Textual for the picker screen. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-19-nvidia-rate-limiter-fairness-design.md` (user-approved 2026-11-19, committed on this branch). Where plan and spec conflict, the spec wins; ledger the deviation as a `Ruling:` line.

**Status:** completed

> **Lasting Plans:** when every task below is done, set the Status line to `completed` by running `lasting-plans complete "docs/superpowers/plans/2026-11-20-rate-limiter-fairness.md"` as the closing step.

## Global Constraints

1. Python stdlib only; no new dependencies.
2. Tests are hermetic: `tests/conftest.py` forces a temp `LA_NVIDIA_THROTTLE_STATE` and fails any test that opens the real one. Never point any run, script, or CLI call at the live state file.
3. Full-suite runs go parallel (`pytest -n auto`); targeted red-green runs stay serial.
4. State-file schema is append-only: existing keys (`stamps`, `tokens`, `last_refill`, `cooldown_until`, `models`, `owners`, `consecutive_429s`, `last_429_time`) keep their exact semantics; new keys are additive only.
5. Every read-modify-write stays under one exclusive flock; the grant evaluation happens inside `try_acquire`'s existing lock — never a second lock, never lock-free.
6. Zero idle capacity (anti-goal): never reserve or withhold slots; the lane cap only reorders who gets a free slot.
7. No hardcoded user-facing threshold: each new knob resolves explicit arg > `LA_NVIDIA_*`/`LA_LIMITER_*` env > saved picker setting > default, is validated in `session_menu_state._validate_rate_limiter`, and is editable in the csl rate-limiter submenu. No placeholder settings.
8. Both limiter modes are first-class — the user's live mode is `smooth_bucket` with `bucket_capacity` 10, `max_cooldown` 900.
9. Backward compatible with concurrently running old-code sessions; `policy: legacy` is the hot kill-switch; new settings hot-reload via an mtime-cached saved-settings read.
10. Dispatch/background requests boost only within tier 0 (never above aged tiers or manual lanes), via `LA_LIMITER_KIND=dispatch` set by the dispatch entry points.
11. Agent-initiated priority lanes require user approval by default (`lane_approval=ask`: non-TTY `set-owner --priority` exits non-zero unless `--user-approved`; picker/launcher are the user acting and pass no gate).
12. All work happens in this worktree on branch `docs/rl-fairness-design` (base `6a7cfd7`); the canonical checkout stays on `main`, untouched. Stage by path, never `git add -A`.
13. Every commit ends with both trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Co-Authored-By: GLM 5.3 <noreply@z.ai>`. Release notes carry no 🤖 trailer.
14. Write home paths as `~/...`; JSON files get no comments and are validated with `json.load` after every edit.
15. Every script/flag gets `--help` verified by RUNNING it, never by grepping.
16. Ship depth authorized (user, 2026-11-20): FULL — gates → merge to default branch → tag → GitHub release → get-haiggoh/plugin refresh → confirm the update is visible. Dogfood/confirm-live against real NVIDIA traffic only in the empty-field window (no live NVIDIA sessions on ports 4141–4151); otherwise record it as deferred with a receipt — that gate is measurement validity, not install safety.
17. PROHIBITED: killing/restarting any process on ports 4141–4151 or 8000–8010; hand-editing the live throttle state, the plugin cache, or plugin registries; switching the canonical checkout's branch; force-push or moving published tags; hand-editing `~/.claude/waypoints.json`; committing secrets; literal expanded home paths in committed content.

## Authority matrix

| Operation | Status |
|---|---|
| Edit + commit in this worktree's branch | ALLOWED NOW |
| Run hermetic tests; run `bin/rate_limiter.py` against temp state files | ALLOWED NOW |
| Read-only inspection of the canonical checkout and live config | ALLOWED NOW |
| Push branch, merge to default branch, tag, GitHub release, get-haiggoh refresh, confirm visible | ALLOWED after the ship gate in Final delivery (user authorized "Full ship") |
| Dogfood/confirm-live against real NVIDIA traffic | GATED on the empty-field window; else deferred with a receipt |
| Anything beyond the above (new publish scope, force-push, tag moves, killing proxies, editing plugin cache) | NEEDS SEPARATE USER PERMISSION — prohibited by default |

## Phase 0 — forced preflight (read-only; nothing mutates before this passes)

Strategy: Tasks 1–5 build the queue, policies, and lanes in `bin/rate_limiter.py` against hermetic tests; Tasks 6–8 make every knob real in settings/picker/launchers; Tasks 9–10 prove mixed old/new safety and run the QC receipts. Every task is TDD with a watched RED plus a known-bad mutation that must go RED before green counts. The run ends at the authorized full ship from this worktree, dogfood gated on the empty-field window.

- [ ] Workspace: worktree clean, branch `docs/rl-fairness-design`, BASE = `git merge-base HEAD main` recorded (`6a7cfd7`); canonical checkout clean and on `main`.
- [ ] Ledger: run the executing-plans `sdd-workspace` script for this plan → `<repo>/.superpowers/sdd/2026-11-20-rate-limiter-fairness/progress.md`, first line `# SDD ledger — plan: docs/superpowers/plans/2026-11-20-rate-limiter-fairness.md`.
- [ ] Baseline: `~/.local/bin/pytest tests/test_rate_limiter.py tests/test_session_menu_state.py tests/test_session_picker_model.py -q` → Expected: all pass; record N.
- [ ] Old module for the mixed-writer test: `git show 6a7cfd7:bin/rate_limiter.py` — record sha256.
- [ ] Ship surfaces: read `rtc-ship.json`; resolve the installed run-to-completion plugin's ship loop (`bin/rtc-ship.sh` under `~/.claude/plugins/cache/haiggoh/run-to-completion/<version>/` — this repo ships no in-repo script, rule plain-repo-rtc-ship.md) and run its `--help` / `--versions-only` from the worktree root; record the current version and every version surface.
- [ ] Tooling: `~/.local/bin/pytest` present; `python3 --version`; sdd-workspace script path resolved.
- **Receipt:** ledger line `Phase 0: complete — baseline N/N green at BASE 6a7cfd7, old-module sha256 …, ship surfaces …` with commands and exit codes.

## Review Focus

1. **900 s machine-wide cooldown with waiting sessions** (user's live `max_cooldown`): wait-loop sleeps must be clamped to `min(need, waiter_poll_seconds)` or the TTL prunes a live waiter mid-cooldown and it loses queue position. Pinned by a Task 1 test: cooldown active 900 s, waiter heartbeats keep it alive past the TTL, it still wins when the cooldown ends.
2. **Old code running concurrently on the same state file**: its wholesale rewrites drop `waiters`; new code must re-write them within one poll while old window accounting stays exact. Pinned by the Task 9 mixed-writer contract test.
3. **Priority lane under contention**: never idles capacity (no normal waiters → priority takes the whole window) and never crowds normal traffic out (cap binds while ≥1 normal waiter). Pinned by Task 3 tests, including a mutation that removes the cap.
4. **Empty queue = today's behavior**: tokenless `try_acquire` callers see identical semantics (the untouched existing suite is the parity proof); fast sessions are never slowed by the registry existing.
5. **Non-TTY agent lane request**: `set-owner --priority` without `--user-approved` exits non-zero with instructions. Pinned by a Task 3 subprocess test with stdin not a TTY.

## Task 1 — waiter registry + grant core (sliding window) + acquire protocol

**Files:**
- Modify: `bin/rate_limiter.py` (state read/write, `try_acquire`, `acquire`/`aacquire`, helpers)
- Test: `tests/test_rate_limiter.py` — new class `WaiterQueueTests`

**Interfaces (produced; consumed by Tasks 2–5, 9):**
- `_ENV_FOR` extended with the 10 new keys (see Task 6 table); `_saved_settings_cached()` (mtime-keyed cache over `_saved_settings()`); `_hot(key, default)` = env > cached saved > default; `_cfg()` = all hot knobs snapshot.
- `_waiter_token(owner)` → `"<owner>:<pid>:<n>"` (itertools counter); `_kind()` → `LA_LIMITER_KIND` ∈ {interactive, dispatch, background}, default interactive.
- `SlidingWindowLimiter._read` → 6-tuple `(stamps, cooldown_until, consecutive_429s, last_429_time, models, waiters)`; `_write` gains `waiters=None, grants=None, deficit=None` (None = preserve current). Update the two unpack sites (`set_owner`, `owners_info`).
- `try_acquire(priority=None, token=None, kind=None, model=None)` — `token=None` is the legacy peek: exactly today's take-if-free semantics, registry untouched. With a token: full queue protocol below.
- `acquire`/`aacquire(max_wait=None, priority=None, kind=None, model=None)` — create one token per call, pass it every iteration, sleep `min(need, waiter_poll_seconds)`, deregister in a `finally` (locked `_release(token)`).
- Grant protocol (under the one lock, inside `try_acquire`): prune stale (`now - last_seen > waiter_ttl_seconds`; `granted` entries exempt until `granted` + 2×ttl); cooldown active → no grant, return remaining; registry = waiters minus granted entries minus caller; if policy != legacy and registry non-empty and capacity free → pick winner by policy rank (Task 1: FIFO by `arrived`), book the stamp as `{"t": now, "o": winner_owner}`, set `waiters[winner]["granted"] = now`; if winner owner == caller owner, the caller takes the slot itself (remove own entry, return 0.0); else caller registers/heartbeats and returns poll seconds. If registry empty (or legacy): capacity free → take slot (today's path, stamp with own owner); else register/heartbeat, return need. A caller whose own entry is `granted` removes it and returns 0.0 (the slot was booked for it).
- Register = `waiters[token] = {"owner", "arrived", "last_seen", "class", "kind", "model"}`; re-register preserves `arrived`, refreshes `last_seen`.

**Steps:**
- [ ] Write the failing tests in `WaiterQueueTests` (frozen clock, temp state, two owners via `LA_LIMITER_OWNER` patching): `test_no_barging_fresh_arrival_registers_when_queue_non_empty`, `test_grant_books_stamp_under_winner_owner_and_winner_acks`, `test_stale_waiter_ttl_prune`, `test_heartbeat_clamp_survives_900s_cooldown`, `test_timeout_deregisters_waiter`, `test_legacy_policy_is_tokenless_parity`, `test_state_without_waiters_key_loads`, `test_own_owner_winner_takes_slot_for_caller`. Run: `~/.local/bin/pytest tests/test_rate_limiter.py::WaiterQueueTests -q` → Expected: RED (AttributeError/behavior missing).
- [ ] Implement. Run → Expected: GREEN; then `~/.local/bin/pytest tests/test_rate_limiter.py -q` → existing classes still pass unchanged (parity proof for Review Focus #4).
- [ ] Discriminants (each a temp edit, run, must go RED, restore): (a) remove the no-barging branch → `test_no_barging…` RED; (b) drop TTL prune → `test_stale_waiter_ttl_prune` RED; (c) drop `last_seen` refresh → `test_heartbeat_clamp…` RED; (d) drop granted-ack → `test_grant_books…` RED. Record each RED line in the ledger.
- [ ] Run `pytest -rs tests/test_rate_limiter.py -q` → 0 unexpected skips.
- [ ] Commit (stage by path: `bin/rate_limiter.py tests/test_rate_limiter.py`).
- **Receipt:** ledger line with the task's green run, the four mutation RED lines, and the `-rs` line.

## Task 2 — aging tiers + dispatch boost + rpm_scale eligibility skip

**Files:** Modify `bin/rate_limiter.py`; Test `tests/test_rate_limiter.py` (extend `WaiterQueueTests`).

**Interfaces:** `_tier(arrived, now, t1, t2)` → 0/1/2; `_rank(w, now, cfg, owners)` = `(tier, boost, arrived)` where `boost` = 0 iff tier==0 and `dispatch_boost` on and kind ∈ {dispatch, background}, else 1 (Task 3 prepends the lane class); `_pick_winner(waiters, now, cfg, owners, own_counts, limit, prio_grants)` skipping owners at their `own_limit`.

**Steps:**
- [ ] Failing tests: `test_tier1_aged_waiter_outranks_fresher_arrival`, `test_dispatch_boost_within_tier0_only` (a dispatch waiter beats an earlier interactive in tier 0, but loses to any tier-1+ interactive), `test_rpm_scale_owner_skipped_when_at_cap`. Run → RED.
- [ ] Implement. Run → GREEN; full `test_rate_limiter.py` GREEN.
- [ ] Discriminants: (a) tiers always 0 → aging test RED; (b) boost applied in every tier → dispatch test RED; (c) eligibility filter removed → scale test RED.
- [ ] `pytest -rs` line; commit.
- **Receipt:** as Task 1.

## Task 3 — manual priority lanes: queue-jump + share cap; retire the reserve partition; approval gate

**Files:** Modify `bin/rate_limiter.py` (`try_acquire`, `set_owner`, `main`), `tests/conftest.py` (comment only), `tests/test_rate_limiter.py` (rewrite `OwnerAttributionPriorityTests`), `rtc-ship.json` (`_comment_limiter_knobs` wording — validate JSON after edit).

**Interfaces:** rank gains class first: `(class_rank, tier, boost, arrived)`, `class_rank` 0 for priority owners; `_lane_cap(limit, share_cap)` = `max(1, round(share_cap*limit))`; `_prio_grants_sliding(stamps, owners)` = in-window stamps whose owner is priority; cap binds only while ≥1 normal waiter is registered; `set_owner(limiter, owner, priority, rpm_scale, kind=None)`; CLI `set-owner --kind {interactive,dispatch,background}` and `--user-approved`; lane approval gate in `main()`.

**Steps:**
- [ ] Failing tests (rewrite the reserve tests): `test_priority_waiter_jumps_normal_queue`, `test_lane_cap_binds_under_contention` (limit 40, cap 0.2 → 8), `test_lane_cap_free_when_no_normal_waiters` (priority takes the whole window — zero idle capacity), `test_reserve_no_longer_partitions_normal` (normal uses full 40 when no waiters), `test_set_owner_kind_and_cli`, `test_lane_approval_refuses_nontty_without_user_approved` (subprocess `main(["set-owner", …, "--priority"])` with stdin not a TTY → rc != 0; with `--user-approved` → rc 0 and owner opts record it). Run → RED.
- [ ] Implement: retire the `normal_limit = limit*(1-reserve)` partition and the `LA_LIMITER_RESERVE` read in `try_acquire`; cap enforced in `_pick_winner` via `_prio_grants_sliding`; approval gate in `main()` (`lane_approval` hot value; TTY → confirm prompt; non-TTY → refuse unless `--user-approved`; `agent` mode skips). Update `--help` epilog. Update the conftest comment (fixture keeps `LA_LIMITER_RESERVE=0.2`+`LA_LIMITER_PRIORITY=1`, now meaning cap semantics).
- [ ] GREEN + full file; discriminants: (a) cap check removed → `test_lane_cap_binds…` RED; (b) gate removed → approval test RED; (c) partition restored → `test_reserve_no_longer_partitions…` RED.
- [ ] `pytest -rs`; `python3 bin/rate_limiter.py --help` run → lists `--kind`, `--user-approved` (rc 0); commit.
- **Receipt:** as Task 1, plus the `--help` run output line.

## Task 4 — deficit policy (opt-in)

**Files:** Modify `bin/rate_limiter.py`; Test `tests/test_rate_limiter.py` (new class `DeficitPolicyTests`).

**Interfaces:** state key `deficit`: `{owner: residual}`; at evaluation, owner debt = residual + Σ over its waiters of `(now - arrived) * deficit_rate`, capped at `deficit_cap_seconds`; rank = `(−debt, arrived)`; on grant, spend credit `WINDOW_SECONDS/limit` and persist the remainder. `LA_NVIDIA_POLICY=deficit` selects it.

**Steps:**
- [ ] Failing tests: `test_owner_with_two_waiters_accrues_debt_faster_and_wins`, `test_debt_capped_at_deficit_cap_seconds`, `test_grant_spends_credit_and_persists_residual`, `test_policy_switch_via_env` (aging → deficit hot). RED → implement → GREEN; full file GREEN.
- [ ] Discriminants: (a) credit not persisted → spend test RED; (b) cap removed → cap test RED.
- [ ] `pytest -rs`; commit. **Receipt:** as Task 1.

## Task 5 — smooth-bucket integration (user's live mode: capacity 10)

**Files:** Modify `bin/rate_limiter.py` (`SmoothTokenBucket._load_state/_save_state/try_acquire/acquire/aacquire/status`); Test `tests/test_rate_limiter.py` (rewrite `test_bucket_reserve_blocks_normal_but_priority_passes`, add bucket queue tests).

**Interfaces:** `grants` rolling list `[{"t","o","c"}]` (new top-level key, 60 s window) for the lane cap; capacity check `tokens >= winner_cost` with the reserve token-check retired; grant books `tokens -= winner_cost` and appends to `grants`; `_load_state/_save_state` carry `waiters`/`grants`/`deficit`; same shared core.

**Steps:**
- [ ] Failing tests: `test_bucket_no_barging_and_grant_books_tokens_for_winner`, `test_bucket_lane_cap_via_grants_list`, `test_bucket_user_scenario_capacity10` (capacity 10, rpm 40, frozen clock: fill, two waiters, poll loop with clamped sleeps, refill advances — winner served, tokens exact), rewrite of the old reserve test to cap semantics. RED → implement → GREEN; full file GREEN.
- [ ] Discriminants: (a) reserve token-check kept → user-scenario RED; (b) grants list dropped → bucket cap test RED; (c) clamp dropped → heartbeat test RED.
- [ ] `pytest -rs`; commit. **Receipt:** as Task 1.

## Task 6 — settings plumbing: env + saved settings + validators + hot reload

New settings (all in `bin/rate_limiter.py._ENV_FOR`, `session_menu_state` validators, defaults):

| key | env | default | range/values |
|---|---|---|---|
| `policy` | `LA_NVIDIA_POLICY` | `aging` | `aging\|deficit\|legacy` |
| `tier1_seconds` | `LA_NVIDIA_TIER1_SECONDS` | 45 | 5–600 |
| `tier2_seconds` | `LA_NVIDIA_TIER2_SECONDS` | 150 | 10–3600 |
| `waiter_ttl_seconds` | `LA_NVIDIA_WAITER_TTL` | 15 | 5–120 |
| `waiter_poll_seconds` | `LA_NVIDIA_WAITER_POLL` | 2 | 0.5–10 |
| `deficit_rate` | `LA_NVIDIA_DEFICIT_RATE` | 1.0 | 0.1–10 |
| `deficit_cap_seconds` | `LA_NVIDIA_DEFICIT_CAP_SECONDS` | 600 | 30–7200 |
| `lane_share_cap` | `LA_LIMITER_RESERVE` (name kept) | 0.2 | 0.05–0.9 |
| `dispatch_boost` | `LA_NVIDIA_DISPATCH_BOOST` | true | bool |
| `lane_approval` | `LA_LIMITER_LANE_APPROVAL` | `ask` | `ask\|agent` |

**Files:** Modify `bin/rate_limiter.py`, `bin/session_menu_state.py`; Test `tests/test_session_menu_state.py`, `tests/test_rate_limiter.py` (hot-reload test).

**Steps:**
- [ ] Failing tests in `test_session_menu_state.py`: round-trip save/load each new key; reject unknown key, out-of-range, bad choice, bool-for-numeric; `dispatch_boost` bool only. In `test_rate_limiter.py`: `test_hot_settings_reload_on_config_mtime` (write config tier1=45 via `LA_SESSION_MENU_CONFIG_DIR` temp, evaluate `_cfg()`, rewrite file tier1=5 with bumped mtime, `_cfg()` reflects it) and `test_env_beats_saved_for_new_keys`. RED → implement (`_RL_NUMERIC` extensions, new `_RL_CHOICES`, `_RL_BOOL`, `_validate_rate_limiter` branches, `_saved_settings_cached` mtime cache, `_hot`/`_cfg`) → GREEN.
- [ ] Discriminants: (a) mtime cache never invalidates → hot-reload test RED; (b) validator accepts unknown key → reject test RED.
- [ ] Full `pytest -n auto` on the three test files; `pytest -rs`; validate the user's live config still loads: `python3 -c` load via `session_menu_state.load()` against a COPY (never write the real file) → schema_version 10 with the old 8 keys parses unchanged. Commit. **Receipt:** as Task 1.

## Task 7 — picker submenu rows + queue view + per-owner lane toggle

**Files:** Modify `bin/session_picker_model.py` (`RateLimiterScreen` ~line 2649, the rl-status/rl-reset tool handlers, the `n` nav action ~line 1096); Test `tests/test_session_picker_model.py`.

**Steps:**
- [ ] Failing reachability test: from the DEFAULT nav, press `n` → `rate_limiter` screen; assert one Action per new setting exists; cycle `policy` through all three choices and assert `on_save` received `{"policy": …}` each time (persisted through `save_rate_limiter`); assert the live-status action shows `waiters` once `status()` includes it. RED → implement rows (steps/labels per key, `dispatch_boost` on/off, `lane_approval` ask/agent, lane toggle action `tool:rl-lane` calling `set-owner` for the screen's owner with `--user-approved` since the click IS the user) → GREEN.
- [ ] Discriminants: (a) a row missing from `actions()` → reachability test RED; (b) on_save not called → persistence assertion RED.
- [ ] `pytest tests/test_session_picker_model.py -q` GREEN + `-rs`; commit. **Receipt:** as Task 1.

## Task 8 — launcher + dispatch wiring (LA_LIMITER_KIND / --priority)

**Files:** Create `bin/rl-lane-env.sh` (tiny sourced helper, `--help` verified by running: `--priority` prints `export LA_LIMITER_PRIORITY=1`; `--kind K` prints `export LA_LIMITER_KIND=K`); Modify `launch-claude-agent.sh`, `remote-session.sh` (new `--priority` flag → source the helper into the proxy env), `bin/free-agent-tool.py`, `bin/remote-agent-dispatch.py` (`os.environ.setdefault("LA_LIMITER_KIND", "dispatch")` in a `_limiter_kind_env()` called at the top of `main()`); Test: new `tests/test_lane_wiring.py`.

**Steps:**
- [ ] Failing tests: run `bin/rl-lane-env.sh --priority` (subprocess) → stdout contains `LA_LIMITER_PRIORITY=1`; `--kind dispatch` → `LA_LIMITER_KIND=dispatch`; `--help` rc 0; unknown flag rc != 0 with usage. Load each dispatch tool via importlib, call `_limiter_kind_env()`, assert `os.environ["LA_LIMITER_KIND"] == "dispatch"`. `bash -n` both launchers. `remote-session.sh --help` run → lists `--priority`. RED → implement → GREEN.
- [ ] Discriminants: (a) helper ignores `--priority` → output assertion RED; (b) dispatch tool never calls `_limiter_kind_env()` → env assertion RED.
- [ ] `pytest -rs` on the new file + the picker/limiter files; commit. **Receipt:** as Task 1, with the `--help` runs quoted.

## Task 9 — mixed-writer contract test + observability + module docs

**Files:** Create `tests/test_rate_limiter_mixed.py`; Modify `bin/rate_limiter.py` (`status()`/`owners_info()` gain `waiters`, `probe` gains queue position, docstring + argparse epilog).

**Steps:**
- [ ] Failing contract test: extract the old module with `git show 6a7cfd7:bin/rate_limiter.py` into a temp dir, importlib-load it as `old_rl`; spawn mp processes — 2 old-code workers (tokenless `try_acquire`, frozen clock, rpm=4) and 2 new-code workers (`acquire` with tokens, `max_wait` small) against ONE temp state file; assert: (a) final `in_window` ≤ limit (old invariant exact under mixed writers), (b) the file is valid JSON throughout, (c) after an old-code wholesale write drops `waiters`, one new-code touch re-writes the key (self-heal ≤ one poll), (d) no worker crashed. RED → implement load/save of new keys → GREEN.
- [ ] Observability tests + runs: `status()` includes `waiters` (owner, arrived, tier, class, kind); `owners` includes per-owner waiter counts; `probe` includes this owner's queue position. Run for real against a temp state: `LA_NVIDIA_THROTTLE_STATE=$(mktemp) python3 bin/rate_limiter.py --help`, `status`, `owners`, `probe`, `set-owner … --kind dispatch` → rc 0, JSON out; quote outputs in the receipt.
- [ ] Module docstring: fairness section + the new env vars. Discriminant: (a) new code drops `waiters` on write → self-heal assertion RED.
- [ ] Full `pytest -n auto`; `pytest -rs`; commit. **Receipt:** milestone M5 pre-check.

## Task 10 — QC milestones, self-review, ship

- [ ] Full suite: `~/.local/bin/pytest -n auto` → GREEN (record N); `pytest -rs` → 0 unexpected skips.
- [ ] QC milestones (playbook, adapted inline — lanes saturated, so the executor runs the dispatch brief's questions itself and records the verdict table in the ledger; resume a real /offload dispatch only if lanes recover):
  - **M1 after Tasks 1–2** — queue core + aging: requirements = no-barging, empty-queue parity, TTL prune, heartbeat clamp, tier ranking, tier-0-only dispatch boost, rpm_scale skip. Verdict table per requirement (PROVEN/WEAK/UNPROVEN) + each mutation's RED line + `pytest -rs` line.
  - **M2 after Task 3** — lanes + cap + approval: jump, cap-under-contention, zero-idle-capacity, non-TTY refusal. Mutations + RED lines.
  - **M3 after Task 5** — smooth bucket: capacity-10 user scenario, cap via `grants`, token accounting exact. Mutations + RED lines.
  - **M4 after Tasks 6–8** — settings/picker/launchers: validator round-trips, hot reload, default-nav reachability, `--help` runs, env wiring. Mutations + RED lines.
  - **M5 after Task 9, before ship** — mixed-writer contract, observability CLI runs, full suite. Mutations + RED lines.
- [ ] Final whole-branch review: build the review package (`review-package` script, MERGE_BASE `6a7cfd7`); subagents are dead under saturation, so per executing-plans' fallback the executor reads `code-reviewer.md` and reviews the package itself; ledger `Final review: self-review (no subagent tool)`; Critical/Important findings get ONE fix pass, each fix RED→GREEN + green suite; minors ledgered as deferred.
- [ ] Ship (authorized FULL depth, FROM THIS WORKTREE — never the canonical checkout): run the run-to-completion plugin's ship loop (its `bin/rtc-ship.sh`, resolved under `~/.claude/plugins/cache/haiggoh/run-to-completion/<version>/`) from this worktree root; verify every version surface agrees (VERSION / CHANGELOG.md top entry / manifest per `rtc-ship.json`) and add the CHANGELOG entry for the change; merge to default branch; tag; GitHub release; get-haiggoh/plugin refresh; confirm the update is VISIBLE (`get-haiggoh plan` or `hooks/check-tag-visibility.py`) — if not visible, stop and correct before continuing.
- [ ] Dogfood/confirm-live: check for live NVIDIA sessions (listeners on ports 4141–4151). Empty-field window → exercise the installed copy against real traffic and record the receipt. Sessions live → record `dogfood: deferred — empty-field window not available` in the ledger; do NOT touch those processes.
- [ ] Flip `**Status:** completed` in this file in the last commit before the ship (so the merged plan on the default branch already carries it); after the ship, run `lasting-plans complete "docs/superpowers/plans/2026-11-20-rate-limiter-fairness.md"` as the closing step.
- **Receipt:** ship verdict lines (tag, release URL, visibility check output), milestone receipts M1–M5, all `Ruling:` lines collected into the final message.

## Adversarial review (shortcut → false success → harm → preventive rule → detecting receipt)

| Shortcut | False success | Harm | Rule | Detector |
|---|---|---|---|---|
| Edit the plugin cache instead of the repo | update overwrites it silently | change never ships | edit source repo only | ship visibility check |
| Work on the canonical checkout's branch | launchers resolve through it | user's live tooling changes | worktree only, canonical stays on `main` | Phase 0 canonical-clean check |
| "Green suite" without watching RED | test never fired | requirement unmet | TDD red-green per task + mutation RED | ledger mutation lines |
| Registry read but never enforced (barging kept) | fairness tests pass via ordering luck | starvation remains | no-barging branch + its mutation | M1 mutation (a) |
| Cap that idles capacity when priority alone waits | cap applied unconditionally | anti-goal: reserved idle slots | cap binds only with ≥1 normal waiter | M2 zero-idle test |
| Sleep the full `need` in the wait loop | tests with small cooldowns pass | 900 s cooldown pauses heartbeats, TTL drops the waiter | clamp to `waiter_poll_seconds` | M1 heartbeat mutation (c) |
| Old-code wipe assumed harmless | new keys lost | queue invisible to new code | self-heal on every poll + contract test | M5 self-heal assertion |
| Approval gate only on the CLI path | picker path bypasses | agent grants lanes unapproved | gate in `set-owner` writer, picker passes `--user-approved` as the user | M2 subprocess refusal test |
| Picker rows added but unreachable from default nav | model-level tests pass | user can't set thresholds | reachability test from `n` nav | M4 reachability test |
| Version bumped in one surface only | tag pushed, installer reads HEAD manifest | update invisible | verify all surfaces + tag-visibility check after push | ship gate receipt |
| Secrets in receipts/ledger | — | key leak | no live-state reads, no argv dumps | audit scan at wrap |

## Final delivery

- Implemented vs planned: every task above with its ledger completion line; anything skipped is stated here, not implied.
- Receipts that ran: Phase 0 baseline, per-task green runs, all mutation RED lines, `pytest -rs` lines, M1–M5 tables, ship verdicts, dogfood receipt or deferral.
- User work preserved: canonical checkout verified clean on `main`; no live state touched; no process on 4141–4151/8000–8010 touched.
- Authority boundary: stopped at full ship as authorized; dogfood gated on the empty-field window; nothing prohibited was done.
- Blockers: none unless stated; deferred items named.

lasting-plans complete "docs/superpowers/plans/2026-11-20-rate-limiter-fairness.md"
