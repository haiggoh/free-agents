# Design: Cloud screen completion and the token-profile submenu (next release after 0.42.0)

Date: 2026-10-23. Status: design, awaiting the maintainer's approval. Source of the decisions: the
maintainer's answers in the 2026-10-23 session, recorded on waypoint `csl-cloud-session`, and the
plan file `~/.claude/plans/Csl menu in free agents needs submenus for configuring the profiles ….md`.
0.42.0 already shipped the small parts (Classifier row removed from the Cloud screen, Security
Review shown only while its plugin is active, memory guard renamed and moved, one-family dispatch
fallback). This document covers what is left.

Marking: **[decided]** = the maintainer's decision. **[my call]** = a choice made while writing this,
for the maintainer to check. **[verify]** = a fact the builder must establish before building on it.

---

## 1. Intent

csl (the session picker) is where a free-agents user configures how their sessions behave. Two of
its promises are not kept today:

1. **The Cloud Session Configuration screen** says it holds settings that change cloud sessions
   (native Anthropic login or an LLM gateway such as JoyIA). An audit on 2026-10-23 found only two of
   its four rows did that; 0.42.0 removed or fixed the misleading ones. What remains is to make the
   screen *complete*: per-path control over which subagents run on free models, and a way to get
   bypass-permissions mode in cloud sessions.
2. **Token-saving profiles exist but are invisible.** The maintainer's private launchers pass lean
   settings profiles and flags that cut a session's prefill a lot, but csl exposes none of it, except
   one MCP on/off toggle on the free lanes. A fresh plain `joyia agent claude` session measured
   **286,955 tokens of context and $0.81** after one trivial command, with **397 MCP tools loaded**
   (magnific alone 222). Users need to see what each lever costs and turn it down from the menu.

Success: a new user opens csl, understands within seconds what each cloud setting does and what each
token lever saves, and every row does exactly what its label says on the lane it names.

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. Subagent routing on paid cloud sessions is configurable **per delegation path**, deterministic,
  default **free first, paid only after free options are honestly exhausted**.
- G2. Bypass permissions can be made the **default permission mode** for cloud sessions from the Cloud
  screen, with a cost explanation shown only to users for whom it is true.
- G3. A token-profile submenu: per lane, categories with levels, and a live, **measured** tally of the
  expected prefill / per-turn tokens with the percentage each choice saves.
- G4. The existing MCP toggle moves into that submenu (no duplicate).

**Non-goals**
- Rerouting the cloud auto-mode classifier to a local or free model (separate future work; the
  Classifier row returns to the Cloud screen only when that works).
- Changing how quality routing works: dispatch preferences already order models (Gemini 3.8 Flash
  first) [decided]; this menu does not add quality logic.
- Editing the maintainer's private launcher scripts (`~/.claude/scripts/claude-cloud-lean` etc.).

**Anti-goals (would make it a failure even if it technically works)**
- A row whose label claims an effect it does not have on that lane (the defect 0.42.0 started fixing).
- Any setting that silently spends gateway money from a free session. In particular: no option to
  use "the official classifier for free" on free inference; server-side classifier checks only ride
  inside Anthropic model requests, so on a free lane they would be billed own-classifier calls [decided].
- A cost claim shown to a user it is not true for (the gateway info box must be gated on evidence).
- Overriding an organization policy that disables bypass mode.
- Tests that touch the user's real `~/.claude` (the 0.42.0 build hit this once; the suite now has a
  tripwire, `tests/conftest.py`).

## 3. Verified facts the design rests on

- **Bypass in the Shift+Tab cycle** (docs: code.claude.com/docs/en/permission-modes): bypass joins the
  cycle only with `--permission-mode bypassPermissions`, `--dangerously-skip-permissions`,
  `--allow-dangerously-skip-permissions`, or `permissions.defaultMode: "bypassPermissions"` in user,
  `--settings` or managed settings. **No settings key makes it available without also starting in it**;
  only the `--allow-` flag does, and JoyIA 0.28.0's option parser rejects that flag ("Unknown option")
  while accepting `--dangerously-skip-permissions`. `skipDangerousModePermissionPrompt` only skips the
  warning dialog.
- **No org policy reaches JoyIA sessions**: `/status` reads "Managed settings (remote): not fetched —
  not available with a custom ANTHROPIC_BASE_URL"; no managed-settings file exists on disk.
- **Settings precedence**: managed > `--settings` (command line) > project local > shared project > user
  (`~/.claude/settings.json`). `permissions.defaultMode` values `auto`/`bypassPermissions` are honoured
  only from user, `--settings` or managed settings, not from project files.
- **Classifier billing** (docs: /auto-mode-classifier-billing): server-side checks inside the session's
  own model requests are free; Pro/Max/Team never see the "isn't eligible" notice. A gateway that drops
  the `safeguards` request field or the `safeguard_results` response field forces Claude Code's own
  classifier requests, billed as token usage. `/status` → "Auto mode server: Enabled/Disabled".
  `CLAUDE_CODE_AUTO_MODE_MODEL` (≥ 2.1.302) picks the classifier model; `CLAUDE_CODE_AUTO_MODE_SERVER=0`
  stops asking the server. If the provider cannot serve the classifier model, auto mode denies every
  checked action.
- **JoyIA eligibility is unresolved**: in an auto-mode JoyIA session, after an unallowlisted `curl`, the
  row read `Disabled`, yet the session's usage listed only the main model and no notice appeared. Two
  hand tests were inconclusive (the first command never reached the classifier: `wc`/`date` are among
  the maintainer's 1,001 allow rules). Every "isn't eligible" notice in the transcripts so far came
  from FREE sessions (requests through our localhost proxy).
- **Subagent interception today**: `hooks/hooks.json` intercepts only `PreToolUse` on `Agent`
  (`hooks/cloud-agent-intercept.py`). Workflow / ultracode runs are not intercepted.
- **Where the lean levers live**: the maintainer's `claude-cloud-lean` script runs
  `joyia agent claude --strict-mcp-config --tools "<11 tools>" --settings
  ~/.claude/launch-profiles/lean-cloud-general.json --permission-mode bypassPermissions`. The profile
  sets `enabledPlugins` (25 entries, 10 off), `promptCacheTtl`, `enableWorkflows: false`,
  `crossSessionInbound: "hold"`, MCP allow rules, sandbox off. `lean-local-general.json` is the local
  counterpart (16 plugins off). A plain `joyia agent claude` passes none of it. MCP and tool limits are
  **launch flags**, which a settings file cannot express.
- **csl's current MCP toggle**: `enable_mcp` (default ON), rows at `bin/session_picker_model.py` (Local
  and Remote lane screens), exported as `LA_ENABLE_MCP` to the launchers.

## 4. Part A — Cloud Session Configuration screen

The screen's rule [decided]: it lists only settings that change cloud sessions, not free sessions
launched from csl.

### A1. Subagent routing per delegation path [decided: per path, deterministic]

One row per delegation path. Paths, initially [my call]: `Agent` subagents (incl. review agents) ·
Workflow / ultracode runs; more rows only if the interception can tell them apart [verify]. Each row
cycles through three modes [decided 2026-10-23], and each mode has an **info box** that says in plain
words what it does, since the names alone were not clear enough:

| Mode | What happens on that path | Info box (gist) |
|---|---|---|
| **Free first, paid as last resort** (DEFAULT) | The delegation runs on free models; the paid subagent runs only after free options were honestly exhausted (rule below), and the result says so with every attempt and its error. | "Runs on free models. Only if several free models really fail does this one task fall back to the paid model, and you are told." |
| **Free only** | Free models only; if they are exhausted the call fails with the reason and how to change the mode. Never spends paid tokens. | "Never spends paid tokens on this kind of task. If no free model can answer, the task fails and says why." |
| **Paid (free-agents stays out)** | No interception; the native paid subagent runs. The startup nudge no longer tells the agent to offload this path; free agents are used for it only when the user explicitly asks in the conversation. | "This kind of task always runs on your paid model. Free agents are used for it only if you ask for that yourself." |

- Default for a user who never touched it: **Free first, paid as last resort** on every path. The
  ask-once prompt is removed.
- **"Honestly exhausted"** [my call, for the maintainer to check]: the dispatch walks the preference
  order as today; only REAL attempts count (a request was sent and failed: quota/429 after one retry
  that honours a Retry-After of at most 30 s, 5xx, timeout, or an empty/unusable answer). Entries
  skipped for a missing key or missing weights do not count. The paid fallback is allowed once **at
  least 3 real attempts on at least 2 different providers** have failed, or every available entry has
  failed when fewer exist, and no new free attempt starts after a 10-minute wall-clock cap (local
  models can take minutes). Numbers live in `config/dispatch-preferences.json` so they are tunable,
  not code.
- **Mechanism for the fallback** [verify]: today the hook denies the `Agent` call and the agent runs
  `free-agent-tool.py` itself; the fallback therefore needs the tool to record a short-lived,
  session-scoped "free exhausted" mark (with the attempt list) that the hook honours by letting the
  NEXT native `Agent` call for that task through. Establish that the mark is session-scoped and expires,
  so one exhaustion never opens paid routing for the rest of the session.
- **The startup nudge follows the modes**: `hooks/offload-nudge.py` reads the per-path settings; a path
  set to *Paid* is not described as offload-by-default, and a path set to *Free only* says it never
  falls back. Otherwise a *Paid* path would still be offloaded by the agent on the nudge's advice.
- Storage: `~/.claude/settings.json` `env` [my call: `FA_ROUTE_AGENT`, `FA_ROUTE_WORKFLOW`, values
  `free-first|free-only|paid`; the legacy `FA_REPLACE_AGENTS=1` maps to `free-first`, `0` to `paid`].
  Reach is cloud-only because the only readers (the intercept hook and the nudge) already skip FREE
  sessions (`is_free_session`).
- **[verify] before building the Workflow row**: whether workflow/ultracode agents pass through the
  `PreToolUse` `Agent` hook at all, or need their own interception point. If no interception point
  exists, the Workflow row is not offered (a row that cannot act is the anti-goal), and the gap is
  recorded.
- Quality routing stays in the dispatch preferences; no quality logic here [decided].

### A2. Bypass permissions as the default for cloud sessions [decided]

- Row: "Default permission mode for sessions you start yourself: Auto / Bypass permissions"
  (writes `permissions.defaultMode` in `~/.claude/settings.json`). [my call on the wording: user scope
  applies to EVERY session started without a mode flag, including a plain native `claude`, so the
  label must not suggest a narrower reach.]
- csl's own lanes pass `--permission-mode` explicitly and are unaffected.
- The row also shows, read-only, when a higher layer decides it: a `--settings` profile that sets
  `defaultMode`, or a launcher flag known to set the mode. Neither lean profile sets `defaultMode`
  today, so the user setting takes effect in `claude-cloud-lean` sessions too.
- If a managed policy disables bypass (`permissions.disableBypassPermissionsMode: "disable"`), the
  option is shown disabled with that reason; csl never works around it [decided].
- The "available but not active" variant is impossible through settings (§3). Recorded, not built.

### A3. The gateway cost info box, gated on evidence [decided: only for users it applies to]

- Shown next to the A2 row only when this machine's cloud sessions are **not eligible** for free
  server-side classifier checks. Direct-Anthropic users for whom it is free see nothing.
- Content [my call, facts only]: on this setup auto mode's safety checks are billed as normal token
  usage; bypass with allow/deny rules avoids those calls; the lasting fix is a gateway that passes the
  `safeguards` / `safeguard_results` fields through unchanged (docs link). If the gateway is later
  found eligible, the box disappears.
- **Eligibility probe** (the gate). Eligibility depends on plan, gateway behaviour and Anthropic's
  rollout, so the probe measures the outcome instead of inferring it from "gateway or not":
  - Run one headless auto-mode request (`claude -p --permission-mode auto --output-format stream-json`)
    whose action is **provably outside every allow rule** (the probe must check that against the
    merged allow list before running; an allowlisted action never reaches the classifier).
  - Evidence of **not eligible**: the notice arrives as a `system` warning in stream-json, OR the
    gateway ledger (cost-tracker) shows a classifier-model request for that probe.
  - Evidence of **eligible** must be POSITIVE [decided principle: absence fails open]: the notice is
    suppressed for 24 h after a named-gateway acknowledgment, so "no notice" alone proves nothing.
    **[verify]** whether stream-json, the init message or the debug log carries the server-review
    state; if no positive signal exists, the probe may only conclude "not eligible" or "unknown", and
    "unknown" shows no cost claim.
  - Cost: one small request on the user's account, run on demand from the screen and cached with its
    date [my call]; never run automatically on a free lane.
- The JoyIA feature request (pass the fields through) is drafted only once the probe reads
  "not eligible" [decided].

## 8b. Spike outcomes (dated facts, 2026-10-24)

- **V3 eligibility — VERDICT: NOT-ELIGIBLE on the JoyIA gateway.** Auto mode's safety check is a
  second billed `claude-sonnet-5` request per non-read-only tool call: budget-tally's 9-day
  least-squares fit against the gateway's own cost counter puts it at $0.0265/call (rms $2.85/day,
  2026-09-15..26), with auto-mode-classifier-error dumps measuring 60–97k tokens per call. The
  user-authorized probes (3 runs, ~$0.10 total) showed no `system` notice — a *successful*
  classifier round-trip leaves no notice, so the stream alone is not a sufficient detector; the
  daily ledger's `estimated_classifier_usd: 0.0795` = exactly 3 × $0.0265 confirms live calls.
  Consequences: the gateway cost box (A3) now shows for this machine (verdict cached at
  ~/.local/state/free-agents/eligibility.json); the JoyIA pass-through feature request may be
  drafted (per the [decided] rule); and a POSITIVE eligibility signal remains unfound —
  stream-json, the init message and the debug log carry no server-review state, so "eligible"
  is unreachable by the probe on this gateway, only not-eligible/unknown.
- **V1 Workflow interception — AGENT half answered** (a subagent dispatch surfaces as an `Agent`
  PreToolUse event, nested claude + sandboxed hook, 2026-10-23); the WORKFLOW half is unresolved
  (the first nested-claude run from a session shell hit the PAID gateway, ~$0.19 — the injected
  session env; a free-lane headless vehicle is the prerequisite, tracked on csl-cloud-session).

## 5. Part B — Token-profile submenu

### B1. What it is [decided by the plan]

One submenu, named so a new user understands it at once [my call on the name: "Token savings —
what each session loads"], reached from Home. Per lane: **remote free-API sessions**, **local
sessions**, **cloud sessions (gateway / Anthropic)**. Categories with levels, e.g. "Available tools":
*all tools, no limits* · *restricted toolset* · *minimal essential tools*; "MCP servers": all ·
essential only · none. At the bottom, a running tally of the expected prefill / per-turn tokens; as the
user cycles a level, the tally updates and each row shows the percentage it saves.

### B2. Where each lane's choices are written [decided: both, by lane]

- **Free lanes** (local, remote): csl launches these, so it can pass flags (`--tools`,
  `--strict-mcp-config` / `--mcp-config`) and a `--settings` profile directly. Choices persist in csl's
  own store (`session-menu.local.json`).
- **Cloud lane** [decided 2026-10-23: requires a cloud profile]: csl does not launch cloud sessions,
  and `~/.claude/settings.json` is read by EVERY session, csl's free ones included, where list keys
  (e.g. permission deny rules) merge and cannot be undone. So the cloud lane's token choices are
  written **only into a registered cloud profile** (a `--settings` file; path kept in csl's store,
  e.g. the maintainer's `lean-cloud-general.json`). Without one, the cloud rows offer to CREATE
  `~/.claude/launch-profiles/csl-cloud.json` and show the exact `--settings <path>` addition for the
  user's launcher; until a launcher passes it, plain cloud sessions are untouched, and the rows say so.
- **[verify] cloud-lane reach per category**: tools and MCPs are launch flags. Establish which
  settings-file keys can reduce them (e.g. permission deny rules for whole tools, MCP server enable/
  disable keys, `enabledPlugins`) and whether they actually remove the definitions from the prefill or
  only block calls. A category with no settings-file lever is shown for the cloud lane as
  "set by your launcher" (read-only), not as a working row.
- The existing `enable_mcp` toggle moves here as the MCP category of the free lanes; its env
  (`LA_ENABLE_MCP`) and saved value keep working [decided: no duplicate].

### B3. Step 0 — measure before building the tally [decided: re-evaluate properly]

The tally shows measured numbers, never estimates [my call: estimates are shown only as "≈" and only
until a measurement exists].
- For each lever and level, one short headless run reads the real prefill token count from the
  response usage. Runs on a free lane, so the measurement costs $0; tool and MCP definitions do not
  depend on which lane serves the model [verify on one cloud spot-check].
- First targets, from the 2026-10-23 measurement: the 397 MCP tools (per server; magnific 222 is
  configured in `~/.claude.json` at local scope for `$HOME`, so every session started from the home
  folder loads it), `CLAUDE.md` + memory, hook SessionStart output, plugin skills (`/status` shows only
  ~720 tok/turn for skills), the agents view. The maintainer recalls MCPs as the largest lever; step 0
  confirms or corrects it.
- Results cached per machine with the inputs they depend on (installed plugins, MCP config, CLAUDE.md
  hash); stale when those change; re-measured on demand.

## 6. Carry-overs and coordination

- **Registry auto-sync leaves the OOM-protection screen** [decided 2026-10-23: it is not an
  out-of-memory setting]. "Auto-sync local registry with the disk (no prompts)" (row `a`, setting
  `memguard.registry_auto_sync`, exported as `LA_REGISTRY_AUTO_SYNC`) moves to the **Local screen**:
  it runs when the Local model list loads (`bin/session_picker.py` ~728) and, when OFF, the
  register / clean-up panels on that screen ask instead. The saved key and env stay as they are so no
  setting resets [my call]; the row needs a key other than `a` (Auto-mode on the Local screen). Can
  ship on its own before the rest of this release.

- The GLM 5.3 Flash branch `fix/rapid-auto-mode-tests` (unpushed) ships as **0.42.1** before or
  independently of this release; this release takes the next minor number at build time.
- Picker settings persistence (`csl-settings-persistence`, queue #2) overlaps B2's storage; build B2 on
  the read/write-symmetric store that item establishes, or fix the symmetry for the new rows here.
- Every new picker test runs under the harness with `CLAUDE_CONFIG_DIR` sandboxed; the conftest
  tripwire must stay green.

## 6b. Builder-check answers (independent review, DeepSeek V4.1 Flash, 2026-10-23)

- **A1 reach** (answered): the routing variables live in `settings.json` `env` and so reach every
  session, but the only readers (`hooks/cloud-agent-intercept.py`, `hooks/offload-nudge.py`) already
  pass FREE sessions through (`is_free_session`, localhost base URL). The effect is cloud-only.
- **Probe representativeness** [my call, revised by the maintainer 2026-10-23]: eligibility depends on
  the endpoint and account, not on plugins or tools, but the endpoint is set at launch (JoyIA exports
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` only into the child; `settings.json` holds none). The
  probe runs **plain `claude`** with the cloud credentials loaded from a **credential source**, in order:
  (1) `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` already in the environment, or a
  native Claude login; (2) a configured "credential command" that prints the env as JSON, default
  **`joyia agent claude --setup-only --json`** when `joyia` is installed (it prints the exports without
  launching; recorded in memory `llm-gateway-budget-limit`). Then
  `claude -p --permission-mode auto --output-format stream-json`. The token is passed only through the
  child's environment, never on a command line, in a log, in the verdict cache or in a receipt. Env that
  changes eligibility (`CLAUDE_CODE_AUTO_MODE_SERVER`, `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`) is read
  from the same layers and shown with the result.
- **One write rule for higher layers** [my call, revised by the 2026-10-23 decision]: token levers
  (B2) go only into the ONE registered cloud profile (candidates offered from
  `~/.claude/launch-profiles/*.json`). The bypass default (A2) stays in `~/.claude/settings.json`: a
  single scalar whose purpose is to reach plain `joyia agent claude` sessions; if the registered
  profile sets `defaultMode`, the A2 row shows that it is overruled. An unregistered `--settings` file
  cannot be detected from csl, so the A2 row carries one line: "If your launcher passes its own
  --settings file that sets a permission mode, it wins." Read-only display otherwise applies only to
  managed policy.
- **Tally per lane** [my call]: measured per lane, with each lane's own flags and profile; the cache
  key is the lane plus its inputs (installed plugins, MCP config, profile file hash, CLAUDE.md hash).
  The cloud lane is measured by running a FREE-lane session with the cloud lane's profile and flags
  applied (definitions do not depend on the serving model; one cloud spot-check confirms it).
- **Probe action outside every allow rule** [my call]: never reimplement the merge. Use a Bash command
  whose first word is a unique nonexistent token (`fa-eligibility-probe-<random>`): prefix allow rules
  cannot match it. Only a blanket `Bash` / `Bash(*)` allow rule in any layer can, and then the probe
  reports "unknown" without running.
- **Editing a profile csl does not own** [my call]: in-place edit of the owned keys only, everything else
  byte-preserved (the `cloud_session_env._write_env` pattern: mode kept, symlinks followed), a
  timestamped backup next to the file before the first write, and one configured profile at a time.
- **Tally baseline** [my call]: "saves X%" is relative to *everything on* (all installed tools, MCPs,
  plugins), and the screen shows the current total too. With no free lane to measure on, the tally reads
  "not measured" and offers a cloud measurement with an explicit cost estimate and a confirmation, never
  automatically.

## 7. Left to the builder

- Exact row keys, emoji, and ordering on both screens (follow `check_action_table` and existing
  conventions).
- The level contents per category (which tools are "essential"), proposed from the existing lean
  profiles and step-0 numbers, shown to the maintainer before release.
- How the tally is laid out and refreshed.
- Release slicing: Part A and Part B may ship separately if Part B's step 0 takes longer.

## 8. Open branches (answers arrive during the build)

- JoyIA eligibility (A3 probe) → decides whether the info box shows for JoyIA users and whether the
  feature request is drafted.
- Workflow interception point (A1) → decides whether the Workflow row exists.
- Cloud-lane settings-file levers (B2) → decides which cloud categories are working rows.
