# Execution Plan: Single Source of Truth for Remote Provider Config

**Date:** 2026-10-16
**Branch:** `feat/scan-all-remote-models-2026`
**Status:** SUPERSEDED (2026-10-18) — implemented in 0.37.0 with a YAML source and generated JSON + bash artifacts instead of the PSV drafted here; see CHANGELOG 0.37.0 and docs/remote-session/README.md.

---

## 1. Problem Statement

`bin/remote-session.sh` (bash) and `bin/remote_provider_core.py` (Python) each maintain their own copies of provider endpoint data:

| Data | Bash lives in | Python lives in |
|------|--------------|-----------------|
| Provider base URLs | `_openai_base()` (line 207) | `PROVIDERS` dict |
| Catalog paths | `_catalog_request()` (line 516) | `Provider.models_path` / `catalog_url` |
| LiteLLM routing prefixes | `_litellm_route()` (line 768) | *(none — bash-only)* |
| Provider availability (e.g. github retired) | `_available()` (line 198) | *(none — bash-only)* |
| Provider env vars | `_clear_provider_env()` (line ~210) | `Provider.key_env` / `extra_env` |
| Model roster (alias\|provider\|model\|display\|tier) | `LA_REMOTE_AGENTS` array | *(none — bash-only)* |

The WIP commit (`4e46efe`) added 8 new providers to the Python `PROVIDERS` dict to match the bash roster. This is a **parallel declaration** — it reduces drift but does not eliminate it. A future edit to `_openai_base()` in bash would silently diverge from `PROVIDERS` in Python.

The user's request: *"a single source of truth outside both scripts, maybe a yaml file or psv or json (honestly weigh which fits best) and use variables that are sourced by both scripts from the single source."*

---

## 2. Design Decision: Format Weighing

### Options evaluated

| Format | Pros | Cons |
|--------|------|------|
| **YAML** | Human-readable, supports comments, natural nesting | No native bash parser; requires `yq` or `python3 -c 'import yaml'` both of which may be absent |
| **PSV** (pipe-separated) | Trivially parseable in both bash (`IFS='|' read`) and Python (`line.split('|')`); used already in this repo (e.g. `model-catalog.psv`, `local-capable-remote-models.psv`) | No comments; no nesting; every field must be positional |
| **JSON** | Native Python; parseable in bash via `jq` | Requires `jq` in bash; heavier syntax for simple data |
| **Bash sourceable .sh** | Native to bash; Python can `source` via subprocess | Not importable by Python without parsing bash syntax |

### Verdict: **PSV**

This repo already uses PSV for model catalogs (`model-catalog.psv`, `local-capable-remote-models.psv`, `model-catalog.acquisitions.*.psv`). The convention is established, the parsing is trivial in both languages, and it supports the flat provider-record structure we need. Two files:

1. **`config/remote-providers.psv`** — provider-level endpoints (one row per provider)
2. **`config/remote-agents.psv`** — model-level roster (derived from existing `remote-agents.sh`, one row per model entry)

The existing `config/remote-agents.sh` is kept as-is (it drives the picker UI and bash-only tools); the PSV becomes the canonical source that both bash and Python read.

---

## 3. Proposed PSV Schemas

### 3a. `config/remote-providers.psv` — provider endpoints

```
# id|display|tier|base_url|chat_path|models_path|catalog_url|key_env|extra_env|static_headers|default_models|supports_responses|notes
# Fields separated by |. Empty fields are omitted (use - for truly empty).
# Comments start with #. Order = preference order.
gemini|Gemini API|renewing_free|https://generativelanguage.googleapis.com/v1beta/openai|/chat/completions|/models||GEMINI_API_KEY|||gemini-3.6-flash,gemini-2.5-flash,gemini-2.5-pro|false|OpenAI-compatible. Free tier with a resetting per-day quota — the primary emergency lane.
groq|Groq|renewing_free|https://api.groq.com/openai/v1|/chat/completions|/models||GROQ_API_KEY|||llama-3.3-70b-versatile|true|OpenAI-compatible, fast. Free tier resets.
openrouter|OpenRouter|unknown|https://openrouter.ai/api/v1|/chat/completions|/models||OPENROUTER_API_KEY|||openrouter/free|false|Free models are namespaced ('openrouter/free' or ':free').
cloudflare|Cloudflare Workers AI|renewing_free|https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1|/chat/completions|/models||CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID|CF-Access-Client-Id:workers-ai;CF-Access-Client-Secret:|@cf/meta/llama-3.1-8b-instruct|true|Free monthly quota. Requires account id + API token.
github|GitHub Models|renewing_free|https://models.github.ai/inference|/chat/completions||https://models.github.ai/catalog/models|GITHUB_TOKEN||X-GitHub-Api-Version:2026-03-10|openai/gpt-4o-mini,meta-llama/llama-3.1-70b-instruct|false|Requires models:read on the token.
cerebras|Cerebras|trial|https://api.cerebras.ai/v1|/chat/completions|/models||CEREBRAS_API_KEY|||llama-3.1-70b|false|CLASSIFIED AS TRIAL, not permanently free.
nvidia|NVIDIA NIM|unknown|https://integrate.api.nvidia.com/v1|/chat/completions|/models||NVIDIA_API_KEY|||meta/llama-3.1-70b-instruct|false|Free tier but the EFFECTIVE limits are dynamic.
mistral|Mistral|unknown|https://api.mistral.ai/v1|/chat/completions|/models||MISTRAL_API_KEY|||mistral-medium-2604,codestral-2508,mistral-small-2603|false|OpenAI-compatible via mistral/ LiteLLM prefix.
zai|ZAI|unknown|https://api.z.ai/api/paas/v4|/chat/completions|||ZAI_API_KEY||||false|ZAI Coding Plan endpoint is distinct; this route uses general API billing.
siliconflow|SiliconFlow|unknown|https://api.siliconflow.com/v1|/chat/completions|/models?type=text||SILICONFLOW_API_KEY||||false|OpenAI-compatible. International .com endpoint.
llm7|LLM7|unknown|https://api.llm7.io/v1|/chat/completions|/models||LLM7_API_KEY||||false|OpenAI-compatible. Catalog contains free and paid models.
kilo|Kilo Gateway|unknown|https://api.kilo.ai/api/gateway|/chat/completions|/models||KILO_API_KEY||||false|OpenAI-compatible. Free and paid models differ.
vercel|Vercel Gateway|unknown|https://ai-gateway.vercel.sh/v1|/chat/completions|/models||AI_GATEWAY_API_KEY||||false|OpenAI-compatible. Credits and paid account billing apply.
sambanova|SambaNova|unknown|https://api.sambanova.ai/v1|/chat/completions|/models||SAMBANOVA_API_KEY||||false|OpenAI-compatible. Account trial, credit and paid billing vary.
modelscope|ModelScope|unknown|https://api-inference.modelscope.cn/v1|/chat/completions|/models||MODELSCOPE_API_KEY||||false|OpenAI-compatible API-Inference endpoint.
streamlake|Streamlake|unknown|https://api.streamlake.ai/v1|/chat/completions|/models||STREAMLAKE_API_KEY||||false|OpenAI-compatible.
```

### 3b. `config/remote-agents.psv` — model roster

Migrated from `config/remote-agents.sh` `LA_REMOTE_AGENTS` array. One row per entry:

```
# alias|provider|model_id|display|tier|notes|autocompact_default
nvidia-glm53-thinking|nvidia|z-ai/glm-5.3-flash|NVIDIA GLM 5.3 Flash (thinking)|unknown|PARTIAL: same model as the base alias — thinking is always-on on NIM; this alias just defaults reasoning_effort high. Fast.|-
... (all rows from remote-agents.sh)
```

---

## 4. Implementation Phases

### Phase 0: Scaffold (NO behavior change)
- [ ] Create `config/remote-providers.psv` from the current `PROVIDERS` dict in `remote_provider_core.py`
- [ ] Create `config/remote-agents.psv` from `config/remote-agents.sh` `LA_REMOTE_AGENTS`
- [ ] Create `bin/remote-provider-registry.py` — a small parser that reads `remote-providers.psv` and exposes `PROVIDERS`, `IMPLEMENTED`, `RATE_LIMITED`, `CATALOG_SCANABLE` as Python constants (same API as current `remote_provider_core.py`)
- [ ] Create `bin/remote-provider-registry.sh` — a small bash sourceable script that sources `remote-providers.psv` and sets `_openai_base()`, `_catalog_request()`, `_available()`, `_litellm_route()`, `_clear_provider_env()`
- [ ] Add `--help` to both scripts
- [ ] Tests: verify the parser round-trips (PSV → Python dict → PSV is identical)

### Phase 1: Python migration (safe, reversible)
- [ ] Modify `bin/remote_provider_core.py` to **import from** `bin/remote-provider-registry.py` instead of owning the `PROVIDERS` dict directly
- [ ] Keep the inline `PROVIDERS` dict as a fallback / documentation alias, but have the parser be the authoritative source
- [ ] Run `tests/remote/test_remote_fallback.py` — must stay green
- [ ] The test at line 160 asserts `set(core.PROVIDERS) == {"gemini", "groq", "openrouter", "cloudflare", "github", "cerebras", "nvidia"}` — this will need updating to include the new providers

### Phase 2: Bash migration (safe, reversible)
- [ ] Modify `bin/remote-session.sh` to **source** `bin/remote-provider-registry.sh` instead of defining `_openai_base()`, `_catalog_request()`, `_available()`, `_litellm_route()`, `_clear_provider_env()` inline
- [ ] Remove the inline definitions (lines 198–210, 516–540, 768–780)
- [ ] Run existing bash tests / smoke tests
- [ ] Verify `remote-session.sh --help` still works
- [ ] Verify `remote-session.sh --verify <alias>` still works for a few aliases

### Phase 3: Cleanup
- [ ] Remove the now-redundant inline provider data from `remote_provider_core.py` (keep the dataclass definition — that's still Python code)
- [ ] Update docstrings in `remote_provider_core.py` to point at the PSV as the source of truth
- [ ] Add a comment in `config/remote-agents.sh` pointing at `config/remote-agents.psv` as the canonical form
- [ ] Consider whether `config/remote-agents.sh` can be deprecated entirely (the picker reads it — check all consumers first)
- [ ] Run full test suite: `pytest tests/` + any bash smoke tests

---

## 5. Files Changed

| File | Action | Phase |
|------|--------|-------|
| `config/remote-providers.psv` | **Create** | 0 |
| `config/remote-agents.psv` | **Create** | 0 |
| `bin/remote-provider-registry.py` | **Create** | 0 |
| `bin/remote-provider-registry.sh` | **Create** | 0 |
| `bin/remote_provider_core.py` | **Modify** (import from registry) | 1 |
| `bin/remote-session.sh` | **Modify** (source registry, remove inline defs) | 2 |
| `tests/remote/test_remote_fallback.py` | **Modify** (update PROVIDERS set assertion) | 1 |
| `bin/remote-provider-doctor.py` | Read-only (already imports from core) | — |
| `bin/remote-probe-log.py` | Read-only (already imports from core) | — |
| `bin/agent-fallback.py` | Read-only (already imports from core) | — |
| `bin/remote-agent-dispatch.py` | Read-only (already imports from core) | — |

---

## 6. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| PSV parsing bug corrupts provider data | Mutation test: feed malformed rows, assert graceful skip |
| Bash source order changes behavior | Keep `remote-provider-registry.sh` self-contained; no external deps |
| Existing scripts that grep `remote-agents.sh` for provider names | Audit grep consumers first; add a compatibility layer if needed |
| `github` is "retired" in `_available()` but still in PROVIDERS | Keep the retired-github check in the bash registry script; Python PROVIDERS stays complete for documentation |
| PSV file gets out of sync with `remote-agents.sh` during manual edits | Add a CI check or a `--verify` flag that compares the two |
| `static_headers` field contains `|` characters (e.g. `CF-Access-Client-Id:workers-ai;CF-Access-Client-Secret:`) | Use a different delimiter for the headers sub-field (semicolon), or escape `|` as `\|` in the PSV |

**Delimiter risk is the biggest concern.** The `static_headers` field for cloudflare contains semicolons, not pipes — so `|` as delimiter is safe for current data. But if a future provider adds a header with `|` in it, it breaks. Mitigation: document the escape rule (`\|` = literal pipe) and enforce it in the parser.

---

## 7. What This Does NOT Do

- Does not change the picker UI or model selection behavior
- Does not add or remove any providers (the WIP's 8 new providers stay)
- Does not change the LiteLLM proxy format
- Does not touch the local MLX model roster
- Does not change `config/remote-agents.sh` content (only adds a pointer to the PSV)

---

## 8. Decision Points for Reviewer

1. **PSV vs YAML vs JSON** — I recommended PSV because the repo already uses it. If you prefer YAML (better comments, nested structures), I'll switch.
2. **Keep `remote-agents.sh` or deprecate it** — The picker and bash tools read it. Full deprecation is Phase 3; we can keep it as a compatibility shim indefinitely.
3. **How to handle `github` (retired)** — Keep it in PROVIDERS for documentation + test coverage, but block it in `_available()`. Or remove it entirely.
4. **Where `IMPLEMENTED`, `RATE_LIMITED`, `CATALOG_SCANABLE` live** — These are Python-only concepts (bash doesn't have an equivalent). Keep them in `remote_provider_core.py` as derived sets, or move them to the registry too?

---

## 9. Estimated Effort

| Phase | Effort | Notes |
|-------|--------|-------|
| 0: Scaffold | ~1 session (this one) | Write PSV files + parser scripts |
| 1: Python migration | ~0.5 session | Import plumbing + test update |
| 2: Bash migration | ~0.5 session | Source + remove inline defs |
| 3: Cleanup | ~0.5 session | Docstring updates, deprecation decisions |
| **Total** | **~2 sessions** | All phases are reversible via git |
