# NVIDIA rate-limiter fairness — design

- Date: 2026-11-19
- Status: brainstorm design, user-approved; builder check pending
- Repo: free-agents (worktree `.wt/rl-fairness`, branch `docs/rl-fairness-design`)
- Scope: `bin/rate_limiter.py`, `bin/la_proxy_hooks.py` (call site only),
  `bin/session_menu_state.py` (validators), `bin/session_picker.py` (rate-limiter
  submenu), launchers (`launch-claude-agent.sh`, `remote-session.sh`), tests, docs.

## 1. Intent and why

The machine-wide NVIDIA rate limiter (40 RPM free tier, one shared API key) is
grant-agnostic: a slot goes to whichever process happens to call `try_acquire()`
when capacity frees. Two measured-in-code consequences:

1. **Barging.** Waiting is invisible — the state file records only successful
   stamps (`rate_limiter.py:544-561`). A fresh arrival checks immediately
   (`la_proxy_hooks.py:415`) and captures capacity a sleeping waiter never sees.
2. **Lottery.** When saturated, all waiters compute the same `need` (time until
   the oldest stamp expires), wake in lockstep, and race the `flock`. Fast
   sessions (high turn cadence) enter far more races than slow-model sessions
   (one request per turn), so throughput share ∝ arrival rate.

Net effect: sessions on slower models stall for many turns or hours because
their rare requests keep landing in full windows. The existing priority lane
(0.50.1) is a **capacity partition** (`LA_LIMITER_RESERVE` fraction held idle,
`rate_limiter.py:599`) — the overcorrection this design replaces — and is wired
only into test fixtures (`tests/conftest.py:112`).

## 2. Goals / non-goals / anti-goals

**Goals**
- Grant by policy, not by polling luck: make waiting visible, rank waiters.
- Default policy always on: FIFO + aging tiers.
- Deficit ranker built and opt-in (candidate for a later default promotion).
- Manual priority lanes on demand: queue-jump + bounded share, reachable from
  the picker rate-limiter screen and a launcher flag.
- Zero idle capacity: scheduling only reorders demand, never withholds supply.
- Full settings integration: every threshold settable (env > saved picker
  setting > default), validated, editable in the csl rate-limiter submenu.
- Backward compatible with concurrently running old-code sessions; kill-switch.

**Non-goals**
- Non-NVIDIA providers (the limiter is NVIDIA's alone; `la_proxy_hooks.py:390`).
- Changing RPM accounting, 429 classification, or backoff behaviour.
- A daemon architecture; cross-machine coordination; new dependencies.

**Anti-goals (failure even if it works)**
- Any reserved/idle capacity ("just in case" slots).
- Hardcoded thresholds that the user cannot set in the submenu.
- Cutting off or degrading already-running sessions at install time.
- A priority stream crowding normal traffic out entirely (the reserve's
  protective intent must survive in cap form).

## 3. Current mechanism (verified)

- File-backed state `~/.claude/free-agents/.nvidia_throttle_state`, every
  read-modify-write under `fcntl.flock` (`rate_limiter.py:533-537`).
- Two modes: `SlidingWindowLimiter` (default) and `SmoothTokenBucket`
  (opt-in via `mode`), sharing 429/backoff helpers (`_cool_model`).
- Stamps carry owner ids since 0.50.1 (`{"t", "o"}`); per-owner options
  `{priority, rpm_scale}` live in the state's `owners` map, editable live via
  `rate_limiter.py set-owner`. `rpm_scale` caps an owner's own window count.
- `acquire`/`aacquire` loop: `try_acquire` → sleep(need) → retry, bounded by
  `max_wait` (300 s) → `RateLimitTimeout`.
- Machine-wide 429 cooldown pauses everyone; per-model throttle cools one model.
- Owner id: `LA_LIMITER_OWNER` > session id (`LA_SESSION_IDENTITY`) > `pid-N`.
  No launcher sets these today; lanes are test-fixture-only.

**Live user config (verified 2026-11-19, `session-menu.local.json`):**
`mode=smooth_bucket, bucket_capacity=10, cooldown=10, backoff_multiplier=1.5,
max_cooldown=900, max_retries=10, max_wait=300, rpm=40`. Smooth bucket is the
user's active mode and a first-class citizen here. Note `max_cooldown=900`:
machine-wide pauses up to 15 min are a real case in this configuration.

## 4. Design

### 4.0 Layer 0 — the waiter registry

New top-level key `waiters` in the shared state file:

```json
"waiters": {"<token>": {"owner": "...", "arrived": 1732...,
                         "last_seen": 1732..., "class": "normal|priority",
                         "model": "..."}}
```

- `token` = unique per `acquire()` call (owner + pid + counter), so an owner
  with several concurrent in-flight requests registers each separately.
- `acquire()` registers on its first failed `try_acquire`; every loop iteration
  refreshes `last_seen` (idempotent per token; `arrived` never resets on
  re-register).
- Deregistration on grant, on `RateLimitTimeout`, and in a `finally` guard.
- **Stale-waiter TTL**: entries with `now - last_seen > waiter_ttl_seconds`
  are pruned by any grant evaluation, so a killed proxy cannot hold the queue
  head. A corpse's slot claim lapses; its next live request re-registers.
- **Heartbeat clamp (the cooldown bug)**: today `aacquire` sleeps the full
  `need` — with `max_cooldown=900` that stops heartbeats for 15 minutes, the
  TTL would drop the waiter, and it would re-queue at the back with a reset
  clock after every storm. Fix: every wait-loop sleep is clamped to
  `min(need, waiter_poll_seconds)`. A few flocks/second per waiter — negligible.
- **No barging**: if the registry holds any other waiter, a fresh arrival
  registers instead of taking an available slot. Empty registry → behaviour
  identical to today (instant take; uncontended latency unchanged).
- Back-compat: no `waiters` key → empty registry → today's semantics.

### 4.1 Grant rule (one shared core for both limiter modes)

Evaluated inside `try_acquire` under the existing flock:

1. Prune stale waiters (TTL).
2. Machine-wide cooldown active → no grants (registrations and heartbeats
   continue; queue order and aging accrue through cooldowns).
3. Eligibility filter: an owner at its `rpm_scale` cap is skipped this round
   (the slot goes to the next eligible waiter; `rpm_scale` semantics unchanged).
4. Rank the eligible waiters by the active **policy** and grant to the winner:
   take the slot/token, remove its waiter entry, update owner stats.
5. The caller that is *not* the winner gets a short poll interval back.

The core is shared by `SlidingWindowLimiter` and `SmoothTokenBucket` (same
pattern as `_cool_model`) so the two modes cannot drift; the capacity check
differs (window count vs tokens ≥ cost), the ranking does not.

**Policies** (`policy` setting):

- `aging` (**default, always on**): rank = `(tier desc, arrived asc)`.
  Tier 0 normal; tier 1 once this request has waited `tier1_seconds`;
  tier 2 at `tier2_seconds`. Self-extinguishing: grant resets to served.
  Fast sessions only ever yield to a request that has already waited
  `tier1_seconds` — i.e. only under genuine saturation.
- `deficit` (**built, opt-in**): rank = `(debt desc, arrived asc)`. Debt is
  computed, not ticked: at grant evaluation each owner's debt =
  stored residual + Σ over its waiting requests of `(now - arrived)`, capped at
  `deficit_cap_seconds`; a grant spends a fixed credit (the inter-slot period,
  ~1.5 s at 40 RPM) and persists the remainder as the owner's residual.
  Handles owners with several concurrent in-flight requests (a session
  dispatching children through one proxy), which per-request aging cannot see.
- `legacy` (**kill-switch**): today's semantics — no registry enforcement, any
  capable caller wins. Hot-appliable to running new-code proxies (§4.5).

### 4.2 Manual priority lanes — queue-jump + bounded share

`class` comes from the owner's `priority` flag (state map via `set-owner`, or
`LA_LIMITER_PRIORITY=1` env at registration).

- **Jump**: a priority-class waiter outranks all normal waiters at equal tier.
- **Share cap (the reserve, repurposed)**: while ≥ 1 normal waiter is
  registered, a priority grant is allowed only if the priority class holds
  fewer than `max(1, round(lane_share_cap × limit))` grants in the rolling
  60 s window. Cap reached → the slot goes to the best normal waiter; the
  window self-corrects as priority stamps age out. With **no** normal waiter
  registered, priority uses the whole window — idle capacity never occurs.
  The old reserve-as-partition branches are retired; `LA_LIMITER_RESERVE`
  keeps its name (compat) and gains a saved-setting + submenu row.
- Cap accounting: sliding mode derives it from existing stamps (owner's class
  in the `owners` map at grant time — no new state); smooth mode keeps a small
  additive `grants` rolling list (`[{"t","o","c"}]`, new top-level key,
  self-healing like `waiters`).
- The cap applies universally — launcher-flagged, picker-flagged, and test
  fixtures alike — so test priority streams cannot crowd out everything else.

**Approval gate** (`lane_approval` setting, default `ask`): applies to
agent-initiated lanes. `rate_limiter.py set-owner --priority` in `ask` mode:
interactive TTY → confirm prompt; non-TTY (an agent calling it) → exit
non-zero with instructions unless `--user-approved` is passed, which records
that the agent asked the user first. `agent` mode skips the gate. Picker
clicks and launcher flags are the user acting — no gate.

### 4.3 Mode integration (first-class, verified against live config)

- **Sliding window**: capacity = window count; grant when
  `len(stamps) < normal_limit` and owner cap not hit (unchanged checks, plus
  ranking). Freed capacity appears at stamp expiries; ≤2 s polling never
  misses it.
- **Smooth bucket** (user's mode, capacity 10): tokens accumulate, they do not
  expire — a ≤2 s poll loop captures every token a barger could have taken.
  Grant when `tokens ≥ cost` and `tokens - cost ≥ 0` (the token-reserve check
  is retired with the partition). Uncontended bursts (capacity 10) unchanged.
- `rpm_scale` token costs (`1/s` per acquisition) unchanged in both modes.

### 4.4 Settings — nothing hardcoded, no placeholders

Precedence unchanged: explicit arg > `LA_NVIDIA_*` env > saved picker setting
(`session-menu.local.json`, validated by `session_menu_state._validate_rate_limiter`)
> default. The eight existing settings are untouched. New settings — every one
read by the limiter via `_setting()`, validated (type + range) in
`_validate_rate_limiter`, editable in the csl rate-limiter submenu, covered in
`test_session_menu_state`:

| Setting | Env var | Default | Range / values | Purpose |
|---|---|---|---|---|
| `policy` | `LA_NVIDIA_POLICY` | `aging` | `aging\|deficit\|legacy` | grant policy / kill-switch |
| `tier1_seconds` | `LA_NVIDIA_TIER1_SECONDS` | 45 | 5–600 | aging tier 1 threshold |
| `tier2_seconds` | `LA_NVIDIA_TIER2_SECONDS` | 150 | 10–3600 | aging tier 2 threshold |
| `waiter_ttl_seconds` | `LA_NVIDIA_WAITER_TTL` | 15 | 5–120 | stale-waiter prune |
| `waiter_poll_seconds` | `LA_NVIDIA_WAITER_POLL` | 2 | 0.5–10 | heartbeat/poll clamp |
| `deficit_rate` | `LA_NVIDIA_DEFICIT_RATE` | 1.0 | 0.1–10 | debt accrual multiplier |
| `deficit_cap_seconds` | `LA_NVIDIA_DEFICIT_CAP_SECONDS` | 600 | 30–7200 | debt ceiling |
| `lane_share_cap` | `LA_LIMITER_RESERVE` (name kept) | 0.2 | 0.05–0.9 | priority share cap under contention |
| `lane_approval` | `LA_LIMITER_LANE_APPROVAL` | `ask` | `ask\|agent` | agent-initiated lane gate |

**Hot reload**: `policy` and the tier/TTL/poll/deficit knobs are read per
acquire attempt through a mtime-cached settings read (~1 s cache), so flipping
`policy: legacy` or a threshold in the submenu takes effect on running
new-code proxies without restart (today's settings are read once at
`__init__`; the cache keeps the per-call cost to one stat()).

### 4.5 Backward compatibility and rollout

The state file is shared by all concurrent NVIDIA sessions; the update must
never cut a running session off.

- **Dev and pre-release testing touch nothing live.** Verified: `conftest.py:55`
  forces a temp `LA_NVIDIA_THROTTLE_STATE`, and the 0.54.0 tripwire
  (`conftest.py:225`) fails any test that opens the real file. Tests run
  against fake upstreams — the single shared API key's budget is never spent.
- **Install**: each proxy imports `rate_limiter.py` once at proxy start;
  running processes keep the old module in memory regardless of plugin
  updates. New sessions load new code.
- **Mixed running (old + new code on one file) — the append-only contract**:
  - Schema changes are additive only: new top-level keys (`waiters`, smooth's
    `grants`), new fields inside stamp dicts; every existing key
    (`stamps`, `tokens`, `last_refill`, `cooldown_until`, `models`, `owners`,
    `consecutive_429s`, `last_429_time`) keeps its exact semantics.
  - Old code ignores unknown keys but rewrites the file wholesale on every
    `try_acquire` — it will repeatedly drop `waiters`/`grants`. New code
    re-writes them on every poll (≤2 s), so the registry self-heals within one
    poll interval. Transitional effect: old sessions keep barging (their
    behaviour is unchanged); fairness applies among new-code sessions only.
  - Append-only keeps the JSON always parseable by old code, so its
    corrupt-file → empty-window → oversend path is unreachable.
  - **Mixed-writer contract test**: check out the pre-change module from its
    tag into a temp dir; run old and new limiter processes concurrently
    against one state file; assert old code's window/token accounting stays
    exact, no parse failures, and the new registry survives old-style wipes.
- **Kill-switch**: `policy: legacy` hot-applies (§4.4) — a field misbehaviour
  reverts to today's semantics without touching the plugin install.
- **Dogfood gate**: the ship loop's confirm-live/dogfood step waits for the
  empty-field window (all NVIDIA sessions at their natural end, then fresh
  sessions start on the new code). Reason: measurement validity — old bargers
  would pollute every fairness signal — not install safety.

### 4.6 Observability

- `rate_limiter.py status` / `owners` gain a `waiters` section (queue snapshot:
  who, arrived, tier/debt, class) and per-owner wait/served stats.
- The picker's rate-limiter screen shows the live queue and per-owner
  priority toggle + the new settings rows (validated like the existing eight).
- `probe` extended with this owner's current queue position.

## 5. Constraints

- One shared NVIDIA key → no second live limiter; tests fake the upstream.
- Python stdlib only (fcntl, json, os, time); no new dependencies.
- flock discipline and the existing two-mode factory stay.
- The canonical checkout's branch must not switch; all work happens in this
  worktree (`~/ClaudeWorkspace/.wt/rl-fairness`).

## 6. Explicitly left to the builder

- Exact submenu UI layout and wording for the new rows.
- Launcher flag name (`--priority` proposed) and its env plumbing.
- Test matrix expansion beyond the required list in §4.5/§4.6.
- Internal function decomposition of the shared grant core.
- CHANGELOG/VERSION wording at implementation time (bump per ship rules).

## 7. Open questions resolved in brainstorm

- Grant policy: all of aging (default) + deficit (opt-in) + manual lanes
  (user, 2026-11-19); pure FIFO is the aging policy's base ordering.
- Reserve mechanism: kept, repurposed as the lane share cap so its protective
  intent survives without idle capacity (user: "combine the best of both
  worlds"; tests queue-jump but cannot crowd out everything else).
- Lane wiring: picker screen + launcher flag, both (user, 2026-11-19).
- Approval: agent-initiated lanes require user approval by default,
  flippable to agent-choice (user, 2026-11-19).
