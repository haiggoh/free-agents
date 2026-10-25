# Execution Plan — session thinking toggle (both lanes) + dispatch thinking policy

🚨 **System instructions for the AI developer** — This plan is self-contained. Revalidate live
source before mutation. TDD every layer: failing test first, watch it fail, minimal code, watch it
pass. Stage by path, never `git add -A`. Commit trailer:
`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` +
`Co-Authored-By: GLM 5.3 <noreply@z.ai>`. Ports 4141–4143 serve live sessions — never touch them.

## 📌 Context and objectives

**Design authority:** waypoint `fa-thinking-toggle` (user design 2026-10-24, quoted in the handoff
`docs/handoffs/2026-10-25-glm-flash-continuation.md`). This plan only sequences it.

**Verified surfaces (read 2026-10-25, worktree of 5bbb4db / 0.43.6):**
- `bin/session_menu_state.py` — preferences store; schema 6; sections tuple refuses unknown keys;
  `_doc` omits empty sections; CLI subcommands per setting.
- `bin/session_picker_model.py` — `Settings` dataclass (line ~439); `LocalScreen.actions()` rows
  e/o then temperature `o`; `RemoteScreen.actions()` same order; `_request_for` builds launch
  `env`/`argv`; per-screen `_note()` builds the info box.
- `bin/remote-session.sh` — `THINKING` derived from alias suffix (line ~1847); constraint facts in
  `_model_param_lines` (788–884): Nemotron honors chat_template_kwargs both directions; GLM on NIM
  ignores them (reasoning_effort is the only dial, low → ≈0 reasoning tokens, verified direct);
  Gemini disabled unless asked, explicit effort asks; banner prints `thinking : $THINKING` (~2087).
  `--swap-model` re-derives thinking from the new alias's suffix (line ~1266).
- `bin/local-session.sh` + `config/config-lib.sh` (`la_lookup` sets `LA_CUR_THINK` from
  `LA_THINK[$alias]`) + `bin/local-llm-hotswap.sh` (profile-mode `THINK` from profile JSON, alias
  fallback line ~193; THINK=true → `--reasoning-parser` + temp 0.6 at server load) +
  `bin/launch-claude-agent.sh` (banner prints `LA_CUR_THINK`).
- `bin/free-agent-tool.py` — dispatch preference entries; `dispatch_entry` builds
  `remote-agent-dispatch.py` argv; result JSON. `remote-agent-dispatch.py` accepts
  `--reasoning-effort low|medium|high` only — that is the dispatch's sole thinking dial.

**Current → required behavior:** a session launch honors a remembered thinking choice (default
None = ON) on both lanes; a model constraint may override the choice and the UI must say so; a
stateless dispatch derives thinking from its role, applies model constraints last, and reports the
resolution in its result JSON. Direct CLI launches without the env keep today's alias-derived
behavior.

**Hard constraints:** no paid spend; do not change dispatch behavior when entries carry no
thinking field beyond the documented role defaults; schema bump must keep schema-6 files loading.

**Acceptance criteria:** every layer above has tests that failed before the change; the full suite
passes; the shipped release is 0.44.0 with CHANGELOG entry.

## 🔒 Authority matrix

| Operation | Status | Evidence/gate |
|---|---|---|
| Read-only inspection | allowed | this worktree, branch `feat/thinking-toggle` |
| Edit worktree files, commit, push branch | allowed (handoff rule 3) | tests green, staged by path |
| Release 0.44.0 via `~/.claude/scripts/fa-ship.sh` | allowed (standing authorization) | full suite green; origin/main version checked first |
| Live launch to verify | allowed on scratch port ≥4144 | never 4141–4143; never `--stop` |
| Paid API calls | **prohibited** | handoff rule 8 |

## 🛑 Phase 0 — preflight (DONE in survey)

Worktree exists; handoff committed (19c2286). Suite baseline: `~/.local/bin/pytest -q tests
-p no:cacheprovider -n auto` — run before first green claim.

## 🏗️ Phase 1 — persistence (`session_menu_state.py`)

Section `thinking` = `{"session": true|false}`; key absent ⇒ `State.thinking_session is None`.
Schema 7; READABLE_SCHEMAS += 7; schema-6 files still load. CLI `get-thinking` / `set-thinking
true|false` (value `default` clears). Tests: roundtrip; absent ⇒ None; invalid value refused with
file untouched; a schema-6 file loads; unknown section still refused; `show` includes it.

## 🏗️ Phase 2 — picker model + screens (`session_picker_model.py`)

`Settings.thinking_session: bool | None = None`, `on_thinking_saved` callback. Helper
`thinking_row_choices()` = ((None, "ON (default)"), (False, "OFF")) — the row stores only choices
that differ behaviorally; True stays constructible via the API. Row after temperature on both
screens (key `k` free on Local; Remote uses… verify free letter). `_request_for` on both screens
exports `LA_THINKING_OVERRIDE=true|false` when the setting is not None (True and None both export
`true` — None means ON). Constraint mirror `thinking_constraint(provider, model, choice)` →
`(choice, notice|None)`: GLM on NVIDIA + OFF ⇒ notice "GLM on NIM always thinks; reasoning_effort
low is the closest available (≈0 reasoning tokens)"; the info box (`_note`) appends the notice for
the highlighted model. Persist via `on_thinking_saved` → `sms.save_thinking_session`.

## 🏗️ Phase 3 — remote launcher (`remote-session.sh`)

Resolution after `PROV`/`MODEL` are known, before `start_proxy`: alias suffix ⇒ base; then
`LA_THINKING_OVERRIDE` (validate `true|false`, else warn + ignore); then constraint pass — GLM on
NVIDIA: OFF stays OFF (effort low path unchanged) but the banner gains the always-thinks notice;
Nemotron/Gemini/others honor the choice through the existing per-family rules. Banner prints the
resolved value + an override/constraint note when one applies. `--swap-model` applies the same
resolution (suffix, then env, then constraint). `--help` documents the env. Tests via the existing
bash harness in `tests/test_remote_session.py`.

## 🏗️ Phase 4 — local lane (`config-lib.sh`, `local-session.sh`, `local-llm-hotswap.sh`,
`launch-claude-agent.sh`)

`la_lookup` applies `LA_THINKING_OVERRIDE` (validated) over `LA_THINK[$alias]` — one point, so
launcher, hotswap, preflight and session identity agree. Hotswap profile mode: the override beats
the profile's THINK. `local-session.sh --dry-run` prints the override and resolved value;
`launch-claude-agent.sh` banner shows an override note when set. Local has no measured model
constraints — no invented notices; the note only states that the alias's configured value was
overridden.

## 🏗️ Phase 5 — dispatch policy (`free-agent-tool.py`, `dispatch-preferences.json`)

`resolve_thinking(entry, role)` → `(resolved, overridden, note)`:
1. entry `thinking` ∈ on|off|auto (absent = auto): auto or absent ⇒ role default (reasoner,
   validator → on; operator, utility, no-role → off — the council's cross-family validator is a
   validator, so the council rule is preserved).
2. Constraint pass LAST, from provider+model: GLM on NVIDIA — on ⇒ entry reasoning_effort else
   high; off ⇒ `low` unless the entry pins reasoning_effort (then kept; overridden=true, note).
   Nemotron on NVIDIA — off cannot be honored (`enable_thinking` is not sendable through
   `remote-agent-dispatch.py`) ⇒ overridden=true, note; on ⇒ nothing to send.
   All others — on ⇒ reasoning_effort (entry's else high); off ⇒ no reasoning_effort line.
`dispatch_entry` translates the resolution into argv; result JSON gains `thinking`,
`thinking_overridden`, `thinking_note`; the stderr announcement names an override. Entries keep
working unchanged when they add nothing (auto). Tests in `test_free_agent_tool_preferences.py`.

## 🏗️ Phase 6 — version, CHANGELOG, docs

VERSION 0.44.0, plugin.json, CHANGELOG entry, README launcher env tables, release via
`fa-ship.sh` after origin/main check. Independent dispatch review (handoff rule 5) before release.

## 🧨 Adversarial review

- *Shortcut risk:* picker exports the override only on one screen → other lane keeps old default.
  Detect: test asserts env on BOTH `_request_for` paths.
- *False success:* schema-7 write, then an old reader loads it silently. Detect: test reads a
  schema-7 file with READABLE_SCHEMAS=(1..6) semantics — unknown-version refusal must fire.
- *Silent regression:* a dispatch with an explicit `reasoning_effort` changes behavior after the
  change. Detect: golden test — entry without `thinking` and with explicit effort on a validator
  role sends the SAME argv as before for non-GLM entries… (role default on ⇒ effort kept).
- *Unrunnable risk:* live thinking behavior of GLM low-effort ≈0 tokens is a 2026-10-13/14
  measurement, not re-verified here — the launcher comment cites it; no live probe in this phase.

## 🏁 Final review

Answered at release time in the Progress log of the handoff doc.
