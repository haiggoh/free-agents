#!/usr/bin/env bash
# tests/test_hardware_target.sh — hardware_target catalog filtering and the hardware branches of
# install/install-backend.sh.
#
# No GPU, no network, no downloads: CUDA hardware is faked with a stub `nvidia-smi` placed first on
# PATH (bin/la-hw-detect.sh decides from what is on PATH and ignores a preset LA_HARDWARE), and every
# downloader call is `--list`, which only prints. Cases that need real Apple Silicon are skipped
# elsewhere and say so.
#
# Two defects this pins (both found when stash@{0} of 2026-10-06 was audited):
#   * `--hf-repo` rows were tagged `dynamic|cuda`, so on a Mac the row the user explicitly asked for
#     was filtered out of its own `--list`.
#   * install-backend.sh switched on `mac)`, a value la-hw-detect.sh never emits (it emits `mlx`),
#     so the macOS prerequisite check could never run.

set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
DL="$REPO/install/download-models.sh"
IB="$REPO/install/install-backend.sh"
HW="$REPO/bin/la-hw-detect.sh"

PASS=0; FAIL=0; SKIP=0
check() { # check <0|1> <label>
    if [ "$1" -eq 0 ]; then PASS=$((PASS + 1)); printf 'PASS  %s\n' "$2"
    else FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$2"; fi
}
skip() { SKIP=$((SKIP + 1)); printf 'SKIP  %s\n' "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/stub"
cat > "$TMP/stub/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
# Fake nvidia-smi: a 24 GiB card. Answers only the queries la-hw-detect.sh makes.
case "$*" in
    *name*)           echo "Fake RTX 4090" ;;
    *driver_version*) echo "999.99" ;;
    *memory.total*)   echo "24576" ;;
    *)                echo "Fake RTX 4090" ;;
esac
STUB
chmod +x "$TMP/stub/nvidia-smi"

# detect [stub|native] — prints "LA_HARDWARE LA_VRAM_GB" as la-hw-detect.sh decides it.
detect() {
    local use_path="$PATH"; [ "$1" = stub ] && use_path="$TMP/stub:$PATH"
    # The PATH change is deliberate and confined to this subshell; the detector reads what is on PATH.
    # shellcheck disable=SC2030,SC1090
    ( PATH="$use_path"; set +u; . "$HW" >/dev/null 2>&1; echo "${LA_HARDWARE:-} ${LA_VRAM_GB:-}" )
}
# dl_list [stub|native] <download-models.sh args...> — the --list output under that hardware.
dl_list() {
    local use_path="$PATH"; [ "$1" = stub ] && use_path="$TMP/stub:$PATH"; shift
    # shellcheck disable=SC2031
    ( cd "$TMP" && PATH="$use_path" HOME="$TMP/home" bash "$DL" "$@" --list 2>&1 )
}
mkdir -p "$TMP/home"

NATIVE="$(detect native)"; STUBBED="$(detect stub)"

echo "== hardware detection =="
case "$STUBBED" in "cuda 24") check 0 "stub nvidia-smi => LA_HARDWARE=cuda, 24 GB";;
                   *)         check 1 "stub nvidia-smi => LA_HARDWARE=cuda, 24 GB (got: $STUBBED)";; esac
HAVE_MLX=0; case "$NATIVE" in mlx*) HAVE_MLX=1;; esac

echo "== catalog filtering by hardware_target =="
OUT_CUDA="$(dl_list stub)"
printf '%s\n' "$OUT_CUDA" | grep -q 'qwen2.5-7b-awq' ; check $? "cuda host lists the cuda catalog model (qwen2.5-7b-awq)"
printf '%s\n' "$OUT_CUDA" | grep -q 'qwen36-27b-4bit'; r=$?; check $((r == 0 ? 1 : 0)) "cuda host hides the mlx-only catalog model (qwen36-27b-4bit)"

if [ "$HAVE_MLX" -eq 1 ]; then
    OUT_MLX="$(dl_list native)"
    printf '%s\n' "$OUT_MLX" | grep -q 'qwen36-27b-4bit'; check $? "mlx host lists the mlx catalog model (qwen36-27b-4bit)"
    printf '%s\n' "$OUT_MLX" | grep -q 'qwen2.5-7b-awq'; r=$?; check $((r == 0 ? 1 : 0)) "mlx host hides the cuda catalog model (qwen2.5-7b-awq)"
else
    skip "mlx-host catalog cases (this machine is not Apple Silicon: detected '$NATIVE')"
fi

echo "== --hf-repo rows are never hidden by the hardware filter =="
# The user asked for this repo by name. Discriminant: with the old `dynamic|cuda` tag the mlx case
# fails (row missing); with `dynamic|any` both pass.
HF=(--hf-repo Qwen/Qwen2.5-7B-Instruct-AWQ --alias tt-dyn-awq --include '*.json')
# Capture first: `cmd | grep -q` under pipefail reports the writer's SIGPIPE as a failure.
OUT_DYN_CUDA="$(dl_list stub "${HF[@]}")"
printf '%s\n' "$OUT_DYN_CUDA" | grep -q 'tt-dyn-awq.*status=dynamic\|^  [0-9]*) .*tt-dyn-awq'; check $? "cuda host lists the dynamic --hf-repo row"
if [ "$HAVE_MLX" -eq 1 ]; then
    OUT_DYN_MLX="$(dl_list native "${HF[@]}")"
    printf '%s\n' "$OUT_DYN_MLX" | grep -q 'tt-dyn-awq.*status=dynamic\|^  [0-9]*) .*tt-dyn-awq'; check $? "mlx host ALSO lists the dynamic --hf-repo row"
else
    skip "mlx-host dynamic-row case (not Apple Silicon)"
fi

echo "== install-backend.sh branches on values la-hw-detect.sh can emit =="
EMITTED="mlx cuda rocm opencl cpu-only"
LABELS="$(awk '/case "\$LA_HARDWARE" in/{f=1;next} /^[[:space:]]*esac/{f=0} f && match($0,/^[[:space:]]*[a-z-]+\)/){s=substr($0,RSTART,RLENGTH); gsub(/[[:space:]()]/,"",s); print s}' "$IB" | sort -u)"
BAD=""
for l in $LABELS; do case " $EMITTED " in *" $l "*) ;; *) BAD="$BAD $l";; esac; done
LABEL_LIST="$(printf '%s ' $LABELS)"
if [ -n "$LABELS" ]; then check 0 "found case labels on \$LA_HARDWARE (${LABEL_LIST% })"
else check 1 "found at least one case label on \$LA_HARDWARE"; fi
if [ -z "$BAD" ]; then check 0 "every case label is a value the detector emits"
else check 1 "every case label is a value the detector emits (unknown:$BAD)"; fi
grep -qE '^[[:space:]]*(#.*)?LA_HARDWARE' "$HW"; check $? "detector still documents LA_HARDWARE values"

if [ "$HAVE_MLX" -eq 1 ]; then
    # install-backend.sh runs `chmod +x` on its manager scripts even under --dry-run, so run a scratch
    # copy: the test must not change file modes in the checkout it is checking.
    mkdir -p "$TMP/repo-copy"; cp -R "$REPO/install" "$REPO/bin" "$REPO/config" "$TMP/repo-copy/"
    OUT_IB="$(cd "$TMP" && HOME="$TMP/home" bash "$TMP/repo-copy/install/install-backend.sh" --dry-run 2>&1)"; rc=$?
    [ "$rc" -eq 0 ]; check $? "install-backend.sh --dry-run exits 0 on Apple Silicon (rc=$rc)"
    printf '%s\n' "$OUT_IB" | grep -q 'Hardware: mlx'; check $? "install-backend.sh reports Hardware: mlx"
else
    skip "install-backend.sh --dry-run on Apple Silicon (not Apple Silicon)"
fi

printf '\n%d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
