#!/usr/bin/env bash
# tests/test_registry_hygiene.sh — picker-offered registry hygiene commands
#
# The picker lists on-disk models csl's registry does not know (auto-scan) and hides registered
# models whose weights are gone. Two csl commands close the loop:
#   csl --register-model <folder>   append a managed la_register block to config.local.sh
#   csl --unregister-model <alias>… move la_register + bound la_role lines into a tombstone
#                                   (config/removed-models.local.psv), with a timestamped backup
# And csl --inventory reports registered-but-missing models as "#missing<TAB>alias<TAB>subdir"
# so the picker can offer the cleanup.
# Builds entirely in a temp dir: fake model folders, no real models, no network, no writes
# outside $TMP.
set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
MODELS_DIR="$TMP/models"
mkdir -p "$MODELS_DIR/registered-model" "$MODELS_DIR/auto-dream-7b"
cat > "$MODELS_DIR/registered-model/config.json" <<'EOF'
{"architectures": ["Qwen3ForCausalLM"]}
EOF
dd if=/dev/zero of="$MODELS_DIR/registered-model/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
cat > "$MODELS_DIR/auto-dream-7b/config.json" <<'EOF'
{"architectures": ["LlamaForCausalLM"], "model_type": "llama"}
EOF
dd if=/dev/zero of="$MODELS_DIR/auto-dream-7b/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
# Backend detection (bin/la-detect-backend.py): stub ENGINE interpreters, never the real venvs.
# rapid knows llama; oMLX additionally knows laguna. A laguna folder must register as omlx, an
# unknown architecture must be refused, and a GGUF folder must register as llama_cpp.
mkdir -p "$MODELS_DIR/laguna-only" "$MODELS_DIR/alien-arch" "$MODELS_DIR/q-gguf" "$TMP/stub"
echo '{"model_type": "laguna"}' > "$MODELS_DIR/laguna-only/config.json"
echo '{"model_type": "alien_arch"}' > "$MODELS_DIR/alien-arch/config.json"
echo '{"model_type": "llama"}' > "$MODELS_DIR/q-gguf/config.json"
for d in laguna-only alien-arch; do
  dd if=/dev/zero of="$MODELS_DIR/$d/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
done
dd if=/dev/zero of="$MODELS_DIR/q-gguf/model-Q4_K_M.gguf" bs=1 count=0 seek=2097152 2>/dev/null
printf '#!/bin/sh\necho %s\n' "'{\"mlx_lm\": [\"llama\", \"qwen3\"]}'" > "$TMP/stub/rapid-py"
printf '#!/bin/sh\necho %s\n' "'{\"mlx_lm\": [\"llama\", \"qwen3\", \"laguna\"]}'" > "$TMP/stub/omlx-py"
chmod +x "$TMP/stub/rapid-py" "$TMP/stub/omlx-py"
export LA_DETECT_RAPID_PY="$TMP/stub/rapid-py" LA_DETECT_OMLX_PY="$TMP/stub/omlx-py"

# registered-model: on disk. gone-model: registered but its weights were deleted (the
# deepseek-r1-architect case) — it also carries la_role bindings that must move with it.
# Unquoted heredoc: $MODELS_DIR must expand INTO the file (test_inventory_auto_scan.sh:49 idiom).
cat > "$TMP/config.local.sh" <<EOF
LA_MODELS_DIR="$MODELS_DIR"
la_register registered-model registered-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
la_register gone-model gone-model-v2-dir rapid qwen "" false "" high "" "" "" ""
la_role reasoner gone-model high dispatch
la_role validator gone-model max dispatch
EOF
export LA_CONFIG_DIR="$TMP"
TAB="$(printf '\t')"

lookup_ok() { # lookup_ok <alias> -> 0 if la_load_config + la_lookup accept the alias
  ( set +u; . "$REPO/config/config-lib.sh" >/dev/null 2>&1
    la_load_config >/dev/null 2>&1; la_lookup "$1" >/dev/null 2>&1 )
}

# --- inventory reports registered-but-missing ----------------------------------------------
inv=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --inventory 2>&1)
printf '%s\n' "$inv" | grep -q "^registered-model${TAB}"
check $? "inventory still lists the on-disk registered model"
printf '%s\n' "$inv" | grep -q "^#missing${TAB}gone-model${TAB}gone-model-v2-dir$"
check $? "inventory emits #missing<TAB>alias<TAB>subdir for the deleted model"
printf '%s\n' "$inv" | grep -q "^gone-model${TAB}"
r=$?
check $(( r == 0 ? 1 : 0 )) "inventory does not list the missing model as launchable"

# --- register-model: managed block, valid line, idempotent ---------------------------------
lines_before=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model auto-dream-7b >/dev/null 2>&1
check $? "csl --register-model exits zero"
grep -q '^la_register auto-dream-7b auto-dream-7b mlx auto ' "$TMP/config.local.sh"
check $? "managed la_register line appended"
lookup_ok auto-dream-7b
check $? "appended line is a valid registration (la_lookup finds it)"
lines_mid=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model auto-dream-7b >/dev/null 2>&1
lines_after=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
check $(( lines_after == lines_mid ? 0 : 1 )) "second register call is idempotent"
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model registered-model >/dev/null 2>&1
check $? "registering an already-registered folder is a no-op success"
lines_final=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
check $(( lines_final == lines_after ? 0 : 1 )) "no-op register did not write"

# --- register-model picks the backend from evidence (user 2026-10-15) ---------------------------
out=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model laguna-only 2>&1)
check $? "registering an oMLX-only architecture succeeds"
grep -q '^la_register laguna-only laguna-only omlx ' "$TMP/config.local.sh"
check $? "oMLX-only architecture is registered serve=omlx"
printf '%s' "$out" | grep -q 'omlx'
check $? "the register message names the detected backend"
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model q-gguf >/dev/null 2>&1
grep -q '^la_register q-gguf q-gguf llama_cpp ' "$TMP/config.local.sh"
check $? "GGUF folder is registered serve=llama_cpp"
lines_pre_alien=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
out=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model alien-arch 2>&1); r=$?
check $(( r != 0 ? 0 : 1 )) "an architecture no backend supports is refused (non-zero exit)"
printf '%s' "$out" | grep -q 'alien_arch'
check $? "the refusal names the unsupported model_type"
check $(( $(wc -l < "$TMP/config.local.sh" | tr -d ' ') == lines_pre_alien ? 0 : 1 )) "a refused registration writes nothing"
inv=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --inventory 2>&1)
printf '%s\n' "$inv" | grep -q "^laguna-only${TAB}omlx"
check $? "the registered oMLX model is listed as launchable on omlx"
# Leave the later unregister arithmetic exactly as written: remove what this block added.
for a in laguna-only q-gguf; do
  awk -v a="$a" '!($1 == "la_register" && $2 == a)' "$TMP/config.local.sh" > "$TMP/c.tmp" && cat "$TMP/c.tmp" > "$TMP/config.local.sh"
done

# --- unregister-model: lines move to the tombstone, lookup fails, backup kept ---------------
lines_before_unreg=$(wc -l < "$TMP/config.local.sh" | tr -d ' ')
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --unregister-model gone-model >/dev/null 2>&1
check $? "csl --unregister-model exits zero"
grep -q '^la_register gone-model ' "$TMP/config.local.sh"
r=$?
check $(( r == 0 ? 1 : 0 )) "la_register line removed from config.local.sh"
grep -q 'la_role .* gone-model' "$TMP/config.local.sh"
r=$?
check $(( r == 0 ? 1 : 0 )) "bound la_role lines removed too"
lookup_ok gone-model
r=$?
check $(( r == 0 ? 1 : 0 )) "la_lookup no longer resolves the alias"
if [ -f "$TMP/removed-models.local.psv" ]; then
  grep -q "gone-model|gone-model-v2-dir" "$TMP/removed-models.local.psv"
  check $? "tombstone row written with alias and subdir"
else
  check 1 "tombstone row written with alias and subdir"
fi
ls "$TMP"/config.local.sh.bak-unregister-* >/dev/null 2>&1
check $? "timestamped backup of config.local.sh kept"
registered_rows=$(grep -c '^la_register registered-model ' "$TMP/config.local.sh")
check $(( registered_rows == 1 ? 0 : 1 )) "other registrations untouched"
tomb_rows=$(grep -c '|' "$TMP/removed-models.local.psv" 2>/dev/null)
tomb_rows=${tomb_rows:-0}
LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --unregister-model never-registered >/dev/null 2>&1
r=$?
tomb_rows2=$(grep -c '|' "$TMP/removed-models.local.psv" 2>/dev/null)
tomb_rows2=${tomb_rows2:-0}
if [ "$r" -ne 0 ] && [ "$tomb_rows2" = "$tomb_rows" ]; then
  check 0 "unknown alias fails without touching the tombstone"
else
  check 1 "unknown alias fails without touching the tombstone"
fi
check $(( $(wc -l < "$TMP/config.local.sh" | tr -d ' ') == lines_before_unreg - 3 ? 0 : 1 )) "config shrank by exactly the three moved lines"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
