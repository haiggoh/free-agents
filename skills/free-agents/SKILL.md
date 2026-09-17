---
name: free-agents
description: 'Use when the user wants to run Claude Code work on FREE inference instead of the paid gateway — either a LOCAL MLX model or a free cloud API. PRIMARILY dispatching a delegatable sub-task from their normal paid session (Opus/Sonnet); also running a full free session. Triggers: "dispatch this to a local model", "offload this to save cost", "run this on qwen locally", "start a local session / local mode", "run a remote session", "use a free API for this", "run the model tournament", or working offline. Explains how to drive the free-agents overlay: dispatch focused prompts (main use), launch a full session on either lane (remote is faster; local is for offline and long unattended runs), and run the diagnostic harnesses. ALSO use the moment you are about to delegate anything after being asked to use free/local agents, or when you catch yourself reaching for the Agent/Task tool to "delegate cheaply" — that tool CANNOT route to a free model and silently runs a full-price subagent; this skill has the only routes that are actually free. Do NOT use for ordinary paid-gateway work that is not being offloaded.'
---

# free-agents — driving local MLX inference

This overlay runs Claude Code work on **free** inference over two equal lanes: a **local** MLX
server on Apple Silicon, and **free-tier remote APIs**. Models, ports, and paths come from
`config/config.local.sh` and `config/remote-agents.sh`. First-time setup and full details are in
the plugin README; this skill is the quick operational guide.

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

## Prereqs (once)
Backend + models must be installed: `./install/install-backend.sh`, then
`cp config/config.example.sh config/config.local.sh` and edit it, then `./install/download-models.sh`.
If the user hasn't done setup, point them to the README rather than guessing paths.

## Interactive local session
```
./bin/launch-claude-agent.sh <alias> [effort-override]   # e.g. qwen-3.6-operator, or ... deepseek-r1-architect max
./bin/csl                                                # menu built from the configured aliases
```
The launcher hotswaps the model onto a free port, exports direct-routing env, and starts `claude`.
It injects a self-preservation + tool-use nudge so the local model won't kill its own server port
or leak reasoning markup.

## Dispatch (PREFERRED for focused work — fast, sub-second to seconds)
Full interactive turns on a local 27B are minutes/turn (large prompt × local prefill). For a
bounded task, dispatch instead of launching a session:
```
PORT=$(./bin/local-llm-hotswap.sh <alias> | grep -o 'SUCCESS_PORT=[0-9]*' | cut -d= -f2)
curl -s http://localhost:$PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"<spoof-or-alias>","messages":[...],"max_tokens":512}'
```
For long generations use `./bin/librarian-dispatch.py` (SSE + stall watchdog).

## Diagnostics
- `./bin/direct-route-acceptance.py --port <p>` — validate the fork patches (system-msg normalization, output-limit, KV-cache hit) on a live server.
- `./bin/tournament-dispatch.py` — Stage-A smoke test across your registered models (edit its MODELS list).
- `./bin/cancellation-matrix.py --port <p>` — verify disconnect/timeout retires work (admission safety).
- `./bin/check-tool-roundtrip.py` — after a local session, verify the native tool round-trip + no leak.
- `./bin/auto-mode-probe.sh setup|analyze` — diagnose Claude Code Auto Mode against a local endpoint.

## Safety rules the agent must respect
- NEVER kill/pkill processes on the local server ports — that terminates a running local session.
- Use `--permission-mode acceptEdits` (the launcher sets this); Auto Mode's safety classifier
  can't be served by the local spoofed model.
