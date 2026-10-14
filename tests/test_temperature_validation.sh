#!/usr/bin/env bash
# test_temperature_validation.sh — RUN the --temperature validators with real values.
#
# 0.34.5: both validators wrapped sys.exit(0) in a bare `except:`, which catches SystemExit,
# so EVERY value — 1.0 included — was rejected and no local session with a temperature could
# launch. The older tests only grepped the source, so they stayed green. This one executes the
# exact validator each script carries, extracted from the script itself.
# Usage: bash tests/test_temperature_validation.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  PASS: $1"; }
bad() { fail=$((fail+1)); echo "  FAIL: $1"; }

for script in bin/launch-claude-agent.sh bin/local-llm-hotswap.sh; do
    # The validator is the python -c '...' body following `--temperature)` in each script.
    code="$(awk '/--temperature\)/{f=1} f&&/python3 -c '"'"'/{g=1;next} g&&/^'"'"' "\$2"; then/{exit} g' "$ROOT/$script")"
    [ -n "$code" ] || { bad "$script: validator not found"; continue; }
    for v in 0 0.0 0.6 1.0 1 2.0 2; do
        python3 -c "$code" "$v" && ok "$script accepts $v" || bad "$script REJECTS valid $v"
    done
    for v in -0.1 2.01 abc "" nan inf; do
        python3 -c "$code" "$v" 2>/dev/null && bad "$script accepts invalid '$v'" || ok "$script rejects '$v'"
    done
done

echo "=== Summary ==="; echo "Pass: $pass  Fail: $fail"
[ "$fail" -eq 0 ]
