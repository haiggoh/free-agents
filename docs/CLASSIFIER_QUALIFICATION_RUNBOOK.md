# Classifier Qualification Runbook

**Purpose**: Qualify a local model as an Auto Mode classifier against genuine captured requests.

**Reference**: ROADMAP.md `0.13.9` — Locally routed Auto Mode correctness

---

## Prerequisites

1. **Rapid-MLX 0.14.3** installed and working
   ```bash
   /path/to/rapid-mlx --version
   # Should output: rapid-mlx 0.14.3
   ```

2. **Classifier fixtures** captured and verified
   - Location: `~/.cache/local-agents/rapid-auto-fixtures/<hash>/`
   - Required: `classifier-request.json` (Stage 1 fixture A)
   - Optional: `verification-request.json` (Stage 1 fixture B, genuine changed prefix)
   - Stage 2 fixture: `max_tokens=8192`, single message (capture separately)

3. **Candidate model** downloaded and registered
   - Current leading candidate: **Devstral Small 2 24B** (`devstral-small2`)
   - Registration: `serve=rapid`, `tool_parser=mistral`, `thinking=false`

---

## Qualification Procedure

### Phase 1: Server Setup

```bash
# Start Rapid server with candidate model as classifier
# The classifier runs on the SAME port as the session model (dual-identity)
# Session model: claude-opus-5 (served via --served-model-name)
# Classifier model: claude-sonnet-5 (retained via symlink in serve_root)

MODEL_DIR=~/.models/Devstral-Small-2-24B-4bit
RAPID_BIN=~/.venvs/rapid-mlx-0.14.3/bin/rapid-mlx
PORT=8002

# Use the dedicated launcher which handles dual-identity setup
LA_RAPID_AUTO_BIN="$RAPID_BIN" \
LA_RAPID_AUTO_MODEL_DIR="$MODEL_DIR" \
CLAUDE_CODE_AUTO_MODE_SEGMENTED_TRANSCRIPT=1 \
bash bin/launch-claude-agent-rapid-auto.sh devstral-small2 high
```

### Phase 2: Automated Qualification (classifier-qualify.py)

```bash
cd ~/ClaudeWorkspace/local-agents

# Stage 1: Changed-prefix reuse (critical gate)
python3 bin/classifier-qualify.py \
    --candidate devstral-small2 \
    --stage1-fixture ~/.cache/local-agents/rapid-auto-fixtures/<hash>/classifier-request.json \
    --stage1-synthetic-b \
    --backend-url http://127.0.0.1:8002 \
    --server-log ~/.claude/logs/rapid_auto_8002.log \
    --warm-runs 4 \
    --merge-adjacent-user-messages \
    --json /tmp/stage1_report.json

# Stage 2: Contract + warm latency (if Stage 1 passes)
python3 bin/classifier-qualify.py \
    --candidate devstral-small2 \
    --stage2-fixture ~/.cache/local-agents/rapid-auto-fixtures/<hash>/stage2-fixture.json \
    --backend-url http://127.0.0.1:8002 \
    --server-log ~/.claude/logs/rapid_auto_8002.log \
    --warm-runs 4 \
    --json /tmp/stage2_report.json
```

### Phase 3: Live Smoke Test

```bash
# Run a real Claude Code Auto Mode session
JOYIA_LOCAL_AUTO_CLASSIFIER=1 \
bash bin/launch-local-auto-mode.sh qwen-3.8-operator a high

# In the session, execute a consequential Bash command
# The classifier should approve it within the 45s warm deadline
```

---

## Success Criteria

| Stage | Gate | Threshold |
|-------|------|-----------|
| **Stage 1** | Changed-prefix reuse | ≥ 90% cache reuse (measured from server log) |
| **Stage 1** | Contract validity | 100% of runs pass classifier contract |
| **Stage 2** | Contract validity | 5/5 runs (cold + 4 warm) pass contract |
| **Stage 2** | Warm reuse | 4/4 warm runs ≥ 90% reuse |
| **Stage 2** | Warm latency | All warm runs ≤ 45 seconds |
| **Live** | Real session | Consequential Bash approved without timeout |

---

## Outcome Codes

| Code | Meaning |
|------|---------|
| `DIRECT_PASS` | All gates passed without adapters |
| `MODEL_PASS_WITH_ADAPTER` | Passed but needed adjacent-user-message merge (Mistral) |
| `EXACT_ONLY` | Exact replay OK, changed-prefix reuse FAILED (Qwen3.6 shape) |
| `CONTRACT_FAIL` | Response violated classifier contract |
| `CACHE_OR_LATENCY_FAIL` | Contract OK but cache reuse or latency failed |
| `RUNTIME_OR_CONTEXT_FAIL` | Backend refused (OOM, context overflow, HTTP error) |
| `HARNESS_FAILURE` | Measurement incomplete (no server log for cache evidence) |

---

## Known Failure Modes

1. **Qwen3.6 (EXACT_ONLY)**: Hybrid cache `non_trimmable=True` → rejects changed prefix despite 98.84% shared tokens
2. **Devstral without adapter (RUNTIME_OR_CONTEXT_FAIL)**: Adjacent user messages violate Mistral alternation → HTTP 400
3. **No server log (HARNESS_FAILURE)**: Cache reuse cannot be measured → UNJUDGED, not a failure

---

## Devstral Adapter

If Devstral fails Stage 1 with HTTP 400 (alternation), enable adapter:
```bash
--merge-adjacent-user-messages
```
This merges adjacent user-role messages, preserving text. A pass with adapter is reported as `MODEL_PASS_WITH_ADAPTER`, never `DIRECT_PASS`.

---

## Next Steps After Qualification

1. If `DIRECT_PASS` or `MODEL_PASS_WITH_ADAPTER`:
   - Update `LA_RAPID_AUTO_MODEL_DIR` in config.local.sh to Devstral path
   - Wire `launch-claude-agent-rapid-auto.sh` into default Auto Mode route
   - Update ROADMAP.md to mark `0.13.9` item complete

2. If `EXACT_ONLY`:
   - Candidate cannot serve live Auto Mode (prefix grows every turn)
   - Try next candidate or different quantization

3. If `HARNESS_FAILURE`:
   - Ensure `--server-log` points to active Rapid log
   - Verify Rapid log emits `cached_tokens` / `prompt_tokens` lines