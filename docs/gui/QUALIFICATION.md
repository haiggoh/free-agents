# GUI launcher qualification receipts

## 2026-11-03 — macOS 26.7.2 (25G332), Apple Silicon, MANAGED Mac with Xcode installed

Not a clean default Mac: corporate-managed, developer tools present. Clean-Mac behaviour is UNTESTED.

Generation: `install/gui-launcher.py generate` from a source checkout (channel `checkout`), 0.23 s.
- `xattr`: `com.apple.provenance` only, no `com.apple.quarantine`
- `codesign -dv`: `Identifier=io.github.haiggoh.free-agents.launcher`, `Signature=adhoc`, `TeamIdentifier=not set`; `--verify --strict --deep` OK
- `spctl --assess`: `rejected` (expected for ad-hoc; Gatekeeper only blocks quarantined downloads)
- resolver skipped the installed plugin cache 0.50.4 (no GUI) and used the checkout via PLUGIN_HINT

Finder double-click (user, 10:38): the app opened the native window and rendered the core's Home
menu with emoji correct. Interim page is labels-only (no click/hover/loading yet, by design until
plan Task 9). Log: `locate.sh: starting …/bin/free-agents-gui --native`, no error. After the window
was closed: 0 `fa_gui/main.py` processes left.
- assumed (not explicitly reported): no "Apple could not verify" sheet and no Terminal window —
  the user reported only that the app opens. Re-confirm (a)/(b) at the next run.

Native selftest (Task 2): WKWebView loaded the UI (`connected`), button round trip wrote v0.50.4,
window close exited 0 with no leftover processes.

Known gaps: Dock label reads the Python process (cosmetic); a second click starts a second
instance; failure drills (renamed venv / missing plugin → dialog) NOT yet run on the real app
(covered only by locate.sh unit tests).

UNTESTED: clean default Mac, other macOS versions, Intel, Homebrew bottle lifecycle, MDM-restricted
Macs, Linux (no Linux runtime on this machine).

## 2026-11-04 — failure drills on the real app (Task 4 remainder, run by session 86018b5a)

Both drills launched the real `~/Applications/Free Agents.app` via `open` with a Finder-like
environment (`env -i HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin`):

- Drill A — `GUI_VENV=/nonexistent` in `~/.config/free-agents/gui-launcher.conf`: the app showed
  a native dialog naming `install/setup-gui.sh` (user-confirmed: "Dialog naming setup-gui.sh
  (expected)"). `locate.sh --launch` under the same env printed the missing-venv message and
  exited 2.
- Drill B — `FREE_AGENTS_PLUGIN=/nonexistent`: native dialog about the bad plugin path
  (user-confirmed expected); `locate.sh --launch` printed "… is not a Free Agents plugin dir."
  and exited 2.
- After each drill the config was restored byte-identical (`cmp` against the pre-drill copy:
  RESTORED-IDENTICAL) and `locate.sh --check` resolved plugin + gui venv again with exit 0.

Task 4 is therefore complete on this Mac: generation, code validity (adhoc signature, spctl
expected rejection for non-quarantined), Finder first-launch, native selftest round trip, clean
process exit, and both failure drills with actionable native dialogs.

## 2026-11-07/08 — continuation-plan Phases 0–8 (branch feat/nicegui-frontend, session receipts)

Browser-mode evidence (playwright + unit, all on this managed Mac; GUI venv nicegui 3.19.0 /
pywebview 6.2.1 / playwright 1.64.1 / Python 3.12.16, probed via importlib.metadata):

- Phase 0 reconcile: branch `d560acd`, clean, 12 ahead/35 behind main (base `0a5b60b`); baseline
  core 112 passed/2 skipped, GUI-venv 55 passed/147 subtests after restoring the missing
  playwright chromium-headless-shell cache (`gui-venv/bin/python -m playwright install
  chromium-headless-shell` — env-only fix).
- Phase 1 (nav metadata, `3a83e4f`): `Action.nav_target` stamped on all 36 nav sites;
  adapter raises on a nav row without it; app.py never calls `run()` at render (AST guard +
  runtime raiser test). Mutations M1/M2/M5 red-verified.
- Phase 2 (per-client state, `be14454`): UI state keyed by websocket client id; Loader
  per-client page generations + coalesced same-kind loads; two-client playwright isolation
  test; mutation red-verified.
- Phase 3 (keyboard + dependents, `288887f`, `ffe496b`): focus-scoped Left/Right/Enter/Space
  on cycle rows (search-input keys never step); dependent-row refresh via render_plan id-diff
  (classifier row appears/disappears with auto-mode); `data-action` row identity. M3/M10 red.
- Phase 4 (`096f454`, `6e48eab`, `02e3489`, `477502c`): GUI-only appearance store
  (gui-appearance.json; semantic store untouched), strict validators, theme-screen section;
  review-pass fixes (load warn-path, de-vacuated render test). M4/M6/M7 red.
- Phase 5 (`3f15c75`, `160d937`): modern emoji tiles with identical nav targets/handlers;
  test-isolation fix for the shared appearance file.
- Phase 6 (`ed11fdd`): `native_window_args()` (transparent/vibrancy) passed to ui.run in
  NATIVE mode only; one live #tint-layer at the stored opacity (50..100 tested .50/.75/1.00).
- Phase 8 (`73ef1d6`): dark tokens via `body:has(.dark-shell)`; dark tint; computed-style test
  proves light/dark differ.
- Final gates: non-browser 135 passed/2 skipped/83036 subtests; GUI-venv 69 passed/266
  subtests (playwright 22/22). Two cross-family Nemotron validator dispatches over the diff
  (d560acd..HEAD); all real findings fixed, claimed-false ones verified against source.

NOT TESTED (explicitly, per plan receipt rules): native desktop blur/vibrancy visual A/B/C
(needs real OS screenshot/observation, not a terminal session); clean default Mac, Intel,
other macOS versions; Linux (no runtime on this machine — waypoint nicegui-linux-qualification);
MDM-restricted Macs; Homebrew bottle lifecycle.
