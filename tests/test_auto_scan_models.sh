#!/usr/bin/env bash
# tests/test_auto_scan_models.sh — la_auto_scan_models (config/config-lib.sh) must keep MTP models.
#
# MTP (Multi-Token Prediction) builds such as Qwen3.8-27B-MTP-4bit are what let Qwen 3.8 launch with
# speculative decoding, which is markedly faster than without. An earlier exclusion list dropped every
# folder with "mtp" in its name, treating it like a TTS/vision/drafter leftover. Builds entirely in a
# temp dir: fake model folders, no real models, no network.
set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
M="$TMP/models"
for d in Qwen3.8-27B-4bit Qwen3.8-27B-MTP-4bit kokoro-82m flux-schnell Qwen3.6-27B-drafter speculative-head mmproj-clip; do
    mkdir -p "$M/$d"; echo '{}' > "$M/$d/config.json"
done

OUT="$( set +u; . "$REPO/config/config-lib.sh" >/dev/null 2>&1; LA_MODELS_DIR="$M" la_auto_scan_models | sort )"
has() { printf '%s\n' "$OUT" | grep -qxF "$1"; }

has Qwen3.8-27B-4bit;      check $? "plain text model is listed"
has Qwen3.8-27B-MTP-4bit;  check $? "MTP model is listed (needed for fast Qwen 3.8 launches)"
has kokoro-82m;            r=$?; check $((r==0?1:0)) "TTS model is still excluded"
has flux-schnell;          r=$?; check $((r==0?1:0)) "image model is still excluded"
has Qwen3.6-27B-drafter;   r=$?; check $((r==0?1:0)) "drafter is still excluded"
has speculative-head;      r=$?; check $((r==0?1:0)) "speculative head is still excluded"
has mmproj-clip;           r=$?; check $((r==0?1:0)) "vision projector is still excluded"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
