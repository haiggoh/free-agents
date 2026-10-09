#!/usr/bin/env bash
# Free sessions keep Agent + Workflow (0.27.0): the default LA_DENY_TOOLS must not withhold them,
# and the local launcher must cap parallel subagents to the server's slots. Pure config + source
# inspection with a sandboxed HOME; starts no server and no session.
#   tests/test_free_session_subagents.sh [--help]
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,5p' "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0 fail=0
ok() { pass=$((pass+1)); echo "  PASS: $1"; }
bad() { fail=$((fail+1)); echo "  FAIL: $1"; }
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
deny=$(env -i HOME="$SB" PATH="$PATH" bash -c 'source "$1/config/config-lib.sh" >/dev/null 2>&1; la_apply_defaults >/dev/null 2>&1 || true; printf %s "${LA_DENY_TOOLS-UNSET}"' _ "$ROOT")
[ "$deny" = UNSET ] && deny=$(sed -n 's/.*: "\${LA_DENY_TOOLS:=\(.*\)}"/\1/p' "$ROOT/config/config-lib.sh")
case ",$deny," in *,Agent,*|*,Workflow,*) bad "default LA_DENY_TOOLS still withholds Agent/Workflow: $deny" ;; *) ok "default LA_DENY_TOOLS keeps Agent + Workflow" ;; esac
case ",$deny," in *,DesignSync,*) ok "the lean-prompt denials remain (DesignSync etc.)" ;; *) bad "lean-prompt denials lost: $deny" ;; esac
ex=$(sed -n 's/^LA_DENY_TOOLS="\(.*\)".*/\1/p' "$ROOT/config/config.example.sh")
case ",$ex," in *,Agent,*|*,Workflow,*) bad "config.example.sh still withholds Agent/Workflow" ;; *) ok "config.example.sh matches the new default" ;; esac
L="$ROOT/bin/launch-claude-agent.sh"
grep -q 'export CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS="${LA_SUBAGENT_MAX_CONCURRENT:-${LA_RAPID_MAX_NUM_SEQS:-2}}"' "$L" \
  && ok "local launcher caps parallel subagents to the server slots" || bad "subagent cap export missing"
spoof_line=$(grep -n '^    MODEL_SPOOF="\$DISPATCH_MODEL"' "$L" | cut -d: -f1)
cap_line=$(grep -n 'export CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS' "$L" | cut -d: -f1)
[ -n "$spoof_line" ] && [ -n "$cap_line" ] && [ "$cap_line" -gt "$spoof_line" ] \
  && ok "subagent model export comes after the served spoof id is known" || bad "export order wrong"
grep -q 'INTERCEPT_AGENTS' "$L" "$ROOT/bin/csl" "$ROOT/bin/remote-session.sh" \
  && bad "a free-session launcher still reads INTERCEPT_AGENTS" || ok "no free-session launcher reads INTERCEPT_AGENTS"
echo "free-session subagents: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
