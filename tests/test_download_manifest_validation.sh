#!/usr/bin/env bash
# install/download-models.sh writes a portable manifest ONLY if it validates: build to a temp file,
# `local-model-manifest.py validate`, then mv into place; a failure leaves NO manifest, removes the
# completion marker and exits non-zero. Driven end to end with a stub `hf` (writes a 2 MB fake
# weight) and, for the failure case, a manifest tool shim whose `build` plants an invalid manifest.
# No network, temp dirs only.   Usage: tests/test_download_manifest_validation.sh [--help]
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,6p "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/root" "$SB/stub" "$SB/models"
cp -R "$REPO/install" "$REPO/bin" "$REPO/config" "$SB/root/"
cat > "$SB/catalog.psv" <<'EOF'
dl-ok|OK model|org/dl-ok|main|dl-ok|0.01|test|candidate||rapid-mlx
EOF
cat > "$SB/stub/hf" <<'EOF'
#!/usr/bin/env bash
# fake hf: `hf download REPO --revision R --local-dir D ...` -> a config and a 2 MB "weight"
dest=""; while [ $# -gt 0 ]; do [ "$1" = --local-dir ] && dest="$2"; shift; done
[ -n "$dest" ] || exit 0
mkdir -p "$dest"
echo '{"text_config":{"max_position_embeddings":262144},"architectures":["Qwen3ForCausalLM"]}' > "$dest/config.json"
dd if=/dev/zero of="$dest/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
EOF
chmod +x "$SB/stub/hf"
run() { PATH="$SB/stub:$PATH" LA_ROOT="$SB/root" LA_MODELS_DIR="$SB/models" HOME="$SB" \
        bash "$SB/root/install/download-models.sh" --catalog "$SB/catalog.psv" --target "$SB/models" \
        --headroom-gb 0 --select dl-ok 2>&1; }
M="$SB/models/dl-ok/.local-model-manifest.json"

echo "=== 1: a valid build is validated and moved into place ==="
out=$(run); rc=$?
check $(( rc == 0 ? 0 : 1 )) "download succeeds (exit $rc)"
[ -f "$M" ] && check 0 "manifest written" || check 1 "manifest written: $out"
python3 "$SB/root/install/local-model-manifest.py" validate "$M" >/dev/null 2>&1; check $? "written manifest validates"
case "$out" in *"written and validated"*) check 0 "reports validated write" ;; *) check 1 "no validated-write line" ;; esac
ls "$SB/models/dl-ok" | grep -q "manifest.*tmp\|\.XXXX" && check 1 "temp file left behind" || check 0 "no temp file left behind"

echo "=== 2: an invalid build leaves NO manifest and fails the acquisition ==="
rm -rf "$SB/models/dl-ok"
mv "$SB/root/install/local-model-manifest.py" "$SB/root/install/local-model-manifest.real.py"
cat > "$SB/root/install/local-model-manifest.py" <<'EOF'
#!/usr/bin/env python3
# shim: `build` plants a manifest whose autocompaction exceeds its context; everything else is real
import json, os, sys, subprocess
real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local-model-manifest.real.py")
if len(sys.argv) > 1 and sys.argv[1] == "build":
    out = sys.argv[sys.argv.index("--output") + 1]
    subprocess.run([sys.executable, real] + sys.argv[1:], check=True, capture_output=True)
    d = json.load(open(out)); d["capabilities"]["claude_autocompact_tokens"] = 900000
    json.dump(d, open(out, "w")); sys.exit(0)
os.execv(sys.executable, [sys.executable, real] + sys.argv[1:])
EOF
chmod +x "$SB/root/install/local-model-manifest.py"
out=$(run); rc=$?
check $(( rc != 0 ? 0 : 1 )) "acquisition fails on an invalid manifest (exit $rc)"
[ -f "$M" ] && check 1 "invalid manifest was written" || check 0 "no manifest written"
[ -f "$SB/models/dl-ok/.la-download-complete" ] && check 1 "completion marker kept" || check 0 "completion marker removed"
case "$out" in *"acquisition incomplete"*) check 0 "reports acquisition incomplete" ;; *) check 1 "no 'acquisition incomplete': $out" ;; esac

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
