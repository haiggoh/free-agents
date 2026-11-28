# Proxy port band: wider band, one shared definition, queue-and-wait (free-agents 0.67.0)

**Status:** draft v2 (user preference round 2026-11-28 09:4x–10:2x folded in), awaiting user approval · waypoint `proxy-port-band-4141-4151` · train plan `~/.claude/plans/2026-11-28-free-agents-release-train.md` (row 0.67.0)

## Intent and why

Every free remote session gets its own LiteLLM proxy on a port in the band `LA_REMOTE_PROXY_PORT_MIN..MAX` (today 4141–4151, 11 ports). With ~10 concurrent free sessions on the machine the band fills, and `free_port()` (`bin/remote-session.sh`) **fails hard**. Measured 2026-11-26: rtc-ship's v0.63.4 gate failed at 775 s on `test_last_effort_0493.py`'s real-launch test with "proxy band 4141-4151 full", all ports held by other sessions' live proxies. Each time this happens, a launch or a ship gate is lost for no reason other than port count.

Success: a launch never fails just because the band is busy. It either gets a port at once (the wider band makes waiting rare), or waits — visibly, and visibly to the session that launched it — until a session ends and frees one.

## Goals

1. Band widened to **4141–4190** (50 ports).
2. **One definition** of the band that every consumer reads, so the launcher and the safety logic can never disagree again.
3. **Queue-and-wait** when the band is full: wait for a port for as long as the launching process lives (12 h backstop), visibly in the launcher's own output AND to the parent session through its verify tools.
4. cost-tracker's statusline keeps recognising free sessions on every band port.

## Non-goals

- Fair (FIFO) ordering between waiters (see Approaches; recorded as a `shortcut:`).
- A port allocator daemon or registry service.
- Changing the LOCAL band (8000–8010) — different launcher, not exhausted.

## Anti-goals (a failure even if it "works")

- Touching any live proxy: never stop, restart, reclaim or probe-kill a port another session holds. A port stays held while its owning session lives, even if its proxy died (today's rule, kept).
- A consumer that still hard-codes 4141–4151 after the change: a proxy on 4152+ that `la-evict.sh` does not recognise as a proxy could be **evicted** — the dangerous drift this design exists to remove.
- A silent wait: a launch that blocks without saying why looks identical to the 775 s hang.
- A silent fallback on a bad override (`LA_PORT_QUEUE_WAIT_S=abc` must abort naming it).

## Constraints

- Portability (user rule): no hard dependency between free-agents and cost-tracker; neither may require the other installed.
- macOS `/bin/bash` 3.2 compatibility for shipped shell.
- The existing atomic allocation lock (`_alloc_lock`, mkdir + holder record, `LA_PORT_LOCK_WAIT_S`) and the "who holds what" report (`_band_full_report`) are reused, not replaced.

## Current state (verified on main e362c76)

- Band source: `config/remote-agents.sh:149-150` (`LA_REMOTE_PROXY_PORT_MIN/MAX`, defaults 4141/4151) — configurable, but only the launcher reads it.
- Hard-coded consumers: `bin/la-evict.sh:72-73` (`PROXY_LO/HI`, eviction protection), `bin/la-session-identity.sh:174` (`case` on `414[1-9]|415[01]` → `free_api`), `bin/la-telemetry-remote-tokrate.sh:18` (comment + gate), hook primers and skill text (`hooks/guard-own-endpoint.py` PRIMER, `skills/free-session-handover/SKILL.md`, `bin/fa_capture.py` standing rule 7), and **cost-tracker** `bin/statusline-render.sh:305` (`case` on the same pattern).
- `swap_scratch_port()` (`bin/remote-session.sh:637`) uses `MAX+1..MAX+20` — relative, so it follows the band.
- Nothing listens on 4152–4199 today (checked 2026-11-28 02:5x).

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | **Poll inside `free_port()`**: when full, release the alloc lock, print the status line, sleep ~5 s, retake the lock, rescan, until the deadline | ~20 lines in the one function that owns allocation; reuses lock + report. Not FIFO-fair: a later launcher can win a freed port; bounded by the deadline | **chosen by the user** |
| B | FIFO ticket queue in `$RUNDIR/port-queue/` | strict fairness; ~3× the code plus dead-ticket cleanup and a new state dir | rejected (cost; queues are rare at 30 ports) |
| C | Allocator daemon | central and fair; a new process every launch depends on — if it dies, all launches die | rejected |

For the band definition reaching cost-tracker, the user chose **session env + fallback** over a published band file or hand-widened literals.

## Decided design (user decisions, 2026-11-28)

1. **Band 4141–4190** (50 ports; user 10:0x — headroom for 0.68.0 headless + 0.70.0 all-providers) — default of `LA_REMOTE_PROXY_PORT_MIN/MAX` in `config/remote-agents.sh`.
2. **Queue-and-wait, approach A.**
3. **cost-tracker learns the band from the session env**: the launcher exports `LA_REMOTE_PROXY_PORT_MIN/MAX` into the `claude` it starts; the statusline (spawned with that env) classifies `free_api` when `ANTHROPIC_BASE_URL` is localhost/127.0.0.1 on a port `lo ≤ p ≤ hi`, with `lo=${LA_REMOTE_PROXY_PORT_MIN:-4141}`, `hi=${LA_REMOTE_PROXY_PORT_MAX:-4190}`. Still gated on the URL, so a leaked variable alone never relabels a gateway session.
4. **Ship order: cost-tracker first** (its widened fallback also covers today's band, so it is safe alone), then free-agents 0.67.0. The reverse order would show ❓ on every session landing on 4152+.
5. **Wait length (user):** effectively unlimited while it is clearly visible. Implemented as: wait while the LAUNCHING process (the launcher's parent) is alive — an orphaned waiter exits instead of polling forever — with a **12 h backstop** (`LA_PORT_QUEUE_WAIT_S`, default 43200; `0` = today's hard fail).
6. **5-minute parent notice (user):** after `LA_PORT_WAIT_NOTICE_S` (default 300) of waiting, the launcher writes a wait record `$RUNDIR/port-wait-<launcher-pid>.json` (since, held/total, condensed holders incl. kept proxies) and keeps it current; `bin/handover-verify.sh` and launch-claude-session's verify read it and report `WAITING_FOR_PORT since HH:MM (indefinite; backstop 12h)` instead of a false trust-dialog timeout. Works for window, headless and Bash launches. The record is removed when a port is won or the wait ends.
7. **Gates and tests that launch a real proxy (user): wait, capped below the test timeout** — the test environment sets `LA_PORT_QUEUE_WAIT_S=600` (under pytest's 900 s per-test timeout).
8. **Kept proxies (user): never touched; named.** A `LA_REMOTE_KEEP_PROXY=1` proxy whose owner is gone stays held; the status line and the wait record name it with the command to stop it.

## Builder's calls (proposed by me; the user may override)

- Scratch ports stay relative (`MAX+1..MAX+20` → 4191–4210 once widened; nothing listens in 4151–4210, checked 2026-11-28 09:5x) — no new setting.
- `LA_PORT_QUEUE_WAIT_S` and `LA_PORT_WAIT_NOTICE_S`: whole seconds only, anything else aborts naming the variable. Poll interval 5 s (`LA_PORT_POLL_S`, a test knob).
- Status line every ~30 s on stderr: `remote-session: waiting for a LiteLLM port (50/50 in use) — 5m02s; waiting while the launcher lives (backstop 12h); holders: …` (reusing `_band_full_report`'s per-port holder text, condensed).
- At the backstop, or when the launching process is gone: the existing hard-fail message plus the full holder report, exit status unchanged; the wait record is removed.
- free-agents consumers read the band through ONE shell helper sourced from `config/remote-agents.sh` (shell) and one Python helper (for `guard-own-endpoint.py` if it needs the range); prose (primers, skills, standing rules) says "the proxy band (default 4141–4190, `LA_REMOTE_PROXY_PORT_MIN/MAX`)".

## Tests the builder must leave (delivery level where it matters)

- Parallel launches beyond band size: with a 2-port test band and 3 launchers, the third waits, prints the status line, and gets a port when the first exits (barrier-based, like the 0.64.2 parallel-launch test).
- Backstop: a full band with `LA_PORT_QUEUE_WAIT_S=2` fails after ~2 s with the holder report; `LA_PORT_QUEUE_WAIT_S=abc` and `LA_PORT_WAIT_NOTICE_S=x` abort naming them. Production defaults (43200 / 300 / 5) pinned by reading the shipped config, never by waiting in real time.
- Parent notice: with `LA_PORT_WAIT_NOTICE_S=1` the wait record appears after ~1 s, stays current, and disappears when a port is won; `handover-verify.sh --pid <launcher>` prints `WAITING_FOR_PORT` while it exists (delivery level: the real script, not a unit).
- Orphaned waiter: kill the launching parent of a waiting launcher → the waiter exits within one poll and removes its record.
- No consumer literal left: a grep-based test that `414[1-9]|415[01]` and `4141-4151` appear nowhere in `bin/ hooks/ config/ skills/` except the one definition (planted positive first).
- `la-evict.sh` refuses to evict a proxy on a port inside the WIDENED band (e.g. 4180) — mutation-checked.
- `la-session-identity.sh` classifies 4160 as `free_api`; cost-tracker statusline does the same with the env set AND unset (fallback).

## Left to the builder

Exact helper names and file placement; how `_band_full_report` output is condensed for the status line; migration note in CHANGELOG/README for anyone who set `LA_REMOTE_PROXY_PORT_MAX` themselves.

## Builder check (2026-11-28, Nemotron 3 Ultra via free dispatch) — resolutions

No question needed the user; each was answered by the code or settled as a builder's call (marked "call").

1. **`la-session-identity.sh`** runs inside the session, so it reads the exported `LA_REMOTE_PROXY_PORT_MIN/MAX` with the same `4141`/`4190` fallback as cost-tracker (call).
2. **Helper contract** (call): `config/remote-agents.sh` keeps the MIN/MAX defaults and gains `la_in_proxy_band <port>` (exit 0 inside, 1 outside); every free-agents shell consumer sources it instead of carrying literals.
3. **`la-evict.sh` is standalone** (call): it sources `config/remote-agents.sh` itself and protects the UNION of the shipped default band and the configured band — eviction never protects fewer ports than today, even under a misconfigured override.
4. **Condensed status line** (call): ONE line — counts per holder kind (live session / kept proxy, owner gone / unknown) plus the longest-held port; the full per-port report only at the deadline.
5. **`guard-own-endpoint.py`** is a PreToolUse hook in the session (not inside the proxy) and protects its OWN port from `ANTHROPIC_BASE_URL` plus `LA_GUARD_EXTRA_PORTS`; the band appears only in its PRIMER text → prose update, no Python helper needed.
6. **Scratch ports** 4191–4210 (v2: band now 4141–4190): nothing listens in 4151–4210 (verified 09:5x); swaps are rare and serialized per session, so the scratch range stays 20 (call).
7. **`LA_REMOTE_KEEP_PROXY=1` proxies whose owner is gone** stay HELD — never reclaimed, never reused (reclaiming = touching a live proxy, an anti-goal). The status line names them ("kept, owner gone") so the user can stop one deliberately (call, forced by the anti-goal).
8. **Invalid `LA_PORT_QUEUE_WAIT_S`**: exit non-zero with a message naming the variable before any port is touched; never a fallback (already stated above).
9. **Process tree**: Claude Code spawns the statusline command as its own child, so variables the launcher exports before starting `claude` reach it; the delivery test runs the statusline with the variables set AND unset.
10. **Code vs prose**: `guard-own-endpoint.py` PRIMER, `bin/fa_capture.py` standing rule 7 and `skills/free-session-handover/SKILL.md` carry the range in prose only; `la-evict.sh` and `la-session-identity.sh` are the code consumers (verified on main e362c76).

## Prerequisite found after the builder check (ponytail audit must-fix #2, verified 2026-11-28)

`_alloc_lock`'s dead-holder break (`bin/remote-session.sh:661-663`) is not atomic: two waiters reading the same stale holder can both end up holding the lock (the second deletes the first's fresh lock). The queue in this design re-enters that lock every 5 s per waiter, which multiplies the race window — so the builder fixes it FIRST: break by atomic rename (`mv "$lock" "$lock.stale.$$"`, only one rename wins), then retry `mkdir`; barrier test with two waiters on a planted dead holder, mutation-checked.
