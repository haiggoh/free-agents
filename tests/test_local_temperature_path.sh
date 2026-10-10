#!/usr/bin/env bash
# End-to-end: LA_TEMPERATURE from the picker env reaches local-llm-hotswap.sh's Rapid command and
# the server-reuse identity, through csl --picker-launch -> launch-claude-agent.sh. Stubs only:
# hotswap is replaced by a recorder, nothing is served.   Usage: tests/test_local_temperature_path.sh [--help]
set -euo pipefail
case "${1:-}" in -h|--help) sed -n 2,4p "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0 fail=0; ok(){ pass=$((pass+1)); echo "  PASS: $1"; }; bad(){ fail=$((fail+1)); echo "  FAIL: $1"; }
# 1) the hotswap Rapid command and meta carry the env value (static: the command is built in one place)
H="$ROOT/bin/local-llm-hotswap.sh"
grep -q 'LA_TEMPERATURE="${LA_TEMPERATURE:-}"' "$H" && ok "hotswap reads LA_TEMPERATURE from the environment" || bad "hotswap ignores env"
grep -q 'RAPID_CMD+=(--default-temperature "$LA_TEMPERATURE")' "$H" && ok "Rapid gets --default-temperature" || bad "no serve flag"
grep -q '\[ "$_meta_temp" = "${LA_TEMPERATURE:-}" \]' "$H" && ok "reuse requires the same temperature" || bad "reuse ignores temperature"
grep -q 'echo "temperature=$LA_TEMPERATURE"' "$H" && ok "meta records the temperature" || bad "meta lacks temperature"
# 1b) spelling-insensitive: 0.600 and 0.6 are one temperature (normalised before reuse/meta)
n=$(LA_TEMPERATURE=0.600 bash -c 'LA_TEMPERATURE="${LA_TEMPERATURE:-}"; '"$(sed -n '/^# Canonical form/,/^fi$/p' "$H")"'; printf %s "$LA_TEMPERATURE"')
[ "$n" = "0.6" ] && ok "0.600 normalises to 0.6" || bad "normalisation gave '$n'"
# 2) the env survives csl --picker-launch -> launcher (launcher is stubbed to dump its env)
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/bin"; cp "$ROOT/bin/csl" "$SB/bin/"; cp -R "$ROOT/config" "$SB/config"
cat > "$SB/bin/launch-claude-agent.sh" <<'EOF'
#!/usr/bin/env bash
printf 'argv=%s temp=%s\n' "$*" "${LA_TEMPERATURE:-unset}" > "$CSL_TEST_OUT"
EOF
chmod +x "$SB/bin/launch-claude-agent.sh"
out="$SB/out"
CSL_TEST_OUT="$out" LA_TEMPERATURE=0.3 HOME="$SB" CSL_LAUNCHER="$SB/bin/launch-claude-agent.sh" \
  bash "$SB/bin/csl" --picker-launch qwen-3.8-operator high >/dev/null 2>&1 || true
if [ -f "$out" ]; then
  grep -q 'temp=0.3' "$out" && ok "LA_TEMPERATURE survives csl --picker-launch" || bad "lost: $(cat "$out")"
  grep -q '^argv=qwen-3.8-operator high temp=' "$out" && ok "effort argument untouched" || bad "argv changed: $(cat "$out")"
else
  bad "csl did not reach the launcher stub (check CSL_LAUNCHER support)"
fi
echo "local temperature path: $pass passed, $fail failed"; [ "$fail" -eq 0 ]
