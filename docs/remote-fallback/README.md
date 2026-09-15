# Remote emergency-fallback lane

Keep agent work moving **after Claude Code's daily budget is exhausted**, without
spending real money and without silently leaking your files. The local MLX stack
is the first lane; explicitly-approved **remote free allowances** are the
emergency lane behind it.

This is an **additive** feature. The existing local dispatch path is untouched
(the router composes with `local-agent-dispatch.py` through a subprocess). When
no remote key is configured — the default — this entire lane is inert and the
router behaves as pure `local-only`.

## The privacy and cost rules, in one paragraph

Remote is **opt-in**. Nothing in the code makes a network call to a non-local
host unless you explicitly pick a remote routing mode **and** a key is set.
Every transmission prints a loud `⚠ REMOTE` banner (provider, model, key
reference, destination) **before** the payload leaves the machine. Files are
refused for remote dispatch unless you pass `--allow-remote-files`. The router
never silently falls through to a **trial** or **paid** tier — paid and
consumer-web tiers appear in no routing mode.

## Components

| File | Role |
|---|---|
| `bin/remote_provider_core.py` | The single source of provider truth: the 7 providers, their tiers, the `Result` record, and `classify()`. Import-only. |
| `bin/remote_http.py` | Transport only. OpenAI-compatible SSE client on stdlib `http.client`; captures status, `Retry-After`, usage, and cut-vs-clean stream. |
| `bin/remote-agent-dispatch.py` | The remote lane CLI. Builds the body, prints the REMOTE banner, streams, returns a structured `Result`. |
| `bin/remote-provider-doctor.py` | Read-only connectivity/key check (`GET /models`). Never sends a prompt, never burns quota. |
| `bin/agent-fallback.py` | **The router.** Applies a routing mode, drives local-then-remote, enforces the failover policy and retry ceiling. |
| `bin/agent-handoff.py` | Builds a deterministic continuation directory (git state + task + verification) for another agent to resume from. Excludes secrets, ignored files, home files, transcripts. |
| `config/remote-providers.example.json` | Documentation of each provider's env var, tier, and where to get a key. Not loaded (providers are defined in code). |

## Enable a provider

Set the env var for the provider you want; the others stay disabled:

```sh
export GEMINI_API_KEY=…        # primary emergency lane (renewing_free)
# export GROQ_API_KEY=…        # later phase
```

Check what's actually alive (read-only, no quota):

```sh
bin/remote-provider-doctor.py            # every configured provider
bin/remote-provider-doctor.py gemini     # just Gemini
```

## Routing modes

```sh
# The privacy default — local MLX only, no remote contact:
bin/agent-fallback.py --mode local-only --prompt "..."

# Normal emergency — local first, then remote RENEWING_FREE on a clean failure:
bin/agent-fallback.py --mode local-first-free --prompt "..."

# Only these free remote providers (skips local):
bin/agent-fallback.py --mode remote-free-only --remote-provider gemini --prompt "..."

# Local first, then renewing-free AND trials (never paid):
bin/agent-fallback.py --mode include-trials --prompt "..."
```

See `bin/agent-fallback.py --help` for the full flag set (`--dry-run` plans the
routing without sending anything; `--max-attempts` sets the per-lane retry
ceiling; `--json-out` writes the structured routing record).

## The failover policy

Fall over to the next lane/provider **only** on: `quota`, `transient`,
`timeout` (before output), `model_unavailable`. **Never** on: `auth`,
`malformed`, `policy`, or a `partial` stream. A partial stream is **preserved**
(whatever text made it to disk stays there) but is **not retried**. `Retry-After`
is honoured when the provider sends one. This is encoded once, in
`remote_provider_core.Result.retriable`, and re-checked in the router — no
caller can drift from the policy.

## Continuing across a stop: the handoff

When work must stop, build a continuation directory:

```sh
bin/agent-handoff.py --repo ~/ClaudeWorkspace/myproject \
  --task "Finish the retry ceiling in agent-fallback.py" \
  --verify "run tests/remote/test_router.py; all pass; git diff clean" \
  --title "remote-fallback MVP"
```

It writes `HANDOFF.md`, `manifest.json` (per-file SHA-256), `git-status.txt`,
`git-diff.txt` (secret-shapes scrubbed), `git-diff-stat.txt`,
`git-recent.txt`, `task.txt`, `verify.txt`. Pass that directory to the next
agent; the integrity manifest lets it verify nothing was altered in transit.

## MVP scope and what's not in it

Gemini is the one provider with a **runnable** remote dispatch in this phase.
The other six are declared (so the router and doctor can name them and report
"declared, not implemented") and get their dispatch paths in a later phase.
The plan deliberately does **not** refactor `local-agent-dispatch.py` or
`librarian-dispatch.py` — they are composed, not rewritten. Nothing here commits,
pushes, publishes, or enables a provider on its own: enabling is a separate,
explicit step.

## The remote-session.sh alternative

For a full interactive Claude Code session using remote free APIs (as opposed to
dispatch-only sub-tasks), see the `bin/remote-session.sh` script. This provides
a drop-in replacement for Anthropic's paid gateway with:

- Interactive session support with full tool use
- Provider selection including NVIDIA (preferred), Gemini, Groq, and others
- Interactive flags matching `csl` (`-w` watcher, `-a` auto-mode, `-t` telemetry,
  `-c` choose-effort, `-i` install-keys)
- Session banner showing selected model like local sessions
- NVIDIA prioritization due to generous rate limits and no known daily quota
- Support for models like NVIDIA Nemotron 3 Super, Kimi K3, and more

See `bin/remote-session.sh --help` for usage details.