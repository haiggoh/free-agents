# Unified Backend Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify management of ALL inference backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) into a single cohesive system with versioned installations, automated update checks, qualification tracking, and canonical installation via install-backend.sh — replacing the current fragmented approach (manual git clone for vllm-mlx, Homebrew-only for oMLX, dedicated Rapid-MLX manager).

**Architecture:** Extend the existing `manage-rapid-mlx.py` pattern into a generic `manage-backend.py` that handles multiple backends through a plugin architecture. Each backend gets a BackendManager subclass with consistent interfaces for install/validate/promote/check-updates. The weekly update check and daily upgrade scripts are consolidated. install-backend.sh becomes the single entry point that sets up all backends correctly from the start.

**Tech Stack:** Python 3.11+ (existing), bash (existing launchers), launchd (existing scheduling), PyPI API, GitHub API, Homebrew API (for oMLX), llama.cpp release assets.

**Spec:** This plan implements the unified backend management vision described in the user's request, building on:
- Existing `install/manage-rapid-mlx.py` (reference implementation)
- Existing `install/install-backend.sh` (canonical installer)
- Existing `scripts/local-stack-update-check.sh` (weekly update check)
- Existing `scripts/daily-package-upgrade.sh` (daily upgrade)
- Tournament waypoint `local-model-acquisition` and plan `Plan — Local Model Acquisition...`

## Global Constraints

- Python version: 3.11+ (managed by Homebrew on this machine)
- All versioned environments in `~/.venvs/<backend>-<version>/`
- All update checks must be read-only (notify only, never auto-upgrade)
- Rapid-MLX remains DEFAULT backend (LA_DEFAULT_MLX_BACKEND=rapid)
- vllm-mlx stays LEGACY comparison lane (explicit pin only)
- oMLX stays ISOLATED optional backend (Flash-Next/streaming)
- llama.cpp for GGUF only (dispatch-only)
- litellm for remote free-API routing (NVIDIA, Gemini, Groq, etc.)
- No breaking changes to existing launcher scripts (launch-claude-agent.sh, csl, etc.)
- All new code must support `--help` and `--dry-run`
- Pin promotion requires clean git worktree (existing invariant)

## Review Focus

1. **Backend version skew**: Rapid-MLX 0.15.3 pinned but vllm-mlx at v0.4.1+local vs PyPI 0.5.0 — upgrade must not silently invalidate recorded Metal/cache evidence
2. **Fork patch compatibility**: vllm-mlx local patches don't apply to v0.5.0 — must detect and handle version-specific patch sets
3. **oMLX Homebrew vs PyPI**: oMLX not on PyPI — version check must use GitHub releases + Homebrew formula
4. **llama.cpp binary management**: Not a Python package — must download/verify release binaries or build from source
5. **litellm as proxy**: Not a local inference engine — manages remote API routing, different update semantics
6. **Concurrent backend operation**: Multiple backends may run simultaneously on different ports — version checks must identify which environment maps to which backend

---

### Task 1: Design Backend Manager Abstraction

**Files:**
- Create: `install/manage-backend.py` (new unified manager)
- Modify: `install/manage-rapid-mlx.py` (refactor to use base class)
- Test: `tests/test_manage_backend.py`

**Interfaces:**
- Consumes: None (foundational)
- Produces: `BackendManager` base class, `RapidMLXManager`, `VLLMMLXManager`, `OMLXManager`, `LlamaCppManager`, `LitellmManager` subclasses

### Task 2: Implement RapidMLXManager (refactor existing)

**Files:**
- Modify: `install/manage-rapid-mlx.py` → `install/managers/rapid_mlx.py`
- Create: `install/managers/__init__.py`
- Test: `tests/test_rapid_mlx_manager.py`

### Task 3: Implement VLLMMLXManager

**Files:**
- Create: `install/managers/vllm_mlx.py`
- Test: `tests/test_vllm_mlx_manager.py`

### Task 4: Implement OMLXManager

**Files:**
- Create: `install/managers/omlx.py`
- Test: `tests/test_omlx_manager.py`

### Task 5: Implement LlamaCppManager

**Files:**
- Create: `install/managers/llama_cpp.py`
- Test: `tests/test_llama_cpp_manager.py`

### Task 6: Implement LitellmManager

**Files:**
- Create: `install/managers/litellm.py`
- Test: `tests/test_litellm_manager.py`

### Task 7: Unified CLI Entry Point

**Files:**
- Create: `install/manage-backend.py` (main CLI)
- Modify: `install/manage-rapid-mlx.py` (thin wrapper for backward compat)
- Test: `tests/test_manage_backend_cli.py`

### Task 8: Update install-backend.sh

**Files:**
- Modify: `install/install-backend.sh`
- Test: Manual integration test

### Task 9: Consolidate Update Checks

**Files:**
- Modify: `scripts/local-stack-update-check.sh` → `scripts/check-backend-updates.sh`
- Modify: `scripts/daily-package-upgrade.sh` (remove backend-specific logic)
- Create: `scripts/check-backend-updates.py` (Python implementation for complex logic)
- Test: `tests/test_update_check.py`

### Task 10: Launchd Integration

**Files:**
- Modify: `~/Library/LaunchAgents/com.haiggoh.local-stack-update-check.plist`
- Create: `~/Library/LaunchAgents/com.haiggoh.backend-update-check.plist` (if separate)
- Test: Manual launchd load/test

### Task 11: Configuration Integration

**Files:**
- Modify: `config/config-lib.sh` (backend discovery)
- Modify: `config/config.example.sh` (document new backend config)
- Test: `tests/test_config_integration.py`

### Task 12: Tournament Integration

**Files:**
- Modify: `install/download-models.sh` (use new backend managers)
- Create: `install/qualify-backend.py` (backend qualification for tournament)
- Test: `tests/test_tournament_integration.py`

### Task 13: Documentation & Migration

**Files:**
- Create: `docs/backend-management.md`
- Modify: `README.md` (update installation instructions)
- Modify: `CHANGELOG.md`

---

## Task Details (Skeleton — to be fleshed out)

[Each task above will be expanded with detailed steps following the writing-plans skill format]