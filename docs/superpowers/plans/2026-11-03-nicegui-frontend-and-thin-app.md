# NiceGUI frontend, shared picker core and locally generated thin app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stylish native NiceGUI frontend for Free Agents on macOS/Linux, launched on macOS from a thin `Free Agents.app` that setup generates locally on the user's Mac, while the Textual picker and direct CLI keep working unchanged.

**Architecture:** `bin/session_picker_model.py` already is the Textual-free behavioural core (one `Action` table per `Screen`, `Settings`, `LaunchRequest`). This plan (a) adds typed setting metadata to `Action` so a GUI never parses labels, (b) lifts the non-terminal parts of the Textual `Picker` app (settings construction, savers, loaders, screen building, launch command) into a new UI-free `bin/picker_core.py` that both frontends call, and (c) adds a NiceGUI adapter that renders any `Screen` generically, so a new action appears in both frontends from one definition. The macOS launcher is an AppleScript applet compiled on the target with `/usr/bin/osacompile` (present on every Mac, no Xcode needed) and ad-hoc re-signed with `/usr/bin/codesign`; it only locates the installation and starts the managed GUI interpreter.

**Tech Stack:** Python 3.12 (uv-managed, like the picker venv), NiceGUI 3.19.x, pywebview 6.2.x (Cocoa/WKWebView on macOS, Qt or GTK on Linux), Playwright 1.64.x (test-only), AppleScript applet + codesign (launcher), existing pytest suite.

**Spec:** `~/.claude/plans/Current implementation brief v2 — Free Agents NiceGUI, shared Textual core and locally generated thin app.md` (brief v2). Executors read both.

**Session scope (user, 2026-11-03):** this session runs Phases A–C (proof + shared core + vertical slice). Phases D–E are planned here and run later. Git: branch `feat/nicegui-frontend` in its own worktree (the canonical checkout's branch is never switched), push the branch; **no merge, tag or release**. Qualification target: this Mac, reported as "managed macOS with developer tools"; clean-Mac behaviour is marked untested.

## Global Constraints

- TUI and direct CLI must keep working with **no** NiceGUI/pywebview/PyObjC/Qt/GTK installed; `bin/session_picker_model.py` and `bin/picker_core.py` import neither Textual nor any GUI package at module level.
- Existing Textual shortcuts, entry points (`csl`, `session-picker [screen]`), defaults and tests stay; the full suite (`pytest -n auto`) stays green after every task.
- GUI venv: `~/.local/share/free-agents/gui-venv` (override `LA_GUI_VENV`), separate from `picker-venv` and from inference-runtime venvs; pins in `install/gui-requirements.txt`.
- No DMG, no self-contained bundle, no vendored Python or plugin copy inside the `.app`; no `.command` release artefacts; no Developer ID / notarization.
- Never strip quarantine, disable Gatekeeper/SIP, or drive System Events to hide Terminal.
- App location policy: `~/Applications/Free Agents.app` (override `--dest`); never inside the plugin cache, a temp/download dir, or another user's folder; no sudo.
- Root resolution reuses `bin/free-agents-resolve.sh` order (`$FREE_AGENTS_PLUGIN` → highest plugin-cache version → checkout); no new precedence, no baked version directory.
- Settings go through `session_menu_state` / `cloud_session_env` savers only (locked field-level merges); no browser-storage copy of semantic preferences.
- Loopback only (`127.0.0.1`) for every GUI server, test or production; no arbitrary shell endpoint.
- Every new script supports `--help`, rejects unknown flags non-zero, and a set-but-invalid override env var aborts naming it.
- Emoji come from `bin/emoji_constants.py`; every control has an accessible text name that works without the emoji.

## Review Focus

1. **Finder environment ≠ shell** — the applet runs with a minimal PATH and `cwd=/`; a launcher that works from Terminal but finds no `uv`/python/plugin from Finder. Expect an actionable native dialog, never a silent nothing. (Task 3 test: run the locate script under `env -i HOME=… PATH=/usr/bin:/bin`.)
2. **Stale settings overwrite across frontends** — TUI and GUI open at once; GUI writes effort from a stale in-memory `Settings` and clobbers a newer TUI temperature. Expect field-level merge: only the changed field is written. (Task 6 test.)
3. **Stale inventory completion** — user opens Remote, navigates Home, the slow load finishes and repaints the wrong page or a later load is overwritten by an older one. Expect generation-token discard. (Task 9 test.)
4. **Duplicate launch** — double-click / Enter-repeat on Launch opens two Terminal sessions. Expect one launch per request while the first is in flight. (Task 10 test.)
5. **Missing/broken GUI venv or plugin after an upgrade** — clicking the app after `gui-venv` was deleted or the plugin cache moved to a new version. Expect the new version to be found without regenerating the app, and a dialog naming the repair command when the venv is gone. (Tasks 3, 4.)

---

## File Structure

| Path | Responsibility |
|---|---|
| `install/gui-requirements.txt` | pinned GUI runtime deps (nicegui, pywebview; pyobjc pulled by pywebview on macOS) |
| `install/gui-dev-requirements.txt` | test-only: playwright, pytest-playwright-free helpers |
| `install/setup-gui.sh` | create/refresh/`--check` the GUI venv (mirrors `setup-session-picker.sh`) |
| `bin/free-agents-gui` | bash wrapper: resolve venv, actionable exit 3 when missing, exec `bin/fa_gui/main.py` |
| `bin/fa_gui/__init__.py` | package marker |
| `bin/fa_gui/main.py` | argv parsing (`--native` default, `--browser --port N` for tests, `--selftest`), `ui.run` |
| `bin/fa_gui/adapter.py` | generic `Screen` → NiceGUI renderer (sections, typed settings, groups, nav) |
| `bin/fa_gui/jobs.py` | async inventory loads with generation tokens; bounded background tool jobs |
| `bin/fa_gui/terminal_host.py` | open an interactive session in a terminal (macOS Terminal / Linux emulator), duplicate guard |
| `bin/fa_gui/theme.py` | design tokens (light/dark), fonts, emoji sizing CSS |
| `bin/picker_core.py` | UI-free: `build_settings`, `Savers`, loaders, `build_screen`, `command_for`, `TOOLS` |
| `bin/session_picker.py` | Textual: re-imports moved names from `picker_core` (tests patch them here) |
| `bin/session_picker_model.py` | `Action` gains typed fields; `setting()` fills them |
| `install/gui-launcher.py` | generate/check/repair/remove the thin `.app` (applet) and Linux `.desktop` (Phase E) |
| `install/gui-launcher/FreeAgents.applescript` | applet source template |
| `install/gui-launcher/locate.sh` | applet-side resolver: config file → resolve root → venv → exec wrapper; error text for dialog |
| `tests/test_action_typed_settings.py` | typed metadata on every stepping row |
| `tests/test_picker_core.py` | core seams: settings build, savers merge, build_screen, command_for |
| `tests/test_gui_capability_coverage.py` | every action of every screen is rendered by the adapter or in `GUI_PENDING` |
| `tests/test_gui_launcher.py` | generator: idempotent, owned-vs-foreign, dry-run, Finder-env locate |
| `tests/test_gui_playwright.py` | browser-mode UI: nav, search, settings → state file, stale load, duplicate launch, empty roster |
| `tests/fixtures/gui/` | fake `remote-session.sh` / `csl` inventory scripts with configurable delay |
| `docs/gui/QUALIFICATION.md` | dated qualification receipts per platform (what was tested, what is not) |

---

## Phase A — Early delivery proof (this session)

### Task 1: GUI environment and wrapper

**Files:** Create `install/gui-requirements.txt`, `install/setup-gui.sh`, `bin/free-agents-gui`, `bin/fa_gui/__init__.py`, `bin/fa_gui/main.py`; Test `tests/test_gui_env.py`.

**Interfaces:** Produces `free-agents-gui [--native|--browser] [--port N] [--selftest] [--help]`; exit 3 + one-line repair command when the venv is missing; `LA_GUI_VENV` override (invalid ⇒ abort naming it).

- [ ] Step 1: tests — `test_wrapper_help_exits_0`, `test_wrapper_missing_venv_exit_3_names_setup_gui` (LA_GUI_VENV=tmp empty dir), `test_setup_gui_check_reports_missing` (exit 1), `test_main_rejects_unknown_flag` (exit 2), `test_core_imports_without_gui_packages` (run `python3 -c "import session_picker_model, picker_core"` with `PYTHONPATH=bin` in the system interpreter; asserts `nicegui`/`webview` not in `sys.modules`).
- [ ] Step 2: run, expect FAIL (files missing).
- [ ] Step 3: implement; pins `nicegui==3.19.0`, `pywebview==6.2.1`. `main.py --selftest` serves one page with a button whose click calls `session_picker_model.plugin_version()` and writes `{"clicked": true, "version": …}` to `$LA_GUI_SELFTEST_FILE`.
- [ ] Step 4: `install/setup-gui.sh` then `install/setup-gui.sh --check` → `gui venv OK`; tests PASS.
- [ ] Step 5: commit `feat(gui): managed GUI venv, wrapper and minimal NiceGUI entry`.

### Task 2: Native window round trip

**Files:** Modify `bin/fa_gui/main.py`; Test: manual + `tests/test_gui_env.py::test_browser_selftest_round_trip`.

- [ ] Step 1: test — start `free-agents-gui --browser --port <free> --selftest`, Playwright (chromium) clicks the button, asserts the selftest file holds the plugin version; server stops on SIGTERM with no child left (`pgrep -P`).
- [ ] Step 2: native run `free-agents-gui --native --selftest` from Terminal: window opens (WKWebView, no browser tab), button round trip writes the file, closing the window exits the process (check `pgrep -f fa_gui/main.py` empty). Record observations.
- [ ] Step 3: commit.

### Task 3: Thin app generator (macOS applet)

**Files:** Create `install/gui-launcher.py`, `install/gui-launcher/FreeAgents.applescript`, `install/gui-launcher/locate.sh`; Test `tests/test_gui_launcher.py`.

**Interfaces:** `gui-launcher.py {generate,check,repair,remove} [--dest DIR] [--dry-run] [--force] [--help]`. Generated bundle: `CFBundleIdentifier = io.github.haiggoh.free-agents.launcher`, `FreeAgentsLauncherContract = 1`, `FreeAgentsGeneratedAt`, `FreeAgentsSourceRoot` (diagnostic only). Per-user config `~/.config/free-agents/gui-launcher.json` = `{"contract":1,"plugin_hint":"<root at setup>","channel":"brew|curl|marketplace|checkout","venv":"<path>"}`.

Decisions: applet runs `Contents/Resources/locate.sh` via `do shell script`, which resolves the root in brief order (`FREE_AGENTS_PLUGIN` from the config file, else highest plugin-cache version, else `plugin_hint`) and launches `bin/free-agents-gui --native` **detached** (`nohup … >log 2>&1 &`, log at `~/Library/Logs/free-agents/gui.log`, rotated at 1 MB); a non-zero exit with a message on stdout ⇒ applet `display dialog` with that message and an "Open log" button. The applet is stay-open only if the prototype shows the Python process cannot own a Dock identity; otherwise it quits after hand-off (record which in QUALIFICATION.md).

- [ ] Step 1: tests — `test_generate_into_tmp_creates_owned_bundle` (plist keys present, `codesign --verify --strict` exit 0), `test_generate_twice_is_idempotent` (same contract ⇒ no rewrite unless `--force`), `test_refuses_foreign_app_at_dest` (dest has a bundle with another identifier ⇒ exit 1, untouched, `cmp`), `test_dry_run_writes_nothing`, `test_remove_only_owned_and_keeps_config_and_venv`, `test_locate_under_finder_env_finds_plugin` (`env -i HOME=$tmp PATH=/usr/bin:/bin` with a fake plugin cache of two versions ⇒ picks the higher), `test_locate_missing_venv_prints_setup_command` (exit 3, stdout names `setup-gui.sh`), `test_new_plugin_version_found_without_regeneration`.
- [ ] Step 2: FAIL. Step 3: implement (`osacompile -o`, write plist via `plistlib`, copy icon, `codesign --force --sign - --deep`). Generation into a `*.tmp` sibling then atomic rename, so a failed regenerate keeps the working app. Step 4: PASS. Step 5: commit.

### Task 4: Qualification on this Mac

**Files:** Create `docs/gui/QUALIFICATION.md`.

- [ ] Step 1: `install/gui-launcher.py generate` into `~/Applications`; record `xattr -l` (expect no `com.apple.quarantine`), `codesign -dv --verbose=2`, `spctl --assess -vv` (record verbatim; ad-hoc is expected to read "rejected" without blocking a locally created app).
- [ ] Step 2: launch via `open ~/Applications/Free\ Agents.app` AND ask the user to double-click it in Finder once; record: any "Apple could not verify" sheet, any Terminal window, selftest round trip file, process tree.
- [ ] Step 3: close window ⇒ `pgrep -f fa_gui` empty; relaunch works.
- [ ] Step 4: failure drills: rename `gui-venv` ⇒ dialog names the repair command; point config at a missing plugin ⇒ dialog; restore.
- [ ] Step 5: write receipts with date, macOS version (`sw_vers`), "managed Mac with Xcode", and an explicit UNTESTED list (clean Mac, other macOS versions, Brew bottle lifecycle, MDM-restricted Macs). Commit.

## Phase B — Shared core (this session)

### Task 5: Typed setting metadata on `Action`

**Files:** Modify `bin/session_picker_model.py:74-105`; Test `tests/test_action_typed_settings.py`.

**Interfaces:** `Action` gains `choices: tuple | None = None`, `value: Callable[[], object] | None = None`, `put: Callable[[object], object] | None = None`, `name: str = ""` (label without the current value), `choice_label: Callable[[object], str] | None = None`, `id: str = ""` (stable semantic id; empty ⇒ adapters use `f"{type(screen).__name__}.{key}"`). `setting()` gains keyword-only `name=""`, `choice_label=None` and fills `choices/value/put`.

- [ ] Step 1: tests — `test_every_stepping_row_is_typed` (for each Screen subclass built by `picker_core.build_screen` over synthetic Settings with every lane populated: every action with `step` has `choices` and `value`), `test_put_typed_value_equals_step_effect` (telemetry: `put(True)` ⇔ one `step(+1)` from False, same Settings field), `test_textual_behaviour_unchanged` (existing `tests/test_session_picker_model*.py` pass untouched).
- [ ] Step 2: FAIL. Step 3: implement; rows that hand-roll `step` without `setting()` get converted to `setting()`. Step 4: full suite green. Step 5: commit.

### Task 6: `picker_core.py` — lift non-terminal logic out of the Textual app

**Files:** Create `bin/picker_core.py`; Modify `bin/session_picker.py:82-400, 698-833, 1160-1186, 1194-1262`; Test `tests/test_picker_core.py`.

**Interfaces:** Produces
- `build_settings(flags: argparse.Namespace | None, savers: Savers, env: Mapping[str,str] = os.environ) -> m.Settings`
- `class Savers(config_dir: Path)` with `.warnings: list[str]`, `.notices: list[str]`, `effort/temperature/thinking/hidden_providers/cloud/last_launched/rate_limiter/memguard` methods (bodies moved from `Picker._save_*`).
- `build_screen(nav: m.Nav, s: m.Settings, cache: dict) -> m.Screen` (the `goto` switch minus rendering/prefetch; returns also `prefetch: tuple[str,...]` via `ScreenBuild(screen, prefetch)` dataclass).
- moved verbatim: `load_local_models, MISSING_MODELS, auto_sync_registry, load_remote_agents, catalog_local_capable, model_param_count, catalog_model_old, load_remote_catalog, load_catalog, load_versions, command_for, TOOLS, _mb`.
- `session_picker.py` re-imports those names (`from picker_core import …`) so existing tests that patch `session_picker.<name>` keep working; `Picker` methods keep calling the module-global names.

- [ ] Step 1: tests — `test_build_settings_reads_store_and_env` (tmp `LA_SESSION_MENU_CONFIG_DIR`, `CSL_TELEMETRY=1` ⇒ telemetry True), `test_savers_field_level_merge_two_frontends` (two `Savers` on one dir: A saves effort, B (stale) saves temperature ⇒ file holds both), `test_build_screen_covers_every_target` (every target in the old `goto` switch), `test_command_for_lanes_unchanged`.
- [ ] Step 2: FAIL. Step 3: move code; `Picker.__init__` becomes `self.savers = Savers(...)`, `self.settings = build_settings(flags, self.savers)`, `state_warnings` aliases `savers.warnings`. Step 4: full suite `pytest -n auto` green, plus a manual `session-picker` smoke in a terminal. Step 5: commit.

### Task 7: Capability coverage check

**Files:** Create `tests/test_gui_capability_coverage.py`; `bin/fa_gui/adapter.py` exports `GUI_PENDING: dict[str, str]` (semantic id → reason) and `render_plan(screen) -> list[RenderedControl]` (pure, no NiceGUI import; NiceGUI binding happens in `adapter_ui`).

- [ ] Step 1: tests — `test_every_action_rendered_or_pending` (for every screen from `build_screen` over the full synthetic matrix, every action id appears in `render_plan` or in `GUI_PENDING`), `test_pending_entries_still_exist` (a stale `GUI_PENDING` id that no screen emits fails), `test_typed_setting_rendered_as_select_not_button` (a row with `choices` ⇒ control kind `select`/`toggle`, never `button`).
- [ ] Step 2–5: FAIL → implement `render_plan` → PASS → commit. The mutation check: delete one action's rendering branch ⇒ the first test fails naming it.

## Phase C — Vertical slice (this session)

### Task 8: Design direction

- [ ] Step 1: invoke `/frontend-design` and `/design-taste-frontend` (read their actual instructions; if absent, report and continue).
- [ ] Step 2: produce two directions as static NiceGUI pages over synthetic data (Home + Remote lane): lane navigation, searchable catalogue, selected-model info, settings panel, launch action. Keep the radio/robot-arm identity, lane emoji, playful theme hue from the picker's theme config; proportional UI font, monospace only for aliases/commands/logs.
- [ ] Step 3: Playwright screenshots light/dark at 1280×800 and 900×700; pick one with the critique notes in the commit message; record in `docs/gui/DESIGN.md`.

### Task 9: Home + Remote + Local lanes on the real core

**Files:** `bin/fa_gui/adapter.py`, `bin/fa_gui/jobs.py`, `bin/fa_gui/main.py`; Test `tests/test_gui_playwright.py`, `tests/test_gui_jobs.py`.

**Interfaces:** `jobs.Loader.load(kind: str, fn: Callable[[], T]) -> Awaitable[T | Stale]` — each `kind` keeps a generation counter; a result whose generation is not the latest, or whose page was left, returns `Stale` and is not applied. Shares the `cache` dict semantics of the Textual app (no reload on Back).

- [ ] Step 1: tests (fixtures: fake `CSL_REMOTE_LAUNCHER`/`CSL_SELF` scripts emitting synthetic inventory with `FAKE_DELAY`; tmp `LA_SESSION_MENU_CONFIG_DIR`; tmp HOME) — `test_nav_home_remote_back`, `test_search_filters_groups_multi_open`, `test_effort_select_persists_to_state_file` (assert the JSON field, not the label), `test_stale_inventory_after_navigation_not_applied`, `test_empty_roster_shows_explanatory_state`, `test_hardware_ineligible_action_disabled_with_reason`, `test_keyboard_focus_reaches_launch`.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS + screenshots in `docs/gui/screens/`. Step 5: commit.

### Task 10: Launch hand-off

**Files:** `bin/fa_gui/terminal_host.py`; Test `tests/test_gui_terminal_host.py` + Playwright `test_duplicate_launch_single_request`.

**Interfaces:** `open_in_terminal(cmd: list[str], env: dict, cwd: Path) -> HostResult(accepted: bool, detail: str)`; macOS: write `~/.local/state/free-agents/launch/<uuid>.sh` (0700, env exported with `shlex.quote`, `exec` of the command, self-deleting) and run `open -a Terminal <file>` (no Automation permission); Linux: `$FA_GUI_TERMINAL` or `x-terminal-emulator`/`gnome-terminal`/`konsole` `-e`. Override `FA_GUI_TERMINAL` invalid ⇒ error naming it. Test stub: `FA_GUI_TERMINAL_STUB=<file>` records the request instead of opening.

- [ ] Step 1: tests — `test_launch_uses_command_for_and_version_env` (stub record has `csl --picker-launch …` and `LA_FREE_AGENTS_VERSION`), `test_duplicate_click_one_request`, `test_needs_register_asks_first`, `test_host_failure_shown_not_swallowed`.
- [ ] Step 2–4: FAIL → implement → PASS; one manual real launch of a REMOTE free session only with user consent (live inference is not authorized by the brief — ask first; otherwise verify with the stub and `--dry-run` launcher flags).
- [ ] Step 5: commit; push branch `feat/nicegui-frontend`.

## Phase D — Parity expansion (later sessions)

### Task 11: Remaining screens through the generic adapter
Remove entries from `GUI_PENDING` one screen at a time: CloudConfig, ThemeConfig (hue/accent), RateLimiter, MemoryGuard (OOM), RemoteFilters, APIKeys (masked input + hide/show; key saved via the same `Store`), RuntimeManager/Versions/Launchd (background jobs with bounded log panel, cancel, timeout), Download (disk guard + confirmation), Lowkey (local/API, conversation/one-shot), About. Every confirmation in Textual's `CONFIRMS` becomes a native dialog with the same safe default. Shared `dispatch` classification (`picker_core.classify(result) -> Effect`) replaces the duplicated branch logic in both frontends. Exit criterion: `GUI_PENDING` empty or each remaining entry explicitly agreed with the user.

### Task 12: Drift tests
Shared scenario table (`tests/gui_scenarios.py`): each scenario = start screen, events, expected `Settings` fields / `LaunchRequest`; run through the Textual Pilot driver and the NiceGUI Playwright driver. Discriminants: omitted shared action, ineffective dropdown, stale preference overwrite, wrong stable model ID, duplicate launch, stale inventory completion.

## Phase E — Delivery channels and Linux (later sessions)

### Task 13: Channel integration
`free-agents gui setup` (= `setup-gui.sh` + `gui-launcher.py generate`) offered as an explicit optional step by the curl `.sh` installer and documented for marketplace users. Homebrew (the `homebrew-tap-fa` repo, `Formula/free-agents.rb`): test whether `post_install` can write `~/Applications` under Brew's sandbox; expected answer is no ⇒ `caveats` naming the single follow-up command. Bottle built on CI never contains the app. Update/repair: plugin upgrade ⇒ app keeps working (Task 3 test), contract bump ⇒ `repair` regenerates beside the old app and swaps only after `check` passes.

### Task 14: Linux companion
`gui-launcher.py generate` writes `~/.local/share/applications/free-agents.desktop` (`Terminal=false`, `Exec=` with Desktop Entry quoting rules, `TryExec`), pywebview Qt (`PyQt6-WebEngine`) or GTK (`WebKit2GTK`) detected and named in `setup-gui.sh --check`; never as root, never `--no-sandbox`. Qualify concrete profiles (Ubuntu 24.04 GNOME Wayland, Fedora KDE X11) in `docs/gui/QUALIFICATION.md`; others UNTESTED.

---

## Offload annotation (credit-efficient mode)

- Textual screen/action/setting inventory → `local:` free stateless dispatch (done 2026-11-03, checked against source before use in Task 7/11).
- Task 12 scenario-table expansion and Task 11 per-screen `GUI_PENDING` removal drafts → free dispatch first pass, reviewed here.
- Tasks 1–10 stay `cloud:` this session: native-window/Finder qualification and design critique need tool-driving and visual judgement in the session the user is present in (expires once Phase A is qualified).
