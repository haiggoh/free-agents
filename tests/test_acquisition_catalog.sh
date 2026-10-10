#!/usr/bin/env bash
# Acquisition catalogues (0.29.0, promised in 0.16.0): the reader validates every row, the CLI aliases
# agree, and the remote local-capable filter uses an EXACT catalogue match as visible-only evidence,
# fail-open on a broken catalogue. Temp files only.   Usage: tests/test_acquisition_catalog.sh [--help]
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,4p "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; B="$ROOT/bin"
pass=0 fail=0; ok(){ pass=$((pass+1)); echo "  PASS: $1"; }; bad(){ fail=$((fail+1)); echo "  FAIL: $1"; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
H='# alias|label|repo|revision|subdir|size_GB|group|status|include_patterns|runtime'
printf '%s\nqwen3-coder-next-4bit|Coder|mlx-community/Qwen3-Coder-Next-4bit|main|Qwen3-Coder-Next-4bit|45.0|g|candidate||rapid-mlx\n' "$H" > "$T/good.psv"
printf '%s\nshort|x|org/m|main\nbad size|x|org/m|zzz|d|abc|g|c||rapid-mlx\n' "$H" > "$T/bad.psv"
# 1) the shipped catalogues are clean, through all three entry points
python3 "$B/acquisition_catalog.py" --report >/dev/null && ok "shipped catalogues validate" || bad "shipped catalogues have problems"
python3 "$B/read-acquisition-catalog.py" --report >/dev/null && ok "read-acquisition-catalog.py alias works" || bad "alias broken"
python3 "$B/la-catalogue-generate.py" acquisitions >/dev/null && ok "la-catalogue-generate.py acquisitions works" || bad "subcommand broken"
# 2) planted bad rows: non-zero exit, every problem named with file:line
out=$(python3 "$B/acquisition_catalog.py" --report "$T/bad.psv" 2>&1); rc=$?
[ $rc -eq 1 ] && ok "bad catalogue exits 1" || bad "bad catalogue exit $rc"
for want in "bad.psv:2: expected 10 fields, got 4" "bad.psv:3: invalid alias" "bad.psv:3: invalid revision" "bad.psv:3: size_GB not a positive number"; do
  case "$out" in *"$want"*) ok "reports '$want'" ;; *) bad "missing '$want'" ;; esac
done
python3 "$B/acquisition_catalog.py" --parse "$T/good.psv" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if len(d["rows"])==1 and not d["problems"] else 1)' \
  && ok "--parse emits JSON rows" || bad "--parse output wrong"
# 3) filter evidence: an UNCLASSIFIED id that matches exactly is marked, stays visible; a near miss is not
printf '# empty policy: no row classified\n' > "$T/policy.psv"
roster='coder|groq|qwen/qwen3-coder-next|Coder|unknown
near|groq|qwen/qwen3-coder|Near|unknown'
j=$(printf '%s\n' "$roster" | LA_ACQUISITION_CATALOGS="$T/good.psv" bash "$B/local-capable-filter.sh" --parse "$T/policy.psv" --roster -)
chk() { printf '%s' "$j" | python3 -c "import json,sys; r={x['alias']:x for x in json.load(sys.stdin)['rows']}; sys.exit(0 if $1 else 1)"; }
chk "r['coder']['classification']=='potentially-local-capable' and r['coder']['visible'] and '45.0 GB' in r['coder']['reason']" \
  && ok "exact match marked potentially-local-capable (visible, size in reason)" || bad "exact match not marked: $j"
chk "r['near']['classification']==''" && ok "near miss stays unclassified (no fuzzy match)" || bad "near miss matched"
# 4) fail-open: a malformed or missing catalogue changes nothing
j=$(printf '%s\n' "$roster" | LA_ACQUISITION_CATALOGS="$T/bad.psv:$T/none.psv" bash "$B/local-capable-filter.sh" --parse "$T/policy.psv" --roster -)
chk "all(x['visible'] for x in r.values()) and r['coder']['classification']==''" && ok "broken catalogue: roster unchanged, nothing hidden" || bad "broken catalogue changed the roster: $j"
j=$(printf '%s\n' "$roster" | LA_LC_ACQUISITION_EVIDENCE=0 LA_ACQUISITION_CATALOGS="$T/good.psv" bash "$B/local-capable-filter.sh" --parse "$T/policy.psv" --roster -)
chk "r['coder']['classification']==''" && ok "LA_LC_ACQUISITION_EVIDENCE=0 disables it" || bad "opt-out ignored"
# 5) load-time gate: la-model-profile.py validate refuses a malformed catalogue it consumes (sandboxed copy)
S="$T/repo"; mkdir -p "$S"; cp -R "$ROOT/bin" "$ROOT/config" "$S/"
python3 "$S/bin/la-model-profile.py" validate >/dev/null 2>&1 && ok "validate passes on the shipped catalogues" || bad "validate fails on shipped catalogues"
echo "broken row|x" >> "$S/config/model-catalog.acquisitions.omlx.psv"
out=$(python3 "$S/bin/la-model-profile.py" validate 2>&1); rc=$?
[ $rc -ne 0 ] && ok "validate exits non-zero on a malformed acquisition row" || bad "validate ignored the bad row"
case "$out" in *"acquisitions.omlx.psv:"*"missing repo"*) ok "the problem names file:line" ;; *) bad "no file:line in: $out" ;; esac
echo "acquisition catalogue: $pass passed, $fail failed"; [ "$fail" -eq 0 ]
