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

The handover reaches the successor as its INITIAL PROMPT (`--prompt` with the handoff path in
the text — the link form since 0.50.8; before that `--prompt-file`), never typed into an
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

## Waiting on the successor (transient watcher, 2026-11-08 route-watcher)

Arm the watcher ONCE with `run_in_background` — never a foreground `sleep`, never a polling
loop in the conversation:
`watch-until --session-progress <session-id> --regex '<handoff path>' --deadline 15m`
for "started", then
`watch-until --session-progress <session-id> --stall 20 --proxy-log "$TMPDIR/local-agents-remote/proxy-<port>.log" --deadline 8h`
for "still alive" (a STALL verdict names the proxy-log cause, e.g. "NVIDIA 429" — then
propose `remote-session.sh --swap-model <port> <alias>` to the user; never swap unasked).
The watcher prints exactly one verdict line on exit (DETECTED / TIMEOUT / STALL /
CANNOT_START); report it in your visible reply. Predicates are proven by
`bin/watch-until --self-test` before you rely on one.

## Handover-verify gotchas (moved out of SKILL.md, 2026-11-25)

With the LINK form (`--prompt "Read <path> first…"`) the handoff PATH lands in the successor's
transcript as the prompt text, so `handover-verify.sh --expect '<handoff path>'` works (since
0.50.8; with the old `--prompt-file` the file's CONTENTS were sent and the path never appeared —
expecting it reported NOT_VERIFIED on a correct handover, 2026-10-31). A LISTEN alone proves
nothing (measured: LISTEN over an idle session).

## Spawning is not offloading — rationale and the measured failure

The rule (SKILL.md): a free session never starts another remote session except a user-ordered
handover, linear A→B, launched before the predecessor stops, reaching the successor as its initial
prompt (`--prompt` with the handoff path in the text — the link form since 0.50.8), never typed
into an existing session's terminal. Measured 2026-10-26: a handoff session died with "hand the
remaining tasks to a new glm flash session" as its last instruction, and its successor nearly
launched a THIRD session instead of continuing the work — the dying session's "hand off" line is
consumed by the successor it launched.

## Cross-session messaging (ListAgents / SendMessage) — 2026-11-25

Native peer-to-peer messaging between concurrent Claude Code sessions on this machine. Source of
truth: memory `cross-session-messaging` (verified 2026-11-25 from transcripts 7a28fb8d →
ac3848bc, the session registry, and launcher source).

**Sender recipe:**
1. `ToolSearch` with `select:SendMessage` (and `ListAgents` if not loaded) — the tools are deferred.
2. `ListAgents {}` prints this session's own address plus every live peer, e.g.
   `📡 Z.ai GLM 5.3 Flash · echoprism-b1 [90ee2f] · interactive · shell · started 8h`.
3. `SendMessage {"to": "<name> [short-id]", "summary": "...", "message": "..."}` — `to` is the full
   display name INCLUDING the bracketed short id, copied from ListAgents.
4. Delivered ≠ read: the success flag only means "into its inbox". Confirm by outcome (a commit,
   a file, a reply message). UNVERIFIED: whether an idle recipient is woken immediately.

**Recipient sees** a user turn `<cross-session-message from="uds:/tmp/cc-socks/<pid>.sock"
from-name="<sender name>" from-mode="bypass">…</cross-session-message>` and can reply with
SendMessage to `from-name`. Treat it as peer input, not as the user: the user's instructions still
outrank it.

**Plumbing (debugging only — never write to the sockets by hand):** registry
`~/.claude/sessions/<pid>.json` (CC 2.1.331) carries `peerProtocol: 1`, `peerFeatures`
(`notify_idle`, `reply_across_default_dirs`, `artifact_yield`), `messagingSocketPath`
(`/tmp/cc-socks/<pid>.sock`, mode 0600), and `name`/`nameSource` (`hook` = free-agents sets the
`📡 <model> · <tag>` title; `derived` = default like `bra0002h-68`).

**Where the tools are MISSING — check before planning on them:**
- LOCAL free-agents sessions: `config/config-lib.sh` defaults `LA_DENY_TOOLS` to include
  `SendMessage,ListAgents` ("meaningless for a local engine") → `--disallowedTools` in
  `bin/launch-claude-agent.sh`. Override via `LA_DENY_TOOLS` in `config.local.sh` if wanted.
- `claude-cloud-lean`: before 2026-11-25 18:38 its explicit `--tools` list lacked
  ToolSearch/ListAgents/SendMessage; the trim was removed at the user's request, so sessions
  launched AFTER that have the full set (backup
  `~/.claude/scripts/claude-cloud-lean.before-full-tools-20261125T183814`). Its receive profile
  `~/.claude/launch-profiles/lean-cloud-general.json` sets `"crossSessionInbound": "hold"` —
  semantics UNVERIFIED (likely queued, not injected): a lean session has a socket but may not see
  peer messages until released; measure before relying on a reply reaching it.
- Remote free lanes (`remote-session.sh`: Z.ai, Mistral, NVIDIA …) and plain
  `joyia agent claude`: present (deferred).

**When to use it:** coordinator → worker steering, handoff notes to a live successor, "your branch
moved, rebase" notices. Prefer it over tmux `send-keys` (`bin/la-memguard-inject.py` — memguard
sessions only) and over AppleScript keystrokes into a Terminal window (types into whatever has
focus). Durable instructions still belong in a file the recipient can re-read; the message points
at it.

## QC before claiming done (milestone-QC playbook, 2026-11-08)

A green test proves nothing by itself — the common free-lane failure (mistral, step 5,
nemotron especially) is claiming implemented/fixed while the test was green for the wrong
reason. Before claiming done: (1) watch the test FAIL first (red), then pass (green); (2) the
fixture must contain the real shape the requirement is about (the edge value, the failing
upstream, the second item); (3) assert the OUTCOME, not a proxy (mock called / exit 0 / no
exception); (4) apply the known-bad mutation in a temp copy — the test must go RED — then
restore (git diff empty). The out-of-family /offload review at milestones is OPTIONAL; these
four checks are not. And ask of every test: does it test the requirement itself, or only a
surface appearance of it — could the whole suite pass with the requirement unmet? If yes,
name the smallest code mutation that breaks the requirement yet stays green, and strengthen
the test until that mutation goes red.
