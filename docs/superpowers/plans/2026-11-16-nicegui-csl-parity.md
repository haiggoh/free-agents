# NiceGUI csl-parity implementation plan (feat/nicegui-frontend)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native execution).
> Steps use checkbox syntax.

**Goal:** bring the NiceGUI front-end (`bin/fa_gui/`) to screen parity with csl for the
sub-menus and interactive screens inventoried in the spec, including the local model list
registration/update flows.

**Architecture:** the GUI already renders csl screens generically through
`picker_core.build_screen` + `bin/fa_gui/adapter.py` (typed Action metadata →
switch/select/stepper controls). Parity work = register one `@ui.page` per missing csl
target (pattern exists: `make_lane`), teach the adapter any new control shapes, and add
write-path flows (download queue, catalog overlay edits) as real GUI screens that call
the SAME underlying scripts csl calls — never a second implementation.

**Tech Stack:** NiceGUI (python), bin/picker_core.py + bin/session_picker_model.py (shared
with csl), bash CLIs (download-models.sh, la-catalog-overlay.sh, la-model-profile.py),
pytest + Playwright GUI tests (`tests/test_gui_*.py`).

**Spec:** `docs/gui/PARITY-csl.md` (the durable inventory; the plan argues from it).

## Global Constraints

- Work ONLY on `feat/nicegui-frontend` in `~/ClaudeWorkspace/.wt/fa-nicegui` (rebased onto
  main 0.59.3 at b2e581f; re-check origin/main before each ship).
- csl behavior is UNCHANGED: the GUI adapts csl's model; shared code changes must keep
  `tests/test_picker_*.py` / `tests/test_session_picker_pty.py` green.
- TDD per screen: a Playwright/browser test that drives the real route (`tests/test_gui_browser.py`
  pattern) — a screen counts as done when the test clicks through its controls and asserts
  the EFFECT (the underlying script's argv / a settings write / a store file change), not
  that the tile exists. Mutation check per screen.
- Every write path (download, catalog edit, backend op) goes through the existing CLI
  (delivery-shaped assertion = the CLI's argv or the store file), with a dry-run/fixture
  mode in tests — never a real download in a test.
- Version surfaces: this branch ships its own releases when it merges; do NOT touch
  VERSION/plugin.json/CHANGELOG/ROADMAP mid-branch except in the branch's own release
  commits (the branch already has its numbering history; keep it).
- Commit attribution trailers per the free-lane table (GLM 5.3 row).

## 🔒 Authority matrix

| Operation | Status |
|---|---|
| Edit/commit on `feat/nicegui-frontend` in the fa-nicegui worktree | allowed |
| Push the branch (force-with-lease only after a rebase; plain push otherwise) | allowed |
| Merge/tag/release | NOT granted here — the branch merges via the normal review+ship later; plan ships nothing |
| Force-push without lease, moving tags, editing canonical checkout | prohibited |
| Real downloads / network / port binds in tests | prohibited — fixture CLIs + dry-run only |

## 🛑 Phase 0 — preflight

1. Three sentences: the missing screens get real routes; each write-path screen calls the
   same CLIs csl calls; the branch merges parity-complete with a green GUI suite.
2. `git status` clean, branch = feat/nicegui-frontend, `git log origin/main..HEAD --oneline | wc -l` = branch length.
3. Baseline: the GUI gate additions in `.rtc/pre-flight.sh` (GUI_TESTS) + `pytest tests/test_gui_*.py tests/test_picker_core.py -n 12` → record EXIT/counts.
4. Re-read the spec `docs/gui/PARITY-csl.md`; for each ❌/◐ row confirm the nav_target still
   exists in the model (it changed during the rebase).

## Tasks (build order from the spec)

### Task 1: tool:download + tool:lowkey routes exist and render real screens
- Pages `/download`, `/lowkey` registered like `make_lane`; adapter maps controls;
  Playwright test drives /download's queue→review→confirm and asserts the exact
  `download-models.sh --select …` argv (fixture CLI, dry-run).

### Task 2: settings screens — api_keys, rate_limiter (+rl tools), memguard, theme_config, cloud_config
- One route per screen; typed controls via the existing Action metadata; tests assert the
  settings write (store file) or the tool screen's command.

### Task 3: runtime manager + versions + launchd (the backend update functionality)
- `/runtime_manager` with install/upgrade/validate/repair rows driving the same
  backend-manager CLIs; tests assert argv, no real installs (fixture).

### Task 4: local model list registration & update in the GUI
- Download screen from Task 1 covers add; add catalog-overlay edit screen
  (view `model-catalog.local.psv`, add/remove rows through `la-catalog-overlay.sh`-equivalent
  path logic), the #missing cleanup flow, and profile display via la-model-profile.py.
- Tests assert the overlay store file changes (fixture store), never the real one.

### Task 5: remote_filters, about, prompts/warns, hidden-model report
- Routes + prompt/dialog pattern in the GUI; tests click through.

### Task 6: parity doc updated + full GUI gate green
- `docs/gui/PARITY-csl.md` statuses flipped as screens land; full GUI+picker suite green;
  QC dispatch (out-of-family, per Playbook-milestone-qc-offload-test-proof) before the
  branch is proposed for merge; its findings verified in code, mutations RED.

## ⚠️ Adversarial review

- A route that renders but whose controls call nothing (declared-only) → delivery-shaped
  argv/store assertions required per screen.
- Adapter change that breaks csl's TUI rendering → picker PTY tests in the same gate.
- Write path that touches the real user store in a test → fixture stores only.
- Rebase drift: another rebase of this branch must replay clean (rerere is on).

## 🏁 Delivery questions

Implemented vs planned per screen; receipts (gate EXIT/counts, QC model/route, mutations);
PARITY-csl.md statuses; what remains ❌ and why; no version/tag/release shipped by this plan.
