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
META_KV_COUNT=$(grep -c 'echo "kv_dtype=$MG_KV"' "$HOTSWAP")
if [ "$META_KV_COUNT" -ge 2 ]; then
  ok "both meta-writing branches (rapid, omlx) record kv_dtype"
else
  bad "expected kv_dtype in both rapid and omlx metas, found $META_KV_COUNT (llama/vllm write no metas — pre-existing)"
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

# 3. BEHAVIOURAL: end-to-end through the scratch clone. Fixtures: one gemma alias (rapid
#    rejects explicit int8 for it), one qwen alias (accepts int8); a temp menu store.
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
append_both 'la_register kvtest-gemma kvtest-gemma-fake mlx qwen "" false "" high "" mlx-community/gemma-3-1b-it-4bit 1.0'
append_both 'la_register kvtest-qwen  kvtest-qwen-fake  mlx qwen "" false "" high "" mlx-community/Qwen3.0-1.7B-4bit 1.0'

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
run_swap() { FREE_AGENTS_CONFIG_DIR="$CFGSTORE" "$SCRATCH/bin/local-llm-hotswap.sh" "$1" 2>&1; }
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

# b) gemma + int8 must be refused BEFORE any server work, with no SUCCESS_PORT.
kv_set kvtest-gemma int8 >/dev/null 2>&1
out=$(run_swap kvtest-gemma); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi 'refused its saved KV precision'; then
  ok "gemma + int8 is refused pre-launch (rc=$rc)"
else
  bad "gemma + int8 was not refused (rc=$rc)"
fi
if printf '%s' "$out" | grep -q 'SUCCESS_PORT='; then
  bad "SUCCESS_PORT printed despite the KV refusal"
else
  ok "no SUCCESS_PORT on refusal"
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
