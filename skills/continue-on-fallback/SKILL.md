---
name: continue-on-fallback
description: 'Use WHEN work must keep moving but the current model is unavailable — the cloud budget is exhausted, the local model is down, or a session is dying and needs to hand off. Triggered by a budget wall, a dead local endpoint, or the need to preserve in-flight work for another agent. Do NOT use for a normal in-budget turn, for choosing between equally-available models, or for verifying already-produced output (that is verify-delegated-work).'
---

# continue-on-fallback — keep the work moving past a dead lane

You are mid-work and the lane you were running on just stopped: the cloud budget
is spent, the local MLX endpoint died, or the context is too large to continue.
The goal is to **keep producing** on whatever lane is actually available — local
first, then an explicitly-approved remote free allowance — and, if you must stop,
to leave a handoff that another agent can resume exactly where you stopped.

## Decide the routing mode from the situation, not from habit

Pick the mode that matches what actually stopped. Local is free and private;
remote is the emergency, and it is your data leaving the machine.

- **Budget spent, local MLX alive** → `local-first-free`. Local does the work for
  $0; a remote free tier is the backup only if local fails for a clean reason.
- **Budget spent, local MLX also dead** → `remote-free-only` (a remote free
  allowance is the only thing left that isn't spending a dollar).
- **You will genuinely stop and resume later** → build a handoff (below) *before*
  the context is too large to carry the state.

Default to the least-permissive mode that still makes progress. Do not reach for
`include-trials` or `research-emergency` on a routine task — those touch
non-renewing or unpredictable allowances and are for a research box whose only
goal is to keep output flowing.

## Remote is a privacy event — make it visible, never silent

The router and dispatch print a loud `⚠ REMOTE provider=… model=… key=…` banner
**before** the payload transmits. If you see that banner, your words are going to
a third party. Two hard gates:

- **Files never go remote unless approved.** Pass `--allow-remote-files` only when
  the user's intent clearly includes sending those specific files. A text prompt
  goes out freely; a file is a bigger privacy step and must be a deliberate choice.
- **Confirm the remote key exists** with `remote-provider-doctor.py <provider>`
  (read-only, no quota) before relying on a remote lane — a free-tier model list
  changes and a hardcoded model id may already be gone.

## The failover is automatic and policy-bound — don't second-guess it

`agent-fallback.py` already retries/fails-over **only** on quota, transient,
timeout-before-output, or model-unavailable. It never retries on auth, malformed,
policy refusal, or a partial stream. If it reports a partial output, **keep that
partial output** — it is real, recoverable work — and do not ask it to retry the
same request. If it reports an auth failure, stop and tell the user the key is
bad; do not keep hammering a dead key.

## When you must stop: build the handoff

```sh
bin/agent-handoff.py --repo <worktree> \
  --task "<the verbatim task, so the next agent has the goal>" \
  --verify "<what counts as done — the check you will run to confirm>" \
  --title "<short name>"
```

This writes a deterministic directory (HANDOFF.md + manifest with per-file
SHA-256 + git status/diff/recent + the task and verification texts). It collects
**only** git-derived state and those two texts — never secrets, git-ignored
files, home files, or transcripts. Pass the resulting directory to the next agent.
Tell the user where it is and what the resume step is.

## The command, shaped

```sh
# Budget spent, local alive:
bin/agent-fallback.py --mode local-first-free --prompt "$TASK" \
  [--files … --allow-remote-files] [--max-attempts 2] [--json-out fallback.json]

# Dry-run first if you are unsure what it will touch:
bin/agent-fallback.py --mode local-first-free --prompt "$TASK" --dry-run
```

The final answer comes back on **stdout** (so `> answer.txt` works); the routing
ledger, the REMOTE banner, and pass/fail go to stderr.