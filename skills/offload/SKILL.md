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

Self-check before you claim you offloaded anything: **name the process that ran the tokens.** If
the answer is not "a localhost port" or "a free provider's API", it was not free.

## Default routing on a PAID session — decide the route BEFORE the call

1. **Self-contained text work** (review, analysis, extraction, drafting, a cross-check) → a
   stateless dispatch through `bin/free-agent-tool.py`. Attach the inputs as `"files"`: the model
   cannot read the disk. It walks `config/dispatch-preferences.json` and fails over on its own.
2. **Work that must read files or run commands** → a free SESSION (`remote-session.sh <alias>`,
   or `csl` locally). For OFFLOADING (you stay in this session) run that session HEADLESS in the
   background. For a HANDOVER (a successor session the user will type into) always launch
   INTERACTIVE in its own Terminal window — see `free-session-handover`; a headless launch is never
   correct for a handover (incident 2026-10-31).
3. **A paid subagent** only with a stated reason: security-critical work, the final verification of
   a free result, or a free lane that is measured as failing right now.

**Announce the model and route BEFORE every offload** ("offloading to Gemini 3.8 Flash, stateless
dispatch"). The tool prints the model it tried on stderr and returns `model_used` and `route`;
repeat those in your reply. Session models are NOT the dispatch order — see `free-session-handover`.

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
