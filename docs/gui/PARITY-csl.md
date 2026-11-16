# csl ↔ NiceGUI parity inventory (2026-11-16)

Measured on branch `feat/nicegui-frontend` (rebased onto main 0.59.3, commit b2e581f).
Sources: csl screens = `class *Screen` in `bin/session_picker_model.py` + all `nav_target=`
values; GUI pages = `@ui.page` routes in `bin/fa_gui/app.py` (`/`, `/home`, `/local`,
`/remote`, plus the `/{full_path:path}` catch-all) + the tile→route mapping in
`bin/fa_gui/app.py:454` (`tool:lowkey`→`lowkey`, `tool:download`→`download` — routes that
do not exist as pages).

**Legend:** ✅ real GUI page · ◐ page exists but the screen's actions are partial ·
❌ no page — falls into the catch-all (empty page + Back-to-Home) · the GUI's adapter maps
the nav target but no `@ui.page` exists for it.

## Screen parity table

| csl screen / nav target | csl source | GUI status | Notes |
|---|---|---|---|
| home | `HomeScreen` | ✅ `/home` | tiles render; tool tiles map lowkey/download (see below) |
| local sessions | `LocalScreen` | ◐ `/local` | model list renders (`load_local_models`); missing: download entry, #missing cleanup offer, per-model launch params (effort/temp/thinking rows exist via model group) |
| remote sessions | `RemoteScreen` | ◐ `/remote` | API list + filters row exists in csl; GUI remote page lacks `remote_filters` screen and tool:rl-* panels |
| lowkey | `LowkeyScreen` via `tool:lowkey` | ❌ mapped to `/lowkey`, no page | catch-all empty page |
| download local models | `DownloadScreen` via `tool:download` | ❌ mapped to `/download`, no page | queue-then-confirm flow → `download-models.sh --select` |
| about | `AboutScreen` | ❌ | |
| api keys | `APIKeysScreen` | ❌ | key presence/setup, open: URLs |
| rate limiter | `RateLimiterScreen` | ❌ | incl. tool:rl-status / tool:rl-reset |
| memguard (OOM guard) | `MemoryGuardScreen` | ❌ | |
| cloud session config | `CloudConfigScreen` | ❌ | routes, security review |
| theme config | `ThemeConfigScreen` | ❌ | swatches/hue steppers exist in csl model |
| remote filters | `RemoteFiltersScreen` | ❌ | |
| runtime manager / backend | `RuntimeManagerScreen` | ❌ | install/upgrade/repair/validate backends, version ops (`runtime_versions:*`) |
| runtime + launchd | `RuntimeScreen`, `LaunchdScreen` | ❌ | incl. tool:rt-launchd-* |
| hidden-model report | `tool:report` | ❌ | |
| prompts (session name, rt-remove, download review) | `prompt:*` | ❌ | modal prompts — GUI needs its own dialog pattern |
| warnings | `warn:*` | ❌ | |
| quit | — | ✅ handled in app.py (target == "quit") | |

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
