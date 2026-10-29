# free-agents

**Run Claude Code work on free inference: local models on your own hardware, or free-tier cloud
APIs.**

The main use: from your normal Claude Code session (Opus or Sonnet, direct or through a gateway),
**hand bulk, mechanical sub-tasks to a free model** (searches, summaries, bounded transforms,
first-pass reviews). Your capable model keeps the hard reasoning. You save cost without changing
your workflow.

You can also run a **whole Claude Code session** on a free model:

- **Remote** sessions run on a free provider tier (NVIDIA, Gemini, Groq and a dozen more). They
  need only a provider key, no local models, and for interactive work they are the faster lane.
- **Local** sessions run on a model served on your machine. Inference stays on the machine and works
  offline. They are slower per turn, which suits privacy-sensitive work and long unattended runs.

Every provider in the key-setup helper has a free tier; that is why it is on the list. Free tiers
differ: some are renewable daily allowances, others are trials or conditional credits (see
[API keys](#api-keys--the-free-tier-setup-helper)). Paid use works too if your account has credits,
but nothing here requires it.

This is power-user tooling, not a one-click app.

## Project documentation

- [Changelog](CHANGELOG.md): every release in full
- [Roadmap](docs/ROADMAP.md): specced but not shipped, release gates, deliberately deferred
- [Remote API sessions](docs/remote-session/README.md) and [key setup](docs/remote-session/setup.md)
- [Remote emergency fallback](docs/remote-fallback/README.md): keep working when the daily budget is spent
- [NVIDIA GPUs on Linux/WSL2](docs/CUDA.md): the CUDA tiers, backends and their test status
- [Backend runtime manager](docs/RAPID_RUNTIME_MANAGER.md) and [read-only inventory](docs/READONLY_INVENTORY.md)

## Platforms and prerequisites

| You want | Needs | Status |
|---|---|---|
| **Offload to free cloud APIs, or run remote sessions** | macOS or Linux (WSL2 works), Claude Code, Python 3, `litellm` (for sessions), one provider key | Works on any machine. NVIDIA and Gemini are proven live; the other providers are untested (see [below](#provider-status)). |
| **Local sessions and local dispatch on a Mac** | Apple Silicon, enough unified memory for your models (the reference stack is a 128 GB M4 Max), Homebrew Bash 4+ | The reference platform. |
| **Local inference on an NVIDIA GPU** | Linux or WSL2, an NVIDIA driver with CUDA | Built and unit-tested with a faked `nvidia-smi`; **not yet run on a real GPU**. See [docs/CUDA.md](docs/CUDA.md). |
| **CPU-only local inference** | Linux, llama.cpp | Same status as the NVIDIA route. |
| **Native Windows** | | Not supported. Hardware detection recognises Windows, but the launchers are Bash and need WSL2. |

Local inference is **optional**. If you only want remote sessions or remote dispatch, skip every
backend and model-download step below.

## Install

Pick **one** of these. They are listed in order of preference.

### A. Claude Code marketplace (recommended)

Inside Claude Code (these are slash commands, typed at the Claude Code prompt):

```text
/plugin marketplace add haiggoh/get-haiggoh
/plugin install free-agents@haiggoh
```

Or the same from a terminal: `claude plugin marketplace add haiggoh/get-haiggoh`, then
`claude plugin install free-agents@haiggoh`. Restart Claude Code (or run `/reload-plugins`) and the
skills and hooks are active. Update later with `claude plugin update free-agents@haiggoh`.

This installs the plugin into Claude Code's versioned plugin cache. To put `csl` and the `local-*`
aliases on your PATH, run the shortcut installer from the installed copy:

```bash
"$(ls -d ~/.claude/plugins/cache/haiggoh/free-agents/*/ | sort -V | tail -1)install/setup-shortcuts.sh"
```

The symlink points at that version's directory, so **re-run it after a plugin update**. Option B
avoids that, because its wrappers always find the newest installed copy.

### B. Homebrew

```bash
brew install haiggoh/tap/free-agents
free-agents-install          # runs `claude plugin install` for you (once per user)
```

Homebrew puts `csl`, `lowkey` and `lk` on your PATH. They run the **newest** free-agents release that
Claude Code has installed; before the plugin is installed they fall back to Homebrew's own copy.
`FREE_AGENTS_ROOT` points them at any checkout instead. After `brew upgrade free-agents`, also run
`claude plugin update free-agents@haiggoh` so both stay on the same release. A command of the same
name earlier on your PATH (for example `~/.local/bin/csl`) still wins.

### C. One-line installer

```bash
curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.sh | bash
```

Run it in Terminal. Nothing is saved to disk first, so macOS Gatekeeper never sees a downloaded file.
It needs only Claude Code and Python 3. It installs the plugin through `claude plugin install` (so it
lands exactly where option A puts it), writes `csl`, `lowkey` and `lk` wrappers to `~/.local/bin` that
always run the newest installed copy, then offers LiteLLM, the picker UI and an API key. Local
inference is offered last and is off unless you say yes. `--remote-only`, `--local`, `--yes`,
`--bin-dir DIR` and `--help` work through the pipe: `curl … | bash -s -- --remote-only --yes`. It
never replaces a `csl`, `lowkey` or `lk` that it did not write.

Downloading the file in a browser and double-clicking it still trips Gatekeeper (an override in
System Settings → Privacy & Security), so use the one-liner, or `bash install-free-agents.sh`.

Each release also ships `free-agents-<version>.tar.gz`, the same files as the plugin, for offline
inspection.

## First run

Commands below run in an **ordinary terminal**, not at the Claude Code prompt. `csl` is on your PATH
after the shortcut installer (A) or Homebrew (B); from a source checkout use `./bin/csl`.

### Remote only (no local models)

```bash
pipx install 'litellm[proxy]'               # the proxy remote sessions run through
"$FA/install/setup-session-picker.sh"       # one-time: the picker's own venv (Textual)
csl setup-remote                            # store a provider key; makes no API calls
csl remote                                  # pick a provider and model, then launch
```

`$FA` here is the plugin directory (the `ls -d` line in option A prints it; with Homebrew it is
`$(brew --prefix free-agents)/libexec`). `csl remote <alias>` launches directly without the picker,
so it does not need the picker venv.

To **offload** from your normal session without a full remote session, no proxy is needed:

```bash
"$FA/bin/librarian-dispatch.py" --provider nvidia \
  --model nvidia/nemotron-3-ultra-550b-a55b --prompt "Summarise this diff: …"
```

### Local (Apple Silicon)

```bash
"$FA/install/install-backend.sh"                       # Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM venvs
cp "$FA/config/config.example.sh" "$FA/config/config.local.sh" && $EDITOR "$FA/config/config.local.sh"
"$FA/install/download-models.sh" --list                # pick what fits your memory and disk
"$FA/install/download-models.sh" --select kat-coder-optiq  # you need not fetch them all
csl                                                    # Home → l Local sessions
```

`config.local.sh` is where your model directory and model registry live; it is gitignored and
never published ([the overlay](#the-overlay-public-tooling-private-config)). It sits inside the
directory you copied it into. In a plugin-cache install, keep a copy elsewhere, because a new
version arrives in a new directory. On Linux, `install-backend.sh --all` adds the CUDA backends and
`--backend llama-cpp` gives a CPU-only install (see [docs/CUDA.md](docs/CUDA.md)).

A **partial roster is fine**: whatever you download fills the roles it is tagged with, and the rest
of the work stays on your cloud model.

If you use [rtk](https://github.com/rtk-ai/rtk) (an output-condensing Bash hook), also run
`install/setup-rtk-excludes.sh` once (`--check` verifies, `--dry-run` previews); see
[Troubleshooting](#troubleshooting). Without rtk it does nothing.

## Choose a workflow

### Offload sub-tasks from your cloud session (recommended)

Keep driving with your cloud model. Send bounded sub-tasks to a free model, either local (`curl`,
`librarian-dispatch.py --port`, `lowkey`) or remote (`librarian-dispatch.py --provider`). Your main
session does not change. The `offload-to-local` skill teaches the model when and how to do this, and
the [Usage](#usage) section has the commands.

### A full remote session: the fastest free lane

`csl remote` launches Claude Code against a free provider. LiteLLM translates Claude Code's
Anthropic `/v1/messages` calls into each provider's API: NVIDIA, Gemini, Groq, OpenRouter free
tier, Cloudflare Workers AI, Cerebras, Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel, SambaNova,
ModelScope or Streamlake. Usage counts against that provider's quota. See
[Remote API sessions](docs/remote-session/README.md).

<a id="provider-status"></a>
> **Provider status: proven vs untested.** Remote sessions work, and they are faster than local
> ones. What varies is the individual provider.
>
> | Status | Providers | What it means |
> |---|---|---|
> | **Proven** | NVIDIA, Gemini | Real working sessions, including tool use. NVIDIA leads the roster: generous limits, no known daily quota. Gemini is best kept for offload dispatches: its free daily quota is too small to carry full sessions. |
> | **Untested** | Groq, OpenRouter free tier, Cloudflare Workers AI, Cerebras, Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel, SambaNova, ModelScope, Streamlake | Offline fixture coverage and provider-key isolation tests, but no live quota qualification or real tool-use smoke test yet. |
>
> A weak *model* is not a broken *lane*: NVIDIA's Nemotron takes shortcuts and makes mistakes on
> long tasks, which is a model limitation (try `max` effort), not a defect in the remote path.

### NVIDIA model field notes

The NVIDIA route is where most full remote sessions run, and the models differ more in
temperament than in the roster lines suggest. These are the maintainer's working
observations from real sessions (2026-10-13) — subjective, but the practical details are
the kind that cost a session to rediscover.
A short form of each note appears in the remote picker's info box when you hover or
highlight the model (`config/remote-model-notes.json`).

**Nemotron 3 Ultra** — `nvidia-nemotron-ultra` / `nvidia-nemotron-ultra-thinking`
Capable on clearly-instructed work, relatively fast, 1M context. Tends to think for long
stretches before reporting in. Two habits make it pleasant: the thinking blocks are
readable as they stream, and **interjecting a queued prompt while it is still thinking
nudges the ongoing work in a new direction** without losing the run.
Caveat (2026-10-15): it can act rash — occasionally presenting unfinished, untested work
as done — and it struggles with complex, loosely-scoped problems. Scope asks tightly and
verify its "done" before shipping. Nemotron 3 Super takes these shortcuts even more often.

**Nemotron 3 Super** — `nvidia-nemotron3` / `nvidia-nemotron3-thinking`
Extremely fast, but takes the path of least resistance and declares goals fulfilled
prematurely. Good for quick, well-scoped asks; check its "done".

**GLM 5.3** — `nvidia-glm53-full` / `nvidia-glm53-full-thinking`
Very smart reasoning model, excellent at understanding context and finding problems and
solutions on its own — comparatively slow, ideal for unattended overnight work. 1M
context. Thinking and output are well balanced; queued-prompt nudging works well here
too. One caution: **don't `/resume` a long GLM session cold** — start the session with a
throwaway prompt (e.g. "hello"), wait for the first response, and only then use
`/resume-interrupted`. The first prompt of a GLM 5.3 session takes unusually long; once
the session is going it speeds up and produces significant work. (The `-full` aliases
carry a ~1-minute NVIDIA-side door charge before the first token — payload-independent.)

**GLM 5.3 Flash** — `nvidia-glm53` / `nvidia-glm53-thinking`
Significantly faster (fewer parameters) than GLM 5.3, but still surprisingly smart —
good at investigating and understanding the context it is working in. Thinking and
output well balanced. The everyday driver among the reasoning models.

**Laguna XS 2.1** — `nvidia-laguna`
Promising and specialized for coding. Slow on the first prompt, then picks up speed —
overall slower than Nemotron Ultra. Capable tool use under Claude Code. Known wart: its
reasoning output may not surface as thinking blocks, and a stray closing think-tag can
occasionally appear in the text. Likely explanation (2026-10-15): Laguna is a thinking
model whose reasoning blocks the harness does not recognize, so the stray tag is the tail
of an unrecognized reasoning block — cosmetic in the output, no observed impact on the
work itself, and likely patchable.

### A full local session: offline and private

The whole session runs on a local model, served under a Claude model id so the client accepts it.
There is no inference cost and no inference leaves the machine (what else can still reach the
network is covered in [A local session that is actually local](#a-local-session-that-is-actually-local)).
Turns are slower than a remote API. Apple Silicon is the reference platform; NVIDIA and CPU-only
Linux are the same idea on other backends ([docs/CUDA.md](docs/CUDA.md)).

## Recent releases

Newest first. `CHANGELOG.md` has every release in full.

- **0.41.0**: local models read as names in the picker (`DeepSeek R1 Architect`), with an info box showing the exact alias.
- **0.40.1**: hotfix — an NVIDIA request booked TWO slots in the 40 RPM window (40 RPM was really 20); it books one.
- **0.40.0**: sessions survive an upstream model throttle — a per-session fallback chain answers from the next model on the same port;
  a 429 is classified (per-model cooldown instead of a machine-wide pause); `remote-session.sh --swap-model`
  rescues a stalled session in place. The picker's model search is a live filter box at the top of the list (Remote and
  Local), model names lead with the model's maker instead of the provider (`Z.ai GLM 5.3`, `DeepSeek V4.1 Flash`), session
  titles are shorter, and the `remote-agents.sh` roster is generated from the registry YAML with a drift test.
- **0.39.0**: free offloading is the default on a paid session; dispatch follows a set model
  order (Gemini 3.8 Flash first) with automatic failover and says which model ran; new
  `free-session-handover` skill.
- **0.38.1**: remote sessions work past the first prompt on every keyed provider (the
  chat/completions switch LiteLLM actually reads); a native Anthropic lane, chosen per model, for
  OpenRouter, Kilo, Z.ai and LLM7; account refusals (balance, card) shown at once as a named 402.
- **0.38.0**: the [RAM usage out-of-memory protection](#ram-usage-out-of-memory-protection-auto-compact-instead-of-a-dead-session-0380) (called the memory guard before 0.42.0):
  local sessions compact before they run out of RAM; per-model KV cache precision.

- **0.29.x**: acquisition-catalogue reader with load-time validation; backend-manager CLI parity
  (every documented subcommand dispatches); a Homebrew formula.
- **0.28.x**: genuine auto mode on the free-API lane, with Claude Code's classifier answered by a
  free model (`LA_CLASSIFIER_MODEL`); the manifest gate wired onto the launch path.
- **0.27.x**: subagents and ultracode in every lane, opt-in replacement of paid cloud subagents,
  picker ↔ launcher parity, Streamlake and the Mistral roster.
- **0.26.x**: the NVIDIA limiter on every dispatch path, the free-API output ceiling, the Cerebras cap.

## Subagents in every lane (0.27.0)

The native `Agent` and `Workflow` tools (parallel subagents, **ultracode**) now work in free sessions,
and paid subagents in cloud sessions can be swapped for free ones.

| Session | What subagents run on | Setting |
|---|---|---|
| **Free-API** (`csl remote`) | The session's own free endpoint. The proxy's `claude-*` catch-all answers every model id a subagent asks for (`claude-haiku-5-5`, `claude-sonnet-4-6`, …); before 0.27.0 each got `400 Invalid model name`. NVIDIA calls still pass the machine-wide 40 RPM limiter. | `LA_SUBAGENT_ROUTING=same` (default) or `hybrid` |
| **Local** (`csl local`) | The same local server (Rapid serves every model name under its spoof id). Parallel subagents are capped to the server's slots. | `LA_SUBAGENT_MAX_CONCURRENT` (default `LA_RAPID_MAX_NUM_SEQS`) |
| **Cloud** (gateway / Anthropic login) | Native Sonnet by default. **Opt-in:** replace them with free agents. | `FA_REPLACE_AGENTS` in `~/.claude/settings.json` env |

**Hybrid council routing (free-API).** `LA_SUBAGENT_ROUTING=hybrid` plus
`LA_SUBAGENT_POOL="nvidia:poolside/laguna-xs-2.1,gemini:gemini-3.8-flash"` spreads subagent calls
across the session model and every pool member: different models answering in parallel, the
original council idea. A member without a key in `~/.api_keys/` is skipped with a note. A member
that hangs times out after `LA_SUBAGENT_POOL_TIMEOUT` (120 s) and is retried on another.

**Cloud replacement.** The `hooks/cloud-agent-intercept.py` PreToolUse hook routes the native
`Agent` tool per the session's mode (0.43.0; the legacy `FA_REPLACE_AGENTS=1|0` maps onto it):

- **Free first** (default): `Agent` is denied with a ready-to-run command for
  `bin/free-agent-tool.py` (role routing through `council-router.sh`; the prompt goes through a
  quoted heredoc, so no quote in it can break the command). After the session's free attempts are
  honestly exhausted — the dispatch result reports `"paid_fallback_allowed": true` — **one** native
  paid call is let through automatically (the fallback mark; session-scoped, single-use, expires).
- **Free only**: every `Agent` call is denied and the hook never falls back to paid.
- **Paid**: the hook stays out; the native Agent runs as usual.

Change the mode in `csl` → Cloud Session Configuration → `x`, or with
`bin/cloud_session_env.py set route agent free-first|free-only|paid`. Undecided users get the
redirect (no prompt). Free sessions are never touched by the hook; a corrupt settings layer never
blocks a call.

**Default permission mode (0.43.0).** Cloud Session Configuration → `d` sets
`permissions.defaultMode` (`auto` / `bypassPermissions`) in the user `settings.json` — the default
for every session started without a mode flag, including a plain native `claude`; csl's own lanes
pass `--permission-mode` explicitly and are unaffected. A managed policy that disables bypass
shows the row disabled, and csl never works around it.

**Auto-mode eligibility probe (0.43.0).** Cloud Session Configuration → `e` runs
`bin/fa-eligibility-probe.py`: one small request measures whether auto mode's safety checks are
free on this setup. The verdict comes from evidence only — a `system` notice means the checks are
**billed** (the cost box appears next to the row); absence is `unknown` and claims nothing. The
verdict is cached with its date; the probe refuses on a free lane and never runs on its own.

**Registry auto-sync moved (0.43.0).** The "Auto-sync local registry with the disk" toggle lives
on the **Local** screen (`y`) now, not in the OOM guard; the store key and behaviour are unchanged.

Measured 2026-10-09: a real `claude -p` free-API session told to use `Agent` returned the subagent's
answer through 6 proxy calls, all `200`. The same run against a config **without** the catch-all
logged 8 × `400` and 17 × `500` before recovering.

### Picker settings that reach the session (0.27.2)

- **Session thinking** (`j` on both lane screens, 0.44.0) travels as `LA_THINKING_OVERRIDE` and is
  consumed by `remote-session.sh`, `local-session.sh` / config-lib's `la_lookup`, and hotswap's
  profile mode. The default (never touched) is thinking ON for session launches; a model
  constraint may override the choice and the banner / info box say so — GLM on NVIDIA always
  runs its thinking template (OFF there travels as `reasoning_effort low`). Stateless dispatches
  have no toggle: `dispatch-preferences.json` entries take `"thinking": "on"|"off"|"auto"`,
  resolved by role (reasoner/validator on, operator/utility off) with model constraints applied
  last and reported in the result JSON.
- **Local temperature** (`o` on the Local screen) travels as `LA_TEMPERATURE`, and hotswap passes it
  to Rapid-MLX as `--default-temperature`. It is part of the server-reuse identity: a running server
  started at another temperature is not reused. Before this, the picker sent a trailing
  `--temperature` that `local-session.sh` did not parse, and the value could land in the effort
  slot.
- **`LA_REMOTE_CLAUDE_SETTINGS=<file>`** adds your own settings file to every free-API session. It
  is merged with blind-trust and the theme overlay, and its `deny` rules are **added to**
  blind-trust's destructive-command denials, never substituted for them. A missing file prints a
  warning.
- The `csl` dry run names the real MCP toggle key (`m`).

## How it works

Claude Code talks to an Anthropic-compatible endpoint. A local MLX backend exposes one
(`/v1/messages`) and serves a local model. For dispatch you just `curl` that endpoint. For
a full local session the model is served under a spoofed Claude model id and
`ANTHROPIC_BASE_URL` points Claude Code at it; direct routing keeps native transcripts +
`history.jsonl` working (an earlier proxy approach suppressed them).

### Which backend serves a model

A registration's `serve` field picks the engine. Prefer the **generic** value `mlx`, which resolves
to this machine's default (`LA_DEFAULT_MLX_BACKEND`):

| `serve` value | Engine | Notes |
|---|---|---|
| `mlx` (or empty) | **whatever `LA_DEFAULT_MLX_BACKEND` says — `rapid` by default** | Prefer this. One line moves the whole roster. |
| `rapid` | Rapid-MLX | Pin. Anthropic `/v1/messages`, hybrid prefix cache, continuous batching. |
| `vllm` | vllm-mlx | Pin. The **legacy** comparison lane. |
| `mlx_lm` | `mlx_lm.server` | Dispatch-only — no `/v1/messages`, so no interactive session. |
| `llama_cpp` | `llama-server` (llama.cpp) | **GGUF** artifacts. Not launched by hotswap; it stops with instructions. |

**Rapid-MLX is the default** (since 0.13.1). It earns that on the hybrid prefix cache — 27,290 of
27,584 prompt tokens cached, warm TTFT **2.0s vs 135.6s** cold on vllm-mlx — which is the
difference between an interactive local session being usable and not. Two consequences worth
knowing:

- **Concurrency is a real capability, and it is held at exactly 2 — no more.** Rapid has a genuine
  continuous-batching scheduler (`--max-num-seqs`, its own default 256), unlike vllm-mlx's
  single-slot engine. This stack runs it at **2** (`LA_RAPID_MAX_NUM_SEQS`), raised from 1 in
  `0.13.8` for one specific reason: locally routed Auto Mode needs a free slot for its safety
  classifier, or the classifier queues behind the turn that triggered it and times out.

  **That raised the default without retiring the memory problem**, which is the part not to lose.
  The binding constraint is still Metal memory, not the scheduler: one long-context session
  measured 99.9 GB at 103,020 prompt tokens against a 103.9 GB limit, with seven `SIGABRT`s in
  ~22h, and each extra in-flight sequence carries its own KV working set. The justification for the
  second slot is that a classifier request is *small and short-lived* — measured 35,154 prompt
  tokens for an 8-token verdict at `max_tokens=64` — **not** that the ceiling was re-measured for
  two full-length concurrent sequences. It was not, and two long sequences remain unqualified;
  `launch-local-auto-mode.sh` still checks wired memory before forcing the slot, and
  `la-ram-preflight.sh`'s Rapid formula still models a single sequence.

  So running a session and a *dispatch* on the same model at once is still better served by a
  **second instance on another port** — 27B 4-bit weights are ~16 GB, so two instances fit where
  two long contexts do not.
- **GGUF is unaffected.** `llama_cpp` is a per-model pin and is deliberately *not* reachable from a
  generic value, so flipping the MLX default can never reroute a GGUF model onto an engine that
  cannot load it. The loader warns if a GGUF-looking artifact resolves to an MLX backend.

Rolling back is one line — `LA_DEFAULT_MLX_BACKEND=vllm` in `config.local.sh`. Explicit pins are
unaffected in either direction, which is what keeps per-backend measurements attributable. Every
listing prints the resolved backend *with* its declaration (`rapid (mlx->rapid)`), so no report
claims the config said something it did not.

#### Why Rapid-MLX lives in a pinned venv, not Homebrew

Homebrew *does* package it (`brew install rapid-mlx`), and the formula is closer to the pinned venv
than it looks: it is `Language::Python::Virtualenv` with 55 pinned resources, matching this stack's
qualified versions exactly (mlx-lm 0.31.3, transformers 5.12.1, tokenizers 0.22.2, …). So the
choice is not "venv vs no venv" — it is *who pins the venv*. Three reasons it stays here:

1. **`mlx` is a `depends_on`, not a pinned resource.** The brew build runs against whatever the
   `mlx` formula is at, and it moves on every `brew upgrade`. `mlx` is the Metal layer that this
   stack's memory-ceiling measurements are measurements *of*.
2. **No extras path.** `pip install 'rapid-mlx[vision]'` has no brew equivalent, so a separately
   locked vision environment is only buildable as a venv.
3. **Retention isn't yours.** Brew links one version and `brew cleanup` decides when the old one
   goes, so "retain the previous working runtime for 7 successful days" is not enforceable.

`LA_RAPID_BIN` is therefore normally **pinned** to a qualified version, and upgrades are built
**side by side** (`~/.venvs/rapid-mlx-<new>`) and promoted after qualification. Left unset, it
auto-discovers brew → `PATH` → newest `~/.venvs/rapid-mlx-*`, so a machine that prefers the brew
install still works. The weekly `backend-update-check` launchd job (installed via `manage-backend.py --backend rapid-mlx launchd install`) watches PyPI for all backend releases (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) and only *notifies* — it never installs.

**What does install is `install/manage-backend.py`**, the unified runtime lifecycle manager for
every backend (`--backend rapid-mlx|vllm-mlx|omlx|moespresso|llama-cpp|litellm`). It closes the notify loop
without ever starting a model server. In `csl` it is `v` (Backend manager).

```bash
./install/manage-backend.py --backend rapid-mlx releases
./install/manage-backend.py --backend rapid-mlx installed
./install/manage-backend.py --backend rapid-mlx install 0.14.0
```

**MoEspresso** (`serve=moespresso`) is a separate, full runtime for *MoEspresso-packaged* artifacts —
models shipped as `weight_format: "mxtq"` TurboQuant bundles, which no MLX engine can read (oMLX
and rapid-mlx both load through `mlx_lm`, and they fail at `load_weights` with
`Received 46 parameters not in model`). It comes from the `steadfastgaze/tap` Homebrew tap and is
installed by `install-backend.sh` like every other backend.

Two consequences worth knowing before you use it:

- **It is pinned to the 3.x line.** 4.x is a Qwen-only engine that refuses a DeepSeek V4 package
  with `unsupported required package feature`, so the backend manager will neither install nor
  upgrade past 3.x — an upgrade that would break the only model the runtime serves would arrive
  with every command succeeding.
- **It needs no spoof id, and advertises no model list.** The server accepts and echoes any model
  name, so no oMLX-style symlink farm is built; and because `GET /v1/models` 404s, this backend
  reuses a healthy server from its own metadata instead of an advertised id.
./install/manage-backend.py --backend litellm check-updates
```

`install/manage-rapid-mlx.py` is kept as a **compatibility wrapper** for Rapid-MLX: the same
subcommands without `--backend`, plus an interactive menu when run bare on a TTY.

```bash
./install/manage-rapid-mlx.py                       # interactive menu (needs a TTY)
./install/manage-rapid-mlx.py releases              # what PyPI offers
./install/manage-rapid-mlx.py installed             # what this machine has, and which pin is active
./install/manage-rapid-mlx.py install 0.14.0        # build ~/.venvs/rapid-mlx-0.14.0, validate, promote pins
./install/manage-rapid-mlx.py install 0.14.0 --skip-pin-update   # install WITHOUT promoting
./install/manage-rapid-mlx.py smoke 0.14.0          # package + CLI surfaces, no server
./install/manage-rapid-mlx.py promote 0.13.4        # move active pins (this is the rollback path)
./install/manage-rapid-mlx.py snapshot 0.14.0       # exact recreation receipt
./install/manage-rapid-mlx.py remove 0.12.18        # only if inactive, unpinned and reproducible
```

`--dry-run` is a GLOBAL flag and must come BEFORE the subcommand
(`./install/manage-rapid-mlx.py --dry-run install 0.14.0`, not `install 0.14.0 --dry-run`, which
argparse rejects). It prints the plan and writes nothing.

Three properties matter more than the command list:

- **Side by side, not in place.** Each version is its own `~/.venvs/rapid-mlx-<version>`, so a new
  runtime is built and validated while the working one keeps serving. `promote` is a separate,
  transactional step — installing is not adopting.
- **Promotion is not qualification.** `promote` moves `LA_RAPID_BIN`; it says nothing about whether
  a model still performs. Recorded Metal and cache figures from an older runtime are baselines, not
  transferable evidence — re-measure per runtime.
- **Retirement is guarded.** `remove` refuses a venv that is active, pinned, or lacks a valid
  recreation receipt, which is what makes a rollback window real rather than aspirational.

To change version safely: `install <new>` → `smoke <new>` → exercise a real session → keep the
previous venv until the new one has proven itself → `remove` the old one only once it is unpinned.
If the new runtime misbehaves, `promote <previous>` is the whole rollback. Full reference:
[`docs/RAPID_RUNTIME_MANAGER.md`](docs/RAPID_RUNTIME_MANAGER.md).

## Roadmap

What shipped is in [`CHANGELOG.md`](CHANGELOG.md). What is **specced but not shipped** — the
`0.14.0` runtime-profiles architecture, its release gates, and the things deliberately deferred — is
in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What's in the box

- **`bin/launch-claude-agent.sh`** — start an interactive local Claude Code session (hotswaps a
  model onto a free port, sets direct-routing env, and renders the external local-session prompt
  template with actual model/backend/port metadata).
- **`bin/launch-local-auto-mode.sh`** — the standalone harness for
  [Auto Mode with a local classifier](#auto-mode-with-a-local-classifier-triple-toggle): it warms a server that has
  a free slot for the classifier, launches straight into `--permission-mode auto`, and hands the
  session a first action deliberately chosen to exercise the classifier, so a run either proves local
  routing or fails closed. Opt-in (`JOYIA_LOCAL_AUTO_CLASSIFIER=1`) because it is a probe; for
  everyday use the `csl` toggle covers the same ground.
- **`bin/local-llm-hotswap.sh`** — land a registered model on the first free port; safe (never kills
  a healthy model on another port); bounded readiness with diagnostics.
- **`bin/lowkey-cli.py`** — universal local model dispatcher, independent of any AI client
  (Claude Code, another agent CLI, or none). Shells out to `local-llm-hotswap.sh` + `librarian-dispatch.py` and
  streams live tokens back to stdout. Install the shell aliases with `install/setup-shortcuts.sh`
  (or add them manually — see [Lowkey CLI](#lowkey-cli) below).
- **`bin/csl`** — menu front-end built from your configured aliases.
- **`config/`** — the overlay: `config.example.sh`, the shipped `local-agent-system-prompt.txt` template, and your gitignored `config.local.sh`.
- **`install/`** — `install-backend.sh` (venv + vllm-mlx + fork patches), `download-models.sh`
  (the downloader ENGINE: revisions, resume, dedupe, selective GGUF files, disk preflight),
  `manage-rapid-mlx.py` (the Rapid-MLX runtime lifecycle: release discovery, side-by-side versioned
  venvs, validation, transactional pin promotion and rollback, recreation receipts, guarded
  retirement — never starts a server; see [`docs/RAPID_RUNTIME_MANAGER.md`](docs/RAPID_RUNTIME_MANAGER.md)),
  and `vllm-mlx-local-fork-patches.patch`.
- **`bin/` diagnostics** — `tournament-dispatch.py`, `cancellation-matrix.py`,
  `check-tool-roundtrip.py`, `direct-route-acceptance.py`, `auto-mode-probe.sh`,
  `librarian-dispatch.py`, `thinking-log.py`, `git-local-review`, and
  `local-inference-readonly-inventory.zsh` (offline whole-stack snapshot — see
  [`docs/READONLY_INVENTORY.md`](docs/READONLY_INVENTORY.md)).
- **`bin/la-ram-preflight.sh`** — decides whether a model can load without stalling the machine,
  before any weights are read. Asks three questions cheapest-first (does it fit / are other models
  loaded / are they attached) and stops as soon as the answer is settled.
- **`bin/la-evict.sh`** — emergency memory-recovery fallback for when the machine is already out of
  RAM and the terminal is unresponsive. It is **not** ordinary lifecycle management. It climbs a
  three-step ladder and **only an argument-less run advances it**: the **first run (tier 0)** sweeps
  every *unattached* (orphaned) server in one pass — dead servers are safe to evict, so there is no
  reason to tier them — and then warns which ports still have a live session attached; the **second
  run (tier 1)** evicts exactly **one** server (ranked, least-costly first) and *holds back* rather
  than take one with a live session attached; the **third and later runs (tier 2+)** are the emergency
  tier, where attached and unrecognised backends become eligible, still one server at a time.
  Escalation applies only within the window (default 600 s, `LA_EVICT_WINDOW` to change); once it
  expires the next invocation is a first run again, and `--reset` clears the saved escalation state
  and exits without evicting. `--port N` (repeatable) evicts one named port and is deliberately
  **outside** the ladder — it never advances the tier and never writes state — but N must lie inside
  the configured model-server range, so the proxy band live sessions depend on cannot be named by a
  typo. See [Safety](#safety).
- **`bin/la-reboot.sh`** — `la-evict`'s sibling for the opposite problem: a session whose backend
  **already crashed**. It restarts the *same* model on the *same* port with the *same* arguments, so
  a still-open Claude Code session reconnects on its next request — no exit, no `/resume`, no context
  replay. Recovery used to be exit → `la-evict` → new session → `/resume`, which pays for a full
  model reload *and* a context replay. This is the bandaid half of the recurring Rapid D-METAL-CAP
  OOM; the climbing-residency root cause is tracked separately.

  It is the **only** script here allowed to stop a server on the session port range, so the guards
  are strict. It acts on exactly one port (never a range). It requires positive evidence of a crash —
  a failed completion probe, a dead listener, or a Metal/OOM signature — because liveness is *not*
  the test: an OOM-refusing Rapid server answers `/v1/models` perfectly while failing every real
  request. A server that still completes a request is refused unless you pass `--force`. And the
  argv is captured from the live process *before* anything is stopped, via `KERN_PROCARGS2` rather
  than `ps` (whose space-joined output would corrupt any argument containing a space) — after the
  kill that information is gone, and re-deriving the command from current config would silently
  launch something *else* if the config changed since launch.

  Recovery also preserves the captured working directory and selected non-secret
  Python/MLX runtime settings. It reports executable failures directly and requires
  a successful Anthropic completion on the original port before reporting success;
  a model listing alone is insufficient. Authentication errors do not establish a
  crash. Old saved argv without runtime context produces an explicit warning and
  uses the calling shell's context. Fake-server tests cover a continued Anthropic
  conversation after the restart command exits; a live crash still needs its own
  outcome check.

  ```bash
  ./bin/la-reboot.sh --status            # what would be targeted, and the evidence for it
  ./bin/la-reboot.sh --dry-run           # the plan, including the exact argv
  ./bin/la-reboot.sh                     # rescue the attached session's port
  ./bin/la-reboot.sh --port 8001 --force # restart a port that still looks healthy
  ```

  With no `--port` it uses this shell's own `ANTHROPIC_BASE_URL`, else the single attached session
  port; with several candidates it refuses to guess. Exit codes: `0` rebooted, `1` nothing to reboot
  or never became ready, `2` usage, `3` refused because the server is healthy.

  **It is a bandaid on purpose.** Running out of memory is not how local inference is meant to work;
  the root cause of the climbing residency is tracked separately. Needing this often is data for that
  investigation, not a reason to automate it.
- **`bin/la-stream-render.py`** — renders a session transcript for a human watcher: thinking as
  clean paragraphs, one concise line per tool call. `local-watch.sh` uses it by default.
- **`bin/la-disk-inventory.sh`** — disk-first inventory: what's actually in your models dir, and
  whether any catalog or the registry accounts for it (catches orphans and metadata-only shells).
- **`bin/model-asset-override.sh`** — build an isolated *symlink-farm* view of a model directory so
  you can add or shadow individual non-weight assets (a missing `video_preprocessor_config.json`, a
  patched `config.json`, an upstream `chat_template.jinja`) for kilobytes instead of duplicating tens
  of gigabytes of weights.
- **`config/model-catalog.psv`** — the default download list (data, not code); your private one goes
  in `config/model-catalog.local.psv`.
- **`bin/new-local-window.sh`** — open a full local session in a new, independent Terminal window (macOS).
- **skills (8)** — two entry points, five that each own one phase of a delegation, and a fallback:
  - `free-agents` — drive local and remote free sessions & dispatch.
  - `offload-to-local` — the habit-forming trigger to delegate bulk/mechanical work to a local model to
    save cost. The plugin's main idea.
  - `compose-the-payload` — build the dispatch **material** from files on disk, so the corpus never
    enters your own context (this is the actual cost lever).
  - `brief-the-delegate` — write the **instructions**: a stateless dispatch inherits nothing, so every
    unstated ambiguity returns as a confident wrong answer.
  - `isolate-parallel-work` — before dispatching: who owns which port, and does this delegate need its
    own worktree?
  - `guard-shared-runtime` — before touching the shared venv/server, and when the server misbehaves
    (including the wedge whose health check still returns 200).
  - `verify-delegated-work` — after it returns: judge by observable outcome change, never "no error",
    and stop at the retry ceiling.
  - `continue-on-fallback` — keeps work moving on a free lane when the local model cannot serve and
    the cloud budget is spent.
- **hooks** (`hooks/hooks.json`) — SessionStart `offload-nudge.py` (paid session: free offloading is the default; free session: cross-family review only) and
  `transcript-identity.py` (marks local and free-API transcripts); UserPromptSubmit
  `session-title-prefix.py` (emoji + model on the session name); PreToolUse `guard-own-endpoint.py`
  (refuses killing the endpoint serving this session), `guard-tool-owned-state.py` (refuses
  hand-edits of Claude Code's plugin registry) and `cloud-agent-intercept.py` (opt-in replacement of
  paid subagents); Stop `local-queue-stop-hook.py` (queued-prompt recovery, off unless
  `LA_QUEUE_STOP_HOOK=1`).

## API keys — the free-tier setup helper

The remote lane needs a provider key. `csl setup-remote` (or press **`k`** in the `csl` picker, or
run `install/setup-api-keys.py`) walks you through it.

**Every provider it offers has a free tier.** That is the selection criterion for the list — the
point of this project is inference that costs nothing. Paid use is *technically* supported (a key is
a key, and if your account has credits or a paid plan the same lane works and simply bills you), but
nothing here requires it and no provider is listed *because* it is paid.

```
csl setup-remote            # interactive picker
csl setup-remote --list     # show providers and key status; makes NO API calls
csl setup-remote gemini 4   # set up specific providers, by name or number
```

What it does, and deliberately does not do:

- **Guides signup, then stores the key.** It points you at each provider's key page (`BROWSER`
  controls the opener) and takes the value in a **hidden terminal prompt** — never a command
  argument, so keys stay out of your shell history.
- **Makes no API calls at all.** Nothing is validated and no inference request is sent, so running it
  cannot consume quota. The status column says `saved / not tested` for exactly that reason: *saved
  means stored, not proven to work*.
- **Never overwrites an existing key file.** Re-running it is safe.
- **Stores keys as individual files** under `~/.api_keys` (override with `LA_API_KEYS_DIR`), one per
  provider, so a session can be handed only the key it needs — see
  [Safety](#safety) for the per-provider scoping.

The picker groups providers by what you can expect:

| Group | Providers | Note |
|---|---|---|
| **Free tier, session-ready** | Google Gemini, Groq, OpenRouter, Cloudflare Workers AI, Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel AI Gateway | Sign up and go. Some ask you to pick a model. Google Gemini is the exception for sessions: its free daily quota runs out almost at once, so use it for offload dispatches only. |
| **Optional / conditional** | SambaNova (promo credits), ModelScope (regional eligibility), Cerebras (trial — opt in with `csl remote --include-trials`), NVIDIA (account credits) | Still free to start, but gated on credits, region, or a trial. |

Quotas and rate limits are the provider's, not this project's — a key that stops working usually
means a daily allowance was reached, not a bug here.

## Acquisition catalogues (0.29.0)

`config/model-catalog.acquisitions.{rapid,gguf,omlx}.psv` list models worth fetching. Every row is
validated, and a problem is reported as `file:line` with its reason:

- exact field count;
- required alias, repo, revision, subdir, size and runtime;
- revision is `main` or a commit hash;
- size is a positive number;
- no duplicate aliases across files.

```bash
bin/read-acquisition-catalog.py --report        # or --parse for JSON; exit 1 on any problem
bin/la-catalogue-generate.py acquisitions       # same reader, as a subcommand
```

`la-model-profile.py validate` refuses a malformed catalogue it consumes. The remote **local-capable
filter** uses an **exact** catalogue match (a normalised id, never fuzzy) as evidence: an
unclassified remote model with an acquisition row is marked `potentially-local-capable`, with its
size in the reason. It stays visible, since a catalogue row says the weights *can* be fetched, not
that they are on disk. A broken catalogue changes nothing (fail-open); `LA_LC_ACQUISITION_EVIDENCE=0`
turns the evidence off. A JoyIA export saved as `foo.psv.md` is found too.

## Downloading models

### Which model to run a full local session on

**KAT-Coder V2.5 OptiQ** (`kat-coder-optiq`, about 20 GB, Rapid-MLX, `qwen` tool-call parser,
non-thinking) is the preferred model for running a whole Claude Code session locally. This comes
from day-to-day use, not a controlled benchmark.

```bash
./install/download-models.sh --select kat-coder-optiq   # download-models-system-trust.sh on a TLS-inspecting network
./bin/launch-claude-agent.sh kat-coder-optiq
```

**Qwen3.8 27B with MTP** is the operator / reasoner workhorse and also drives a session. Fetch both
`qwen38-27b-4bit` and its 0.3 GB draft head `qwen38-27b-mtp`. The registration enables multi-token
prediction (speculative decoding), and that is what makes it fast enough to drive a session.

Two artifacts, and only one of them is code:

```text
install/download-models.sh   the ENGINE    — never edit it to add a model
config/model-catalog.psv     the CATALOG   — this is where models are listed
```

**Adding, removing, or retagging a model is a one-line edit to the catalog.** The engine already
knows how to pin revisions, resume, deduplicate, select individual files out of a multi-quant GGUF
repo, and tell four acquisition states apart. You get all of that by *not* touching it.

**You do not need to download the whole catalogue.** Every row stands alone. Fetch what fits your
memory and disk, and whatever you have fills the roles it is registered for; the rest of the work
stays on your cloud model. The shipped `config/model-catalog.psv` (MLX rows):

| Alias | Size | What it is for |
|---|---:|---|
| `kat-coder-optiq` | 20 GB | the preferred full local **session** model |
| `qwen38-27b-4bit` + `qwen38-27b-mtp` | 16 + 0.3 GB | operator / reasoner (thinking), MTP-accelerated; also drives a session |
| `granite-4.2-30b` | 18 GB | the qualified local **Auto Mode classifier** (DIRECT_PASS in the 2026-10-13 campaign: 99.065% changed-prefix reuse on the pinned rapid-mlx, trimmable KVCache — `docs/classifier-campaign-2026-10-13.md`). The default for `LA_RAPID_AUTO_MODEL_DIR`, resolved via `bin/local-classifier-default.sh` with a tested fallback tier. MLX-only: CUDA machines should run blind-trust mode (no qualified CUDA classifier yet). Disk-tight alternative: a second instance of `kat-coder-optiq` as classifier — cache-blind, but its ~36s full re-prefill fits the 45s deadline. |
| `mistral-small4` | 67.5 GB | Mistral Small 4 119B, MLX build (not the GGUF), 256K context; needs about 70 GB free memory. Also the default validator (a non-Qwen second opinion). |
| `qwen36-27b-4bit` | 26 GB | the previous all-rounder, for machines that already have it |

`--group default` fetches KAT plus Qwen3.8 with its MTP head (about 36 GB). The two CUDA rows are
covered in [docs/CUDA.md](docs/CUDA.md).

```bash
install/download-models.sh --list                    # what's available, and its state on disk
install/download-models.sh --select kat-coder-optiq   # fetch one thing
install/download-models.sh --group default            # fetch a whole group
install/download-models.sh --all --dry-run            # resolve everything, write nothing
```

Your own longer list belongs in `config/model-catalog.local.psv` — gitignored, and loaded *in
addition* to the shipped defaults, so a private roster never has to fork the public one. Point at an
arbitrary file with `--catalog FILE`, or several with `$LA_MODEL_CATALOG` (`:`-separated); either
replaces the search entirely.

Catalog format is eleven `|`-separated fields; `--help` documents each one:

```text
alias|label|repo|revision|subdir|size_GB|group|status|include_patterns|runtime|hardware_target
```

`hardware_target` (`mlx`, `cuda` or `any`) was added in 0.25.0. A ten-field row from an older file
still loads and is treated as `mlx`.

Two fields earn their keep. `revision` takes a commit SHA — pin it whenever the artifact matters,
because `main` moves. `include_patterns` takes `;`-separated globs, so one entry can take exactly
`*UD-Q4_K_XL*.gguf` out of a repo holding a dozen quantizations instead of all of them.

### It won't quietly fill your disk

Before downloading, the queued selection is summed against free space on the target volume minus a
reserve (100 GB by default, or `$LA_DISK_HEADROOM_GB`). If it won't fit you get the shortfall, a
concrete `--select` subset that *does* fit, and any other mounted volume with room:

```text
  ⚠️  WILL NOT FIT — short by ~196.00 GB after reserving 100 GB.
  Option A — a subset that DOES fit (1 of 3, ~114.00 GB):
        --select glm-air-8
```

Non-interactive runs **refuse** rather than fill the disk; interactively you're offered the fitting
subset. Escape hatches: `--target DIR` (another volume), `--headroom-gb N`, `--allow-tight`.
Already-downloaded entries cost nothing and aren't counted. Catalog sizes are display estimates, so
treat this as a guard rail, not an allocation guarantee. (It is unrelated to the runtime *memory*
headroom a model needs to actually serve.)

### Three states that are easy to confuse

| | |
|---|---|
| **download list** | models that *could* be fetched — the catalog. Aspirational. |
| **on disk** | weights that exist. What you can serve today. |
| **registered** | an alias in `config.local.sh`, so a role or launcher can address it. |

They drift apart, so two tools read them from opposite ends:

```bash
bin/la-roles.sh              # registry -> disk:  "which role can I fill right now?"
bin/la-disk-inventory.sh     # disk -> registry:  "what's on my disk, and is it accounted for?"
```

`la-disk-inventory.sh` is what catches the cases nothing else will: an **ORPHAN** (weights on disk
that no catalog and no registry mentions — usually a manual download, or a retired model whose entry
was removed while its weights stayed), and a **NOPAYLOAD** directory (configs and tokenizer but no
weight file — a metadata-only shell from an aborted fetch, which looks installed and isn't). Use
`--orphans`, `--empty`, or `--du` to narrow it.

A missing `.la-download-complete` marker is not corruption. It means this engine didn't fetch it, so
completeness is simply unverified — expected for anything you downloaded by hand.

### Bash 4+

The downloader and the config library use associative arrays, so they need Bash 4 or newer. macOS
ships Bash 3.2, so the script **relaunches itself** into `/opt/homebrew/bin/bash` or
`/usr/local/bin/bash` automatically — you normally notice nothing. If neither exists it stops with a
clear message rather than a confusing shell error; `brew install bash` resolves it. Run the script
directly (it is executable and self-bootstrapping); do not `source` it into zsh.

## The overlay (public tooling, private config)

`config.local.sh` holds everything machine-specific — model directory, ports, memory budget, and the
**model registry** (`la_register` lines mapping an alias to a model dir + parser + spoof + effort).
It's gitignored, so this repo can be public without exposing your setup. The scripts fall back to
`config.example.sh` if no local config exists, so a fresh clone still runs and shows you what to edit.
Launches pass their settings per launch (`--settings`, environment variables), so `~/.claude/settings.json`
is not written by any launcher or by installing the plugin. Two explicit operations do write its `env`
block, and only their own keys: the Cloud Session Configuration screen (`csl` → `c`) and
`bin/cloud_session_env.py set …` (replace-agents, security-review model). No other plugin's config is touched.

Register your models like this (see `config.example.sh` for the full column reference):

```bash
# alias | subdir | serve | tool_parser | reasoning_parser | thinking | spoof_id | effort | [roles] | [hf_repo] | [size_gb]
la_register my-operator  Qwen3.6-27B-UD-MLX-4bit  vllm  qwen  ""  false  claude-opus-4-8  high  operator  unsloth/Qwen3.6-27B-UD-MLX-4bit  16
```

The registry is the **single source of truth**: it drives launching, the interactive installer
(`hf_repo`/`size_gb`), and role routing. The last three fields are optional and additive, so older
8-field lines keep working.

### Roles (routing without hardcoding)

Tag each model with the role(s) it can fill — `operator` (bulk/mechanical/tool-driving), `reasoner`
(reasoning first-pass), `validator` (independent review), `utility` (cheap classification). A role
may be filled by several models (your A/B choice) or none (that work stays on cloud). The offload
rules route by **role name**, never a model name, so your roster can change without touching any rule.

Resolve roles → the models you actually have on disk:

```bash
./bin/la-roles.sh          # per-role table: ● on disk / usable now, ○ registered but not downloaded
./bin/la-roles.sh operator # just the on-disk alias(es) for one role
./bin/csl roles            # same, via the csl front-end
```

## Usage

**Dispatch a sub-task to a local model (the main use).** Free, fast (sub-second to seconds),
private. Do this from anywhere, including inside your normal cloud Claude Code session:

```bash
PORT=$(./bin/local-llm-hotswap.sh my-operator | grep -o 'SUCCESS_PORT=[0-9]*' | cut -d= -f2)
curl -s http://localhost:$PORT/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"my-operator","messages":[{"role":"user","content":"..."}],"max_tokens":512}'
```
For long generations use `./bin/librarian-dispatch.py --port PORT --payload BODY.json --outdir DIR`
(SSE + stall watchdog; `--prompt`/`--model` also work; text lands in `DIR/output.txt`).

The same dispatcher drives **free remote APIs** with `--provider` instead of `--port`, e.g.
`./bin/librarian-dispatch.py --provider nvidia --model nvidia/nemotron-3-ultra-550b-a55b --prompt "…"`.
The key comes from the provider's env var or `~/.api_keys/<provider>` (passed to curl in a 0600 header
file, never on argv). **NVIDIA dispatches take a slot from the same machine-wide 40 RPM bucket every
remote session's proxy uses** (`bin/rate_limiter.py`), so any number of parallel agents queue instead of
draining your live sessions' quota, and a 429 pauses all of them. `remote-agent-dispatch.py` (and the
fallback router / `free-agent-tool.py` built on it) shares that bucket too.

**Supervise it.** Offloading pays off only when you verify the result: warm → route (`la-roles.sh`) →
decide `local:`/`cloud:` per step → dispatch → **verify against ground truth** (never trust local
output blind) → correct/re-dispatch. The `offload-to-local` skill documents this loop in full. (Landing
the change afterwards is ordinary shipping discipline, so this plugin deliberately doesn't specify it.)

**Full local session** (a local model as the session engine):

See [Which model to run a full local session on](#which-model-to-run-a-full-local-session-on).
In `csl`, `g` relaunches the model this lane last ran.

```bash
./bin/launch-claude-agent.sh my-operator                  # interactive local session
./bin/launch-claude-agent.sh qwen-3.8-thinking max        # optional effort override
./bin/csl                                                 # interactive picker (see below)
./bin/new-local-window.sh my-operator                     # open it in a NEW, independent Terminal window (macOS)
```

### Per-launch profiles for full local sessions

`launch-claude-agent.sh` owns the local routing invariants: the compatibility model id, direct
localhost endpoint, permission mode (`auto` by default from `csl`, `acceptEdits` when auto mode is
switched off), RAM preflight, server lifecycle, strict-MCP
default, and the local runtime-safety prompt. Four optional environment variables let one launch
narrow its Claude Code profile without unrestricted argument forwarding or persistent changes to
global Claude Code settings:

| Variable | Purpose |
|---|---|
| `LA_CLAUDE_SETTINGS` | Path to an additional Claude Code settings JSON file. The launcher validates it as JSON and passes it through `--settings` for this launch only. |
| `LA_CLAUDE_TOOLS` | Comma-separated built-in tool list passed through `--tools`, for example `Bash,Read,Grep,Glob,Edit,Write,Skill,AskUserQuestion`. |
| `LA_AUTO_COMPACT_WINDOW` | Per-launch auto-compaction window: `auto`, or an explicit value from `100k` through `1m`. It does not write the user-global setting. |
| `LA_AGENT_PROMPT_FILE` | Override for the model-facing local-session prompt template. Defaults to `config/local-agent-system-prompt.txt`. |

Example lean local session:

```bash
LA_CLAUDE_SETTINGS="$HOME/.claude/launch-profiles/lean-local-general.json" \
LA_CLAUDE_TOOLS="Bash,Read,Grep,Glob,Edit,Write,Skill,AskUserQuestion" \
LA_AUTO_COMPACT_WINDOW="100k" \
  ./bin/launch-claude-agent.sh kat-coder-optiq high
```

The settings file in that example is user-managed and is not shipped by this repository. It can
scope plugins, workflows, and other Claude Code settings for one launch without modifying
`~/.claude/settings.json`.

The launcher validates these controls before model startup:

- `LA_CLAUDE_SETTINGS` must name a readable file containing valid JSON.
- `LA_CLAUDE_TOOLS` must be a comma-separated built-in tool list.
- A tool may not appear in both `LA_CLAUDE_TOOLS` and `LA_DENY_TOOLS`; conflicting profiles fail
  closed instead of silently producing an ambiguous tool surface.
- `LA_AUTO_COMPACT_WINDOW` must be `auto` or resolve to 100,000–1,000,000 tokens.
- `LA_AGENT_PROMPT_FILE` must be readable; known placeholders are substituted, and any
  unresolved `__LA_*__` placeholder causes the launch to fail.

The shipped prompt template records the actual local alias and backend, explains that the Claude
model name is only a compatibility label, marks local inference as zero gateway cost, requires use
of the available tools, and protects every configured serving port from being killed by its own
session. The launcher reads the prompt as data—it never sources or executes the file—and replaces
only these placeholders:

```text
__LA_MODEL_ALIAS__
__LA_MODEL_SPOOF__
__LA_BACKEND__
__LA_CURRENT_PORT__
__LA_PORT_START__
__LA_PORT_MAX__
__LA_HOTSWAP_PATH__
```

Machine-local `LA_MEMORY_DIR` and `LA_COUNCIL_NOTE` additions are appended afterward when configured.

These controls do not change the permission-mode boundary, which is owned by the launcher and by the
`csl` auto-mode toggle (see [Auto Mode with a local classifier](#auto-mode-with-a-local-classifier-triple-toggle)),
never by argument forwarding. Enabling the `Agent` tool does not by itself change how Auto Mode's
classifier is routed or how good its verdicts are — those stay separate questions.

### The session picker (0.22.0): Home, Local and Remote

`csl` (no arguments) opens the **session picker**, a Textual UI (one-time setup:
`install/setup-session-picker.sh`). It replaces the numbered bash menus. Every screen is a list of
single-letter actions, shown in capitals but typed lowercase (Shift+letter is ignored). Digits never
launch anything, and arrow keys plus Enter work everywhere.

- **Home:** `l` Local sessions · `r` Remote free-API sessions · `c` Cloud session configuration ·
  `d` download models · `o` Lowkey dispatch chat · `k` API keys · `v` backend manager (Rapid-MLX,
  vllm-mlx, oMLX, llama.cpp, LiteLLM; the launchd update checks live in there) · `q` quit.
- **Both lanes:** `g` **Go launch** starts the model this lane last launched, without loading any
  model list. `c` **Choose model…** opens the list below the menu, grouped and collapsed, and
  loaded only when asked for. `e` effort, `o` 🌡️ temperature, `m` MCPs, `s` switch to the other
  lane, then the session toggles `a` auto-mode (`z` classifier source when it is on classifier),
  `t` telemetry and `p` queued-prompt hook. `b` goes back to Home (or `q` quits when the lane was
  opened directly).
- **Local only:** `w` watcher, `v` backend manager.
- **Remote only:** `k` API keys, `n` NVIDIA rate limiter, and `y` **Filter settings…**: `h` limited
  trials, `f` locally-runnable models, `u` 🚧 unworking models (`config/broken-nvidia-models.json`),
  `x` hidden-model report.
- **Cloud session configuration** (`c` on Home) writes Claude Code's own `settings.json` `env`, so it
  applies to ordinary cloud sessions: `x` replace subagents with free agents, `s` security-review
  model, `y` classifier source, `p` bypass permissions.

Settings carry over when you switch lanes. Effort and the last-launched model per lane are
remembered across runs in `config/session-menu.local.json`, which is private and gitignored. Every
emoji the picker draws is defined in `config/emoji.sh`.

From inside either lane you can return to Home (`b`) or jump straight across to the other lane
(`s`) — all in one process, with no restart and no state loss. Auto-mode, telemetry, the watcher,
the queued-prompt (stop) hook, and the locally-runnable filter all carry over across every switch —
the filter included, even though only the remote lane displays it. The locally-runnable filter is
deliberately **not** on Home: it only ever affects the *remote* roster, so it lives in the Remote
lane as `f`.

If this installed copy has no `config.local.sh` and fell back to the public `config.example.sh`, the
home screen also prints a warning that private models are not present in this installed copy.

### The picker: why a session is chosen differently from a dispatch

The Local lane's `c` list shows every on-disk model whose backend can drive an interactive Claude
session, grouped by family. A model launches at the lane's effort (`e` cycles it, and the picker
remembers it). This differs from dispatch
routing because the two ways of using this plugin are different decisions:

- **Dispatching local models as subagents** — the fixed roles and archetypes are the point. You route
  a task to the model whose strengths suit it, and that matters most when several agents run at once.
  Roles are what let you say "reasoner" without hardcoding a model name that changes as the roster does.
- **Running a local model as the session engine** — there is exactly *one* engine, and the
  model × effort pairing is your main lever over speed versus depth for everything you do in that
  session. A fixed list of preset pairings hides that lever; free composition exposes it.

Role bindings remain visible as metadata and continue to drive dispatch routing, but they no
longer hide unbound session models from the primary picker. Every available session model is directly
selectable, while custom effort composition remains reachable through `c`.

The watcher is **off by default**, so an ordinary selection opens only the requested session. Toggle
it with `w` in the picker, or set `CSL_WATCH=1` to opt in by default.

Auto mode starts in **blind-trust** (state 0) — cycle it with `a`, or set `CSL_AUTO_MODE_STATE` to
`0`, `1` or `2`. See [the three states](#auto-mode-with-a-local-classifier-triple-toggle) for what each
one buys and costs.

Telemetry is **off by default** — toggle it with `t`, or set `CSL_TELEMETRY=1` to keep stock
behaviour. See [A local session that is actually local](#a-local-session-that-is-actually-local).

### Genuine auto mode on the free-API lane (0.28.0)

`remote-session.sh -a` (or the picker's auto-mode toggle, pressed once) runs **real auto mode**:
`--permission-mode auto`, with Claude Code's own safety classifier answered by a **free** model. The
classifier asks for `claude-sonnet-5[1m]`, and the proxy routes it to one of two places:

- **`LA_CLASSIFIER_MODEL=nvidia:nvidia/nemotron-3-super-120b-a12b`** (recommended): a dedicated
  entry. Measured 2026-10-09: stage 1 took 3.4 s and stage 2 took 8.4 s, and
  `git push --force … && rm -rf ~/…` was **blocked** (`[Git Destructive]`).
- **Unset**: the session model answers via the `claude-*` catch-all. It works (Ultra blocked
  `curl … | sh` as `[Containment Escape]`), but stage 2 took 66–85 s against Claude Code's
  120 s cap.

**Fail-closed was observed live.** A classifier call that timed out under load became "classifier
unavailable, denying with retry guidance": the action was refused, never silently allowed. The
default stays blind-trust (state 0), because unattended runs must not stall on a slow classifier.
The local-session classifier lane (granite-4.2-30b by default; see the campaign report) is documented below.

### Auto Mode with a local classifier (triple-toggle)

Claude Code's Auto Mode judges each consequential tool call with a **separate safety classifier**,
independent of the model you picked for the session. On a cloud-routed session that classifier is a
cloud request, which is why a rate-limit or a spent budget used to take Auto Mode away exactly when
you had fallen back to local work. Because a local session points `ANTHROPIC_BASE_URL` at your own
server, the classifier request follows it and is answered locally, for free.

`csl` launches with a **triple-toggle** for auto mode state (press `a` to cycle):

| State | Key | Behavior | Use case |
|---|---|---|---|
| **0 = blind-trust** (DEFAULT) | `a` | `--permission-mode bypassPermissions` plus a destructive-command deny list. No classifier runs at all (measured: 0 classifier calls). | Default — no waiting on a classifier server; unattended runs never stall. |
| **1 = classifier** | `a` | `--permission-mode auto`; the classifier request is answered by the local dual-identity server. Default **granite-4.2-30b** (DIRECT_PASS in the 2026-10-13 campaign), resolved via `bin/local-classifier-default.sh` with a tested fallback tier. | The real auto mode — requires a classifier-capable local model. |
| **2 = off** | `a` | `--permission-mode acceptEdits`. No auto-mode behaviour. | Turn auto mode off entirely. |

Environment override: `CSL_AUTO_MODE_STATE=0|1|2` sets the initial state directly.
The launcher maps state→env: `0` → `LA_AUTO_MODE=1 LA_BLIND_AUTO=1`, `1` →
`LA_AUTO_MODE=1 LA_BLIND_AUTO=0`, `2` → `LA_AUTO_MODE=0`.

⚠️ **Blind-trust is the default, and it is not auto mode.** Nothing judges the calls: everything
is allowed except what the deny list blocks. Choose it knowingly. The classifier description below
applies to **state 1 only** — toggle to it with `a`, or set `CSL_AUTO_MODE_STATE=1`.

**How the classifier model is chosen (since 0.32.0).** `bin/local-classifier-default.sh`
resolves the default: granite-4.2-30b first, then the campaign-tested latency tier
(kat-coder-optiq, qwen-3.6-35b-a3b, ornith-1.5-35b, nemotron35-lightning, agentworld-35b) —
the first model whose weights are installed wins. If NOTHING classifier-capable is installed,
launch prints a warning with the install command (`install/download-models.sh --select
granite-4.2-30b`), recommends blind-trust, and hides native auto mode from the Shift+Tab cycle
entirely (`"disableAutoMode": "disable"` in the generated settings): bypass stays reachable, but
you cannot cycle into a mode whose classifier is dead. CUDA machines: the qualification campaign
was MLX-only — run blind-trust mode there. Classifier settings stay per-lane (gateway cloud /
local / free-API remote). The blind-trust deny list is shared across lanes; `gh release create`
is on it only if you re-enable the toggle in csl (off by default since 0.32.0).

**Verified 2026-09-05**, on `kat-coder-optiq` served by Rapid-MLX: the local server logged the
classifier arriving under its own model identity and being answered by the loaded engine —

```text
[REQUEST] POST /v1/messages  model='claude-sonnet-5'  max_tokens=64  stream=False  tools=0
Anthropic /v1/messages: request model='claude-sonnet-5' served by loaded engine='claude-opus-5'
```

— while the session held no connection to the cloud gateway at all, and the judged action then ran.
Note the classifier asks for `claude-sonnet-5` even though the session runs as `claude-opus-5`: your
spoof list has to cover it, which it does automatically because Rapid-MLX answers any requested id
with the loaded engine.

What it costs, so the default is chosen knowingly:

- **A free slot is mandatory.** The classifier is a *second, concurrent* request. With
  `--max-num-seqs=1` it queues behind the turn that triggered it and times out — the session then
  refuses the action with `temporarily unavailable`, which is the fail-closed path, not a hang. The
  default is now `--max-num-seqs=2` for exactly this reason, and auto mode forces a fresh server
  start so a server left over from a 1-slot launch is replaced rather than reused.
- **A judged call is slow.** The classifier prompt carries the transcript: measured **35,154 prompt
  tokens for an 8-token verdict, 27.4 s** on a cold prompt cache.
- **Most calls are never judged.** Read-only tools bypass the classifier entirely, and so does
  anything already covered by a `permissions.allow` rule. If you have accumulated a long allowlist,
  ordinary work will not slow down at all — but do not read "nothing happened" as "the classifier is
  not running". Test it with something *outside* your allowlist.

Two honest limits. Routing being local says nothing about **verdict quality** — a local model is not
Anthropic's classifier, and its judgements have not been scored against it, so keep human approval
for genuinely high-risk actions. And the airtight offline proof (block the gateway, then re-run) is
still outstanding; the evidence above is a positive observation of the request arriving locally, not
a demonstration that no fallback path exists.

### RAM usage out-of-memory protection: auto-compact instead of a dead session (0.38.0)

A local model that runs out of RAM mid-turn kills the session (oMLX aborts the request; other
backends wedge or get OOM-killed). The out-of-memory protection (internally `memguard`; named the
memory guard before 0.42.0) compacts the session before that happens:

- **Watchdog** (`bin/la-memguard.py`, one per machine). It reads the same wired-vs-Metal-cap figure as
  the status line. Above the soft threshold (default 85%) it types `/compact` into the **idle**
  local session with the largest footprint. The launchers start claude inside a private tmux
  session for this (`bin/la-memguard-run.sh`); an unsent draft is stashed and restored. Without
  tmux, or with `LA_MEMGUARD_TMUX=0`, the session is registered but cannot be compacted for you.
- **Aborted-turn recovery.** If a turn still dies of memory, a `StopFailure` hook compacts and sends
  `continue` (`LA_MEMGUARD_AUTO_CONTINUE=0` turns that off).
- **KV cache precision per model**: `session_menu_state.py kv-set <alias> auto|int8|int4`. Rapid-MLX
  gets `--kv-cache-dtype`, llama.cpp `-ctk/-ctv`. Architectures Rapid-MLX rejects for explicit
  quantization (Gemma 3/4, GPT-OSS, DeepSeek V3+, Kimi K2.5) are refused **before** launch, with
  the fix command. Lanes with no flag say so rather than ignoring the setting.
- **RAM-calibrated auto-compact window** (off by default, `LA_MEMGUARD_WINDOW=1`): the launcher
  derives the token count at which the model's KV would reach the soft threshold and sets the
  auto-compact window to it. An explicit `LA_AUTO_COMPACT_WINDOW` always wins.

Configure it in the picker (`csl`, **RAM usage out-of-memory protection** screen, `u` on the Local
screen; it affects local sessions only): on/off, tmux,
soft/hard thresholds, quiet window, auto-continue. An environment variable you export
(`LA_MEMGUARD_SOFT_PCT`, `LA_MEMGUARD_HARD_PCT`, `LA_MEMGUARD_QUIET_S`, …) beats the saved setting.
`LA_MEMGUARD=0` skips the guard for one launch.

### A local session that is actually local

Routing inference to your own machine does not, by itself, stop Claude Code talking to the internet.
Measured on 2026-09-05: a session whose every inference request provably went to `127.0.0.1` still
held two outbound HTTPS sockets — one to Anthropic (`160.79.104.10`) and one to Google/Statsig
(`34.149.66.165`). Neither is inference and neither is the gateway, so neither shows up in a cost
check or a routing check. They are simply outbound calls nobody asked for.

The launcher therefore defaults `LA_TELEMETRY=0`, which exports:

```text
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1   # the umbrella the CLI documents in its own messages
DISABLE_TELEMETRY=1                          # Statsig
DISABLE_ERROR_REPORTING=1                    # Sentry
DISABLE_AUTOUPDATER=1                        # update polling
```

The umbrella alone is enough on the current build; the three granular vars are set as well so that a
build which ever narrows the umbrella still gets the specific suppressions instead of quietly
resuming. The session prints its posture at startup (`🔇 Telemetry: OFF`), because a suppression you
cannot see is indistinguishable from one that has silently stopped working.

Verified by outcome, not by assumption: with this on, a session that had completed a real turn
(including a classifier call) held **zero** non-loopback sockets, against two before — while a
control cloud session measured in the same way still showed three, so the check can still detect
sockets when they exist. Confirmed from inside the session as well, since `ps eww` truncates the
environment on macOS and is not evidence either way here.

**What this does not cover.** It silences *Claude Code's* nonessential traffic. It does not touch
anything **you** have configured: if your `settings.json` runs hooks that call out — this repo's
maintainer has four `joyia ping` hooks on session/input/task/stop — those still fire, in separate
short-lived processes, which also means a per-process socket check will not see them. Turning your
own instrumentation off is your call, not the launcher's.

Trade-off, stated because it is real: the umbrella also disables `/design-sync`, Projects, and the
CLI's update check. All three are irrelevant to a local session — `DesignSync` is already withheld by
`LA_DENY_TOOLS`, and backend updates come from the weekly launchd check rather than the CLI's poller.

### MCP tools in blind-trust auto mode

When running in blind-trust auto mode (the default, `AUTO_MODE_STATE=0`), the sandbox `enabled: true`
setting controls the write boundary — but the classifier is bypassed entirely, so every tool call
that isn't explicitly allowlisted would prompt. To make MCP tools work without prompts in blind-trust:

**Local sessions** (`csl` / `launch-claude-agent.sh`):
- `--enable-mcp` flag or `LA_ENABLE_MCP=1`
- `csl` Local lane: press `m` to toggle MCPs (blind-trust auto-mode only)

**Remote free-API sessions** (`csl remote` / `remote-session.sh`):
- `--enable-mcp` flag or `LA_REMOTE_ENABLE_MCP=1`
- `csl` Remote lane: press `m` to toggle MCPs (works in ALL auto-mode states including blind-trust)

**How it works:** A master allowlist (`~/.claude/launch-profiles/allowlist-master.json`; when absent,
the shipped `config/launch-profiles/allowlist-master.example.json`, a modest non-destructive set,
is used and the launcher says so) is merged with the active lean profile (`lean-local-general.json` or
`lean-cloud-general.json`) at runtime. The profiles add the `mcp__*` wildcard, which admits **any**
MCP tool (current: blender, davinci-resolve, joyia, adobe-for-creativity, filesystem, github — and
any future ones). This replaces the previous inline 400+ line allowlist duplicated in each launcher.

**Default is OFF:** MCPs are disabled by default in free-API sessions to conserve provider quota.
Enable them explicitly when you need them.

### What to expect from a local session

Being able to run Claude Code on a local model at all is the win here, and it is a real one: your own
hardware, no per-token cost, and work that keeps going when you are rate-limited, capped, or offline.
It is worth being straight about what it is, though — this is not a drop-in replacement for the cloud
models. It is a deliberate workaround that enables a mode of working Claude Code was not designed for,
and the honest trade is that a locally-served model has less headroom than a frontier one and each
turn spends real time re-reading a large prompt.

Within those bounds it works better than you might expect, and noticeably better since the streaming
timeout fix (long turns used to be killed mid-generation; see [The fork patches](#the-fork-patches-required-for-direct-routing)).
Two practical notes: dispatch is the faster path when the work can be handed off as a self-contained
task, and effort is the dial that decides how much of a turn goes into reasoning — which is why the
picker puts it in your hands rather than fixing it per preset.

**Convenience (optional):** `./install/setup-shortcuts.sh` puts `csl` on your PATH and adds
`local-*` shell aliases (`local-operator`, `local-menu`, `local-window`, …). Idempotent; edits only
a fenced block in your shell rc.

### Timeouts (three guards, not two)

A local model routinely takes minutes on a single turn, and emits **nothing at all** while it prefills
a large prompt. Claude Code's defaults assume a fast cloud endpoint, so the launcher relaxes three
separate limits — all three matter, and the third is the one that is easy to miss:

| Variable | What it bounds |
| --- | --- |
| `API_TIMEOUT_MS` | overall per-request cap (default 600000 = 10 min) |
| `API_FORCE_IDLE_TIMEOUT=0` | the "no bytes arrived yet" abort on a slow first token |
| `CLAUDE_ENABLE_STREAM_WATCHDOG=0` | the streaming idle watchdog added in CLI **2.1.196**, on by default for all providers, which aborts **and retries** after 5 minutes without stream events |

The third was diagnosed on 2026-08-19: with only the first two set, turns died at almost exactly
**600s having emitted 2 chunks** (two 5-minute windows — one abort, one retry), while turns that got
as far as emitting tokens survived past 689s. Because every killed turn still added to the context,
the next prefill was longer, so a large-context session could never recover. Disabling the watchdog is
bounded rather than reckless: `API_TIMEOUT_MS` still caps the request and vllm-mlx's own `--timeout`
still caps it server-side.

## Monitoring a running local session

`csl` starts a watcher only when you turn it on (`w`, or `CSL_WATCH=1`). To attach one by hand — including to a session that
has only just started and has not taken its first turn yet:

```bash
./bin/local-watch.sh --attach <launcher-pid>   # follows one session: engine health now, mutations from turn 1
```


Local sessions run direct (no proxy), so there's **no reasoning tee** — you watch a running local
session (or several) via two per-session sources. `bin/local-watch.sh` discovers what's running and
prints ready-to-run monitor commands; feed them to your watcher (a terminal, or Claude Code's Monitor
tool). It handles **multiple concurrent sessions** — each local server has its own port log, each
session its own transcript.

```bash
./bin/local-watch.sh            # DEFAULT: open a watcher window per running session
./bin/local-watch.sh --list     # print-only: sessions, ports, transcripts + monitor commands
./bin/local-watch.sh --open     # explicit form of the default
./bin/local-watch.sh --health   # just the per-port vllm-log health monitors
./bin/local-watch.sh --mutations# just the transcript mutation/thinking monitors
```

Running it bare **starts the watchers** — one window per running session, so watching two at once needs
no manual pairing of port to transcript. Watching a session is the reason you ran the script, so it does
not require remembering a flag; with nothing running there is nothing to watch, and it falls back to the
listing instead of doing nothing. `--list` forces print-only. Which transcript belongs to which
session is **recorded, not guessed** — since 0.9.0 the launcher itself writes the path to a sidecar
(`~/.claude/logs/local-agents-session-<launcher-pid>.transcript`) as soon as the session takes its first
turn, and the watcher just reads it.

Three inference-based approaches were tried and all fail, which is why the launcher records it instead:
`lsof` on the `claude` child finds nothing (Claude Code appends and CLOSES the `.jsonl`, so it holds no
lasting handle); newest-mtime picks whichever session wrote last, which from a supervising cloud session
is usually that session's *own* transcript; and matching the vllm log's user-message preview matches the
*watcher*, because watching a log copies the watched prompts into the watcher's transcript. Claude Code
exposes no session id on the process and has no `--session-id` flag for a new session. The launcher is
the one uncontaminated observer: it knows its own start instant and cwd. When no sidecar exists yet the
watcher says so plainly and lists unverified candidates rather than claiming a correlation it does not
have.

- **Health / turns / timeouts / stalls** → `~/.claude/logs/vllm_<PORT>.log` (one server = one log, so N
  concurrent servers are unambiguous). Filter to `[REQUEST]`/`CLEANUP done`/timeout/error/disconnect;
  drop the 5-second `disconnect_guard poll` heartbeats.
- **★ Where a long turn actually is, for a model with thinking OFF** → the same log's KV-cache lines,
  now included in the health filter. `cache MISS`/`prefilling N new tokens` means it is still *reading*
  the prompt and has produced nothing yet; `N tokens in Ts (X tok/s)` is the only place the real
  generation rate appears. For an operator alias this is the substitute for watching reasoning: you
  cannot see it think, but you can see which phase it is in and whether it is moving.
- **Mutations + reasoning** → the session's native transcript `.jsonl`: `tool_use` records
  (Bash/Edit/Write/git/waypoints…) and `type:"thinking"` blocks.
- **Post-hoc chain-of-thought** → `bin/thinking-log.py --latest --local-only` (extracts the reasoning
  blocks; `--local-only` = unsigned = the local model's).

**How live is it?** Two separate delays: the model's **generation** latency (minutes for a big local
turn — inherent; reasoning doesn't exist until it's generated, and a non-streaming turn delivers it
whole at the end) and the transcript **flush** lag (turn completes → record on disk). Measured on
Claude Code 2.1.205, the flush lag for the reasoning-carrying `assistant` records is **~0.2–2.6s** —
so once a turn finishes, its thinking/tools are readable within a few seconds. Re-check after a Claude
Code upgrade (it regressed to 30+min on 2.1.197 once):

```bash
# print how many seconds after each new record's timestamp it lands on disk (run during activity)
python3 - <<'PY'
import time,os,json,datetime,glob
p=max(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")),key=os.path.getmtime)
f=open(p); f.seek(0,2)
end=time.time()+120
while time.time()<end:
    ln=f.readline()
    if not ln: time.sleep(0.1); continue
    try: r=json.loads(ln); t=datetime.datetime.fromisoformat(r["timestamp"].replace("Z","+00:00")).timestamp()
    except: continue
    print(f"{r.get('type','?'):10} flush≈{time.time()-t:4.1f}s", flush=True)
PY
```

**Limitation vs the retired proxy era:** the old in-path proxy could livestream reasoning
*token-by-token*; the transcript gives it per-turn (after ~sub-3s flush), not mid-generation. If you
need true live thinking, the non-invasive path is a vllm-mlx **server-side token log** (a fork patch
that tees streamed tokens/reasoning to a per-request file) — restores the live stream without a
request-path proxy that would re-break native transcripts.

## Status line: seeing the memory ceiling before it kills the session

`bin/la-statusline-segment.sh` supplies ONE extra segment to a Claude Code status line,
reporting local-inference memory pressure. It exists because an MLX session dies by OOM
against a Metal wired-memory ceiling well below installed RAM (~103.9 GB on a 128 GB
M4 Max) and, before this, there was no live reading of that anywhere — the ceiling was
invisible until the session was already dead.

- **Measures WIRED memory against the CAP, not installed RAM.** 74 GB is a comfortable 58%
  of 128 GB but 71% of the ceiling that actually refuses work; the reassuring number is the
  wrong one. `ps` RSS is not used at all — it understates MLX pressure by roughly 7x,
  because weights are Metal allocations rather than ordinary resident pages. The derivation
  is the same one `la-ram-preflight.sh` uses, reused rather than re-invented.
- **Gated on the ENDPOINT** (`ANTHROPIC_BASE_URL` at loopback), never on `CLAUDE_IS_LOCAL` —
  that flag is exported by the launcher and leaks into a later gateway `claude` in the same
  shell, which would report a local instrument for a PAID session.
- **`level` carries the thresholds** (warn 70%, crit 90% by default), so the consuming
  renderer selects a colour without re-deriving what "close to the ceiling" means.
- **Silence is a valid answer.** Prints nothing and still exits 0 when there is nothing
  meaningful to say — not a local session, not macOS, no reading available. A status line
  must never be the reason a prompt breaks.
- Environment: `LA_STATUSLINE_CAP_GB` (machine-specific ceiling), `LA_STATUSLINE_RAM_CMD`
  (override the reader; exists so tests never depend on real machine memory),
  `LA_STATUSLINE_WARN_PCT`, `LA_STATUSLINE_CRIT_PCT`.

**Why it is a separate script, and the layering that matters:** a status-line renderer knows
how to lay out a line; it has no business knowing what a Metal cap is. So `free-agents`
MEASURES and classifies, and any renderer merely places the result. The `cost-tracker`
plugin consumes this over a stable contract — no arguments, one line of JSON, exit 0 — and
owns none of the domain knowledge. Either plugin can be updated independently, and if
`free-agents` is absent the segment is simply absent.

```console
$ ANTHROPIC_BASE_URL=http://localhost:8000 bin/la-statusline-segment.sh
{"label":"ram","text":"74.0/103.9G 71%","level":"warn"}
```

Rendered, it lands on line 1 beside the context figure, colour escalating as the ceiling
approaches:

```
JoyIA · Opus 5 (1M) · ctx 34% · ram 74.0/103.9G 71%
```

## The fork patches (required for direct routing)

`install/vllm-mlx-local-fork-patches.patch` applies three fixes not in upstream `v0.4.0`:

1. **`chat_template_safety.py`** — coalesce all system-role messages into one leading block. Without
   it, Claude Code's top-level system + per-turn `<system-reminder>` land as interleaved system turns
   and the chat template 500s with *"System message must be at the beginning."* (Upstream PR #646 is
   still open — keep this patch.)
2. **`api/utils.py`** — treat `model_type: llama4` as text-only so Llama-4 uses the text route instead
   of a VLM text-extraction path it can't fit.
3. **`engine/simple.py`** — the MLLM KV-cache probe accepts `ArraysCache`, so hybrid models (e.g.
   Qwen3.6's `qwen3_5` arch) can use the system-prefix snapshot cache (turn-2+ prefill hits the cache
   instead of re-prefilling the whole prompt).

Re-apply after any `vllm-mlx` reinstall/upgrade: `git -C <vllm-mlx> apply vllm-mlx-local-fork-patches.patch`.

## Safety

- **Self-preservation.** The launcher tells the local model it *is* the server on its port and must
  never kill processes on the serving ports — a local session that "frees ports to start fresh" would
  kill its own runtime. `hotswap` only ever lands on a *free* port and never kills a healthy model.
- **Auto Mode, with its classifier routed locally — and a limit worth reading.** Auto Mode judges
  consequential calls with a *separate* classifier; because a local session points
  `ANTHROPIC_BASE_URL` at your own server, that request is served locally and for free
  ([details and evidence](#auto-mode-with-a-local-classifier-triple-toggle)). That is state 1. `csl`
  **defaults to state 0, blind-trust, where no classifier runs**; state 2 falls back to `acceptEdits`
  (edits auto-apply; other tools prompt). But routing is
  not equivalence: **a local model is not Anthropic's safety classifier**, its verdicts have not been
  scored against it, and a long `permissions.allow` list means many calls are never judged at all. For
  genuinely high-risk actions, keep human approval. The failure mode is at least the safe one — no
  verdict means no execution, not a silent allow.
- **No reasoning leakage.** The nudge forbids emitting `<think>` markup / raw chain-of-thought into
  visible output or tool arguments.
- **`la-evict` is a last resort, and a conservative one.** The escape hatch for when the machine is already out of RAM and the normal lifecycle is unresponsive: it never bulk-kills servers with a live session attached, never touches a server with a live session on its first pass, and escalates only one step per repeat call inside the window — so a healthy stack is left alone. (Its first run does sweep every *unattached* server at once: those are dead, so there is nothing to conserve.) Deliberately standalone (no `config-lib.sh`, no registry): a fallback that runs while the stack is sick must not depend on the stack.

## Testing

The full suite runs in PARALLEL by default (pytest-xdist, 2026-10-20: 692+ tests in ~75 s vs ~250 s serial):

```sh
~/.local/bin/pytest -q tests/ -p no:cacheprovider -n auto     # full suite, all cores
```

- **Serial fallback** — when pytest-xdist is not installed (or you suspect an order-dependence bug), drop
  `-n auto` and run the same command; a parallel-only failure is a real defect to report, not a reason to
  quietly re-serialize.
- **Picker UI tests** run in their own venv (Textual pilot):
  `~/.local/share/free-agents/picker-venv/bin/python -m pytest -q tests/test_new_features.py`.
- **LiteLLM proxy tests** run under the LiteLLM interpreter so they see the installed LiteLLM:
  `~/.local/pipx/venvs/litellm/bin/python tests/test_la_proxy_hooks.py tests/test_anthropic_route.py
  tests/test_litellm_contract.py` (the contract test pins the LiteLLM behaviours this release depends on —
  run it after every LiteLLM upgrade).
- **Shell gates**: `bash tests/lint.sh` (shellcheck) and `bash tests/test_every_script_has_help.sh`.
- Ad-hoc single test files: `python3 tests/<file>.py < /dev/null` (a suite that reads stdin needs the closed
  stdin; see `tests/test_estimate_model_footprint.py`).

## Diagnostics

| Script | What it checks |
|--------|----------------|
| `direct-route-acceptance.py --port <p>` | Validates the direct-routing fork patches on a live server: system-message normalization (all shapes → 200), oversized-`max_tokens` rejection, and system-prefix KV-cache hit (turn-2 reuse). Run after install to confirm the patches took. |
| `tournament-dispatch.py` | Stage-A smoke test per model: load, identity, plain reply, structured tool call, streaming, multi-turn. (Edit its `MODELS` list for your models.) |
| `cancellation-matrix.py --port <p>` | Does a client disconnect/timeout retire the generation (freeing the single slot) rather than block the next request? |
| `check-tool-roundtrip.py` | After a local session, verifies the native tool round-trip (call → result → answer → transcript) with no markup leak. |
| `auto-mode-probe.sh` | Whether Claude Code's Auto Mode classifier request reaches the local endpoint (and where it routes). |
| `local-inference-readonly-inventory.zsh` | Read-only, offline snapshot of the whole stack — venv, backends, model dirs, servers, ports, fork patches. Writes one timestamped report directory under `~/.claude/reports` and nothing else: no sudo, no network (it forces `HF_HUB_OFFLINE`/`PIP_NO_INDEX`), no weights loaded, no service started or stopped, and it refuses to overwrite an existing report path. Reach for it when you need to describe the stack's state without changing it. `--dry-run` lists what it would collect and writes nothing; `--help` shows options. Full reference: [`docs/READONLY_INVENTORY.md`](docs/READONLY_INVENTORY.md). |

## Troubleshooting

- **Model won't load / `hotswap` times out** — check the log tail it prints; confirm the model dir
  exists and matches your `la_register` subdir; ensure enough free RAM.
- **HF downloads hang** — some networks black-hole the CDN's IPv6 and `hf` sits in `SYN_SENT`
  at 0 bytes. Force IPv4 with the shim that ships here, or switch networks:
  ```sh
  PYTHONPATH="$CLAUDE_PLUGIN_ROOT/install/hf-ipv4:$PYTHONPATH" ./install/download-models.sh ...
  ```
  `install/hf-ipv4/sitecustomize.py` is picked up automatically by any Python on that path and
  pins address resolution to IPv4; it affects only processes you launch with it.
- **A session edits a long file as if it only saw the top** (a CHANGELOG or README loses most of
  its entries) — two causes, neither visible from the command's exit code. Claude Code caps Bash
  output: a `cat` of a large file returns a short preview plus "Output too large … saved to …".
  And if you run [rtk](https://github.com/rtk-ai/rtk) as a PreToolUse hook, it condenses some
  commands: `git show <rev>:<file>` came back as ~150 lines plus "(+1800 lines)". Every lane's
  prompt now tells the model to read long files with Read and check it reached the last line.
  Free-API sessions (`csl remote`) also switch rtk's rewriting off (`bin/rtk-lane-shim/rtk`;
  `LA_RTK_IN_FREE_API=1` keeps it on). For local and cloud sessions, where rtk's saving is
  worth having, `install/setup-rtk-excludes.sh` adds the commands that lost content
  (`config/rtk/exclude-commands.txt`) to rtk's `exclude_commands`, keeping any you set yourself.
  Both do nothing if rtk isn't installed.
- **Direct request 500s "System message must be at the beginning"** — the fork patch isn't applied;
  re-apply `vllm-mlx-local-fork-patches.patch`.
- **Interactive turns are slow** — the cost is *prefill*, not generation, and the prompt is mostly
  tool definitions. Measured on this stack (2026-08-17, the real `claude -p` payload intercepted):

  | component | chars | ~tokens | share |
  |---|---:|---:|---:|
  | built-in tool definitions (31) | 90,734 | 24.5k | 36% |
  | MCP tool definitions (68 across 3 servers) | 82,824 | 22.4k | 33% |
  | system prompt | 11,194 | 3.0k | 4% |
  | per-turn context re-injection (project instructions, memory index, hook banners) | 73,153 | 19.8k | 29% |
  | **total first-turn prefill** | **254,045** | **68.7k** | |

  The single heaviest definition is `Workflow` at 21,865 chars (~5.9k tokens) — more than the whole
  system prompt, for a tool a local model will never drive. The launcher therefore ships two levers,
  and their combined effect is measured, not estimated:

  | configuration | tool defs | request | ~tokens |
  |---|---:|---:|---:|
  | default (what a plain `claude` sends) | 99 | 254,045 chars | 68.7k |
  | `--strict-mcp-config` (`LA_STRICT_MCP`) | 28 | 173,729 chars | 47.0k |
  | **+ `--disallowedTools` (`LA_DENY_TOOLS`) — the shipped default** | **15** | **83,903 chars** | **22.7k** |

  That is **−67% of the request** and −86% of the tool weight. The key asymmetry: `--disallowedTools`
  removes a definition from the *payload*, while `--allowedTools` only filters what may *run* and leaves
  every definition in the prompt (measured: restricting to 8 tools left the payload unchanged). So the
  deny-list is a real lever and the allow-list is not.

  What remains is the ~13–20k-token context block, re-prefilled on **every** turn even on a cache hit
  (`cache HIT: reusing 62223 cached tokens` followed by `prefilling 19357 new tokens`). Dispatch sends a
  prompt three orders of magnitude smaller, which is why it feels instant — prefer it for focused work.
- **Use only tools present in the current session.** Claude Code’s built-in tool set varies by
  client version and launch profile. In the live Claude Code 2.1.246 acceptance test, `Glob`,
  `Grep`, `Read`, `Write`, `Edit`, and `Bash` all worked in a local Qwen3.8 Rapid session.
  `LA_CLAUDE_TOOLS` can select the intended built-in surface for one launch, while
  `LA_DENY_TOOLS` removes definitions that should not enter the local prompt. Do not copy an old
  tool inventory into the model-facing prompt as permanent truth; inspect the actual session tool
  list instead.
- **A tool is missing in a local session** — `LA_STRICT_MCP` defaults to `true`, so MCP-provided tools
  are deliberately absent (the launcher prints this at startup). Set `LA_STRICT_MCP=false` to restore
  them, accepting ~23k more prefill tokens per cache miss.
- **`--allowedTools` does not make a session faster** — it filters what may run, not what is *sent*;
  the definitions still occupy the prompt. Measured: restricting to 8 tools left the payload unchanged.
- **★ Every turn seems to take two or three times longer than the model needs** — check the port's log
  for this signature:

  ```
  [disconnect_guard] START poll=0.5s heartbeat=5.0s timeout=300s
  [disconnect_guard] TIMEOUT after 300s, 2 chunks, 60 heartbeats
  [REQUEST] POST /v1/messages (anthropic) stream=False ...   <- same turn, retried
  Anthropic messages: 202 tokens in 220.76s (0.9 tok/s)
  ```

  `vllm-mlx serve` caps every request at **300s by default** and its streaming guard enforces that
  *server-side*, so relaxing Claude Code's client timeouts does nothing: at ~0.9 tok/s an ordinary turn
  exceeds 5 minutes, the server kills the stream, and the client re-runs the whole turn non-streamed.
  You pay 300 wasted seconds before the attempt that actually answers. `hotswap` now passes
  `--timeout $LA_SERVER_TIMEOUT_S` (default 1800). **Serve flags are fixed at launch**, so a server
  started before this change keeps the 300s cap — hotswap detects that on the reuse path and tells you
  to restart it rather than silently handing back a warm-but-broken server.
- **"Request timed out" mid-session / a heavy turn never completes** — Claude Code's `API_TIMEOUT_MS`
  defaults to 600000 (10 min) and it also aborts a stream after 5 min with no bytes; a local model doing
  a big prefill over many tools can exceed both. The launcher relaxes them for local sessions
  (`API_TIMEOUT_MS` = `LA_API_TIMEOUT_MS`, default 30 min; `API_FORCE_IDLE_TIMEOUT=0`). Tune
  `LA_API_TIMEOUT_MS` in your config. See the prefill table above for what is actually consuming the
  time — the `LA_STRICT_MCP` default already removes the largest avoidable slice.

## Savings ledger — what did offloading actually avoid paying for?

Every local dispatch is work a metered cloud model would otherwise have billed you for.
`bin/savings-ledger.py` records each one and values the counterfactual:

```sh
savings-ledger.py record --model qwen-3.6-operator --in 9123 --out 412
savings-ledger.py report --today          # also --week, --month, --since YYYY-MM-DD, --json
savings-ledger.py rollup                  # regenerate the derived rollup
savings-ledger.py rates                   # the rate table and its as-of date
```

`bin/librarian-dispatch.py` appends an event automatically on every successful dispatch
(`--no-ledger` opts out, `--priced-against MODEL` picks the comparison rate). Recording is
best-effort and can never fail a dispatch that already produced output.

**Store** — `~/.claude/local-agents/` (override with `$LOCAL_AGENTS_LEDGER_DIR`), outside the
plugin so updates never touch your data:

- `savings.jsonl` — append-only, one JSON object per dispatch: timestamp, session, local
  model, in/out tokens, the cloud model and rates it was priced against, and the saving.
- `rollup.json` — **derived**, grouped by `(session, UTC day)`. Regenerate it; never hand-edit
  it. It carries `source_events` so you can tell when it's stale.

### Two things it refuses to do

Both are the same principle: a number that looks real but isn't is worse than a stated gap.

**It never prices an unknown model at zero.** A model missing from the rate table records
`saved_usd: null` with `rate_source: "unknown"`, and `report` counts those out loud. Zero
claims the work was free; null says it wasn't valued. (This is not hypothetical — an earlier
tool here priced `claude-opus-5[1m]` at `0.00` because its table was keyed on bare names.
Model ids are normalized before lookup: context-window suffixes `[1m]`, provider prefixes
`anthropic.`, deployment suffixes `-fast`, and dated snapshots all resolve to the catalog name.)

**It never reports a missing prompt-token count as "saved nothing".** Measured on this
backend: it returns `prompt_tokens: 0` even for a ~21,600-character prompt. Offload savings
live mostly on the *input* side — large corpus in, short answer out — so a silent zero would
understate the total by orders of magnitude. Such events are flagged
`input_tokens_source: "unavailable"` and keep the prompt's raw **character** count (a fact,
not an estimate), so `report` says the figure is understated and a later pass can price them
properly:

```text
local offload (all time): 1 dispatch(es), 0 in / 2 out, saved $0.00
  ⚠ 1 event(s) counted the OUTPUT side only covering 9,232 unmeasured prompt characters —
    the server reported no prompt-token count, so the real saving is materially higher
```

### Rates

The built-in table is a **dated snapshot** (`savings-ledger.py rates` prints its as-of date,
and every event records which table priced it). Verify against current vendor pricing before
trusting a large total, and use `--rates-file` to supply your own rather than editing the
plugin.

## License

MIT © Heiko Brantsch

---

## Lowkey CLI

`bin/lowkey-cli.py` is a universal local model dispatcher that works independently of any AI
client — use it from a plain terminal, a shell script, a Claude Code session, another agent CLI, or a
CI pipeline. No cloud account or API key is needed for the dispatched work.

It is also a terminal-native companion interface for interactive local-agent work outside Claude Code.
It reuses the same model registry, hotswap layer, and librarian dispatcher as the rest of this repository.
The current dispatcher version is `0.10.0`. It is functional and in daily use, with known rough edges
tracked in `docs/lowkey-cli/CHANGELOG.md` — expect fixes in subsequent releases rather than
a frozen surface.

### Shell aliases

Run `install/setup-shortcuts.sh` — it writes a fenced, idempotent block for you. The aliases it
installs name **roles**, not models:

```zsh
# Sessions — <role> resolves to whichever model fills it on disk right now.
alias local-operator="/path/to/free-agents/bin/launch-claude-agent.sh operator"
alias local-fast="/path/to/free-agents/bin/launch-claude-agent.sh operator medium"
alias local-xhigh="/path/to/free-agents/bin/launch-claude-agent.sh operator xhigh"
alias local-thinking="/path/to/free-agents/bin/launch-claude-agent.sh reasoner"
alias local-validator="/path/to/free-agents/bin/launch-claude-agent.sh validator"
alias local-menu="/path/to/free-agents/bin/csl"        # session picker (Home → Local/Remote)
alias local-window="/path/to/free-agents/bin/new-local-window.sh"
# Dispatch — one alias, any role or model.
alias lk="/path/to/free-agents/bin/lowkey-cli.py"
alias lowkey="/path/to/free-agents/bin/lowkey-cli.py"
# Inspect.
alias local-roles="/path/to/free-agents/bin/la-roles.sh"
alias local-disk="/path/to/free-agents/bin/la-disk-inventory.sh"
alias local-logs="tail -f $HOME/.claude/logs/*_[0-9][0-9][0-9][0-9].log"
```

**Why roles and not one alias per model.** A hardcoded model name in a shortcut freezes a roster
fact, and it goes stale *silently* — the alias keeps launching successfully, just on last month's
model, with nothing to signal the drift. `operator`, `reasoner`, `validator` and `utility` are
resolved against the on-disk role bindings at invocation time, so the roster can move underneath
them. Pass an explicit alias whenever you do want one specific model; an alias always wins over a
role, and `local-roles` shows which model currently answers to each.

Or create a symlink for shell-agnostic access:

```bash
ln -sf /path/to/free-agents/bin/lowkey-cli.py ~/.local/bin/lowkey
```

### Conversation mode

Start an interactive terminal conversation:

```bash
lowkey --convo
```

Conversation mode supports structured history, model-specific labels,
compact/verbose/quiet progress, multiline `:paste` / `:end` input, rolling
summaries, `:file PATH` attachments, and resumable named sessions.

See [`docs/lowkey-cli/README.md`](docs/lowkey-cli/README.md)
for the complete dispatcher reference and development notes.

### Usage

```bash
# By ROLE — whichever model fills it on disk right now:
lk --model operator --prompt "What does this module do?" --files src/main.py

# The reasoner, for planning and architectural review:
lk --model reasoner --prompt "Review this plan and identify architectural risks"

# A specific model by alias, when you want exactly that one:
lk --model devstral-2-123b --prompt "Refactor the following into clean functions" \
  --files utils.py helpers.py --max-tokens 2048

# Piping output to a file:
lk --model operator --prompt "Summarize test coverage gaps" --files tests/ > summary.txt

# Inside any agent CLI or Claude Code session (prefix ! to run locally, zero cloud quota):
! lk --model validator --prompt "First-pass review of this diff" --files my_changes.patch
```

### Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--model` | `qwen-3.8-operator` | Role name (`operator`/`reasoner`/`validator`/`utility`) or model alias from your `config.local.sh` registry |
| `--prompt` | *(required)* | Task prompt text |
| `--files` | *(none)* | One or more file paths to inline as context |
| `--max-tokens` | `4096` | Max tokens the model should generate |

## Copilot BYOK — experimental, not a supported feature

`bin/copilot_local_proxy.py`, `bin/launch-copilot-agent.sh` and
`bin/test-copilot-byok-integration.sh` are an **unfinished proof of concept**, documented here only
so that nobody who stumbles across them mistakes them for a working feature.

The aim was to let GitHub Copilot fall back to a local model once cloud quota runs out. **That does
not work yet.** What is proven is the streaming wire format; what is not proven is Copilot actually
running on a local engine. In its present state it overlaps `lowkey-cli.py` above, so it adds
nothing you cannot already do — and it leans on undocumented `COPILOT_PROVIDER_*` environment
variables that may stop working without warning.

It lives in this repository rather than its own because it reuses the same model registry, hotswap
layer and launcher conventions as everything else here; splitting it out would mean maintaining a
second copy of that infrastructure and letting the two drift. Nothing in the plugin points at it, it
receives no support, and it may be removed. Details and the honest status:
[`docs/local-copilot-byok/COPILOT-BYOK-README.md`](docs/local-copilot-byok/COPILOT-BYOK-README.md).

### oMLX Auto Mode backend

For long local Auto Mode sessions, oMLX is available as an opt-in backend:

```bash
LA_AUTO_MODE_RUNTIME=omlx \
LA_OMLX_CLASSIFIER_MODEL_DIR=/path/to/a/dense-classifier \
  ./bin/launch-claude-agent.sh <session-alias>
```

The launcher exposes the selected session model under its configured Claude
compatibility ID and exposes a separate dense model as `claude-sonnet-5`.
Both are served by one isolated oMLX endpoint, without a request proxy.

Why this exists: Rapid correctly routed classifier requests locally, but the
tested dense Rapid path reused only 118 prefix tokens. In the equivalent oMLX
test, a 13,856-token growing request reused 12,544 tokens, and a 15,004-token
request reused 13,824. Measured latency fell from 75.8 seconds cold to 9.7 and
8.6 seconds on the growing requests.

The cache is persistent under
`~/.cache/local-agents/omlx-auto` by default. Set
`LA_AUTO_MODE_RUNTIME=rapid` to retain the existing Rapid path.
