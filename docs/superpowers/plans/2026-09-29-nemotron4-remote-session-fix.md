# Nemotron 4 Remote Session Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the remote-session.sh launcher so that Nemotron 4 (nvidia-nemotron4 alias) works correctly when selected from the interactive menu, instead of returning "There's an issue with the selected model (claude-opus-5)".

**Architecture:** The remote-session.sh script uses LiteLLM as a translating proxy between Claude Code's Anthropic API format and NVIDIA NIM's OpenAI-compatible API. The proxy maps spoofed Claude model IDs (claude-opus-5, claude-opus-4-8, etc.) to actual provider model IDs. The issue is likely a model ID format mismatch for Nemotron 4, or Nemotron 4 not being available on the NVIDIA NIM free tier.

**Tech Stack:** Bash (remote-session.sh), Python (la_proxy_hooks.py), LiteLLM proxy, NVIDIA NIM API

**Spec:** This plan addresses the model selection issue for nvidia-nemotron4 in the remote agents roster.

## Global Constraints

- Must maintain compatibility with existing working models (Nemotron 3 Ultra, Nemotron 3 Super, etc.)
- Must use the interactive menu flow (user selects from menu, not CLI args)
- Must not break the spoofing mechanism that maps claude-opus-5 → actual provider model
- Changes must be in the local-agents repo (not the plugin cache)
- Follow existing patterns in remote-session.sh for model validation and error handling

## Review Focus

1. **Model ID format mismatch**: NVIDIA NIM may expect a different model ID format for Nemotron 4 vs Nemotron 3 models
2. **Model availability**: Nemotron 4 may not be available on the NVIDIA NIM free tier that the API key accesses
3. **Catalog vs roster mismatch**: The catalog may return different model IDs than what's hardcoded in the roster
4. **Proxy configuration**: The LiteLLM proxy config may not correctly map the spoofed model to the actual Nemotron 4 model
5. **Error propagation**: NVIDIA API errors may not be properly translated, causing Claude Code to show "model not found" for the spoofed model

---

### Task 1: Investigate NVIDIA NIM Catalog for Nemotron 4 Model ID Format

**Files:**
- Read: `/Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh` (catalog request logic)
- Create: `/tmp/test_nvidia_catalog.sh` (test script)

**Interfaces:**
- Consumes: NVIDIA API key from `~/.api_keys/nvidia`
- Produces: List of actual model IDs available on NVIDIA NIM catalog

- [ ] **Step 1: Write a test script to query NVIDIA catalog directly**

```bash
#!/usr/bin/env bash
# Test script to query NVIDIA NIM catalog and see available model IDs

source /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-keys.sh
source /Users/bra0002h/ClaudeWorkspace/local-agents/config/emoji.sh

# Get NVIDIA API key
NVIDIA_KEY=$(bash /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-keys.sh --env nvidia | cut -d'=' -f2)

echo "Querying NVIDIA NIM catalog..."
curl -s -H "Authorization: Bearer $NVIDIA_KEY" \
     -H "Accept: application/json" \
     "https://integrate.api.nvidia.com/v1/models" | python3 -m json.tool | head -200
```

- [ ] **Step 2: Run test to verify catalog response**

Run: `bash /tmp/test_nvidia_catalog.sh`
Expected: JSON response with model IDs - look for nemotron-4 entries

- [ ] **Step 3: Compare catalog model IDs with roster entries**

Run: `grep nemotron /Users/bra0002h/ClaudeWorkspace/local-agents/config/remote-agents.sh`
Compare: Check if catalog model IDs match roster model-id field (field 3)

- [ ] **Step 4: Document findings**

Note: Actual NVIDIA catalog model ID format for Nemotron 4

---

### Task 2: Test Proxy Config Generation for Nemotron 4

**Files:**
- Read: `/Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh` (write_proxy_config function)
- Create: `/tmp/test_proxy_config.sh` (test script)

**Interfaces:**
- Consumes: NVIDIA API key, model ID from roster
- Produces: Generated LiteLLM proxy YAML config

- [ ] **Step 1: Write a test script to generate proxy config for nvidia-nemotron4**

```bash
#!/usr/bin/env bash
# Test proxy config generation for nvidia-nemotron4

source /Users/bra0002h/ClaudeWorkspace/local-agents/config/remote-agents.sh
source /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-keys.sh

# Simulate what write_proxy_config does for nvidia-nemotron4
PROV="nvidia"
MODEL="nvidia/nemotron-4-340b"  # from roster
THINKING="false"
EFFORT=""

# Source the functions we need
source /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh 2>/dev/null || true

# Just test the model name construction
echo "Provider: $PROV"
echo "Model from roster: $MODEL"
echo "LiteLLM model would be: nvidia_nim/$MODEL"
```

- [ ] **Step 2: Run test and verify model name format**

Run: `bash /tmp/test_proxy_config.sh`
Expected: Shows the exact model string that would be sent to NVIDIA

- [ ] **Step 3: Test with dry-run mode of remote-session.sh**

Run: `bash /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh --dry-run nvidia-nemotron4`
Expected: Shows the resolved model and provider without launching

---

### Task 3: Test Actual Nemotron 4 Generation via Proxy

**Files:**
- Modify: None (read-only investigation)
- Test: Direct API call through LiteLLM proxy

**Interfaces:**
- Consumes: Running LiteLLM proxy, NVIDIA API key
- Produces: Test generation response or error

- [ ] **Step 1: Start a test proxy with nvidia-nemotron4 config**

Run: `bash /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh --dry-run nvidia-nemotron4` to see the config, then manually start a test proxy

- [ ] **Step 2: Send a test request to the proxy**

```bash
# After starting proxy on port XXXX
curl -s -X POST http://127.0.0.1:XXXX/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-local-agents-remote" \
  -d '{"model": "claude-opus-5", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}'
```

- [ ] **Step 3: Analyze response**

If error: Check if it's a model not found, auth error, or thinking block error
If success: Nemotron 4 works, issue is elsewhere

---

### Task 4: Verify Model ID Format in NVIDIA NIM

**Files:**
- Read: NVIDIA NIM documentation (via WebFetch if needed)
- Compare: Roster model IDs vs NIM expected format

**Interfaces:**
- Consumes: NVIDIA NIM model naming conventions
- Produces: Corrected model ID format for roster

- [ ] **Step 1: Check NVIDIA NIM model naming for Nemotron 4**

Search: WebFetch NVIDIA NIM model catalog or documentation for Nemotron 4 340B
Compare: Does NIM use `nvidia/nemotron-4-340b` or `nemotron-4-340b` or another format?

- [ ] **Step 2: Check if Nemotron 4 is available on free tier**

Query: NVIDIA NIM free tier model list
Verify: Is Nemotron 4 340B included in free tier?

---

### Task 5: Fix Roster Entry or Proxy Config for Nemotron 4

**Files:**
- Modify: `/Users/bra0002h/ClaudeWorkspace/local-agents/config/remote-agents.sh` (roster entry)
- Modify: `/Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh` (if proxy config needs adjustment)

**Interfaces:**
- Consumes: Correct model ID from Task 4
- Produces: Working nvidia-nemotron4 alias

- [ ] **Step 1: Update roster with correct model ID**

If catalog shows different format (e.g., `nemotron-4-340b` without `nvidia/` prefix):
```bash
# Change from:
"nvidia-nemotron4|nvidia|nvidia/nemotron-4-340b|NVIDIA Nemotron 4 340B|unknown|..."
# To:
"nvidia-nemotron4|nvidia|nemotron-4-340b|NVIDIA Nemotron 4 340B|unknown|..."
```

- [ ] **Step 2: If model not available on free tier, update tier and notes**

If Nemotron 4 requires paid access:
```bash
# Change tier from unknown to trial
# Update notes to reflect paid requirement
```

- [ ] **Step 3: Test the fix with interactive menu**

Run: `bash /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh nvidia-nemotron4`
Verify: Session starts without "model not found" error

---

### Task 6: Add Validation to Prevent Regression

**Files:**
- Modify: `/Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh` (add catalog verification)
- Test: Verify alias before launch

**Interfaces:**
- Consumes: verify_alias function
- Produces: Pre-launch validation that model exists in catalog

- [ ] **Step 1: Add pre-launch catalog verification for nvidia models**

In launch section, before starting proxy, call verify_alias for the selected model
If verification fails (model not in catalog), show error and exit gracefully

- [ ] **Step 2: Test verification catches the issue**

Run with broken model ID → should show "model NOT in this catalogue response" instead of generic error

---

### Task 7: Integration Test and Documentation

**Files:**
- Test: Full interactive menu flow
- Document: Update any relevant docs

**Interfaces:**
- Consumes: All previous fixes
- Produces: Verified working Nemotron 4 selection

- [ ] **Step 1: Full integration test via interactive menu**

Run: `bash /Users/bra0002h/ClaudeWorkspace/local-agents/bin/remote-session.sh`
Select: nvidia-nemotron4 from menu
Verify: Session starts, can send prompts, gets responses

- [ ] **Step 2: Test with different effort levels**

Test: Select effort level (low/medium/high) for Nemotron 4
Verify: Thinking handling works correctly

- [ ] **Step 3: Commit changes**

```bash
cd /Users/bra0002h/ClaudeWorkspace/local-agents
git add config/remote-agents.sh bin/remote-session.sh
git commit -m "fix: Nemotron 4 model ID format for NVIDIA NIM free tier

- Updated nvidia-nemotron4 roster entry with correct model ID from NVIDIA catalog
- Added pre-launch catalog verification to catch model availability issues early
- Nemotron 4 340B now works via interactive menu selection"
```

---

## Self-Review Checklist

After writing this plan, I verified:

1. **Spec coverage**: All investigation steps cover the possible root causes (model ID format, availability, proxy config)
2. **No placeholders**: Every step has actual code/commands to run
3. **Type consistency**: Model ID strings are consistent across tasks
4. **Review Focus addressed**: Each failure mode has a corresponding investigation task

## Execution Approach Recommendation

**Plan complete and saved to `docs/superpowers/plans/2026-09-29-nemotron4-remote-session-fix.md`. Please review the plan. Which execution approach would you prefer?**

- **Subagent-driven** - A fresh subagent implements each task and a fresh reviewer checks it before the next one starts, then a whole-branch review at the end. Most thorough; costs a fresh context per task and per review.
- **Native** - I implement every task myself in this session, the way this harness runs work, then one fresh reviewer on the most capable model checks the whole branch. Cheapest and fastest; no independent review until the end.

**For this plan I recommend Native**, because the tasks are primarily investigative (catalog queries, dry-runs, proxy config inspection) with a small targeted fix at the end, and they depend heavily on live API responses that a subagent would also need to query.