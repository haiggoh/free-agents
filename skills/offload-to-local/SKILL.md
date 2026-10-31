---
name: offload-to-local
description: 'Use when PLANNING or decomposing ANY multi-step task, and whenever your plan involves reading, searching, summarizing, transforming, drafting, extracting, or editing across files — regardless of whether cost is mentioned. Also when you''re about to do delegatable/bulk/mechanical work on a paid cloud model, or you notice you''re about to burn cloud tokens on work a smaller model could do. It routes delegatable work to a free local model — by DEFAULT to the broad `operator` role, escalating to a specialist role only when needed — instead of spending cloud tokens. Explicit triggers: starting a multi-step plan (especially under a budget constraint / Credit Efficient Mode), "save cost/tokens", "do this locally", "offload this", "use the local model for this". ALSO use before ANY delegation once cost or free/local agents have been mentioned, and whenever you are about to call the Agent/Task tool hoping it will be cheap — it cannot reach a free model and defaults to a full-price subagent, so read this for the routes that are genuinely $0. Note free REMOTE APIs reach far larger models than local, so difficulty alone is not a reason to stay on the paid gateway. Do NOT use it to offload architecture, security-critical, or final-review work.'
---

# offload-to-local — send the legwork to a free local model

The biggest cost saving in Claude Code is **$0 local compute**: keep your capable cloud model
for judgment and delegate the bulk/mechanical legwork to a free model via dispatch.

**Already on a FREE session?** (`ANTHROPIC_BASE_URL` is `localhost` — check the endpoint, never
a label.) Then offloading saves no money; the reason that still holds is QUALITY — an adversarial
review or cross-check from a different model family (`"avoid_family"`) often finds what this
session's own model misses. In-depth material: `references/offload-in-depth.md`.

## ⛔ READ THIS FIRST — the built-in `Agent` tool CANNOT reach a free model

In a CLOUD (paid) session, `Agent`/`Task` only accepts first-party paid tiers; omitting `model`
means the full-price default subagent, not "something cheap". A dispatch that went through `Agent`
saved nothing. The only free routes are HTTP dispatch or a free session:

```bash
bin/lowkey-cli.py             # local model (localhost) — alias lk
bin/librarian-dispatch.py     # long/streaming generations (SSE), local or remote provider
bin/remote-agent-dispatch.py  # free REMOTE API model (bigger; needs --model, --max-tokens)
bin/free-agent-tool.py        # stdin {"prompt","files"} → preference order, fails over; --list
bin/agent-fallback.py         # picks a lane (privacy/cost posture)
bin/csl                       # a full interactive session on either free lane
```

Self-check before claiming an offload: **name the process that ran the tokens** — if not a
localhost port or a free provider's API, it was not free.

## Default routing on a PAID session — offload first, announce always

Decide the route BEFORE the call (Agent/Task, Workflow, fan-out, review agents all start a PAID
model):

1. **Self-contained text work** (review, analysis, extraction, drafting, cross-check) → stateless
   dispatch via `bin/free-agent-tool.py`; attach inputs as `"files"` (it cannot read the disk).
   Parallel independent pieces = parallel Bash calls.
2. **Work that must read files or run commands** → a free SESSION (`remote-session.sh <alias>`,
   or `csl` locally) — see the `free-session-handover` skill.
3. **A paid subagent** only with a stated reason: security-critical, final verification of a free
   result, or a free lane measured as failing right now.

**Announce the model and route BEFORE every offload**; repeat the tool's `model_used`/`route` in
your reply. Council routing: ask by `"role"` and add `"avoid_family"` for adversarial review —
family diversity beats raw capability for review. `--list` prints the live table.

## Which lane? — remote is SMARTER, local is PRIVATE

| | **REMOTE free API** | **LOCAL MLX** |
|---|---|---|
| Size | up to hundreds of B params | ~27–35B, 4-bit |
| Reach for | hard reasoning: analysis, review, planning | volume: extraction, classification, mechanical edits |
| Limits | daily quota per provider (independent per provider) | none — unlimited, offline |
| Privacy | prompt leaves the machine (`--files` needs `--allow-remote-files`) | never leaves the box |

**Heuristic: hard thinking → remote; bulk grind → local; sensitive → local.** Remote is NOT
automatically bigger — check the parameter count, not the lane (details in references/).

## Decide UPFRONT, at decomposition — not mid-work

Decide per step where it runs BEFORE executing anything, and before READING the inputs — "I've
already read it" means the decision came too late. Annotate every step of a multi-step plan:

- `local:<role>` — free LOCAL model (default `operator`)
- `remote:<provider>` — free REMOTE API model ($0 too)
- `cloud:<reason>` — kept on PAID, reason named

Two of three cost nothing, so `cloud:` should be the minority. Valid `cloud:` reasons: quality/
capability (frontier reasoning, security, final review), coupled tool-work needing this session's
live state, or cost-benefit on a one-liner. **A `cloud:` reason must be checkable and name its
expiry** ("the local endpoint is serving a session right now" is good; "too hard to delegate" is a
SPEC gap — split it into `cloud:spec` + `local:` slices). **Watch the distribution:** a plan with
zero local steps owes one explicit sentence why. "Local is slow" is never a reason — check the
current runtime first. Offload is the default, not a mandate: offload real chunks, do one-liners.

**What QUALIFIES for local:** a self-contained transformation over many similar inputs whose
output is checkable against the source (extraction, classification, test-matrix expansion,
first-pass drafts, reformatting). Verify local output against ground truth — never trust it blind.

## Route by role — operator by default

| Role | Reach for it when |
|---|---|
| **operator** (DEFAULT) | anything delegatable; if not clearly below, it's operator |
| **reasoner** | real multi-step reasoning, trade-offs, plans, comparisons |
| **validator** | independent/adversarial validation of a plan or diff |
| **utility** | trivial high-volume classification/extraction/tagging |

Roles are filled by a (model × effort) pairing, not one model each — resolve the live mapping, never
hardcode a name:

```bash
<repo>/bin/la-roles.sh          # per-role table (● on disk / usable now)
<repo>/bin/la-roles.sh <role>   # on-disk alias(es) for one role
```

A partial roster is fine: an unfilled role's work stays on cloud or waits for a download.

## Briefing checklist (a dispatch is stateless — resend everything)

Cover the silent failure points: ordering of returned collections; tie-breaking precedence;
inclusive vs exclusive boundaries; empty/absent/malformed input behavior; timezone/locale values
(assert structurally, e.g. ISO 8601). **ALWAYS mutation-test offloaded tests** — a passing test
can hide a blind spot.

**Dispatch reviews: ask only what the payload can answer.** A stateless dispatch has no tools, no
disk, no diff — asked beyond its inputs it confabulates in the same register as a real finding.
Answerable-from-attached-files → safe (require file:line per claim); needs execution/unseen files
→ not a dispatch, escalate to a free session; design verdicts → don't ask. Triage every finding
against the real code before acting. `avoid_family` must be a roster family name (`--list`).

## Keep on the PAID model

Architecture/design, tricky debugging, security-sensitive logic, FINAL review/verification, and
the orchestration itself. Check the free REMOTE lane before concluding "needs paid" — size is an
argument for a big FREE model, not for spending. Tool use is NOT a reason to avoid a local
*session* (it drives tools fine; only stateless dispatch can't). Advanced pattern — a parallel
local session for a big isolated chunk — in references/offload-in-depth.md.

## How to dispatch

```bash
PORT=$(<repo>/bin/local-llm-hotswap.sh <alias> | grep -o 'SUCCESS_PORT=[0-9]*' | cut -d= -f2)
curl -s http://localhost:$PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"<alias>","messages":[{"role":"user","content":"<role + task + inputs + output spec>"}],"max_tokens":1024}'
# long/open-ended generations: librarian-dispatch.py --port $PORT --payload body.json --outdir /tmp/la-out
```

## The supervised offload loop

1. **Warm** the model once (`local-llm-hotswap.sh <alias>`), capture `SUCCESS_PORT`.
2. **Route** role→model with `la-roles.sh`.
3. **Decide** per step: `local:`/`remote:`/`cloud:<reason>`.
4. **Dispatch** self-contained.
5. **VERIFY** against ground truth (real file list, functional test, diff) — judge by an
   observable outcome change, not "it replied".
6. **Correct** — fix the briefing and re-dispatch, or finish on cloud. Retrying is free.
7. **Hand off** to your own shipping discipline when the work lands a change.

## Branding (📡🦾)

When you begin working under this skill, lead your first substantive reply with the session's lane mark + ` free-agents:` — 📡 on a remote free-API session, 🦾 on a local one.
