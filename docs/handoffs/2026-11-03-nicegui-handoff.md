# Handoff 2026-11-03 — Free Agents NiceGUI frontend (continue from Task 4 remainder / Task 5)

## Why this exists
The previous session (Claude Opus 5.5 on the paid gateway) stopped because the day's gateway
budget was nearly spent (user: "$3 remaining"). Nothing is broken; this is a budget handover.
A CPU-burner load from ANOTHER session (16 `yes` processes, 03:36) made macOS tools slow for a
while — it is gone; if osacompile/codesign take >5 s again, check `uptime` before debugging code.

## Goal (verbatim scope from the user)
Implement the plan `docs/superpowers/plans/2026-11-03-nicegui-frontend-and-thin-app.md`
(spec: `~/.claude/plans/Current implementation brief v2 — Free Agents NiceGUI, shared Textual core and locally generated thin app.md`).
This run's scope = Phases A–C (Tasks 1–10): proof + shared core + vertical slice.
Git: branch `feat/nicegui-frontend` in worktree `~/ClaudeWorkspace/.wt/fa-nicegui`; push the
branch is allowed; NO merge, tag or release. Qualification target: this Mac, labelled
"managed macOS with developer tools".

## State (all on origin/feat/nicegui-frontend)
- b25b88a/13a50b5/28da8e0 plan (+ Linux launcher spec, Task 14)
- fd64a4a Task 1: GUI venv (`install/setup-gui.sh [--check|--dev]`, ~/.local/share/free-agents/gui-venv), `bin/free-agents-gui`, `bin/fa_gui/main.py` — tests/test_gui_env.py 11/11
- ce975c3 Task 2: native round trip; spawn-guard fix (`__main__` only); `.rtc/pre-flight.sh` gui gate (FA_GUI_PY) — tests/test_gui_browser.py (GUI venv)
- d569e9c Task 3: `install/gui-launcher.py {generate,check,repair,remove}`, `install/gui-launcher/locate.sh`, applet template, interim `bin/fa_gui/app.py` (labels-only Home) — tests/test_gui_launcher.py 21/21
- c33deb6 Task 4 receipts `docs/gui/QUALIFICATION.md` — Finder double-click opened the native Home menu, emoji correct, clean exit
- Ledger with every ruling: `.superpowers/sdd/2026-11-03-nicegui-frontend-and-thin-app/progress.md` (git-ignored; read it)
- Baseline full suite at branch base: `pytest -n 12 -q -p no:cacheprovider` → 1397 passed, 141 skipped, 1 xfailed
- GUI tests run under the GUI venv: `~/.local/share/free-agents/gui-venv/bin/python -m pytest -q -p no:cacheprovider tests/test_gui_browser.py`

## User feedback to act on (2026-11-03 14:18)
"the app loads the menu, nothing is clickable though and no hover change yet, no loading
animation either. but it does open and the emojis show correctly." — expected: the interim page
is labels-only. Tasks 5–10 deliver clickable typed controls, hover/info, async loading animation.

## Standing rules
1. Autonomous: decide and record (`Ruling:` line in the ledger) instead of asking; stop only for forks that cannot be weighed.
2. Work only in `~/ClaudeWorkspace/.wt/fa-nicegui`. Never switch the branch of `~/ClaudeWorkspace/free-agents` (the launcher resolves through it).
3. Commit every milestone, staged BY PATH (never `git add -A`), with YOUR running model's co-author trailer (memory `commit-attribution-by-model`). Push the branch; never merge, tag or release.
4. TDD every change (fails for the right reason, then passes); mutation-check each new guard (apply, `cmp`, run, restore); never weaken a test to get green.
5. Review each milestone with an independent dispatch (`bin/free-agent-tool.py`, `avoid_family` your own) and check every finding against the real code.
6. Resolve model/roster facts from the catalog when needed, never from memory.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything on ports 4141–4151, never kill processes you did not start (identify the owner first).
8. Append one progress line to the ledger AND to the "Progress" section below at each task completion, in the same commit.
9. This doc is YOUR handover; continue it directly — never launch yet another session to pass the work on.
10. No live inference launches from the GUI (brief forbids it without user consent): Task 10 verifies launches through the `FA_GUI_TERMINAL_STUB` recorder only.
11. Every new script supports `--help`, rejects unknown flags non-zero; never write the home dir as `/Users/...` — use `~` or `$HOME`.
12. Full suite before calling a task done: `pytest -n 12 -q -p no:cacheprovider` (compare with baseline above) plus the GUI-venv browser tests.

## Next action
1. Task 4 remainder: failure drills on the real app (temporarily set `GUI_VENV='/nonexistent'` in `~/.config/free-agents/gui-launcher.conf` → `open ~/Applications/"Free Agents.app"` must show a dialog naming setup-gui.sh; restore the file, `cmp` it). Record in QUALIFICATION.md.
2. Then Task 5 (typed setting metadata on `Action`, `bin/session_picker_model.py:74-105`) exactly as the plan states, then Tasks 6–10 in order. Pre-flight rulings for T1/T5 ordering are in the ledger.

## Progress
- (successor: append here)
