# HANDOFF UPDATE — main moved to 0.61.1 while you work (read before your next step)

**Context:** the HF-context hotfix you were waiting on is SHIPPED. Your `main` moved
(75d1801 → ce1ff70), and your branch's base is one release behind.

**Verified facts (2026-11-22 15:07 CET):**
- `origin/main` = ce1ff70 = v0.61.1: the hotfix squash-merge `23925d3 fix(hotfix): HF context window
  on every model row + single-scan csl inventory` + version/changelog commits `c307a3a`, `ce1ff70`.
- Installed plugin refreshed to **0.61.1** (verified: `installed_plugins.json`, cache dir, and a live
  dogfood — the warm-up stored `ctx=1048576` for `z-ai/glm-5.3-flash` and the picker row label helper
  printed `GLM 5.3 Flash  · 321.3B · 1M ctx`).
- Your worktree state: `M .rtc/pre-flight.sh`, `M bin/la_proxy_hooks.py`, `M
  tests/live_proxy/conftest.py`, `?? tests/live_proxy/test_error_mapping.py`, `?? tests/test_bridge_shim.py`
  — the GLM successor is still mid-Task 2 (error mapping + bridge shim).

**What this changes in your plan (`2026-11-22-provider-buckets-and-limiter-fixes.md`):**
- **Task 4's fetch+merge steps are retired.** The runtime path now exists: `hf_context()` reads the
  context cached by the warm-up, and provider `/models` catalogs are the same shape. Task 4 becomes
  "consume the runtime facts + keep only hand-pinned LIMITS in provider-limits.json". Waypoint
  `provider-buckets-plan-task-4` is closed with that evidence.
- **Rebase or merge main into your branch** when your current task's test cycle closes (not
  mid-task): `git fetch && git rebase origin/main` — the hotfix touched `bin/csl`,
  `bin/estimate-model-footprint.py`, `bin/session_picker_model.py`, `bin/session_picker.py`,
  `tests/test_csl_inventory_facts.sh` + 2 new test files. Your Task 11 (picker infoboxes) shares
  `session_picker_model.py`/`bin/session_picker.py`, so rebase BEFORE Task 11, not after.
- No authority change: your boundary is still "commit + push your branch".