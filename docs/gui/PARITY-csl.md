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
| local sessions | `LocalScreen` | ✅ `/local` renders with data + #missing cleanup offer (red rows → confirm → `csl --unregister-models`) | the doc's earlier "download entry point" note was a mis-inventory: csl's LocalScreen has no such row either (it lives on Home → /download) |
| remote sessions | `RemoteScreen` | ◐ `/remote` renders with data | `remote_filters` screen's controls, tool:rl-* panels |
| lowkey | `LowkeyScreen` via `tool:lowkey` | ✅ renders via catch-all (data prefetched) + model CHOOSER (local + API agent rows; a row selects, no launch; selection persists across the dialog round-trip) + prompt:session dialog (typed name, csl validation) + prompt:oneshot dialog; convo launch carries --session NAME + --provider (Savers.last_launched 4-arg mode fix) | — |
| download local models | `DownloadScreen` via `tool:download` | ✅ queue rows + review dialog + confirm→CLI argv all browser-proven (2eb5ea1) | — |
| about | `AboutScreen` | ✅ renders | — |
| api keys | `APIKeysScreen` | ✅ renders the provider list with live Saved/Missing status, signup link and Add key; the key write goes through the SAME Store (`install/setup-api-keys.py`, `LA_API_KEYS_DIR`) | key REMOVE action |
| rate limiter | `RateLimiterScreen` | ✅ renders; the reset row confirms (csl's wording) and dispatches the state-file delete | — |
| memguard (OOM guard) | `MemoryGuardScreen` | ✅ renders (switch + cycle controls, switch toggles on name/value click) | — |
| cloud session config | `CloudConfigScreen` | ✅ renders; route/timeout-tier rows cycle AND persist to ~/.claude/settings.json env (firewall-tested with a temp HOME), label rebuilt in place | — |
| theme config | `ThemeConfigScreen` | ✅ renders + GUI-only appearance section (`render_appearance`): presentation cycle (compat/modern tiles), translucent switch, opacity slider 0–100%, blur request, 🌙 dark-mode switch (dark-shell CSS tokens flip text). Native vibrancy MATERIAL now follows dark_mode (mediumLight/ultraDark; fail-open pyobjc patch on pywebview's BrowserView, in-process readback spike-verified 2026-11-20), so 0% opacity is bright in light mode; the ×1.35 tint hack was removed — tint_alpha is the raw opacity in every mode. 🌗 follow-system switch (launch-only). Tile-emoji theme option for modern tiles: subdued (default) | noto (mono line-art variable font, theme ink color, VS16-stripped) | full (Apple color emoji). The Tile emoji row shows only in the tiles menu (irrelevant in compatibility); the presentation value reads "Tiles menu"; theme rows toggle on name/value click (csl parity); the theme menu's real Back-to-home tile is the live style preview; dark-mode subdued = full-brightness bitmap + white silhouette overlay (::after, 0.6) since brightness>1 blew out the highlights. Theme accent rows now show a live swatch chip (name + hex from THEME_PRESETS/the stored custom value) that follows the cycled value. Remaining gap: theme color presets (yellow/lime) + swatch preview | — |
| remote filters | `RemoteFiltersScreen` | ✅ renders; filter toggles APPLY to the remote list at paint time (csl's visible_agents predicate) | — |
| runtime manager / backend | `RuntimeManagerScreen` | ✅ renders; version data prefetched; the `runtime_versions:<op>` sub-screens render the version list and a row click dispatches the op (remove confirms via prompt:rt-remove first) | — |
| runtime + launchd | `RuntimeScreen`, `LaunchdScreen` | ✅ renders; status/run-once rows dispatch; install/uninstall confirm with csl's question + detail line, Yes dispatches `manage-backend.py launchd install|uninstall`, No runs nothing and says "launchd unchanged — nothing was run" (browser-proven) | — |
| hidden-model report | `tool:report` | ✅ the row dispatches the report CLI (browser-proven) | — |
| prompts (session name, rt-remove, download review) | `prompt:*` | ◐ dialog core wired (Task 1): payload + confirm→`command_for` argv unit-tested | browser click-through of the queue UI |
| settings write path (all screens) | savers | ✅ fixed d33c609: every build_screen call passes savers; browser test clicks /rate_limiter's ▶ and asserts session-menu.local.json changes | — |
| warnings | `warn:*` | ✅ notice page (message + Back-to-Home; the versions keep-one-core guard's warn: nav routes to it) | — |
| quit | — | ✅ handled in app.py (target == "quit") | — |

## Special functionality: local model list registration & update

csl's local model list is fed by `csl --inventory` + the catalog overlay store
(`~/.claude/free-agents/model-catalog.local.psv` via `bin/la-catalog-overlay.sh`,
migration notice from the SessionStart hook). Registration/update flows in csl:

1. **Download/register a model** — `DownloadScreen`: queue from the catalog, review,
   one `download-models.sh --select A …` call. GUI: missing (❌ above).
2. **Catalog overlay edits** — user-local rows in `model-catalog.local.psv`
   (`la-catalog-overlay.sh path` to print it); migration from old layouts.
   GUI: ✅ `/catalog` page (add/remove rows through `catalog_overlay.py`'s validated
   read/write, comments preserved, subdir defaults to the repo basename as
   `download-models.sh` requires); reachable via "✏️ Edit overlay catalog" on /download;
   browser-proven: store-file bytes asserted, invalid rows rejected with notify + no write,
   download-screen catalog cache refreshed after a write.
3. **#missing cleanup** — registered alias whose weights are gone is offered once per
   session for removal/migration. GUI: ✅ `/local` renders `#missing` aliases as red
   `data-missing` rows; clicking one opens a confirm dialog whose Yes dispatches
   `csl --unregister-models <alias>` (the same subcommand csl's panel uses), refreshes
   the local inventory synchronously and returns to `/local`; browser-proven (stub argv
   asserted, row detaches after removal).
4. **Backend install/upgrade** — runtime manager screen (`download-models.sh` for
   backends, `runtime_versions:install/validate/info`). GUI: missing.
5. **Model profiles** — `la-model-profile.py` list/artifact/… drive capability display
   in the list. GUI: reads the catalog for display only.

GUI currently has: model LIST display on /local (load_local_models), launch, and the
/catalog overlay-edit screen that writes the overlay store (see item 2).

## Suggested build order (feeds the plan)

1. tool:download + tool:lowkey routes (they already map) — highest user value
2. api_keys, rate_limiter+tools, memguard, theme_config, cloud_config (settings screens)
3. runtime_manager (+versions) — the backend manager, includes update functionality
4. remote_filters, about, prompts/warns, hidden-model report
5. model-list registration/update (section above) — depends on #1 and #3
