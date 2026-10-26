#!/usr/bin/env bash
# test_hotswap_weight_format_gate.sh — hotswap must REFUSE an artifact whose weights no installed
# backend can read, before it starts anything.
#
# WHY this exists (measured 2026-10-25): ~/.models/deepseek-v4-flash-0731-coder declares
# model_type=deepseek_v4, which oMLX's mlx_vlm CAN build, and weight_format="mxtq" (2-bit
# MoEspresso TurboQuant bundles), which it cannot. la-detect-backend.py answered `omlx` from the
# architecture alone, hotswap launched oMLX, 53 GB of weights loaded, and the load died inside
# strict load_weights: "Received 46 parameters not in model: embed.biases, embed.scales,
# head.scales, model.layers.N.ffn.experts.tq_bundle". The session saw only "409 Model ... is
# unavailable after a previous load failure" — no cause, no fix, 10 retries. The catalog row has
# said `MoEspresso>=2.1` in a REQUIRED field since 2026-09-01 and no code ever read it.
#
# The gate's refusal must be EARLY (before the port scan) and must never evict: hotswap is invoked
# BY sessions that must not be killed, so a refusal that ran after the kill paths would strand the
# user with nothing.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$PWD"
HOTSWAP="$REPO/bin/local-llm-hotswap.sh"

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"
_cleanup() { for p in $(/usr/sbin/lsof -t -i :9989 -sTCP:LISTEN 2>/dev/null); do kill -9 "$p" 2>/dev/null; done; rm -rf "$WORK"; }
trap _cleanup EXIT
_cleanup 2>/dev/null || true

echo "hotswap weight-format gate:"

# 1. STATIC: the gate exists and honours a documented override.
if grep -q 'la-detect-backend.py --gate' "$HOTSWAP"; then
  ok "hotswap consults la-detect-backend.py --gate"
else
  bad "hotswap never consults la-detect-backend.py --gate"
fi
if grep -q 'LA_GATE_WEIGHT_FORMAT' "$HOTSWAP"; then
  ok "hotswap honours the LA_GATE_WEIGHT_FORMAT override"
else
  bad "hotswap has no LA_GATE_WEIGHT_FORMAT override"
fi

# 2. POSITION: the gate must run BEFORE the port scan. Contrast with the RAM preflight, which is
#    deliberately AFTER it (reusing a live server loads no weights and must never be refused): this
#    gate judges the ARTIFACT, which a reuse path also serves, and a refusal must never reach a
#    kill path. Both orders are correct for their own gate; asserting the wrong one here would be
#    the same class of bug as the RAM preflight's fail-open reuse credit.
scan_line=$(grep -n 'Scanning ports' "$HOTSWAP" | head -1 | cut -d: -f1)
gate_line=$(grep -n 'la-detect-backend.py --gate' "$HOTSWAP" | head -1 | cut -d: -f1)
if [ -n "$scan_line" ] && [ -n "$gate_line" ] && [ "$gate_line" -lt "$scan_line" ]; then
  ok "the gate runs before the port scan (a refusal can never evict)"
else
  bad "the gate is not before the port scan (scan=$scan_line gate=$gate_line)"
fi

# 3. The refusal must REFUSE, never evict: no kill on the failure path.
if [ -n "$gate_line" ]; then
  gate_block=$(sed -n "${gate_line},$((gate_line+16))p" "$HOTSWAP")
  if printf '%s' "$gate_block" | grep -qE '\b(kill|pkill|la-evict)\b'; then
    bad "the gate's failure path kills something — it must only refuse"
  else
    ok "the gate's failure path refuses without killing anything"
  fi
else
  bad "no gate to inspect (skipped, not passed)"
fi

# 4. BEHAVIOURAL: a real foreign-format artifact must be refused, with its reason, and must never
#    print SUCCESS_PORT.
MODELS="$WORK/models"
mkdir -p "$MODELS/deepseek-v4-flash-0731-coder" "$MODELS/plain-mlx"
printf '%s' '{"model_type":"deepseek_v4","weight_format":"mxtq","mxtq_bits":2,"expert_dtype":"fp4"}' \
  > "$MODELS/deepseek-v4-flash-0731-coder/config.json"
printf '%s' '{"model_type":"deepseek_v4"}' > "$MODELS/plain-mlx/config.json"
# The data block is padded past 1 MB on purpose: hotswap's on-disk gate refuses a metadata-only
# shell with a DIFFERENT message ("holds no model"), so a stub-sized file would make the later
# assertions vacuous — case 6 below would pass while never reaching the backend decision at all.
/usr/bin/python3 - "$MODELS/deepseek-v4-flash-0731-coder/model-00001-of-00001.safetensors" \
                   "$MODELS/plain-mlx/model-00001-of-00001.safetensors" <<'PY'
import json, struct, sys
tq, plain = sys.argv[1], sys.argv[2]
def write(path, key):
    h = {key: {"dtype": "F16", "shape": [1]}}
    h["__metadata__"] = {"format": "pt"}
    b = json.dumps(h).encode()
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(b)) + b + b"\0" * (2 << 20))
write(tq, "model.layers.0.ffn.experts.tq_bundle")
write(plain, "model.layers.0.self_attn.q_proj.weight")
PY
for _f in "$MODELS/deepseek-v4-flash-0731-coder/model-00001-of-00001.safetensors" \
          "$MODELS/plain-mlx/model-00001-of-00001.safetensors"; do
  [ -s "$_f" ] || { echo "FATAL: fixture weight file missing: $_f" >&2; exit 1; }
done

# SAFETY, learned the hard way (test_hotswap_ram_preflight.sh §4): config.example.sh assigns
# LA_PORT_START with a plain `=`, so an environment override does NOT survive la_load_config, and an
# early version of that test launched a real server on a live port. Append the overrides to the
# scratch config instead, and point every backend binary at nothing — the gate is what is under
# test, and no code path past it may be able to start a server.
SCRATCH="$WORK/repo"; mkdir -p "$SCRATCH"
cp -RL "$REPO/bin" "$REPO/config" "$SCRATCH/" 2>/dev/null
for _f in "$SCRATCH/config/config.local.sh" "$SCRATCH/config/config.example.sh"; do
  [ -L "$_f" ] && { echo "FATAL: $_f is a symlink — refusing to write through it" >&2; exit 1; }
done
rm -f "$SCRATCH/config/config.local.sh"
# A fresh scratch registry. It cannot APPEND to config.example.sh: that file is the shipped
# example, whose la_register lines are deliberately COMMENTED OUT, so an append there registers
# nothing and every "Registered aliases:" list comes back empty — the gate then never runs and the
# test reports a pass it never earned. Note the field order (config-lib.sh la_register):
# alias | subdir | serve | tool_parser | reasoning_parser | thinking | spoof | effort | ...
cat > "$SCRATCH/config/config.local.sh" <<CFG
LA_MODELS_DIR=$MODELS
LA_PORT_START=9989
LA_PORT_MAX=9989
LA_RAPID_BIN=/nonexistent/rapid-mlx
LA_OMLX_BIN=/nonexistent/omlx
LA_VENV=/nonexistent/venv/bin
la_register ds-coder deepseek-v4-flash-0731-coder omlx "" "" false "" high "" "" ""
la_register plain-mlx    plain-mlx    omlx "" "" false "" high "" "" ""
CFG
[ -s "$SCRATCH/config/config.local.sh" ] || { echo "FATAL: scratch registry is empty" >&2; exit 1; }

out=$(env -u LA_LIVE_CONFIG_DIR LA_MOESPRESSO_BIN= LA_CONFIG_DIR="$SCRATCH/config" \
      "$SCRATCH/bin/local-llm-hotswap.sh" ds-coder 2>&1); rc=$?
if [ "$rc" -ne 0 ]; then
  ok "a foreign-format artifact makes hotswap exit non-zero (rc=$rc)"
else
  bad "a foreign-format artifact did not stop hotswap (rc=0)"
fi
if printf '%s' "$out" | grep -q 'SUCCESS_PORT='; then
  bad "hotswap printed SUCCESS_PORT for an artifact it cannot load"
else
  ok "no SUCCESS_PORT is printed for an unloadable artifact"
fi
if printf '%s' "$out" | grep -q 'Not launching ds-coder' \
   && printf '%s' "$out" | grep -q 'not readable by any installed backend'; then
  ok "the refusal explains that the WEIGHTS are the problem (not a vague 409)"
else
  bad "the refusal does not name the real problem: $out"
fi
# The runtime hint comes from config/model-catalog.local.psv, which is GITIGNORED machine state —
# absent in a fresh clone or worktree, where the hint is (correctly) omitted. Assert it only where
# the catalog is actually present.
if [ -f "$REPO/config/model-catalog.local.psv" ]; then
  if printf '%s' "$out" | grep -q 'MoEspresso'; then
    ok "the refusal names the runtime the catalog says the artifact needs"
  else
    bad "the refusal omits the catalog's runtime hint (MoEspresso>=2.1)"
  fi
else
  printf '  ~ skipped: no model-catalog.local.psv here (gitignored), so no runtime hint is expected\n'
fi

# 5. PLANTED POSITIVE — a negative result needs one. With the override set, the SAME artifact must
#    get PAST the gate (failing later, for the missing backend binary), proving the refusal above
#    came from the gate and not from some unrelated early exit.
out=$(env -u LA_LIVE_CONFIG_DIR LA_GATE_WEIGHT_FORMAT=0 LA_CONFIG_DIR="$SCRATCH/config" \
      "$SCRATCH/bin/local-llm-hotswap.sh" ds-coder 2>&1); rc=$?
if printf '%s' "$out" | grep -q 'not readable by any installed backend'; then
  bad "LA_GATE_WEIGHT_FORMAT=0 did not bypass the gate — the negative above may prove nothing"
elif printf '%s' "$out" | grep -q 'executable not found'; then
  ok "LA_GATE_WEIGHT_FORMAT=0 passes the gate and fails later (missing binary), as it should"
else
  bad "with the override, hotswap neither refused nor reached the backend: $out"
fi

# 6. A plain artifact must NOT be refused by this gate. Without this case a gate that refuses
#    EVERYTHING would look like a working gate, so the assertion demands the refusal be absent AND
#    a later stage actually be reached — otherwise "not refused" could just mean "nothing ran".
out=$(env -u LA_LIVE_CONFIG_DIR LA_MOESPRESSO_BIN= LA_CONFIG_DIR="$SCRATCH/config" \
      "$SCRATCH/bin/local-llm-hotswap.sh" plain-mlx 2>&1); rc=$?
if printf '%s' "$out" | grep -q 'not readable by any installed backend'; then
  bad "a plain MLX artifact was refused by the weight-format gate — the gate over-reaches"
elif printf '%s' "$out" | grep -q 'oMLX executable not found'; then
  ok "a plain MLX artifact passes the gate and reaches the backend (missing binary)"
else
  bad "a plain artifact neither reached the backend nor was gated: $out"
fi

# 7. The universal net: la_load_config must WARN for an unlaunchable artifact, covering the routes
#    that never reach hotswap (the auto-mode launchers, the prewarm gate, csl's offer). It must
#    warn without failing the load, or one bad artifact would break every listing tool.
net=$(env -u LA_LIVE_CONFIG_DIR LA_MOESPRESSO_BIN= LA_CONFIG_DIR="$SCRATCH/config" bash -c \
      '. "$1/config-lib.sh"; la_load_config' _ "$SCRATCH/config" 2>&1)
if printf '%s' "$net" | grep -q 'deepseek-v4-flash-0731-coder: weights are not MLX-readable'; then
  ok "la_load_config warns about the unlaunchable artifact (covers non-hotswap routes)"
else
  bad "la_load_config issued no warning for the unlaunchable artifact: $net"
fi
if printf '%s' "$net" | grep -q '409'; then
  ok "the warning names the cryptic 409 the user would otherwise see"
else
  bad "the warning does not explain the consequence: $net"
fi
if printf '%s' "$net" | grep -q 'plain-mlx'; then
  bad "the warning names a plain artifact too — it over-reaches"
else
  ok "a plain artifact draws no warning"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
