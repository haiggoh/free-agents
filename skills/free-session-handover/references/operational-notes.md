# Operational notes (moved out of SKILL.md to stay inside the word budget)

Full text of the sections SKILL.md points at.

## Proxy "Connection refused"

A proxy's "Connection refused" is usually an upstream overload or a killed sibling, not a network
fault. Never "fix" it by restarting the proxy your own session runs on.

## Spawning is not offloading

A dispatch returns and ends. A spawned session is an independent agent with its own goal loop
and no handle back. Sessions started with "follow the handoff" believe they are the executor, so
two of them fight over one worktree. **A free session never starts another remote session.** The
one exception is a user-ordered handover: a single successor, linear (A→B, never A→B and C),
launched before its predecessor stops.

The handover reaches the successor as its INITIAL PROMPT (`--prompt-file`), never typed into an
existing session's terminal: typing it mid-session stacks a new executor's goal loop onto a live
one, and the typed text lands in whatever context the session happens to hold (measured
2026-10-25).

## A stalled session: swap its model, don't restart it

A session that stalls on upstream 429s for hours (NVIDIA throttling ONE model; measured
2026-10-20: GLM Flash 429'd all night while DeepSeek on the same key served 46/46) does not
need a restart — its proxy can be relaunched on another model, same port:

    remote-session.sh --swap-model <port> <alias> [--force]

The session's context lives in Claude Code, not the proxy, so it resumes on its next prompt.
The command pre-flights the new model on a scratch port, backs up and rewrites the SAME
config path, keeps the `.owner` file (so `--stop` and stale cleanup still recognize the
proxy), and reverts to the previous model on any failure. It REFUSES a session whose log
answered within the last 10 minutes (`LA_SWAP_HEALTHY_WINDOW`) — a swap would drop an
in-flight turn — unless `--force`. On success it prints which terminal the session sits in.
The last step is always manual: press Enter (or send any message) in that idle session to
resume it.

Sessions started on 0.40.0+ carry their own fallback chain and normally never need this; the
command rescues pre-0.40.0 sessions and chains that failed entirely.

## While a long sub-task runs

Silence from a briefed implementer is normal. Check once (its transcript tail and
`git status --porcelain`), then do useful interim work: prepare the next phase, reconcile records
the finished phases made stale, or finish leftover docs in files the running agent will not
touch. Never run mutation tests or full suites against the same tree concurrently.
