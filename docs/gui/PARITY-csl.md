# csl ↔ NiceGUI parity inventory (2026-11-16, corrected 2026-11-16 after live probing)

Measured on branch `feat/nicegui-frontend` (rebased onto main 0.59.3, commit b2e581f).
Sources: csl screens = `class *Screen` in `bin/session_picker_model.py` + all `nav_target=`
values; GUI = `bin/fa_gui/app.py`.

**Correction to the first draft of this file:** the `/{full_path:path}` catch-all already
renders EVERY known nav target through the shared `picker_core.build_screen` (verified live:
all probed routes return 200). The real gap was never empty pages — it was that only the
`/local` and `/remote` lanes loaded their inventory data; every other screen rendered with
an empty cache. Fixed in Task 1: the catch-all now prefetches each screen's declared
`build.prefetch` kinds through a loader map mirroring the TUI's `LOADERS`
(`tests/test_gui_prefetch.py`). What remains missing per screen is its interactive/write
bits, listed below.

**Legend:** ✅ real GUI page · ◐ page exists but the screen's actions are partial ·
❌ no page — falls into the catch-all (empty page + Back-to-Home) · the GUI's adapter maps
the nav target but no `@ui.page` exists for it.

## Screen parity table

| csl screen / nav target | csl source | GUI status (measured) | Still missing |
|---|---|---|---|
| home | `HomeScreen` | ✅ `/home` renders | — |
| local sessions | `LocalScreen` | ◐ `/local` renders with data | download entry point, #missing cleanup offer |
| remote sessions | `RemoteScreen` | ◐ `/remote` renders with data | `remote_filters` screen's controls, tool:rl-* panels |
| lowkey | `LowkeyScreen` via `tool:lowkey` | ◐ renders via catch-all (data now prefetched) | prompt:session dialog |
| download local models | `DownloadScreen` via `tool:download` | ◐ renders + catalog prefetched (29a6be1); confirm dialog wired (88a3b30) | the queue-row click wiring (entry rows → toggle_selected) — the only untested-by-browser piece left |
| about | `AboutScreen` | ◐ renders | — |
| api keys | `APIKeysScreen` | ◐ renders | key add/remove write path (`setup-api-keys.py`), open: URLs |
| rate limiter | `RateLimiterScreen` | ◐ renders | tool:rl-status / tool:rl-reset actions |
| memguard (OOM guard) | `MemoryGuardScreen` | ◐ renders | — |
| cloud session config | `CloudConfigScreen` | ◐ renders | route/security-review write path |
| theme config | `ThemeConfigScreen` | ◐ renders + GUI-only appearance section (`render_appearance`) | — |
| remote filters | `RemoteFiltersScreen` | ◐ renders | filter write path |
| runtime manager / backend | `RuntimeManagerScreen` | ◐ renders; version data prefetched (Task 1) | install/upgrade/validate/repair WRITE actions (`runtime_versions:*` → backend-manager CLIs) |
| runtime + launchd | `RuntimeScreen`, `LaunchdScreen` | ◐ renders | tool:rt-launchd-* actions |
| hidden-model report | `tool:report` | ◐ renders | report generation action |
| prompts (session name, rt-remove, download review) | `prompt:*` | ◐ dialog core wired (Task 1): payload + confirm→`command_for` argv unit-tested | browser click-through of the queue UI |
| settings write path (all screens) | savers | ✅ fixed d33c609: every build_screen call passes savers; browser test clicks /rate_limiter's ▶ and asserts session-menu.local.json changes | — |
| warnings | `warn:*` | ❌ | toast/banner pattern |
| quit | — | ✅ handled in app.py (target == "quit") | — |

## Special functionality: local model list registration & update

csl's local model list is fed by `csl --inventory` + the catalog overlay store
(`~/.claude/free-agents/model-catalog.local.psv` via `bin/la-catalog-overlay.sh`,
migration notice from the SessionStart hook). Registration/update flows in csl:

1. **Download/register a model** — `DownloadScreen`: queue from the catalog, review,
   one `download-models.sh --select A …` call. GUI: missing (❌ above).
2. **Catalog overlay edits** — user-local rows in `model-catalog.local.psv`
   (`la-catalog-overlay.sh path` to print it); migration from old layouts. GUI: missing.
3. **#missing cleanup** — registered alias whose weights are gone is offered once per
   session for removal/migration. GUI: missing.
4. **Backend install/upgrade** — runtime manager screen (`download-models.sh` for
   backends, `runtime_versions:install/validate/info`). GUI: missing.
5. **Model profiles** — `la-model-profile.py` list/artifact/… drive capability display
   in the list. GUI: reads the catalog for display only.

GUI currently has: model LIST display on /local (load_local_models) and launch — nothing
that writes the list or the overlay store.

## Suggested build order (feeds the plan)

1. tool:download + tool:lowkey routes (they already map) — highest user value
2. api_keys, rate_limiter+tools, memguard, theme_config, cloud_config (settings screens)
3. runtime_manager (+versions) — the backend manager, includes update functionality
4. remote_filters, about, prompts/warns, hidden-model report
5. model-list registration/update (section above) — depends on #1 and #3
