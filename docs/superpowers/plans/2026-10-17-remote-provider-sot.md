# Remote Provider Single Source of Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate drift between `bin/remote-session.sh` (bash) and `bin/remote_provider_core.py` (Python) by moving all provider endpoint data into a single YAML file that both sides parse.

**Architecture:** One `config/remote-registry.yaml` with two top-level sections: `providers:` (endpoint definitions) and `entries:` (model roster). `bin/remote-provider-registry.py` parses the YAML and exposes the same Python API (`PROVIDERS`, `IMPLEMENTED`, `RATE_LIMITED`, `CATALOG_SCANABLE`, `get()`) that `remote_provider_core.py` currently owns. `bin/remote-provider-registry.sh` parses the same YAML for bash consumers. `remote_provider_core.py` re-exports from the registry; `remote-session.sh` sources the registry script.

**Tech Stack:** Python 3.14+, `pyyaml` (installed in `.venv`), bash 3.2+ compatible, no new bash dependencies.

**Spec:** `docs/remote-sot-execution-plan.md` (the agreed design doc)

## Global Constraints

- Must not change any external behavior: same provider IDs, same endpoints, same test expectations (after updating assertions for the new provider set).
- `pyyaml` is a soft dependency: `remote-provider-registry.py` must fail with a clear error message if yaml is absent, not crash with a traceback.
- `remote-provider-registry.sh` must not require `yq` or any bash YAML parser — it calls `python3` as a one-liner helper.
- The `.venv` already exists with `pyyaml==6.0.3`; all Python scripts use the system `python3` (3.15) which cannot import yaml, so registry scripts must locate the venv or the plan must ensure yaml is importable.
- `remote_provider_core.py` must remain importable by all existing consumers (`remote-agent-dispatch.py`, `remote-provider-doctor.py`, `remote-probe-log.py`, `agent-fallback.py`, `tests/remote/test_remote_fallback.py`) without changing their import statements.
- The two existing test failures (`all seven providers are declared` and `gemini + nvidia are implemented`) from the WIP commit must be resolved — the test assertions must match the new full provider set.

## Review Focus

- **YAML parse error on malformed provider entry:** a missing required field should produce a clear error naming the provider and field, not a cryptic KeyError.
- **git_retired provider still in PROVIDERS but blocked by _available():** the retired-github check lives in bash; Python's PROVIDERS must still include github for test coverage and documentation.
- **static_headers with semicolons (cloudflare):** the YAML parser must preserve the exact header map; bash _litellm_route must not corrupt it.
- **Extra env vars (CLOUDFLARE_ACCOUNT_ID):** the Provider.dataclass must carry extra_env as a tuple; the YAML parser must convert YAML lists to tuples.
- **Catalog-unavailable providers (zai, modelscope):** providers with empty models_path and no catalog_url must have `has_catalog == False`; the doctor must skip them gracefully.

---

### Task 1: Create config/remote-registry.yaml

**Files:**
- Create: `config/remote-registry.yaml`

**Interfaces:**
- Consumes: none (new file)
- Produces: the canonical YAML source that all later tasks parse

- [ ] **Step 1: Write the YAML file**

Create `config/remote-registry.yaml` with two top-level keys: `providers` and `entries`.

The `providers` section is a YAML mapping of provider-id → provider object. Each object has:
- `id` (string)
- `display` (string)
- `tier` (string: "local" | "renewing_free" | "trial" | "paid" | "consumer_web" | "unknown")
- `base_url` (string, no trailing slash)
- `chat_path` (string, e.g. "/chat/completions")
- `models_path` (string, may be empty)
- `catalog_url` (string, may be empty)
- `key_env` (string)
- `extra_env` (list of strings, may be empty)
- `static_headers` (mapping of string→string, may be empty)
- `default_models` (list of strings, may be empty)
- `supports_responses` (boolean)
- `notes` (string)

The `entries` section is a YAML list of model roster entries. Each entry has:
- `alias` (string)
- `provider` (string — must reference a provider id)
- `model_id` (string)
- `display` (string)
- `tier` (string)
- `notes` (string)
- `autocompact_default` (string, "-" means no default)

Populate it from the current `PROVIDERS` dict in `bin/remote_provider_core.py` (15 providers) and from `config/remote-agents.sh` `LA_REMOTE_AGENTS` array (~40 entries). Preserve all comments from the bash file as YAML comments in the entries section.

The YAML must be valid, human-readable, and preserve the provider preference order (NVIDIA first, then Gemini, Groq, OpenRouter, Cloudflare, Cerebras, then the OpenAI-compatible providers).

- [ ] **Step 2: Validate the YAML parses**

Run: `python3 -c "import yaml; d = yaml.safe_load(open('config/remote-registry.yaml')); print('providers:', len(d['providers']), 'entries:', len(d['entries']))"`
Expected: `providers: 15 entries: 40` (exact counts may vary slightly — confirm they match the current roster).

- [ ] **Step 3: Commit**

```bash
git add config/remote-registry.yaml
git commit -m "chore: add remote-registry.yaml as canonical provider+roster source

Two top-level sections: providers (15 endpoints) and entries (model roster,
migrated from config/remote-agents.sh LA_REMOTE_AGENTS). Both Python and bash
consumers will parse this file as the single source of truth.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Create bin/remote-provider-registry.py

**Files:**
- Create: `bin/remote-provider-registry.py`

**Interfaces:**
- Consumes: `config/remote-registry.yaml` (created in Task 1)
- Produces: `PROVIDERS` (dict), `IMPLEMENTED` (set), `RATE_LIMITED` (set), `CATALOG_SCANABLE` (set), `get()` (function), tier constants (`LOCAL`, `RENEWING_FREE`, `TRIAL`, `PAID`, `CONSUMER_WEB`, `UNKNOWN`), `TIER_CHOICES`, `Provider` dataclass, `Result` dataclass, `classify()` — all with the same names and signatures as in `remote_provider_core.py`

- [ ] **Step 1: Write the registry parser**

Create `bin/remote-provider-registry.py`. It must:

1. Import `yaml`. If `yaml` is not importable, set `yaml = None` and define a module-level `_YAML_UNAVAILABLE` flag. Do NOT crash at import time.
2. Define the same `Provider` dataclass, tier constants, `TIER_CHOICES`, `Result` dataclass, and `classify()` function that currently live in `remote_provider_core.py` — copy them verbatim.
3. Define `IMPLEMENTED = {"gemini", "nvidia", "groq", "openrouter", "cloudflare", "mistral", "zai", "siliconflow", "llm7", "kilo", "vercel", "sambanova", "modelscope", "streamlake"}` and `RATE_LIMITED = {"nvidia"}`.
4. Define a `_load_registry()` function that:
   - Resolves `config/remote-registry.yaml` relative to the repo root (use `Path(__file__).parent.parent / "config" / "remote-registry.yaml"`).
   - If `yaml` is None, raises `RuntimeError("pyyaml is required for remote-provider-registry.py; install with `uv pip install pyyaml`").`
   - Loads the YAML, extracts `providers` and `entries`.
   - For each provider entry, constructs a `Provider` dataclass instance. Convert YAML lists to tuples (`extra_env`, `default_models`). Convert empty strings to empty tuples/lists where the dataclass expects them. Preserve `static_headers` as a dict.
   - Builds `PROVIDERS = {pid: provider_obj for pid, provider_obj in provider_map.items()}`.
   - Computes `CATALOG_SCANABLE = {pid for pid, p in PROVIDERS.items() if p.models_path or p.catalog_url}`.
   - Returns `(PROVIDERS, entries_list)`.
5. Call `_load_registry()` at module level to populate `PROVIDERS` and a module-level `ENTRIES` list.
6. Define `get(provider_id)` returning `PROVIDERS.get(provider_id)`.
7. Guard the `__main__` block: if run directly, print a JSON summary of providers and entries and exit 0.

The file must be a drop-in replacement for `remote_provider_core.py` — all existing importers (`from remote_provider_core import PROVIDERS, IMPLEMENTED, get`) must continue to work after the next task's re-export change.

- [ ] **Step 2: Run the registry directly to verify it loads**

Run: `python3 bin/remote-provider-registry.py`
Expected: JSON output listing all 15 providers and ~40 entries, exit 0.

- [ ] **Step 3: Verify the Provider dataclass matches the old one**

Run: `python3 -c "
import sys; sys.path.insert(0, 'bin')
import remote_provider_core as old
import remote_provider_registry as new
# Same tier constants
assert old.LOCAL == new.LOCAL
assert old.RENEWING_FREE == new.RENEWING_FREE
assert old.TRIAL == new.TRIAL
assert old.PAID == new.PAID
assert old.CONSUMER_WEB == new.CONSUMER_WEB
assert old.UNKNOWN == new.UNKNOWN
# Same PROVIDERS keys
assert set(old.PROVIDERS) == set(new.PROVIDERS)
# Same IMPLEMENTED
assert old.IMPLEMENTED == new.IMPLEMENTED
# Same RATE_LIMITED
assert old.RATE_LIMITED == new.RATE_LIMITED
# Same CATALOG_SCANABLE
assert old.CATALOG_SCANABLE == new.CATALOG_SCANABLE
# Provider objects are structurally equal
for pid in old.PROVIDERS:
    o = old.PROVIDERS[pid]
    n = new.PROVIDERS[pid]
    assert o.id == n.id
    assert o.base_url == n.base_url
    assert o.key_env == n.key_env
    assert o.tier == n.tier
    assert o.extra_env == n.extra_env
    assert o.static_headers == n.static_headers
    assert o.default_models == n.default_models
print('All assertions passed')
"`
Expected: `All assertions passed`

- [ ] **Step 4: Commit**

```bash
git add bin/remote-provider-registry.py
git commit -m "feat: add remote-provider-registry.py as YAML-backed provider registry

Parses config/remote-registry.yaml and exposes the same API as
remote_provider_core.py (PROVIDERS, IMPLEMENTED, RATE_LIMITED,
CATALOG_SCANABLE, get(), Provider dataclass, Result dataclass, classify()).
Handles missing pyyaml gracefully with a clear error message.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Modify bin/remote_provider_core.py to re-export from registry

**Files:**
- Modify: `bin/remote_provider_core.py`

**Interfaces:**
- Consumes: `bin/remote-provider-registry.py` (created in Task 2)
- Produces: unchanged external API — all existing importers continue to work without modification

- [ ] **Step 1: Back up the current file**

Run: `cp bin/remote_provider_core.py bin/remote_provider_core.py.bak-20261017`

- [ ] **Step 2: Replace the inline PROVIDERS dict with re-exports**

Rewrite `bin/remote_provider_core.py` so that:
1. All the dataclass definitions, tier constants, `Result`, `classify()`, and `get()` are kept verbatim (they're needed for the API compatibility layer).
2. The inline `PROVIDERS` dict (lines 119-270) is removed.
3. After the dataclass/constants section, add:
   ```python
   # Provider data is loaded from config/remote-registry.yaml by
   # remote-provider-registry.py. Re-export everything from there so that
   # existing importers (remote-agent-dispatch.py, remote-provider-doctor.py,
   # remote-probe-log.py, agent-fallback.py) continue to work without changes.
   from remote_provider_registry import (  # noqa: F401,F403
       PROVIDERS,
       IMPLEMENTED,
       RATE_LIMITED,
       CATALOG_SCANABLE,
       ENTRIES,
   )
   ```
4. Keep the module docstring but update it to reference the YAML as the source of truth.
5. Keep the `__main__` block (it currently prints provider definitions — it will now print the registry output via the re-exported `PROVIDERS`).

- [ ] **Step 3: Run existing tests**

Run: `python3 tests/remote/test_remote_fallback.py`
Expected: All tests pass (the WIP's 2 failures should be gone because the test assertions will be updated in Task 5, but first check — if the 2 failures remain, they're from the test asserting the old provider count).

- [ ] **Step 4: Commit**

```bash
git add bin/remote_provider_core.py bin/remote_provider_core.py.bak-20261017
git commit -m "refactor: remote_provider_core.py re-exports from remote-provider-registry.py

The inline PROVIDERS dict is removed; the module now re-exports PROVIDERS,
IMPLEMENTED, RATE_LIMITED, CATALOG_SCANABLE, and ENTRIES from
remote-provider-registry.py which parses config/remote-registry.yaml.

All existing importers continue to work without modification.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Create bin/remote-provider-registry.sh

**Files:**
- Create: `bin/remote-provider-registry.sh`

**Interfaces:**
- Consumes: `config/remote-registry.yaml` (created in Task 1)
- Produces: bash variables `_OPENAI_BASE_MAP` (associative array), `_CATALOG_BASE_MAP` (associative array), `_AVAILABLE_CHECK` function, `_LITELLM_ROUTE_MAP` (associative array), `_CLEAR_PROVIDER_ENV_VARS` (space-separated list), `_PROVIDER_IDS` (space-separated list)

- [ ] **Step 1: Write the bash registry script**

Create `bin/remote-provider-registry.sh`. It must:

1. Define a `_yaml_get_providers()` helper that calls `python3` to parse the YAML and emit provider data as shell-sourceable assignments. The python one-liner should:
   - Read `config/remote-registry.yaml` (resolved relative to the script's directory, going up one level to repo root).
   - Print bash-compatible output: `_PROVIDER_IDS="gemini groq ..."`, `_OPENAI_BASE_MAP=( [gemini]="https://..." [groq]="https://..." ... )`, etc.
2. Source the output of that helper.
3. Define `_openai_base()` using the `_OPENAI_BASE_MAP` associative array.
4. Define `_catalog_request()` using the `_CATALOG_BASE_MAP` associative array (include the same curl logic from the current `remote-session.sh`, but parameterized by the map).
5. Define `_available()` — same retired-github check, plus any other availability rules.
6. Define `_litellm_route()` using a `_LITELLM_ROUTE_MAP` associative array. The liteLLM prefixes are:
   - `gemini` → `gemini/`
   - `groq` → `groq/`
   - `nvidia` → `nvidia_nim/`
   - `openrouter` → `openrouter/`
   - `cerebras` → `cerebras/`
   - all others (cloudflare, mistral, zai, siliconflow, llm7, kilo, vercel, sambanova, modelscope, streamlake) → `openai/`
7. Define `_clear_provider_env()` using `_CLEAR_PROVIDER_ENV_VARS`.
8. Support `--help`: print a usage line and the list of known provider IDs.
9. Guard against being run directly: if `[[ "${BASH_SOURCE[0]}" == "${0}" ]]`, print usage and exit 0 (this script is meant to be sourced, not executed).

The script must be bash 3.2 compatible (no `mapfile`, no `=(...)` array assignment with unquoted values — use `declare -A` for associative arrays).

- [ ] **Step 2: Test the script sources correctly**

Run: `source bin/remote-provider-registry.sh && echo "providers: $_PROVIDER_IDS"`
Expected: `providers: gemini groq openrouter cloudflare github cerebras nvidia mistral zai siliconflow llm7 kilo vercel sambanova modelscope streamlake`

- [ ] **Step 3: Test _openai_base**

Run: `source bin/remote-provider-registry.sh && _openai_base mistral`
Expected: `https://api.mistral.ai/v1`

- [ ] **Step 4: Test _litellm_route**

Run: `source bin/remote-provider-registry.sh && _litellm_route mistral gemini-2.0-flash`
Expected: `mistral/gemini-2.0-flash|`

Run: `source bin/remote-provider-registry.sh && _litellm_route nvidia llama-3.1-70b`
Expected: `nvidia_nim/llama-3.1-70b|`

Run: `source bin/remote-provider-registry.sh && _litellm_route zai some-model`
Expected: `openai/some-model|https://api.z.ai/api/paas/v4`

- [ ] **Step 5: Test --help**

Run: `bin/remote-provider-registry.sh --help`
Expected: Usage line + list of provider IDs, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bin/remote-provider-registry.sh
git commit -m "feat: add remote-provider-registry.sh as bash registry consumer

Sources config/remote-registry.yaml via a python3 one-liner and exposes
_openai_base(), _catalog_request(), _available(), _litellm_route(),
_clear_provider_env() for remote-session.sh and other bash consumers.

bash 3.2 compatible. --help supported. Meant to be sourced, not run.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Migrate bin/remote-session.sh to source the registry

**Files:**
- Modify: `bin/remote-session.sh`

**Interfaces:**
- Consumes: `bin/remote-provider-registry.sh` (created in Task 4)
- Produces: identical external behavior — same CLI flags, same provider routing, same proxy config output

- [ ] **Step 1: Back up the current file**

Run: `cp bin/remote-session.sh bin/remote-session.sh.bak-20261017`

- [ ] **Step 2: Add the registry source at the top**

After the shebang and initial variable setup (but before any function definitions), add:
```bash
# Load the provider registry — single source of truth for endpoints,
# routing prefixes, and availability checks. Sourced, not executed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/remote-provider-registry.sh"
```

- [ ] **Step 3: Remove inline provider definitions**

Remove these functions and replace them with the registry's versions (which are now available because the script is sourced):
- `_available()` (lines 198-205) — replaced by registry's `_available()`
- `_openai_base()` (lines 207-218) — replaced by registry's `_openai_base()`
- `_clear_provider_env()` (lines 222-227) — replaced by registry's `_clear_provider_env()`
- `_catalog_request()` (lines 516-545) — this one is more complex. The registry's python helper emits `_CATALOG_BASE_MAP`. The bash function must be rewritten to use the map + the same curl logic. Keep the curl-based approach (system trust store for corporate proxy compatibility).
- `_litellm_route()` (lines 768-780) — replaced by registry's `_litellm_route()`

For `_catalog_request()`, the registry helper should emit a `_CATALOG_BASE_MAP` associative array. The bash function becomes:
```bash
_catalog_request() {
    local prov="$1" base
    _available "$prov" || return 2
    base="${_CATALOG_BASE_MAP[$prov]}" || { echo "remote-session: no catalog route for $prov" >&2; return 2; }
    # ... same curl logic as current, using $base ...
}
```

- [ ] **Step 4: Verify _available still blocks github**

Run: `source bin/remote-session.sh 2>/dev/null; _available github; echo "exit: $?"`
Expected: exit code 2, error message about GitHub Models being retired.

- [ ] **Step 5: Verify _openai_base still works**

Run: `source bin/remote-session.sh 2>/dev/null; _openai_base sil1conflow`
Expected: `https://api.siliconflow.com/v1`

- [ ] **Step 6: Verify _litellm_route still works**

Run: `source bin/remote-session.sh 2>/dev/null; _litellm_route mistral test-model`
Expected: `mistral/test-model|`

- [ ] **Step 7: Run the remote-session smoke test**

Run: `bin/remote-session.sh --help 2>&1 | head -5`
Expected: help output, exit 0.

Run: `bin/remote-session.sh --verify nvidia-glm53 2>&1 | head -3`
Expected: a catalog check attempt (may fail due to no API key, but should not crash).

- [ ] **Step 8: Commit**

```bash
git add bin/remote-session.sh bin/remote-session.sh.bak-20261017
git commit -m "refactor: remote-session.sh sources remote-provider-registry.sh

Removed inline _available(), _openai_base(), _clear_provider_env(),
_catalog_request(), and _litellm_route() definitions. These are now
provided by the sourced registry script which reads
config/remote-registry.yaml.

External behavior unchanged: same CLI, same routing, same proxy config.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Update tests and verify full suite

**Files:**
- Modify: `tests/remote/test_remote_fallback.py`

**Interfaces:**
- Consumes: updated `bin/remote_provider_core.py` (Task 3)
- Produces: all tests passing, assertions matching the full provider set

- [ ] **Step 1: Update the PROVIDERS set assertion**

In `tests/remote/test_remote_fallback.py`, find the assertion at approximately line 160:
```python
check(set(core.PROVIDERS) == {"gemini", "groq", "openrouter", "cloudflare",
                              "github", "cerebras", "nvidia"},
      "all seven providers are declared")
```
Replace with:
```python
_expected_providers = {"gemini", "groq", "openrouter", "cloudflare",
                       "github", "cerebras", "nvidia",
                       "mistral", "zai", "siliconflow", "llm7",
                       "kilo", "vercel", "sambanova", "modelscope",
                       "streamlake"}
check(set(core.PROVIDERS) == _expected_providers,
      "all fifteen providers are declared")
```

- [ ] **Step 2: Update the IMPLEMENTED assertion**

Find the assertion at approximately line 164:
```python
check(core.IMPLEMENTED == {"gemini", "nvidia"},
      "gemini + nvidia are implemented; the rest are declared, not runnable")
```
Replace with:
```python
_expected_implemented = {"gemini", "nvidia", "groq", "openrouter", "cloudflare",
                         "mistral", "zai", "siliconflow", "llm7", "kilo",
                         "vercel", "sambanova", "modelscope", "streamlake"}
check(core.IMPLEMENTED == _expected_implemented,
      "providers declared vs implemented sets match the registry")
```

- [ ] **Step 3: Add a test for the registry YAML round-trip**

Add a new test section after the existing provider tier model section:
```python
print("== registry YAML round-trip ==")
registry = load("reg", "remote-provider-registry.py")
# The registry loaded from YAML must match the re-exported core
check(set(registry.PROVIDERS) == set(core.PROVIDERS),
      "registry PROVIDERS matches core.PROVIDERS")
check(registry.IMPLEMENTED == core.IMPLEMENTED,
      "registry IMPLEMENTED matches core.IMPLEMENTED")
check(registry.RATE_LIMITED == core.RATE_LIMITED,
      "registry RATE_LIMITED matches core.RATE_LIMITED")
# Spot-check a provider with complex fields (cloudflare has extra_env + static_headers)
cf = registry.get("cloudflare")
check(cf is not None, "cloudflare provider exists")
check(cf.extra_env == ("CLOUDFLARE_ACCOUNT_ID",),
      "cloudflare extra_env is a tuple")
check(cf.static_headers == {"CF-Access-Client-Id": "workers-ai",
                            "CF-Access-Client-Secret": ""},
      "cloudflare static_headers preserved")
# Spot-check a provider with no catalog
zai = registry.get("zai")
check(zai is not None, "zai provider exists")
check(zai.has_catalog is False, "zai has no catalog (models_path and catalog_url empty)")
# Spot-check github (retired but still in PROVIDERS)
gh = registry.get("github")
check(gh is not None, "github provider exists (retired but documented)")
check(gh.catalog_url == "https://models.github.ai/catalog/models",
      "github catalog_url preserved")
```

- [ ] **Step 4: Run the full test suite**

Run: `python3 tests/remote/test_remote_fallback.py`
Expected: All tests pass (73+ passed, 0 failed).

- [ ] **Step 5: Run pytest if available**

Run: `python3 -m pytest tests/remote/test_remote_fallback.py -v 2>&1 | tail -20`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/remote/test_remote_fallback.py
git commit -m "test: update assertions for full provider set + add YAML round-trip checks

The PROVIDERS set assertion now covers all 15 providers. The IMPLEMENTED
assertion matches the registry. New round-trip tests verify that the YAML
parser preserves complex fields (extra_env as tuple, static_headers as dict,
has_catalog for catalog-less providers) and that github remains in PROVIDERS
despite being retired in bash."

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Final verification and cleanup

**Files:**
- Modify: `bin/remote_provider_core.py` (docstring update, already done in Task 3)
- Optional: `config/remote-agents.sh` (add a comment pointing at the YAML)

**Interfaces:**
- Consumes: everything from previous tasks
- Produces: a clean, verified state with no drift surface

- [ ] **Step 1: Verify no consumer is broken**

Run each consumer's --help to confirm it still loads:
```bash
bin/remote-agent-dispatch.py --help 2>&1 | head -3
bin/remote-provider-doctor.py --help 2>&1 | head -3
bin/remote-probe-log.py --help 2>&1 | head -3
bin/remote-session.sh --help 2>&1 | head -3
```
Expected: all exit 0, all print usage.

- [ ] **Step 2: Verify git status is clean**

Run: `git status --short`
Expected: only the planned files changed. No unexpected modifications.

- [ ] **Step 3: Run the full test suite one final time**

Run: `python3 tests/remote/test_remote_fallback.py`
Expected: All tests pass.

- [ ] **Step 4: Add a comment to config/remote-agents.sh**

At the top of `config/remote-agents.sh`, after the existing header comment, add:
```bash
# NOTE: The authoritative provider endpoint data lives in
# config/remote-registry.yaml. This file (remote-agents.sh) is kept for
# backward compatibility with tools that read LA_REMOTE_AGENTS directly.
# New providers should be added to the YAML; this file is a derived view.
```

- [ ] **Step 5: Final commit**

```bash
git add config/remote-agents.sh
git commit -m "docs: point remote-agents.sh at the YAML registry as source of truth

config/remote-registry.yaml is now the authoritative source for provider
endpoints and the model roster. remote-agents.sh is kept for backward
compatibility but delegates to the YAML for provider data.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Delete the .bak files (optional, after user review)**

The `.bak-20261017` files are safety nets. Ask the user whether to keep or delete them before removing. Do NOT delete them in this task — they serve as restore points.

```