# Feature parity audit — through v0.50.1 (2026-11-01)

Every feature shipped in v0.49.7 → v0.50.1, mapped to its claim source (plan/waypoint/roadmap
item), the code that implements it (file:line at tag v0.50.1), its tests, and the commit that
shipped it. Verified per script against the working tree at `origin/main` = `9a78293`
(tag `v0.50.1` = `1023e33`). Claim sources: the 2026-10-31 handoff plan
(`~/.claude/plans/2026-10-31-free-agents-0493-0500-handoff.md`, items H1–H7, F1–F6),
`docs/ROADMAP.md` §0.50.0 items 1–10, the wifi-cutoff plan
(`~/.claude/plans/wifi-cutoff_error_handling_free-agents.md`), the archived waypoints
`wifi-cutoff-error-handling`, `pty-litellm-venv-tests-flake`, and the user's 2026-11-01
regression reports (nvidia catalog pollution, lowkey API list, About pin/count, load times).

## v0.49.7 — roster hotfix + handover skills (H1–H7)

| Feature | Claim | Code | Tests | Commit |
|---|---|---|---|---|
| H1 GLM 5.3 full retired from pinned rows | handoff H1 | `config/remote-registry.yaml` (no `z-ai/glm-5.3` in visible rows), `config/broken-nvidia-models.json` entry | `tests/test_glm53_retired_0493.py` | `551443d` |
| H1 catalog pollution guard (added 0.50.1 after the regression) | user report 2026-11-01 | `tests/conftest.py` `pytest_configure` (write_catalog_cache tripwire), `tests/test_picker_load_race.py` autouse sandbox fixture | load-race suite 31/31 | `7f0a706`, this branch |
| H2 GLM Flash infobox text | handoff H2 | `config/remote-agents.sh` (nvidia-glm53 rows) | roster tests | `551443d` |
| H3 Le Chonk first in session lists | handoff H3 | `config/dispatch-preferences.json`, `config/remote-agents.sh`, `config/remote-registry.yaml` | `tests/test_remote_session.py` | `41da99a`/`1cac67e` |
| H4 infobox name dedupe | handoff H4 | `bin/session_picker_model.py:514 strip_leading_name` | `tests/test_picker_infobox_0493.py` | `3b67f24` |
| H5 fallback chains drop EOL ids | handoff H5 | `bin/session-fallbacks.py:41 broken_model_ids` | `tests/test_fallback_eol_0493.py` | `3b67f24` |
| H5 `--swap-model` refuses EOL ids (0.50.0 F5-adjacent) | handoff follow-up | `bin/remote-session.sh _eol_reason/try_acquire guard` | `tests/test_swap_model.py` | `4ded9fa`/`62f18e3` |
| H6 always-on Read `pages` guard | handoff H6 | `hooks/read-pages-guard.py`, registered in `hooks/hooks.json:77` | `tests/test_read_pages_guard_0493.py` | `3b67f24` |
| H7a stored last-launch effort default | handoff H7a | `bin/last-effort.py`, `bin/remote-session.sh` (get/set at launch) | `tests/test_last_effort_0493.py` | `3b67f24` |
| H7b interactive-window handover launch | handoff H7b | `skills/free-session-handover/SKILL.md` + `bin/handover-verify.sh --expect` | skill + verify tests | `3b67f24` |
| H7e `offload-to-local` → `offload` rename | handoff H7e | `skills/offload/`, `hooks/offload-nudge.py`, README | grep sweep clean (CHANGELOG history only) | `41da99a` |
| H2-corr GLM Flash speed caveat | handoff H2 | `config/remote-agents.sh:81` | — | `41da99a` |

## v0.49.8 — gate de-flake + orphan cleanup

| Feature | Claim | Code | Tests | Commit |
|---|---|---|---|---|
| la_evict port-pick race: lock + retry | waypoint test-la-evict-sh-fixed-port | `tests/test_la_evict.sh` wrapper (mkdir lock, LA_EVICT_BASE_FROM retry) | `tests/test_la_evict_ports.py` + planted fake-stolen-port run | `dd27beb`, `2fffb58` |
| litellm state-file torn-read | waypoint test-litellm-contract-py-test | `tests/test_litellm_contract.py read_json_with_retry` | `StateFileReadTests` (mutation-checked) | `dd27beb` |
| orphaned `skills/offload-to-local/` removed | waypoint free-agents-delete-orphaned | dir deleted; content in `skills/offload/references/` | layout test in `test_skill_budget.py` | `dd27beb` |
| bin_health deadline assertion | waypoint test-backend-bin-health-sh | `tests/test_backend_bin_health.sh` (elapsed < 60 deadline) | suite 10/10 | `73ed86e` |

## v0.50.0 — Picker UX minor (roadmap §0.50.0 items 1–10 + F1–F6)

| Item | Code | Tests | Commit |
|---|---|---|---|
| 1 lowkey menu reorder, no Advanced group | `bin/session_picker_model.py LowkeyScreen.actions` | `LowkeyTests.test_menu_order_…` | `6c6a084` |
| 2 api-keys hide/show toggle (persisted, schema 8) | `APIKeysScreen._hide_item`, `session_menu_state.py hidden_providers` | `APIKeysHideToggleTests`, `test_hidden_providers_round_trip` | `27e3b1c` |
| 3 theme hue rotation + shell swatch | `ThemeConfigScreen._shift_hue/_hue_deg`, `_lane_label` shell slice | `ThemeHueShiftTests` | `29c3ec2` |
| 4 README settings table | `README.md` "Picker settings reference (0.50.0)" | `test_readme_drift.py` | `a099e22` |
| 5 backend manager chooser + no list-releases | `runtime_actions`, `RuntimeManagerScreen` accordion | `BackendManagerUX0500Tests` | `50ae54d` |
| 6 --help sweep remainder | `.rtc/*.sh` guards, `bin/queue_marker.py`, 2 hooks | `tests/test_help_sweep_0500.py` | `f2bd8b4` |
| 7 venv XDG move + legacy read + repair relocation | `bin/../install/managers/base.py backends_root/resolve_venv_dir/repair_version`, `mixins/venv_mixin.py`, `rapid_mlx.py` | `tests/test_backend_xdg_move.py` | `beb086c` |
| 8 lowkey any-model API + picker chooser | `bin/lowkey-cli.py --provider/--api-model`, `LowkeyScreen` api group | `tests/test_lowkey_api.py` | `a0cb707` |
| 9 pyright policy + mechanical fixes | `pyrightconfig.json`, spec asserts, E741, urllib.error | suite green | `a54d67f` |
| F1 pin icon on promoted version | `install/manage-backend.py` pinned column, `VersionsScreen.set_entries` | `test_about_backends` + UX tests | `50ae54d` |
| F2 hardware-gated backends | `_backend_choices(hw)`, no dim row | `BackendManagerUX0500Tests.test_hardware_gates_…` | `50ae54d` |
| F3 long-drag copies entry text | `HoverOptionList._on_mouse_up`, `m._drag_selects` | `DragSelectTests` | `3522d7d` |
| F4 About real values + ⚠️/ℹ️ split + loading placeholders | `AboutScreen.policy`, `_gather_stats_async` cache wait, `state_notices` | `AboutScreen0500Tests` | `5c38d59` |
| F5 (pid, start) identity | `_write_pidf`/`_is_our_proxy` in `remote-session.sh`, `handover-verify.sh` procStart check, `local-watch.sh` warning | `test_swap_model.py` wrong-start test | `b38a790` |
| F6 pyright pile | same as item 9 | — | `a54d67f` |
| review fix: hidden_providers survives intercept-agents save | `session_menu_state.py` intercept path passes `hidden` | `test_hidden_providers_survive_an_intercept_agents_save` | `e51625c` |
| review fix: CLAUDE_CONFIG_DIR registry paths | `handover-verify.sh`, `local-watch.sh` | manual probe | `699bb8f` |
| review fix: chooser re-renders backend manager | `session_picker.py` RuntimeManagerScreen branch | PTY RegistryAutoSync | `d337012` |
| gate contract updates (resolve_target for legacy venvs) | `base.py resolve_target`, `rapid_mlx.py` read sites | `tests/test_backend_*.py` | `7f0a706` |

## v0.50.1 — wifi-cutoff offline + limiter feature + perf/counts

| Feature | Claim | Code | Tests | Commit |
|---|---|---|---|---|
| offline error_class (non-retriable, local-proxy wrap gate) | wifi-cutoff plan + review | `remote_provider_core.py classify`, `remote_provider_registry.py classify`, `remote_http.py` OSError branch | `tests/test_offline_classify.py` | `87ee8b1`, `62f18e3` |
| client message rewrite on offline | wifi-cutoff plan | `bin/la_proxy_hooks.py _is_offline/OFFLINE_DETAIL` + `post_call_failure_hook` branch | hook pattern (402 precedent); classify tests | `87ee8b1` |
| About pin emoji + gap spacing | user report 2026-11-01 (F1 remainder) | `about_backend_lines` pin/gap flags | `test_about_backends.py` pin assertions | `87ee8b1` |
| de-anonymized stamps + owners map | user request (de-anonymize, live throttle/prioritize) | `rate_limiter.py _owner/_owner_opts/_stamp(_parts)`, owners in `_load/_save/_load_state/_save_state/_read/_write` | `OwnerAttributionPriorityTests` | `b701aa3` |
| live set-owner / owners / probe CLI | user request | `rate_limiter.py main` subcommands, `owners_info/set_owner/external_occupancy` | CLI test in the class | `b701aa3` |
| priority reserve lane, OFF by default | user request (20%, tests only) | `try_acquire(priority)` both classes, `_reserve_frac` | reserve tests | `b701aa3` |
| load-scaled test timeouts | gate-load flakes | `tests/loadscale.py`, harness + contract test wiring | `tests/test_loadscale.py` | `b701aa3` |
| catalog-cache tripwire + load-race sandbox | 2026-11-01 regression | `tests/conftest.py`, `test_picker_load_race.py` fixture | tripwire caught the leak live | `b701aa3` |
| lowkey API chooser fill on inventory landing | user report | `session_picker.py:1161/1209` + `_loaded` lowkey branch | model + PTY tests | `b701aa3` |
| fresh catalog cache skips live refetch (69 s → 0) | user report (load times) | `session_picker_model.catalog_cache_fresh`, `_eager_catalog_kinds` | load-race (max-age pinned 0 for live tests) | `50bedcf` |
| batched registry sync (N spawns → 1) | user report (load times) | `bin/csl --register-models/--unregister-models`, `session_picker.py auto_sync_registry` | PTY RegistryAutoSync (batched) | `50bedcf` |
| About/remote counts exclude hidden providers | user report (count wrong) | `_gather_stats_async`, `RemoteScreen.policy` | model + PTY | `50bedcf` |
| RegistryAutoSync batch contract | review/test | `tests/test_session_picker_pty.py` | green | `9a78293` |

## Known gaps (open, not shipped)

- ~~`csl --inventory` ~22 s wall (2.5 s CPU)~~ RESOLVED on `fix/0502-known-gaps`: the wall time was
  subprocess spawn latency, not CPU — `--gate` ran the engine probes despite its own help saying it
  skips them (each probe spawns an engine venv python, ~1 s), `la_auto_scan_models` was called TWICE
  per inventory and spawned a find/grep/basename/sed per model dir, and the alias sanitize spawned a
  sed per folder. Now: gate skips probes (mutation-tested), one auto-scan call, one find answers the
  weight rule for all dirs, pure-expansion escapes/sanitize. Measured 2026-11-01: 22 s → 4.6 s wall,
  byte-identical TSV output (44 lines, diff-verified against the pre-change tree). Waypoint
  `csl-inventory-takes-23s-wall` to be closed with this commit as evidence.
- ~~PTY/litellm/venv tests flaking at extreme load~~ PARTLY RESOLVED on `fix/0502-known-gaps`:
  timeouts are load-scaled (0.50.1) AND the load-sensitive modules (PTY harness drivers,
  litellm/venv subprocess spawn tests) now carry the registered `load_sensitive` pytest
  marker, so a loaded gate runs `pytest -m "not load_sensitive"` instead of flaking
  (marker tests in test_loadscale.py, mutation-checked). Residual: at load > 10 even
  load-scaled waits can still exceed, so the marker is the operational answer, not a fix
  of the environment — waypoint `pty-litellm-venv-tests-flake` stays open for that reason.
- ~~Local pyright binary broken (brew simdjson)~~ RESOLVED 2026-11-01: `~/.local/bin/pyright`
  runs again (1.1.414; single-file 0 errors, full project completes with diagnostics);
  brew now ships libsimdjson.36. The 252 full-project errors are the known pre-existing
  pile (waypoint `free-agents-preexisting-pyright-defects`), not the binary gap.
  Waypoint `local-pyright-binary-broken` closed.
- ~~`rtc-ship.sh --versions-only` check-only, bump action wanted~~ ALREADY SHIPPED:
  run-to-completion 1.0.1 (c12627f) split `--bump-versions` (writes exactly the declared
  surfaces) from `--versions-only`/`--check-versions` (verify only), hardened in 1.0.2
  (95bc4d8, atomic writes). Verified 2026-11-01: `tests/test_rtc_ship.sh` 60/60 incl. the
  bump-versions section; `--help` documents the flag. The waypoint was already pruned
  from the store; nothing left to do.

## README coverage

`README.md` documents the settings table (0.50.0), and a "What 0.50.x added" section now
covers lowkey any-model, the hide toggle, theme hue/shell swatch, offline handling, the
de-anonymized limiter with live set-owner/probe, the backend-manager changes, the venv XDG
root, and the About/count corrections. Verified against this audit; commit on this branch.
