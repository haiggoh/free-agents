# csl 0.55.6 feedback round — cycle-glyph design on every setting surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task (native: you do every task yourself, one final review at the end). Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** every setting row in csl's Textual picker that cycles or toggles a value renders as
`KEY) name: ❮ value ❯`, with glyphs and hover colour coming from one definition.

**Architecture:** the `❮ value ❯` look is ALREADY drawn in one place: `render_model()` in
`bin/session_picker.py` (~L1718-1768) draws it for every `Action` whose `step` is not `None`. So a
surface gets the design by being *built as a stepper* in `bin/session_picker_model.py`, never by
adding drawing code. Rows still built as plain `Action(key, label, toggle_fn)` render as bare
text — those are the bugs.

**Tech stack:** Python 3, Textual (in the picker venv), unittest/pytest via `tests/picker_harness.py`.

**Spec:** the user's feedback, verbatim (2026-11-07 18:17 CET, waypoint `csl-0-55-6-feedback-round`):

> Auto-sync local registry with the disk should get the same treatment as all other toggle options.
> likewise, the RAM usage out-of-memory protection submenu should follow the same design and so
> should the cloud session configuration submenu as well. remote filter settings and nvidia
> ratelimiter also need the same design. ideally they all get their design from a single source of
> truth so for example if we ever want to change the glyphs or their hover color all surfaces that
> use them get updated from the single source of truth. effort in lowkey should use the same shorter
> phrasing the other two lanes are using rather than "sent as resoning effort" which makes the value
> insite the glyphs only unnecessarily long. also in low key "apply Remote-screen filters: is a
> toggle option and should get the same design all other toggle options get. in the theme color
> menu, the hue values should be in the same column with the color names centered below them so all
> four rows feel like they are in one centered column.

## Standing rules (read before anything else)

1. Work ONLY in `~/ClaudeWorkspace/.wt/fa-0556-feedback` on branch `fix/0556-feedback-round`.
   NEVER `cd` into or switch the branch of `~/ClaudeWorkspace/free-agents` (the canonical checkout —
   the user's launchers run from it).
2. Do NOT touch branch `feat/tui-toggle-knob` (a parked design the user chose not to ship).
3. **Highest boundary granted: local commits on `fix/0556-feedback-round`.** No push, no merge, no
   tag, no release, no version bump, no `rtc-ship.sh`. The reviewing session ships it.
4. Stage BY PATH (`git add <file>…`), never `git add -A` / `git add .`.
5. Never weaken or delete an assertion to make it pass. If an existing test pins OLD wording that a
   task deliberately changes, update that string only, and list it in the task's receipt.
6. Never type the home directory expanded — write `~/` or `$HOME/`.
7. Test runs: targeted files in the foreground; the full suite only as in Task 7 (backgrounded with a
   log file — a silent foreground run over ~10 min kills your session).
8. "Not run" is reported as not run, never as passed.

## Authority matrix

| Operation | Status |
|---|---|
| Edit files in the worktree, run tests, local commits on the branch | allowed now |
| Update an existing test string that pins wording this plan changes | allowed (list it in the receipt) |
| Add a `## [0.55.6] — unreleased` CHANGELOG section | allowed (Task 7) |
| Bump version surfaces, push, merge, tag, release, refresh plugin | **prohibited** — reviewer does it |
| Anything outside the worktree (settings, other repos, live processes) | **prohibited** |

## Facts (labelled)

- *confirmed live 2026-11-07:* `origin/main` = `27861cc` (csl 0.55.5); the worktree starts there.
- *confirmed live:* picker venv python = `~/.local/share/free-agents/picker-venv/bin/python`.
- *confirmed live:* on pristine main, `tests/test_session_picker_pty.py` and
  `tests/test_picker_lowkey_api_dedupe_0505.py` already have ~43 failures in a serial run on this
  machine (environment-sensitive). These are NOT yours — compare failure SETS (Task 7), not counts.
- *confirmed live:* glyphs live in `bin/emoji_constants.py` as `CYCLE_LEFT = "❮"`, `CYCLE_RIGHT = "❯"`.
  The hover colour is typed out as a literal in `bin/session_picker.py`: rich style `"bright_blue"`
  (L1728-1729) and Textual CSS `ansi_bright_blue` (CSS block, L819-821).
- *confirmed live:* existing stepper patterns in `session_picker_model.py`:
  - `setting(key, label, choices, get, put, labels=..., head=..., value_prefix=..., value_suffix=...)`
    (L110) — for plain value lists. `labels` = display text per choice, SAME ORDER as `choices`
    (sizes the value cell). Example: telemetry row L1020 `*_toggle(s, "telemetry"), labels=("OFF", "ON")`.
  - `Action(key, label, run, step=lambda d: ..., labels=...)` — for custom steppers with side
    logic. Example: theme hue rows L2524-2531.
  - The value is the text after the LAST `": "` of the label, unless `head=` pins the split.
    Trailing notes that are not the value go in `value_suffix` (rendered after `❯`).
- *decision:* NiceGUI (`bin/fa_gui/`) is not on main — out of scope. Note it in the CHANGELOG as a
  follow-up: when it merges, its CSS must read the same constants.

## Review focus

1. Disabled rows (memguard rows when guard OFF; bucket capacity outside `smooth_bucket`; the
   bypass-default row under a managed policy) — Left/Right/click must NOT change them.
2. Direction: Left must step BACKWARD on every converted multi-value row (not just wrap forward).
3. Memguard soft/hard pair: a step must never save soft ≥ hard, in either direction.
4. Persist callbacks (`on_save`, `on_memguard_saved`, `on_cloud_saved`) still fire on EVERY route —
   a stepper whose `put` bypasses them changes the screen but not the saved file.
5. Hotkeys keep working (pressing the row's letter = +1 step), and `check_action_table` stays green.

---

### Phase 0 — preflight (no edits)

- [ ] Write exactly three sentences: what Task 1 changes, how Tasks 2-6 depend on it, and what the
      user sees when Task 7 is done.
- [ ] `cd ~/ClaudeWorkspace/.wt/fa-0556-feedback && git status --porcelain && git log --oneline -1`
      → expect empty status and `27861cc`-based HEAD (this plan's commit on top is fine).
- [ ] Baseline, serial, saved to a file:
      `~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_session_picker_model.py tests/test_cycle_ui_0554.py tests/test_cycle_indicators_0555.py tests/test_menu_polish_0555.py tests/test_cloud_session_env.py tests/test_new_features.py -q -p no:cacheprovider > /tmp/fa0556-baseline.txt 2>&1; echo exit=$?`
      Record the FAILED lines (may be none). Receipt: the exit code + failed list.

### Task 1: one definition for glyphs + hover colour

**Files:** modify `bin/emoji_constants.py` (next to `CYCLE_LEFT`), `bin/session_picker.py` (CSS
block ~L778-830, render ~L1728-1729); test `tests/test_cycle_single_source_0556.py` (create).

**Produces:** `ec.CYCLE_HOVER = "bright_blue"` (rich colour name) and
`ec.CYCLE_HOVER_CSS = "ansi_" + CYCLE_HOVER` (Textual CSS form, derived — never typed separately).

- [ ] Test `test_hover_colour_has_one_definition`: read `bin/session_picker.py` source; assert the
      literals `"bright_blue"` and `ansi_bright_blue` occur ZERO times in it, and assert
      `Picker.CSS` (the class attribute) contains `f"color: {ec.CYCLE_HOVER_CSS};"`.
- [ ] Test `test_glyph_literals_only_in_emoji_constants`: no `❮` / `❯` character in
      `bin/session_picker.py` or `bin/session_picker_model.py` source outside comments
      (strip `#…` comment tails before checking).
- [ ] Run → both FAIL (literals still present).
- [ ] Implement: add the two constants; in the CSS string replace each `ansi_bright_blue` with a
      sentinel `@CYCLE_HOVER@` and append `.replace("@CYCLE_HOVER@", ec.CYCLE_HOVER_CSS)` to the
      `CSS = """…"""` assignment; render uses `ec.CYCLE_HOVER`. Fix any glyph literal in code.
- [ ] Known-bad discriminant: temporarily set `CYCLE_HOVER = "bright_red"`, run
      `tests/test_cycle_ui_0554.py::HoverBehaviour` → must go RED (it pins the CSS text); revert,
      `git diff --stat bin/emoji_constants.py` shows only your intended lines. Receipt: both runs.
- [ ] Run the new file + `tests/test_cycle_ui_0554.py` + `tests/test_menu_polish_0555.py` → green
      except baseline failures.
- [ ] Commit: `refactor(csl): one definition for cycle glyph hover colour (0.55.6)`.

### Task 2: Local "Auto-sync local registry" + Lowkey rows

**Files:** `bin/session_picker_model.py` L1444-1446 (auto-sync), L3445-3448 (lowkey effort),
L3454-3456 (lowkey remote filters); test `tests/test_toggle_surfaces_0556.py` (create — Tasks 2-5 add
to it).

- [ ] Shared test helper in the new file:
      `def assert_stepper(t, action, n_labels=None)` → `t.assertIsNotNone(action.step)`;
      `t.assertTrue(action.labels)`; if `n_labels`, `t.assertEqual(len(action.labels), n_labels)`.
      Plus `def by_key(acts, k)`.
- [ ] Tests (model level, `m.LocalScreen(m.Settings(), [], owner=m.HOME_OWNED)` style — copy
      construction from `tests/test_session_picker_model.py:82`; for Lowkey copy from
      `tests/test_lowkey_api_route_0540.py`):
  - `test_local_autosync_is_a_toggle`: row `y` is a stepper with labels `("OFF","ON")`; `step(+1)`
    then reading `settings.memguard["registry_auto_sync"]` flips it; the `on_memguard_saved`
    callback (set a recording lambda on `Settings`) received `{"registry_auto_sync": <new>}`.
  - `test_lowkey_effort_uses_short_phrasing`: row `e`'s label and every entry of `labels` contain no
    `"sent as"`; labels == the remote lane's form `tuple(_effort_label(c) for c in effort_choices("lowkey"))`.
  - `test_lowkey_remote_filters_is_a_toggle`: row `f` is a stepper with labels `("OFF","ON")`;
    `step(-1)` flips `_remote_filters` (both directions toggle a 2-value row).
- [ ] Run → FAIL.
- [ ] Implement: auto-sync →
      `setting("y", <same label>, (False, True), lambda: bool(s.memguard.get("registry_auto_sync")), self._put_registry_auto_sync, labels=("OFF","ON"))`
      where `_put_registry_auto_sync(v)` is `_toggle_registry_auto_sync` rewritten to SET `v` (keep
      the callback). Lowkey effort: label `f"…Effort: {_effort_label(s.lowkey_effort)}"`, labels as
      in the test. Lowkey filters: `setting("f", …, (False, True), lambda: self._remote_filters,
      <put that sets it exactly like _toggle_remote_filters does>, labels=("OFF","ON"), section="model")`.
- [ ] Known-bad: make the auto-sync put skip the callback → `test_local_autosync_is_a_toggle` RED;
      restore.
- [ ] Run → green. Commit `fix(csl): auto-sync + lowkey rows use the cycle design (0.55.6)`.

### Task 3: Remote Filter Settings + Cloud Session Configuration

**Files:** `session_picker_model.py` `RemoteFiltersScreen.actions` (L2191-2204),
`CloudConfigScreen.actions` (L2250-2268) and its `_cycle_*` helpers (L2275-2364).

- [ ] Tests:
  - `test_remote_filters_rows_are_toggles`: rows `h f u o` are steppers, labels `("HIDDEN","SHOWN")`;
    row `x` (report) stays a plain nav (`step is None`).
  - `test_cloud_rows_are_steppers`: rows `x` (agent dispatch, labels = `cse.ROUTE_LABELS` values in
    `cse.ROUTE_MODES` order), `p` (bypass, `("OFF","ON")`), `d` (default mode) are steppers; `s`
    (security review) too when `security_review_offered`; row `e` (probe) stays plain.
  - `test_cloud_left_steps_backward`: on `x`, `step(+1)` then `step(-1)` returns to the start value.
  - `test_bypass_note_is_outside_the_value`: row `p`'s `labels` contain no `NOT_GATEWAY_NOTE`
    text; the note is in `value_suffix`.
  - `test_default_mode_row_inert_when_policy_blocks`: monkeypatch `cse.bypass_blocked_by_policy`
    → a reason string; row `d` is `enabled=False` and calling `step(+1)` leaves `default_mode` unchanged.
- [ ] Run → FAIL.
- [ ] Implement with `setting(...)` for the four filters and bypass (note → `value_suffix`); give
      `_cycle_route`, `_cycle_default_mode`, `_cycle_security_review` a `d: int = 1` parameter that
      steps that way through their order, and build those rows as `Action(..., run=lambda: f(+1),
      step=lambda d: f(d), labels=...)`. Default-mode's "overruled by the registered cloud profile"
      / "disabled — reason" text goes in `value_suffix`; when disabled, the row may stay a plain
      disabled Action (no step).
- [ ] Known-bad: make `_cycle_route` ignore `d` → `test_cloud_left_steps_backward` RED; restore.
- [ ] Run → green. Commit `fix(csl): remote filters + cloud config use the cycle design (0.55.6)`.

### Task 4: NVIDIA Rate Limiter + RAM-usage (OOM) protection

**Files:** `RateLimiterScreen` (L2543-2600), `MemoryGuardScreen` (L2605-2670), shared `_next` (L809).

- [ ] Tests:
  - `test_rate_limiter_rows_are_steppers`: rows `o r w d u y z` are steppers whose `labels` cover
    every step (e.g. `r` → `("10","20","30","40")`, `w` → `("30s","60s","120s","300s")`,
    `y` → `("×1.5","×2.0","×3.0")`); `c` is a stepper only when mode is `smooth_bucket`, else
    disabled; `i`/`x` stay plain tools.
  - `test_rate_limiter_left_steps_backward`: `r` at 40, `step(-1)` → 30 saved via `on_save`.
  - `test_memguard_rows_are_steppers`: `g t c` toggles `("OFF","ON")`; `s h w` steppers.
  - `test_memguard_pair_never_breaks_either_direction`: from every (soft, hard) start in the steps,
    `step(+1)` and `step(-1)` on `s` and `h` always leave `soft < hard`.
  - `test_memguard_rows_inert_when_guard_off`: guard OFF → `t s h w c` disabled.
- [ ] Run → FAIL.
- [ ] Implement: `_next(choices, current, d=1)` (default keeps old callers identical); rate-limiter
      `_cycle(key, steps, default, d=1)`; memguard `_cycle(key, steps, d=1)` walks `order` in
      direction `d` with the same skip rule. Rows: `Action(..., run=+1, step=d, labels=...)` /
      `setting` for toggles.
- [ ] **Wording decision (the value must sit at the END, after the last `": "`):** the three
      memguard sentences that carry the value mid-sentence become:
      `s` → `"Compact idle sessions at RAM: ≥85%"` (labels `"≥75%"…`), `h` → `"Compact the largest
      session at once at RAM: ≥95%"`, `w` → `"Quiet time before the guard types into a session: 5s"`.
      Use exactly these; the reviewer will confirm wording with the user.
- [ ] Known-bad: drop the skip rule in memguard `_cycle` → pair test RED; restore.
- [ ] Run → green. Commit `fix(csl): rate limiter + RAM guard use the cycle design (0.55.6)`.

### Task 5: theme menu — four rows, one centered value column

**Files:** `ThemeConfigScreen` (L2373-2540), render in `session_picker.py` (~L1740-1763).

**Produces:** a screen attribute `align_cycle_values: bool = False` on `Screen`;
`ThemeConfigScreen.align_cycle_values = True`.

Meaning (decision): when True, every stepper row on that screen pads its head to the widest head
among them (display width, `cell_len`) so `❮` sits in ONE column, and uses ONE value-cell width (the
widest label across those rows) with values centered by the existing lead/trail rule — so `❯` is one
column too. Other screens keep the 0.55.4 per-row width (the user asked for that; do not change it).

- [ ] Pilot test (copy `_action_option` from `tests/test_cycle_ui_0554.py`):
      `test_theme_rows_share_one_value_column`: open the theme screen, for rows `l r y u` assert
      `text.index("❮")` (via `cell_len`) is identical for all four, and `rindex("❯")` too; and each
      value is centered (`right <= left + 1`, both ≥ 1, as in `_cell_parts`).
- [ ] `test_other_screens_keep_per_row_width`: Local screen rows `t` and `m` still have different
      cell widths (`3+2` vs `len("DISABLED")+2`) — the existing 0.55.4 rule.
- [ ] Run → FAIL. Implement. Known-bad: set the attribute False → theme test RED; restore.
- [ ] Run → green. Commit `fix(csl): theme menu values share one centered column (0.55.6)`.

### Task 6: render-level proof on every touched screen

- [ ] Pilot test `test_every_setting_row_renders_glyphs`: for each screen (Local `y`, Lowkey `e f`,
      Remote filters `h f u o`, Cloud `x p d`, Rate limiter `o r w d u y z`, RAM guard `g` + others
      with the guard ON, Theme `l r y u`) the rendered prompt contains `ec.CYCLE_LEFT` and
      `ec.CYCLE_RIGHT`. Find how each screen is reached in existing PTY/pilot tests
      (`grep -n "MemoryGuard\|RateLimiter\|CloudConfig\|RemoteFilters" tests/*.py`); a screen you
      cannot reach in Pilot is listed in the receipt as NOT render-tested, never skipped silently.
- [ ] Known-bad: revert ONE row (e.g. remote filter `h`) to a plain Action → this test RED; restore.
- [ ] Commit `test(csl): render-level glyph check on every setting surface (0.55.6)`.

### Task 7: changelog + full-suite gate

- [ ] Add to `CHANGELOG.md` above `## [0.55.5]` a `## [0.55.6] — unreleased` section, one bullet per
      user-visible change (match 0.55.5's style), plus a "Follow-up" line: NiceGUI must read
      `ec.CYCLE_*` when `feat/nicegui-frontend` merges. Do NOT touch any version field.
- [ ] Full suite on the branch, backgrounded:
      `~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/ -q -p no:cacheprovider -n auto > /tmp/fa0556-branch.txt 2>&1; echo exit=$? >> /tmp/fa0556-branch.txt`
      Then the SAME command on pristine main in a throwaway detached worktree
      (`git worktree add --detach /tmp/fa0556-base origin/main`, run there into `/tmp/fa0556-main.txt`,
      then `git worktree remove /tmp/fa0556-base`).
- [ ] Receipt: `comm -13 <(grep ^FAILED /tmp/fa0556-main.txt | sort) <(grep ^FAILED /tmp/fa0556-branch.txt | sort)`
      — tests failing ONLY on the branch. Each must be fixed, or be an OLD-wording pin updated under
      rule 5 (name it). An empty `comm` output is only valid if both files contain a `passed` line.
- [ ] Commit `docs(csl): 0.55.6 changelog`.

## Adversarial review

- Convert the label but forget `step` → row still renders bare → Task 6 render test.
- `put` that bypasses the persist callback → screen shows new value, file keeps old → Task 2/4 callback assertions.
- Stepper ignores direction → Left goes forward → `*_left_steps_backward` tests.
- Theme alignment applied globally → breaks the user's 0.55.4 per-row rule → `test_other_screens_keep_per_row_width`.
- Hover colour "centralised" by adding a constant but leaving the literals → `test_hover_colour_has_one_definition` (zero literals).
- A pipeline hides pytest's exit code → always `> file 2>&1; echo exit=$?`, never `| tail`.
- Editing in the canonical checkout → rule 1; every receipt starts with `pwd`.

## Final delivery (write this at the end)

Per task: commit SHA, the test command, its result line, the known-bad run's result. List: every
existing test string you updated (rule 5); every screen NOT render-tested; anything not done. State
that nothing was pushed, merged, tagged or version-bumped.
