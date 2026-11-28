# Proxy port band: wider band, one shared definition, queue-and-wait (free-agents 0.67.0)

**Status:** draft, awaiting user approval of this document · waypoint `proxy-port-band-4141-4151` · train plan `~/.claude/plans/2026-11-28-free-agents-release-train.md` (row 0.67.0)

## Intent and why

Every free remote session gets its own LiteLLM proxy on a port in the band `LA_REMOTE_PROXY_PORT_MIN..MAX` (today 4141–4151, 11 ports). With ~10 concurrent free sessions on the machine the band fills, and `free_port()` (`bin/remote-session.sh`) **fails hard**. Measured 2026-11-26: rtc-ship's v0.63.4 gate failed at 775 s on `test_last_effort_0493.py`'s real-launch test with "proxy band 4141-4151 full", all ports held by other sessions' live proxies. Each time this happens, a launch or a ship gate is lost for no reason other than port count.

Success: a launch never fails just because the band is busy. It either gets a port at once (the wider band makes waiting rare), or waits visibly until a session ends and frees one, and fails only at a deadline it states up front.

## Goals

1. Band widened to **4141–4170** (30 ports).
2. **One definition** of the band that every consumer reads, so the launcher and the safety logic can never disagree again.
3. **Queue-and-wait** when the band is full: wait for a port, with a visible status line, until a deadline.
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

1. **Band 4141–4170** — default of `LA_REMOTE_PROXY_PORT_MIN/MAX` in `config/remote-agents.sh`.
2. **Queue-and-wait, approach A.**
3. **cost-tracker learns the band from the session env**: the launcher exports `LA_REMOTE_PROXY_PORT_MIN/MAX` into the `claude` it starts; the statusline (spawned with that env) classifies `free_api` when `ANTHROPIC_BASE_URL` is localhost/127.0.0.1 on a port `lo ≤ p ≤ hi`, with `lo=${LA_REMOTE_PROXY_PORT_MIN:-4141}`, `hi=${LA_REMOTE_PROXY_PORT_MAX:-4170}`. Still gated on the URL, so a leaked variable alone never relabels a gateway session.
4. **Ship order: cost-tracker first** (its widened fallback also covers today's band, so it is safe alone), then free-agents 0.67.0. The reverse order would show ❓ on every session landing on 4152+.

## Builder's calls (proposed by me; the user may override)

- Scratch ports stay relative (`MAX+1..MAX+20` → 4171–4190 once widened) — no new setting.
- Queue deadline `LA_PORT_QUEUE_WAIT_S`, default **600** s; `0` = today's hard fail; whole seconds only, anything else aborts naming the variable. Poll interval 5 s (constant).
- Status line every ~30 s on stderr: `remote-session: waiting for a proxy port (30/30 held) — <N>s left; holders: …` (reusing `_band_full_report`'s per-port holder text, condensed).
- At the deadline: the existing hard-fail message plus the full holder report, exit status unchanged.
- free-agents consumers read the band through ONE shell helper sourced from `config/remote-agents.sh` (shell) and one Python helper (for `guard-own-endpoint.py` if it needs the range); prose (primers, skills, standing rules) says "the proxy band (default 4141–4170, `LA_REMOTE_PROXY_PORT_MIN/MAX`)".
- Ship gates: tests that launch a real proxy inherit the wait; their own pytest timeout (900 s) stays above the 600 s default.

## Tests the builder must leave (delivery level where it matters)

- Parallel launches beyond band size: with a 2-port test band and 3 launchers, the third waits, prints the status line, and gets a port when the first exits (barrier-based, like the 0.64.2 parallel-launch test).
- Deadline: a full band with `LA_PORT_QUEUE_WAIT_S=2` fails after ~2 s with the holder report; `LA_PORT_QUEUE_WAIT_S=abc` aborts naming it.
- No consumer literal left: a grep-based test that `414[1-9]|415[01]` and `4141-4151` appear nowhere in `bin/ hooks/ config/ skills/` except the one definition (planted positive first).
- `la-evict.sh` refuses to evict a proxy on a port inside the WIDENED band (e.g. 4160) — mutation-checked.
- `la-session-identity.sh` classifies 4160 as `free_api`; cost-tracker statusline does the same with the env set AND unset (fallback).

## Left to the builder

Exact helper names and file placement; how `_band_full_report` output is condensed for the status line; migration note in CHANGELOG/README for anyone who set `LA_REMOTE_PROXY_PORT_MAX` themselves.
