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
  default **everything through free agents**.
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

### A1. Subagent routing per delegation path [decided: per path, deterministic, default all free]

- One row per delegation path, each cycling **free / paid**. Paths, initially [my call]:
  `Agent` subagents (incl. review agents) · Workflow / ultracode runs. More rows only if the
  interception can tell them apart [verify].
- Default for a user who never touched it: **free** on every path. The ask-once prompt is removed
  (today: undecided → ask once → native forever, which contradicts the paid-session nudge).
- Storage: `~/.claude/settings.json` `env`, like `FA_REPLACE_AGENTS` today [my call: one variable per
  path, e.g. `FA_ROUTE_AGENT`, `FA_ROUTE_WORKFLOW`, values `free|paid`; `FA_REPLACE_AGENTS` read as a
  legacy alias for the Agent path so existing settings keep working].
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
- **Cloud lane**: csl does not launch cloud sessions. When a launcher passes a `--settings` profile that
  csl can identify (today `~/.claude/launch-profiles/lean-cloud-general.json`), csl edits **that file**,
  because it outranks `~/.claude/settings.json`; otherwise it edits `~/.claude/settings.json`.
  [verify] how csl identifies the active profile (configured path in csl's store [my call], not
  guessed from running processes).
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
