# Remote API sessions

Launch a **full Claude Code session** on Gemini, Groq, NVIDIA, OpenRouter,
Cloudflare Workers AI, or Cerebras. Usage goes to the selected provider, with
its own quota and billing, rather than the Anthropic gateway budget.

Add keys with `csl setup-remote` (or press `i` in `csl`). The guided
[key setup](setup.md) opens official provider pages on request and accepts hidden
paste input. Existing credentials are kept; no API tests run during setup.

```bash
csl                     # then press  r   → the remote roster
csl remote              # same picker, even without local models installed
csl remote gemini-flash  # retain the Gemini 3.6 Flash lane
csl remote gemini-3.8-flash # select Gemini 3.8 Flash independently
csl remote --include-trials # include Cerebras in the picker
bin/remote-session.sh                 # the roster directly
bin/remote-session.sh gemini-flash    # launch a named agent
bin/remote-session.sh --list          # roster + which credentials are present
bin/remote-session.sh --verify groq-oss120   # live credential + model-id check
bin/remote-session.sh --dry-run gemini-flash # print the plan, start nothing
bin/remote-session.sh --stop          # stop a proxy this script started
```

Any other arguments pass straight through to `claude`, so
`remote-session.sh gemini-flash -p '…' --allowedTools Read` works as expected.

## Why a translating proxy is required

Claude Code speaks **only** Anthropic `/v1/messages`. Every free provider we hold
a key for speaks **OpenAI `/chat/completions`** — none of them is Anthropic-
compatible. llama.cpp and Ollama cannot bridge the gap either: they are inference
servers, not forwarding proxies.

[LiteLLM](https://github.com/BerriAI/litellm) does expose a real `/v1/messages`
endpoint in front of an OpenAI-style backend, so `remote-session.sh` starts it on
a free port in the `4141-4151` band, maps the spoofed Claude model ids onto the
chosen remote model, and points the session at it — structurally identical to how
a local session points at Rapid-MLX on `8000-8010`.

Install (once): `pipx install 'litellm[proxy]' && pipx inject litellm truststore`.

## A separate list, on purpose

Remote agents live in `config/remote-agents.sh`, **not** in the local model
roster. A remote agent runs on a third party's hardware, bills against a
quota rather than free local compute, and sends your prompts off the machine.
Those are different decisions from picking a local model, so they get their own
menu instead of being interleaved.

## Model ids rot — always verify

Pinned ids go stale silently and the failure looks like a broken lane:

* Gemini retired `gemini-2.5-flash` for new keys while this repo still pinned it.
* Three of four first-draft Groq/NVIDIA ids did not exist at all.
* `moonshotai/kimi-k3` and `z-ai/glm-5.3-flash` **are listed** in NVIDIA's
  catalogue but return empty bodies — catalogue presence is not qualification.

The expanded roster was checked against provider catalogs on 2026-09-14 without
spending generation tokens. Newly added entries are marked **catalog only** in
the roster notes; successful tool calls or full sessions are not implied.
`--verify` rechecks catalog membership on demand and makes no generation request.
A public catalog may not authenticate the supplied key. Catalog membership does
not establish remaining free quota, tool behavior, or generation permission.

Gemini 3.6 and 3.8 have independent selections using the same Gemini key; neither
automatically falls back to the other. The `-thinking` variants retain provider
default thinking; the other variants disable it. Groq adds Qwen 3.6/3.8 alongside
GPT-OSS. NVIDIA adds Nemotron 3 Ultra alongside the existing Super and GPT-OSS lanes.

`openrouter-free` now uses `openrouter/free`, rather than `openrouter/auto`, which
could select paid models. Two explicit `:free` tool-capable catalog choices are
also listed. Cloudflare uses its account-specific OpenAI-compatible endpoint
and has GPT-OSS 20B and Qwen 3.8 options. Cerebras uses current GPT-OSS 120B and
Qwen 3.8 IDs; the old `cerebras-legacy` alias redirects with a notice to
`cerebras-oss120`. Cerebras retains the existing `--include-trials` opt-in until
the account is requalified. NVIDIA/Cloudflare quota and billing remain marked
unknown. Tier labels do not enforce an account spending cap.

`--verify` uses `curl` rather than Python for the catalogue call because curl uses
the system trust store, which survives the corporate TLS-inspecting proxy. For the
Python side (LiteLLM itself) the repo's existing `install/hf-ipv4` truststore shim
is put on `PYTHONPATH`.

## Briefing a non-native model

The remote models are not native Claude Code models. Unbriefed, they shell out to
`cat`/`sed` instead of using `Read`/`Edit`, and they will happily hand-edit a
plugin's private state file. `config/remote-agent-system-prompt.txt` is the remote
counterpart to `config/local-agent-system-prompt.txt` and states explicitly:
prefer the native tools, treat skills and plugins as harness invocations rather
than shell commands, read a plugin's own docs before driving it, and never edit
`~/.claude/waypoints.json` by hand — use `waypoints.py`.

Placeholders (`__LA_REMOTE_*__`) are filled at launch; if any survives, the
launch **aborts** rather than shipping literal placeholder text to the model.

## Credentials

One file per provider under `~/.api_keys/<provider>` (override with
`LA_API_KEYS_DIR`). `bin/remote-keys.sh` resolves them:

```bash
bin/remote-keys.sh --list             # provider → env var → configured/missing
bin/remote-keys.sh --check gemini     # exit 0 if usable
bin/remote-keys.sh --names cloudflare # the env var names this provider needs
```

It refuses symlinked or non-user-owned credential files, never accepts a secret on
argv, and loads **only the selected provider's** variable into the proxy
environment. The directory is treated as a private store, never sourced as a shell
file.

Cloudflare requires both `cloudflare` (API token) and `cloudflare-account-id`
(32-character account ID). The token must authorize Workers AI on that account.
The additional `cloudflare-workers-ai` file is not loaded implicitly; the existing
`cloudflare` token was sufficient for the catalog check. GitHub Models has no roster
entry while its dedicated `github-models` credential is absent. ElevenLabs is an
audio API, not a Claude Code conversation engine.

Offline regression tests: `python3 tests/test_remote_session.py -v`. These cover
all roster routes, proxy configs, catalog response/error shapes, credential
handling, trial opt-in, and direct `csl remote` access without local model config.

## Privacy

A remote session sends prompts, file excerpts, and tool results to the provider.
The launch banner says so every time. Telemetry is disabled
(`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`) so a third party is not also
receiving harness reporting, and the session is marked with
`CLAUDE_IS_REMOTE_API=true` (distinct from `CLAUDE_IS_LOCAL`).

For anything confidential, use a local session instead.
