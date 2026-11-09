---
name: offload
description: 'Use when PLANNING or decomposing ANY multi-step task, and whenever your plan involves reading, searching, summarizing, transforming, drafting, extracting, or editing across files — regardless of whether cost is mentioned. Routes delegatable work to a $0 lane — a LOCAL model or a REMOTE free API — instead of cloud tokens, choosing per task between a stateless dispatch (one request: review/extraction/drafting/cross-check; inputs via "files" since a dispatch cannot read the disk), a headless free SESSION in the background (multi-step tool work while you stay in this session), and a paid subagent only with a stated reason. Explicit triggers: starting a multi-step plan (especially under a budget constraint / Credit Efficient Mode), "save cost/tokens", "offload this", "use the local model for this", or any delegation once free/local agents are mentioned — the Agent/Task tool cannot reach a free model and defaults to a full-price subagent. Note free REMOTE APIs reach far larger models than local, so difficulty alone is not a reason to stay on the paid gateway. Do NOT use it to offload architecture, security-critical, or final-review work. A HANDOVER (successor session the user types into) is never launched from here headless — that is the free-session-handover skill, always interactive in its own window.'
---
# offload — send the legwork to a $0 lane (local model or remote free API)

The biggest cost saving in Claude Code is not a cheaper cloud model — it's **$0 compute on a free
lane**. Keep a capable cloud model for judgment; delegate bulk/mechanical legwork to a free lane —
a local model (private, unlimited) or a remote free API (bigger models) — via dispatch. This applies
with or without a spending cap.

**Already on a FREE session?** (`ANTHROPIC_BASE_URL` is `localhost` — check the endpoint, never a
label or a document.) Offloading then saves no money; the reason that still holds is QUALITY: an
adversarial review or independent cross-check from a different model family (see *Council routing*)
often finds what this session's own model misses.

## ⛔ READ THIS FIRST — the built-in `Agent` tool CANNOT reach a free model

In a CLOUD (paid) session, `Agent`/`Task` accepts only first-party paid tiers; `model="local"` or
`"free"` is rejected, and **omitting `model` runs a FULL-PRICE subagent** — the illusion of
delegation. (Exceptions since 0.27.0: inside a FREE session, subagents already run on that free
endpoint; and with `FA_REPLACE_AGENTS=1` a hook redirects a cloud `Agent` call to
`bin/free-agent-tool.py` — check the task shape yourself first: a stateless dispatch cannot read
the disk.) The only free routes are HTTP dispatch or a free session:

```bash
bin/free-agent-tool.py        # stdin {"prompt","files"} → maintainer's model order, fails over; --list
bin/remote-agent-dispatch.py  # → free REMOTE API model (bigger + smarter)
bin/librarian-dispatch.py     # long/streaming generations (SSE)
bin/lowkey-cli.py             # (alias lk) free LOCAL model
bin/csl                       # a full interactive session on either free lane
```

Persist a dispatch result with `free-agent-tool.py --out FILE` — it writes the FULL envelope (`content`, `model_used`, `route`, `attempts`, …) atomically; never save `content` alone.

Self-check before you claim you offloaded anything: **name the process that ran the tokens.** If
the answer is not "a localhost port" or "a free provider's API", it was not free.

<!-- fa:routing:begin — SINGLE SOURCE OF TRUTH for the routing text the hooks emit verbatim.
     0.54.0: hooks/cloud-agent-intercept.py and hooks/offload-nudge.py read this block via
     bin/offload_routing.py; editing it changes the hooks. Keep under 1,500 bytes. No model
     NAMES here (rosters rot — point to `bin/free-agent-tool.py --list`). -->
## Default routing on a PAID session — decide the route BEFORE the call

1. **Shape test first: text in / text out, or must it read files / run commands?**
   Text-only → a STATELESS dispatch: `python3 bin/free-agent-tool.py` with
   `{"prompt": ..., "files": [paths]}` (cannot read the disk; `--list` = preference order).
   Must read files / run commands → headless free SESSION: `remote-session.sh <alias> --effort
   <lvl> -p "<brief>"` (resolve `<alias>`: first keyed row of `remote-session.sh --list`). A
   HANDOVER is always INTERACTIVE in its own Terminal window (free-session-handover).
2. **Announce the model and route to the user BEFORE offloading** (the tool prints the model it
   tried on stderr; the result JSON carries `model_used`/`route` — repeat them in your reply).
   Brief delegates with the `brief-the-delegate` skill; session system prompts:
   `config/local-agent-system-prompt.txt`, `config/remote-agent-system-prompt.txt`.
3. **Parallel fan-out** (`superpowers:dispatching-parallel-agents`): independent pieces =
   several parallel Bash calls (dispatches), or several free sessions.
4. **A paid subagent only with a stated reason** (security-critical, final check of the free
   result), and only once the task's OWN free route is exhausted (`"paid_fallback_allowed": true`):
   exhaustion alone never permits paid; the mark lets the ONE stated-reason call through. A
   failed dispatch chain is not by itself a reason — try the OTHER free route first.
<!-- fa:routing:end -->

## Reporting background offloads — two visible reports, never thinking-only

A background (`run_in_background`) completion notice shows only the Bash `description` and
the exit code, so: (1) at launch put the resolved model in the description —
`free dispatch: <task> — <model> @ <provider> (effort <lvl>)` (`remote-session.sh --list` /
`free-agent-tool.py --list` resolve them; never guess ids); (2) say what you launched in your
VISIBLE reply, not only in thinking; (3) when it returns, report in one visible line: model,
provider, effort, `exit code 0` = success, where the result lives (`--out FILE`, or
RUN_DIR/result.json for `--background`); non-zero: what failed and what you did.

## Waiting on a background offload — arm the watcher, never poll

`watch-until --session-progress <session-id> --regex '<handoff path>' --deadline 15m` for
"started", then `watch-until --session-progress <session-id> --stall 20 --proxy-log
"$TMPDIR/local-agents-remote/proxy-<port>.log" --deadline 8h` for "still alive" — both with
`run_in_background`; never a foreground `sleep`, never a polling loop. A STALL verdict names
the proxy-log cause (e.g. "NVIDIA 429") — propose `remote-session.sh --swap-model <port>
<alias>`; do not swap unasked. Report the verdict line visibly when it exits (section above).

## Decide at decomposition — annotate every step before reading any input

Annotate each plan step `local:<role>` (free local model, default `operator`), `remote:<provider>`
(free remote API — also $0; reach for it when a step needs more brains than local has),
or `cloud:<reason>` (paid; the minority — the reason must be checkable and name its expiry).
Waiting until mid-work means the cloud tokens are already spent. A plan that comes out with
**zero** local/remote steps owes one explicit sentence at plan level saying why nothing qualified.

**What qualifies for local — the positive test:** a self-contained transformation over many
similar inputs whose output is **checkable against the source** — extraction, classification,
mechanical edits, first-pass drafts that get reviewed anyway. "Too hard to delegate" is a SPEC
gap, not a model limit: split it into `cloud:spec` (write the design) + `local:` (the slices it
produces). `quality-critical` and `not worth offloading` are jointly exhaustive escape hatches —
watch the distribution, not each step.

## Council routing — pick by role, review across families

Dispatches take `"role"` (operator/reasoner/validator/utility) and `"avoid_family"` (a roster
family from `bin/free-agent-tool.py --list` — e.g. `zhipu` for GLM; unknown names are refused
loudly). For an adversarial review, family diversity beats same-family strength. When nothing on
the machine matches, the result says `"family_avoided": false` / `"role_matched": false` plus a
recommendation to add a model — pass that on rather than treating the review as cross-family.

## Dispatch reviews: ask only what the payload can answer

A stateless dispatch has no tools, no disk, no test runner, no diff — asked a question its inputs
cannot answer, it does not decline: it **confabulates** in the same register as a real finding
(measured 2026-10-29: 25 findings, 2 real). Classify before briefing: answerable from attached
files → safe (require `file:line` + the triggering input per claim, never trim the test file from
the payload); needs execution or a diff → escalate to a free session; needs a design verdict →
don't ask. **Triage every finding against the real code before acting on any.**

## Keep on the PAID model

Architecture/design decisions, tricky debugging, security-sensitive logic, the FINAL
review/verification, and the orchestration & judgment itself. But check the free REMOTE lane
before concluding "this needs the paid model" — "too hard for a local 27B" argues for a 550B
*free* remote, not for spending. Verify free-lane output against ground truth before trusting it
(a smaller model's failure mode is plausible-but-wrong).

---

Long-form material — the remote-vs-local lane axes and size filtering, concrete dated model picks
(rosters and ids rot: verify live with `bin/remote-session.sh --list` and `bin/la-roles.sh`), the
briefing checklist, the mutation-testing rule, the supervised offload loop, streaming dispatch
mechanics, and advanced parallel local sessions — lives in
[`references/offload-in-depth.md`](references/offload-in-depth.md).

## Branding (📡🦾)

When you begin working under this skill, lead your first substantive reply with the session's lane mark + ` free-agents:` — 📡 on a remote free-API session, 🦾 on a local one.
