---
name: offload-to-local
description: 'Use when PLANNING or decomposing ANY multi-step task, and whenever your plan involves reading, searching, summarizing, transforming, drafting, extracting, or editing across files — regardless of whether cost is mentioned. Also when you''re about to do delegatable/bulk/mechanical work on a paid cloud model, or you notice you''re about to burn cloud tokens on work a smaller model could do. It routes delegatable work to a free local model — by DEFAULT to the broad `operator` role, escalating to a specialist role only when needed — instead of spending cloud tokens. Explicit triggers: starting a multi-step plan (especially under a budget constraint / Credit Efficient Mode), "save cost/tokens", "do this locally", "offload this", "use the local model for this". ALSO use before ANY delegation once cost or free/local agents have been mentioned, and whenever you are about to call the Agent/Task tool hoping it will be cheap — it cannot reach a free model and defaults to a full-price subagent, so read this for the routes that are genuinely $0. Note free REMOTE APIs reach far larger models than local, so difficulty alone is not a reason to stay on the paid gateway. Do NOT use it to offload architecture, security-critical, or final-review work.'
---

# offload-to-local — send the legwork to a free local model

The biggest cost saving in Claude Code is not a cheaper cloud model — it's **$0 local compute**.
When your main session runs a capable cloud model (Opus/Sonnet), keep that model for judgment and
**delegate the bulk/mechanical legwork to a local model** via dispatch. This applies to anyone who
wants to save cost, with or without a spending cap.

## ⛔ READ THIS FIRST — the built-in `Agent` tool CANNOT reach a free model

This is the single most common way this skill gets "used" without saving a cent, so it is
stated before anything else.

**Claude Code's `Agent` (Task) tool cannot dispatch to a free agent. Ever.** Its `model`
parameter accepts only first-party paid tiers — `sonnet`, `opus`, `haiku`, `fable`. There is no
value that routes to a local MLX model or to a free cloud API, and the org allowlist blocks
adding one. So:

| What you do | What actually runs | Cost |
|---|---|---|
| `Agent(...)` with **no** `model` | the **default subagent model** (usually the same tier as you) | **full price** |
| `Agent(..., model="haiku")` | Haiku, on the paid gateway | **cheaper, still paid** |
| `Agent(..., model="qwen…"/"local"/"free")` | rejected — not a valid enum value | n/a |
| **`curl` / the dispatch scripts below** | a free local or free-API model | **$0** |

**Omitting `model` is the trap.** It reads like "let the harness pick something cheap"; it
actually means "run a full-price subagent." A dispatch that went through the `Agent` tool has
saved **nothing** — it is the *illusion* of delegation. If you have been asked to use free
agents and you reached for the `Agent` tool, you have not complied yet.

**The only free routes are HTTP dispatch or a free session:**

```bash
bin/local-agent-dispatch.py   # or plain curl to localhost   → free LOCAL model
bin/librarian-dispatch.py     # long/streaming local generations
bin/remote-agent-dispatch.py  # → free REMOTE API model (bigger + smarter, see the map below)
bin/agent-fallback.py         # picks a lane for you, with a privacy/cost posture
bin/csl                       # a full interactive session on either free lane
```

Self-check before you claim you offloaded anything: **name the process that ran the tokens.**
If the answer is not "a localhost port" or "a free provider's API", it was not free.

## Which free agent? — remote is SMARTER, local is PRIVATE

The two free lanes are **not** a quality ladder with local at the bottom and paid at the top.
The free *remote* lane reaches models far larger than anything that fits on this machine, so for
hard thinking the free remote lane often beats the free local one outright — and costs the same
($0). Choose on the axes that actually differ:

| | **REMOTE free API** (`remote-agent-dispatch.py`) | **LOCAL MLX** (`local-agent-dispatch.py`) |
|---|---|---|
| **Size / smarts** | up to **hundreds of B** params — genuinely frontier-adjacent | ~27–35B, 4-bit — capable but visibly smaller |
| **Reach for it when** | the work needs *reasoning*: analysis, review, planning, tricky code, long synthesis | the work needs *volume*: extraction, classification, reformatting, mechanical edits, first drafts |
| **Latency** | fast (provider GPUs); no model load | fast once warm; first call pays a model load |
| **Limits** | a **daily quota per provider** — and quotas are **independent**, so exhausting one leaves the others untouched | none — unlimited, offline, runs all night |
| **Privacy** | ⚠️ the prompt **leaves the machine**; `--files` needs explicit `--allow-remote-files` | never leaves the box; the privacy default |
| **Reliability** | a provider can 404/timeout/return empty; ids rot | fully under your control |

**Default heuristic:** *hard thinking → remote; bulk grind → local; anything sensitive → local,
regardless of difficulty.* When a remote quota is spent, fall back to local rather than to paid
(`agent-fallback.py --mode local-first-free` encodes exactly this).

**⚠️ Remote does NOT mean big.** A remote roster lists small models too — several NVIDIA entries
are 20–35B, i.e. the *same class as (or smaller than) what you already have locally*. Dispatching
one of those remotely buys you **nothing**: no extra capability, but it spends a finite daily quota
and sends your prompt off the machine. **"It is remote" is not evidence that it is smarter — check
the parameter count.**

So filter by SIZE, not by lane:
- **Remote is only the right call when the model is genuinely bigger than local** — the
  hundreds-of-billions tier (e.g. Nemotron Ultra 550B, Kimi K3, DeepSeek V4, Gemini Flash).
- **If a remote entry has a local-capable equivalent, prefer the LOCAL one.** Same capability,
  unlimited, private, no quota burned. The repo tracks this classification for the picker
  (a local-capable filter over the remote roster, hidden by default); apply the *same* judgement
  when choosing a dispatch target, not just when browsing a menu. If that data is available, treat
  a remote entry marked local-capable as "run it locally instead."
- **Never spend a big-model quota on utility work.** Bulk classification goes local even when a
  550B is sitting there idle — save the quota for work that actually needs the brains.

Concrete current picks — **verify with `bin/remote-session.sh --list` and `bin/la-roles.sh`
before using a name; rosters and model ids rot:**

- **Remote, big/smart:** Gemini 3.8 Flash and Gemini 3.6 Flash (note: **separate** daily
  quotas — treat them as two budgets, not one), NVIDIA Nemotron 3 Ultra 550B-A55B, Kimi K3,
  DeepSeek V4 Flash. For utility-tier remote work, Nemotron 3.5 Lightning 30B or Groq
  gpt-oss-120b (very fast).
- **Local, simpler work:** the KAT-Coder 2.5 and Qwen 3.8 class aliases — resolve the live names
  with `bin/la-roles.sh`.

A concrete remote dispatch:

```bash
bin/remote-agent-dispatch.py --provider gemini --prompt '<role + task + inputs + output spec>' \
  --max-tokens 2048 --outdir /tmp/fa-out
# ⚠️ --help lists gemini|nvidia|groq|openrouter|cerebras|cloudflare|github, but only some are
#    IMPLEMENTED — the rest fail fast with "declared but not implemented". Verified 2026-09-17:
#    gemini works; nvidia does NOT (yet). Check before planning a lane around a provider.
# ⚠️ ALWAYS pass --model. The built-in default can be a RETIRED id (measured: it defaulted to
#    gemini-2.0-flash and got HTTP 404 "no longer available"). Model ids rot; pin one explicitly.
# ⚠️ Give it enough --max-tokens. A big model may spend tokens thinking and return a TRUNCATED
#    answer that looks like a terse one (measured: a 700-token cap cut the reply off mid-word).
# --dry-run shows the REMOTE banner and sends nothing; --files needs --allow-remote-files.
```

Both lanes are **stateless per dispatch** — resend the full briefing every call — and both need
their output verified against ground truth before you trust it.

## Decide UPFRONT, at decomposition — not mid-work
The habit is not "notice you're already grinding through files and stop." It's: **when you first
break a task into steps, decide per step where it runs before you execute anything.** Waiting until
you're mid-bulk-work means the cloud tokens are already being spent.

**Decide before you READ the inputs.** This is the sharpest version of the rule, and the easiest to
get wrong. If you open the files to judge whether the task is worth delegating, you have already done
the expensive part — and delegating afterwards is theatre, because the work is spent either way. So
"I've already read the material, it'd be quicker to just do it now" is **evidence the decision came
too late**, not a reason to keep it on cloud. Decide from the *shape* of the step, which you know at
decomposition, not from its contents.

If you catch yourself there anyway, a dispatch still buys something real: run it as an **independent
cross-check** of the work you just did. Diffing your output against a local model's is a genuine
second use, and it costs nothing.

**Make it a required column in the plan — don't leave it implicit** (an implicit "I'll offload
later" reliably becomes "I did it all on cloud"). For any multi-step task that touches files or has
bulk/mechanical steps — and *always* under a budget constraint — annotate **every** step:

- `local:<role>` — delegated to a free LOCAL model (default `operator`; see below),
- `remote:<provider>` — delegated to a free REMOTE API model (also $0; reach for it when the
  step needs more brains than local has, and nothing sensitive leaves the machine), or
- `cloud:<reason>` — kept on the PAID model, with the reason named.

Two of these three cost nothing, so `cloud:` should be the minority annotation in any plan.

**Default to `operator`.** Under a budget constraint the burden flips: a step is `local:operator`
unless you can name why it's `cloud:`. Valid `cloud:` reasons:
- **quality/capability** — frontier reasoning, security-critical, or final review/verification;
- **coupled tool-work** — it needs this session's live context and tool state;
- **cost-benefit** — the work is small or one-off and the spin-up/briefing overhead would exceed the
  saving (`cloud:not worth offloading`). Legitimate, but see the trap below before reaching for it.

### What QUALIFIES for local — the positive test

Listing only what's *excluded* makes the rule unfalsifiable, so here is the shape that qualifies:

> **A self-contained transformation over many similar inputs, whose output is checkable against the
> source.** Cheap to verify, tedious to do by hand.

Extraction, classification, mechanical test-matrix expansion against a harness that already exists, a
first-pass draft that will be reviewed anyway, reformatting a corpus. The *checkable* part is what
makes trusting a smaller model safe: you are not taking its word, you are diffing it against ground
truth.

Worked example, measured: pulling `(target, milestone)` dependency pairs out of 15 free-text notes.
One dispatch, ~1.2k prompt / ~1k completion tokens, about a minute, **$0**. It returned 19 pairs and
all 19 verified — every target present verbatim in the source *and* a real id in the store, every
milestone verbatim. It also correctly **excluded** three near-misses that needed judgement: a "see
also" cross-reference, a sibling the note mentioned as also-blocked rather than as a dependency, and a
parenthetical aside. **If a step fits this shape and you keep it on cloud, that is the miss.**

### The trap: two escape hatches that cover everything

`quality-critical` and `not worth offloading` are **jointly exhaustive** — between them they can
justify keeping *any* step on cloud. Each individual call looks defensible; the pattern only shows up
in the aggregate. A measured instance: four delegatable steps in one plan, four `cloud:` annotations,
two citing each reason, **zero local dispatches** — and every one was individually arguable.

Three rules that make the pair falsifiable again:

1. **A `cloud:` reason must be checkable, and should name its expiry.** "The local endpoint is
   currently serving an interactive session" is good: you can verify it before asserting it, and it
   stops being true later. A reason you cannot check is a preference wearing a reason's clothes.
2. **"Too hard to delegate" is a SPEC gap, not a model limit.** If the work is too tangled to hand
   over, that says the task isn't specified yet. Split it: `cloud:spec` to write the design, then
   `local:` for the slices the spec produces. That converts an unfalsifiable verdict into two
   actionable steps.
3. **Watch the distribution, not the step.** If a plan comes out with **zero** local steps, that is
   the thing to justify — one explicit sentence at plan level about why nothing in it qualified. A
   per-step shrug does not discharge it.

Also not a reason: **"local is slow."** Check what the current runtime actually is before assuming;
timings from a superseded backend are not evidence about the one you'd dispatch to now.

**Offload is the default, not a mandate.** The point is to stop *reflexively* doing bulk/mechanical
work on the paid model — not to force a local hop onto trivia where it costs more than it saves. When
in doubt on a real chunk of work, offload; on a one-liner, just do it. Absent budget pressure the
whole annotation is optional — but the upfront pass costs nothing.

## Route by role — operator by default, escalate when needed
`operator` is the **broad default / catch-all**: any delegatable chunk goes here unless it clearly
needs a specialist. The others are *escalations*, not separate silos — a role is a point on a
spectrum of depth, not a narrow bucket. **When in doubt, `operator`.**

| Role | Reach for it when… | How to brief it |
|---|---|---|
| **operator** (DEFAULT) | anything delegatable: read/search/summarize many files, log/diff/output analysis, mechanical transforms, format conversion, repetitive spec-driven edits, boilerplate/scaffold, draft prose, straightforward extraction. If it's not clearly one of the below, it's operator. | Exact task, inputs inline, exact output shape. Favor it. |
| **reasoner** (escalate) | the chunk needs real multi-step reasoning: analyze *why*, enumerate trade-offs, draft a plan, structured comparison. | Question + full context; ask for its reasoning. |
| **validator** (escalate) | independent validation / second-opinion / adversarial critique of a plan or diff. | Artifact + criteria; ask it to find flaws. Usually a curl dispatch (review needs no tool-driving). |
| **utility** (down-shift) | trivial classification / extraction / tagging at high volume, where even operator is overkill. | Tight instruction + the items; keep it mechanical. |

**Roles are filled by a (model × effort/thinking) pairing — not one model each.** The SAME weights
serve different roles at different depths: fast/thinking-off = operator/utility; higher-effort or
thinking-on = reasoner/validator. So a small roster covers a wide role spectrum by varying effort —
and adding a role rarely means adding a model. Resolve which of *your* on-disk models (and at what
effort) fills each role — **never hardcode a model name**, the roster changes:
```bash
<repo>/bin/la-roles.sh          # per-role table — ● on disk / usable now, ○ not downloaded; shows each model's effort
<repo>/bin/la-roles.sh <role>   # just the on-disk alias(es) for one role (empty = unfilled)
# or `csl roles`
```

**Roles are extensible.** These four are the current canonical set; add more as the roster grows
(e.g. `coder`, `vision`/OCR, `long-context`) — the registry `roles` field takes any tag and the
resolver lists canonical + extras, so a new role is a tag plus a row here. If the existing roles feel
too narrow for the work you keep doing, widen a role's "reach for it when" or add one — the goal is
that most delegatable work maps to *some* role, so offload gets reached for often.

**A partial roster is fine.** If `la-roles.sh` shows a role with no ● (nothing downloaded), keep that
work on cloud or download a model (`install/download-models.sh`, interactive). Several ● under one
role = your A/B choice — pick one or try both.

## Keep on the PAID model (quality-critical)
Architecture / design decisions, tricky debugging, security-sensitive logic, the FINAL
review / verification, and the orchestration & judgment itself. Offload the legwork; keep the
judgment.

**But check the free REMOTE lane before you conclude "this needs the paid model."** "Too hard for
a 27B local model" is an argument for a 550B *free* one, not for spending. A step is only truly
`cloud:` when it needs frontier judgment, this session's live tool state, or must not leave the
machine — so prefer the annotation `remote:<provider>` over `cloud:` whenever the blocker was
size rather than trust or coupling. **Verify local output before trusting it** — it's a smaller model, so a plausible-but-wrong
answer is the risk; the point is that the legwork cost nothing.

**Tool use is NOT a reason to avoid local.** A local *session* (the operator via
`launch-claude-agent.sh`) drives Claude Code's tools fine — qwen-class models handle tool-calling
normally; only a stateless curl *dispatch* can't run a tool loop. So "it involves tools" never
disqualifies local. But be honest about the default: for tool-driving work **coupled to what this
session is already doing**, cloud is usually right — it holds the live context and tool state. The
local-*session* route is a deliberate move for one specific shape of work (below), not the default.

### Advanced: delegate a tool-driving chunk to a parallel local session
Worth it for a **big, isolated, verifiable** chunk (e.g. a mechanical refactor across many files then
run the tests) — not for small or tightly-coupled tool-work. Launch a local session as a side worker
(`bin/new-local-window.sh <alias>` opens an independent window) and let it grind while this cloud
session continues. To make it pay off and stay safe:
- **Isolate** — give it its own git worktree/branch or a disjoint file set, so the two agents can't clobber each other.
- **Watch + verify** — local models hallucinate paths and botch tool schemas; supervise the session (tail its log / a monitor) and review its diff before trusting it. Never merge unsupervised local edits blind.
- **Mind the infra** — check the live concurrency setting rather than assuming; a local session can be slower per turn than a dispatch, so this suits a long *background* chunk over latency-sensitive work. A free *remote* session is the faster option when the work may leave the machine.
- **Amortize** — launching a session costs more than a curl dispatch; reach for it only when the chunk is substantial. Smaller → dispatch it, or keep it on cloud.

The supervision + review is real cloud-attention cost, so the win is cheap *compute*, not zero effort.
Use it when the chunk is big enough that $0 tool-driving compute clearly beats the coordination overhead.

## Briefing (local dispatch is stateless)
A dispatch is a stateless HTTP call — nothing carries between requests — so each one must be
self-contained: the role you want the model to play, the inputs, and the exact output format,
resent every call. For a bounded mechanical stage that's usually all it needs (task + inputs), not
your full rule set. If the delegated work is architecture/code-shaped **and you maintain a standing-rules
index for delegated work**, also fold its relevant lines into the prompt — handing a delegate your
durable *rules* belongs to whatever keeps that index; this skill only covers the local-dispatch
mechanics. (No dependency: with no such index, skip that step.)

## How to dispatch
```bash
# ensure the right model is up (hotswap once for a batch), then curl it:
PORT=$(<repo>/bin/local-llm-hotswap.sh <alias> | grep -o 'SUCCESS_PORT=[0-9]*' | cut -d= -f2)
curl -s http://localhost:$PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"<alias-or-spoof>","messages":[{"role":"user","content":"<role + task + inputs + output spec>"}],"max_tokens":1024}'
```
**Stream long or open-ended generations** with `librarian-dispatch.py` (SSE). Why stream: a **stall
watchdog** aborts a hung local server (no tokens for N s) instead of blocking forever on a dead
request; no timeout death on multi-minute runs (the connection keeps receiving); and live progress.
It takes a **JSON body file** and an output dir (NOT `--prompt`/`--model` flags):
```bash
# write a standard chat-completions body, then dispatch it:
echo '{"model":"<alias-or-spoof>","max_tokens":800,"messages":[{"role":"user","content":"<role + task + inputs + output spec>"}]}' > /tmp/body.json
<repo>/bin/librarian-dispatch.py --port "$PORT" --payload /tmp/body.json --outdir /tmp/la-out
# assistant text streams to /tmp/la-out/output.txt; live heartbeats print on stdout;
# exit 0 = done, 2 = HTTP/engine error, 3 = transport. (The script sets stream=true for you.)
```
Don't wrap dispatches in `timeout` — it's GNU-only (absent on stock macOS) and the built-in stall
watchdog already handles a hung server. Plain curl (above) is fine for short, bounded calls. For a
single trivial item where spin-up costs more than it saves, just do it; when unsure, offload.

## The supervised offload loop (put it together)
Offloading pays off only when you **supervise** it. The repeatable loop:
1. **Warm** the model once up front (`local-llm-hotswap.sh <alias>`, backgroundable) so the first
   dispatch isn't paying cold-start latency; capture the `SUCCESS_PORT` it prints.
2. **Route** — resolve role→model with `la-roles.sh` (never hardcode a name).
3. **Decide** per step: `local:<role>` or `cloud:<reason>` (above).
4. **Dispatch** the local step self-contained (stateless — resend the full briefing).
5. **VERIFY against ground truth** — never trust local output blind. Compare it to something known
   (the real file list, a functional test, a diff) and judge by an *observable outcome change*, not
   "it replied / no error." A smaller model's failure mode is plausible-but-wrong.
6. **Correct** — if it's off (hallucinated path, stale interface, wrong shape), fix the briefing and
   re-dispatch, or finish on cloud. Retrying is cheap; the compute was free.
7. **Hand off to your shipping discipline** (when the work is a repo/package change). Getting a change
   *landed and live* is finish-discipline, not local-model discipline — it applies identically to work
   you did yourself — so it is deliberately **not** specified here. Follow whatever shipping discipline
   you already use for landing a change.

Grab cheap ground truth **before** dispatching (e.g. `ls` the real files) so step 5 is a comparison,
not a fresh guess. The supervision is real cloud attention — that's the cost; the legwork compute is $0.
