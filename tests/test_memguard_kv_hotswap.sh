#!/usr/bin/env bash
# test_memguard_kv_hotswap.sh — Phase 4 plumbing: hotswap must consume the per-model KV
# precision from the menu store, pass the backend flag on the serve command, refuse a
# rapid-rejected architecture BEFORE any server work, and record the precision in the port
# meta. The window/flag/refusal LOGIC is unit-tested in tests/test_memguard_window.py; this
# file pins the GLUE in local-llm-hotswap.sh.
#
# The behavioural part reuses test_hotswap_ram_preflight.sh's scratch-clone recipe: dead port
# range, neutered rapid binary, stub preflight — no code path may be able to start a server.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$PWD"
HOTSWAP="$REPO/bin/local-llm-hotswap.sh"

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "hotswap KV glue:"

# 1. STATIC: the wiring exists and fails open.
if grep -q 'kv-get "$MODEL_NAME" 2>/dev/null || echo auto' "$HOTSWAP"; then
  ok "hotswap reads the KV precision via kv-get (fails open to auto)"
else
  bad "hotswap has no fail-open kv-get"
fi
if grep -q 'kv-flags --backend rapid' "$HOTSWAP"; then
  ok "rapid branch resolves its KV flags"
else
  bad "rapid branch has no kv-flags call"
fi
if grep -q 'LA_REPO\[$MODEL_NAME\]' "$HOTSWAP"; then
  ok "architecture detection reads the roster REPO id, not the local dir name"
else
  bad "kv-flags gets the local dir, not the repo id (a gemma with a non-matching subdir sails past the refusal)"
fi
if grep -q 'kv-flags --backend llama' "$HOTSWAP"; then
  ok "llama branches resolve their KV flags"
else
  bad "llama branches have no kv-flags call"
fi
RAPID_META_KV=$(grep -c 'echo "kv_dtype=$MG_KV"' "$HOTSWAP")
OMLX_META_KV=$(grep -c 'echo "kv_dtype_saved=$MG_KV"' "$HOTSWAP")
if [ "$RAPID_META_KV" -ge 1 ] && [ "$OMLX_META_KV" -ge 1 ]; then
  ok "rapid meta records the APPLIED kv_dtype; oMLX meta records kv_dtype_saved (requested, not applied)"
else
  bad "meta kv records wrong: rapid=$RAPID_META_KV applied, omlx=$OMLX_META_KV saved"
fi
if grep -qE '\[ "\$MG_KV" != "auto" \]' "$HOTSWAP"; then
  ok "flagless backends warn instead of silently ignoring a saved precision"
else
  bad "no not-applied warning for omlx/vllm"
fi

# 2. STATIC: a refusal must exit, not fall through.
ref_line=$(grep -n 'refused its saved KV precision' "$HOTSWAP" | head -1 | cut -d: -f1)
if [ -n "$ref_line" ] && sed -n "${ref_line},$((ref_line+3))p" "$HOTSWAP" | grep -q 'exit 1'; then
  ok "the rapid KV refusal exits"
else
  bad "the rapid KV refusal does not exit"
fi

# 2b. STATIC ORDER: the refusal must sit BEFORE the port scan — the scan reclaims zombie ports
#     and LA_HOTSWAP_FORCE_FRESH kills healthy servers, and a refused precision must never take
#     a server down with it (workflow finding: refusal-after-kill strands the user).
scan_line=$(grep -n 'Scanning ports' "$HOTSWAP" | head -1 | cut -d: -f1)
if [ -n "$ref_line" ] && [ -n "$scan_line" ] && [ "$ref_line" -lt "$scan_line" ]; then
  ok "the KV refusal runs before the port scan (no kill can precede it)"
else
  bad "KV refusal is not before the port scan (ref=$ref_line scan=$scan_line)"
fi

# 2c. STATIC: the healthy-reuse seam must compare the saved precision (workflow finding: a
#     changed kv_dtype was silently never applied on reuse) and the serve commands must append
#     the resolved flags (workflow finding: the appends had no pin).
if grep -q '\[ "$_meta_kv" = "$MG_KV" \]' "$HOTSWAP"; then
  ok "the healthy-reuse match includes kv_dtype"
else
  bad "reuse match ignores kv_dtype — a changed precision never takes effect"
fi
if grep -q 'RAPID_CMD+=($MG_KV_FLAGS)' "$HOTSWAP"; then
  ok "rapid serve command appends the KV flags"
else
  bad "rapid serve command lost the KV flag append"
fi
if grep -q 'LLAMA_CMD+=($MG_KV_FLAGS)' "$HOTSWAP"; then
  ok "llama serve commands append the KV flags"
else
  bad "llama serve commands lost the KV flag append"
fi

# 3. BEHAVIOURAL: end-to-end through the scratch clone. Fixtures: one gemma alias (rapid
#    rejects explicit int8 for it), one qwen alias (accepts int8); a temp menu store.
# SAFETY, inherited from test_hotswap_ram_preflight.sh: the recipe pins a dead port range and
# points the backend binary at nothing, so no code path can start a server; config.example.sh
# assigns ports with a plain `=` so an env override does NOT survive la_load_config — the
# overrides must be APPENDED to the scratch config to be the last assignments.
# The port itself is pre-checked: if anything happens to listen on 9990, the behavioural part
# is SKIPPED rather than letting hotswap's zombie-reclaim see it.
if lsof -i :9990 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "  ⚠️  port 9990 is occupied — skipping the behavioural cases (not a pass)"
  echo
  [ "$fail" -eq 0 ] && { echo "hotswap KV glue: PASS (statics only; behaviourals skipped)"; exit 0; }
  echo "hotswap KV glue: FAIL ($fail failed, $pass passed; behaviourals skipped)"; exit 1
fi
SCRATCH="$WORK/repo"; mkdir -p "$SCRATCH"
cp -RL "$REPO/bin" "$REPO/config" "$SCRATCH/" 2>/dev/null
for _f in "$SCRATCH/config/config.local.sh" "$SCRATCH/config/config.example.sh"; do
  [ -L "$_f" ] && { echo "FATAL: $_f is a symlink — refusing to write through it" >&2; exit 1; }
done
append_both() { printf '%s\n' "$1" >> "$SCRATCH/config/config.example.sh"
                [ -f "$SCRATCH/config/config.local.sh" ] && printf '%s\n' "$1" >> "$SCRATCH/config/config.local.sh"; }
append_both ''
append_both 'LA_PORT_START=9990'
append_both 'LA_PORT_MAX=9990'
append_both 'LA_RAPID_BIN=/nonexistent/rapid-mlx'
append_both 'la_register kvtest-gemma kvtest-gemma-fake rapid qwen "" false "" high "" mlx-community/gemma-3-1b-it-4bit 1.0'
append_both 'la_register kvtest-qwen  kvtest-qwen-fake  rapid qwen "" false "" high "" mlx-community/Qwen3.0-1.7B-4bit 1.0'

# Fixture model dirs: hotswap validates (a) the dir exists and (b) it holds a file >1 MB
# (bin/local-llm-hotswap.sh:201-207), so each fixture alias gets a 2 MB stub weight.
MODELS="$WORK/models"
mkdir -p "$MODELS/kvtest-gemma-fake" "$MODELS/kvtest-qwen-fake"
dd if=/dev/zero of="$MODELS/kvtest-gemma-fake/model.safetensors" bs=1m count=2 2>/dev/null
dd if=/dev/zero of="$MODELS/kvtest-qwen-fake/model.safetensors" bs=1m count=2 2>/dev/null
append_both "LA_MODELS_DIR=$MODELS"

# Stub preflight: pass-through, so the KV gate is the thing under test.
mkdir -p "$WORK/bin"
printf '#!/usr/bin/env bash\nexit 0\n' > "$WORK/bin/la-ram-preflight.sh"
chmod +x "$WORK/bin/la-ram-preflight.sh"
cp "$WORK/bin/la-ram-preflight.sh" "$SCRATCH/bin/la-ram-preflight.sh"

CFGSTORE="$WORK/menu-store"; mkdir -p "$CFGSTORE"
# LA_CONFIG_DIR pins the scratch tree as authoritative: it has no config.local.sh (the
# registrations live in its config.example.sh), so without the pin the live-config resolution
# would load the machine state dir's real registry and the kvtest-* aliases would not exist.
run_swap() { FREE_AGENTS_CONFIG_DIR="$CFGSTORE" LA_CONFIG_DIR="$SCRATCH/config" "$SCRATCH/bin/local-llm-hotswap.sh" "$1" 2>&1; }
kv_set()   { FREE_AGENTS_CONFIG_DIR="$CFGSTORE" python3 "$REPO/bin/session_menu_state.py" kv-set "$1" "$2"; }

# a) UNSET kv (= auto) must never be refused — and must reach the STAGE AFTER the gate (the
#    neutered-binary error), proving the gate really was passed rather than the run dying earlier.
out=$(run_swap kvtest-gemma); rc=$?
if printf '%s' "$out" | grep -qi 'refused its saved KV precision'; then
  bad "auto (unset) KV must never be refused"
elif printf '%s' "$out" | grep -q 'Rapid-MLX executable not found'; then
  ok "auto (unset) KV passes the gate (reached the binary check)"
else
  bad "auto run died somewhere other than the gate or the binary check — inspect: $out"
fi

# b) gemma + int8 must be refused BEFORE any server work AND before the port scan — the old
#    SUCCESS_PORT check was vacuous (unreachable on every path this harness can take); the
#    no-scan assertion is the real pin for the refusal-before-kill ordering.
kv_set kvtest-gemma int8 >/dev/null 2>&1
out=$(run_swap kvtest-gemma); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi 'refused its saved KV precision'; then
  ok "gemma + int8 is refused pre-launch (rc=$rc)"
else
  bad "gemma + int8 was not refused (rc=$rc)"
fi
if printf '%s' "$out" | grep -q 'Scanning ports'; then
  bad "the refusal run reached the port scan — the gate sits after the kill paths"
else
  ok "the refusal fires before the port scan (no kill path can run first)"
fi

# c) qwen + int8 must pass the KV gate AND reach the after-gate stage, or the pass is vacuous.
kv_set kvtest-qwen int8 >/dev/null 2>&1
out=$(run_swap kvtest-qwen); rc=$?
if printf '%s' "$out" | grep -qi 'refused its saved KV precision'; then
  bad "qwen + int8 wrongly refused (the rejection list is too wide)"
elif printf '%s' "$out" | grep -q 'Rapid-MLX executable not found'; then
  ok "qwen + int8 passes the KV gate (reached the binary check)"
else
  bad "qwen run died somewhere other than the gate or the binary check — inspect: $out"
fi

echo
if [ "$fail" -eq 0 ]; then echo "hotswap KV glue: PASS ($pass checks)"; exit 0
else echo "hotswap KV glue: FAIL ($fail failed, $pass passed)"; exit 1; fi
