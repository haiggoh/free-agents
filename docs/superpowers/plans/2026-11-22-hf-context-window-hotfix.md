# HF context window + row facts hotfix — Implementation Plan

**Status:** in-progress
> **Lasting Plans:** when every task below is done, set the Status line to `completed` by running `lasting-plans complete "docs/superpowers/plans/2026-11-22-hf-context-window-hotfix.md"` as the closing step.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native; executed by the writing session).

**Goal:** Every model row (Remote, Lowkey, Local) shows parameter count AND context window, each omitted when the display name already states it; context comes from Hugging Face `config.json` (cached per model id beside the parameter count, provider-independent) and, for local models, from the existing catalogue resolver with the model's own on-disk `config.json` as fallback.

**Architecture:** `bin/estimate-model-footprint.py` gains `context_from_config()` + context caching inside the existing HF cache entry and an offline reader `hf_context()`. `bin/session_picker.py` gains `model_context_window()` (injected into Settings like `model_param_count`) and warms context with the existing warmer. `bin/session_picker_model.py`'s shared `param_size_suffix()` renders `  · 1.05T · 1M ctx`. `bin/csl --inventory` appends column 6 = effective context (`la_get_effective_context`) and column 7 = subdir.

**Spec:** user requests 2026-11-22 (this session): "all models that don't have context listed in the yaml should have that data automatically fetched from huggingface … GLM-5.3-Flash … 1.05M tokens"; "parameter count AND context size in the row with the name. but not duplicated if either is already part of the currently listed name … for all providers and local models too so it's consistent". Evidence: `https://huggingface.co/zai-org/GLM-5.3-Flash/resolve/main/config.json` → `text_config.max_position_embeddings = 1048576` (verified 2026-11-22).

## Global Constraints
- `format_context(n)`: exact multiples of 1024 render binary (1048576 → `1M`, 262144 → `256k`, 131072 → `128k`); otherwise decimal (1000000 → `1M`, 200000 → `200k`, 32768 → `32k`); `None`/≤0 → `""`. Suffix part is `"<label> ctx"`.
- Dedupe per part, word-boundary, case-insensitive: the param label (existing rule) and the context label (`128k`, `128K`, `1M`, `1m`) against the display name.
- A render NEVER waits on the network (offline cache reads only); network/transient HF errors write nothing; a 401/404 on config.json records `ctx: null` (`ctx_done: true`) without touching the cached parameter count.
- Context precedence, remote: HF cache. Local: inventory column 6 (`la_get_effective_context`: overrides > catalogue) > the folder's own `config.json` > HF cache by subdir name.
- Inventory columns 1–5 unchanged; 6 = context (int or empty), 7 = subdir (registered) / folder (auto).
- Highest boundary: commit on `fix/hf-context-window`; ship (merge/tag/release) only on the user's go.

## Merge-conflict strategy (user concern 2026-11-22)
Ship THIS hotfix to `main` first, while the concurrent `fix/limiter-provider-buckets` executor is in
its Tasks 1–3 (`la_proxy_hooks.py`, `rate_limiter.py`, `.rtc/pre-flight.sh`, tests — disjoint from
every file here). That plan already instructs a rebase onto this hotfix before its Task 4; its picker
edits (Task 11) touch different functions of `session_picker_model.py`. Keep this diff minimal in the
shared picker files: no reformatting, no moved code.

## Tasks
0. **Double scan (waypoint `csl-inventory-runs-la-auto`)**: remove the `< <(la_auto_scan_models)` redirect from the `for folder in "${_auto_names[@]}"` loop end (`bin/csl:601`). Test: with a fixture `LA_MODELS_DIR` and a `find` shim on PATH that appends one line per call to a log, `csl --inventory` calls `find` exactly as often as ONE `la_auto_scan_models` run (2 calls) plus none from the auto loop — RED today (4).
1. **`context_from_config(cfg: dict) -> int | None`** (estimate-model-footprint.py): max over `max_position_embeddings`, `max_sequence_length`, `seq_length`, `n_positions`, `model_max_length` at top level and under `text_config`, `llm_config`, `language_config`; with `rope_scaling` `{factor, original_max_position_embeddings}` also `int(factor * original)`. Tests: GLM shape (nested text_config) → 1048576; top-level Llama shape; YaRN shape; empty → None; non-int junk ignored.
2. **HF context caching**: when `hf_total_params` resolves a repo (fresh fetch) OR answers from a fresh entry lacking `ctx_done`, GET `<origin>/<repo>/resolve/main/config.json` once; store `ctx`, `ctx_done: true` in the same entry via `_hf_cache_store`. `hf_context(model_id, *, offline=True) -> int | None`. Tests with `LA_HF_API_BASE`-style origin override against a local fake HTTP server: success stores ctx; 401 stores `ctx: null, ctx_done: true` and keeps `total`; network error writes nothing; offline read never fetches.
3. **Picker wiring**: `model_context_window(provider, model_id)` (memoized "ctx" verdict, popped by the warmer), injected into Settings; `catalog_context()` + `format_context()` + extended `param_size_suffix()` in session_picker_model.py; local rows get the same suffix from inventory col 6/7 (+ on-disk config.json fallback). Tests: rendered Remote row for a fixture `z-ai/glm-5.3-flash` with cached total+ctx shows `· 1.05T · 1M ctx`; a display already containing `128K` shows no ctx part; local row with col6=262144 shows `256k ctx`; local row with empty col6 reads the folder config.json.
4. **csl --inventory** columns 6–7 (bash 3.2 safe). Test: inventory line for a registered fixture alias has 7 columns and col6 equals `la_get_effective_context`.
5. Full suite (`-n 12 -rs`) + `.rtc/pre-flight.sh`; screenshot Remote + Local lists; commit by path; ask the user about shipping.

## Progress log
- 2026-11-22 Task 0 DONE — commit eae754e (single scan, inventory cols 6-7; test 12/12, mutant RED 3/3).
- 2026-11-22 Task 2 WIP — `_hf_fetch_context`, `hf_context`, ctx caching in `hf_total_params` written; tests/test_hf_context_0611.py written (8 tests, RED). MISSING: `context_from_config()` (Task 1) — a free dispatch drafted it: brief + fixtures + result (if it landed) in `docs/measurements/2026-11-22-hf-context-dispatch/`. VERIFY the draft against the three real configs (GLM 1048576, DeepSeek-V3 163840, Qwen3-8B 40960) before pasting it into bin/estimate-model-footprint.py. Then Tasks 3-5. User requirement (2026-11-22): all fetching is done by the code at runtime (warm-up), never by a manual step — future models must work unattended.
- 2026-11-22 Tasks 1-3 DONE — 65beab8 (HF ctx cache; 8 tests), fadab4d (row labels Remote/Lowkey/Local;
  8 unit tests; inventory cols 6-7 with the single-scan fix from eae754e). GLM-5.3-Flash verified
  1048576 from zai-org/GLM-5.3-Flash config.json (text_config.max_position_embeddings). Next:
  Task 5 (full gate + pre-flight + screenshots, then ask the user about shipping).
