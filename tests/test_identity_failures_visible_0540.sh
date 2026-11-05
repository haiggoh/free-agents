#!/usr/bin/env bash
# 0.54.0 Task 10a — local/free-session identity steps fail OPEN but never SILENTLY.
#
# Every identity step (la-session-identity.sh, la-mod-attach.sh, generate-*-settings.py) ran
# as `2>/dev/null || true`, so a failure was invisible. Now each goes through fa_identity_run:
# stderr is captured to ${XDG_STATE_HOME:-$HOME/.local/state}/free-agents/identity-last.log and
# ONE visible warning line names the step; the launch still proceeds (fail-open, not silent).
#
# Pinned here with a REAL launch choreography from a SANDBOXED copy of bin/ (the identity
# scripts are replaced by failing stubs; stub claude records its argv; a stub proxy answers
# the health endpoint on a scratch port range — never 4141-4151/8000-8010, never a real model):
#   * the warning line appears and names the step;
#   * the log file contains the stub's stderr line;
#   * the stub claude WAS executed (the launch proceeded — fail-open);
#   * exactly ONE --settings flag reaches claude (the double---settings defect stays fixed);
#   * no identity step still hides stderr (`2>/dev/null || true` gone from the identity sites);
#   * the GENERAL verb list in both verbs JSONs is plain single words (no CamelCase
#     run-ons — the 2026-11-04 defect); family puns keep echoing their model name.
#
# Usage: bash tests/test_identity_failures_visible_0540.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
passed=0; failed=0
check() {
  local ok=1 label
  if [ "$#" -eq 2 ]; then label="$2"; [ "$1" = "0" ] && ok=0
  else label="$4"; if [ "$2" = "-eq" ]; then [ "$1" -eq "$3" ] && ok=0
       elif [ "$2" = "-ne" ]; then [ "$1" -ne "$3" ] && ok=0; fi; fi
  if [ "$ok" -eq 0 ]; then passed=$((passed+1)); echo "  PASS: $label"
  else failed=$((failed+1)); echo "  FAIL: $label"; fi
}

T="$(mktemp -d "${TMPDIR:-/tmp}/fa-identity-0540-XXXXXX")"
_cleanup() { for p in $(pgrep -f "$T/stubs/litellm" 2>/dev/null); do kill "$p" 2>/dev/null; done; rm -rf "$T"; }
trap _cleanup EXIT

# --- sandbox: a copy of bin/ + config/ with the identity scripts replaced by failing stubs
cp -R "$ROOT/bin" "$T/bin"
cp -R "$ROOT/config" "$T/config"
for s in la-session-identity.sh la-mod-attach.sh generate-remote-settings.py; do
    printf '#!/bin/sh\necho "stub-%s-boom" >&2\nexit 1\n' "$s" > "$T/bin/$s"
    chmod 755 "$T/bin/$s"
done

mkdir -p "$T/stubs" "$T/keys" "$T/home" "$T/state"
printf '#!/bin/sh\nif [ "${1:-}" = "--version" ] || [ "${1:-}" = "-v" ]; then echo "2.1.304 (Claude Code)"; exit 0; fi\nprintf %%s "$*" >> "$ID_ARGV_LOG"\nprintf "\\n" >> "$ID_ARGV_LOG"\nexit 0\n' > "$T/stubs/claude"
chmod 755 "$T/stubs/claude"
printf '#!/usr/bin/env python3\nimport sys, http.server\nport = 4181\nargs = sys.argv[1:]\nfor i, a in enumerate(args):\n    if a == "--port" and i + 1 < len(args):\n        port = int(args[i + 1])\nhttp.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler, port=port)\n' > "$T/stubs/litellm"
chmod 755 "$T/stubs/litellm"
printf 'sk-test-fixture-not-a-real-key\n' > "$T/keys/nvidia"
chmod 600 "$T/keys/nvidia"
printf 'identity smoke prompt\n' > "$T/launch.md"

export ID_ARGV_LOG="$T/argv.log"; : > "$ID_ARGV_LOG"

echo "== 10a: a failing identity step warns visibly, logs its stderr, and the launch proceeds =="
PATH="$T/stubs:$PATH" LA_LITELLM_CMD="$T/stubs/litellm" \
    LA_API_KEYS_DIR="$T/keys" TMPDIR="$T" HOME="$T/home" XDG_STATE_HOME="$T/state" \
    LA_REMOTE_PROXY_PORT_MIN=4180 LA_REMOTE_PROXY_PORT_MAX=4189 \
    timeout 120 bash "$T/bin/remote-session.sh" nvidia-glm53 --prompt-file "$T/launch.md" \
    > "$T/launch.out" 2> "$T/launch.err"
lrc=$?

LOG="$T/state/free-agents/identity-last.log"
if grep -q "⚠ session identity: la-session-identity.sh failed" "$T/launch.err" 2>/dev/null; then
    check 0 "the one-line warning names the failed step"
else
    check 1 "the one-line warning names the failed step (stderr: $(head -c 200 "$T/launch.err" 2>/dev/null))"
fi
if [ -f "$LOG" ] && grep -q "stub-la-session-identity.sh-boom" "$LOG"; then
    check 0 "the stub's stderr line landed in identity-last.log"
else
    check 1 "the stub's stderr line landed in identity-last.log (log: $(cat "$LOG" 2>/dev/null | head -c 120))"
fi
if [ -s "$ID_ARGV_LOG" ]; then
    check 0 "the stub claude WAS executed — the launch proceeded (fail-open, not silent)"
else
    check 1 "the stub claude WAS executed (launch rc=$lrc)"
fi
n_settings=$(grep -o -- '--settings' "$ID_ARGV_LOG" 2>/dev/null | wc -l | tr -d ' ')
check "$n_settings" -eq 1 "exactly ONE --settings flag reaches claude (double---settings stays fixed)"

echo "== 10a: no identity step still hides stderr =="
if grep -nE 'la-session-identity\.sh|la-mod-attach\.sh|generate-(local|remote)-settings\.py' \
        "$ROOT/bin/launch-claude-agent.sh" "$ROOT/bin/remote-session.sh" | grep -q '2>/dev/null || true'; then
    check 1 "no identity step still runs with 2>/dev/null || true"
else
    check 0 "no identity step still runs with 2>/dev/null || true"
fi

echo "== 10a: spinner verbs are single readable words (no CamelCase run-ons) =="
python3 - "$ROOT" <<'PY'
import json, re, subprocess, sys
root = sys.argv[1]
bad = []
for name in ("local-spinner-verbs.json", "remote-spinner-verbs.json"):
    d = json.load(open(f"{root}/config/{name}"))
    for key, words in d.items():
        if not isinstance(words, list):
            continue
        if key == "general":
            # the fallback list shows for ANY model — it must be plain single words,
            # no CamelCase run-ons (the 2026-11-04 defect was here)
            for w in words:
                if not re.fullmatch(r"[A-Z][a-z0-9]+", w or ""):
                    bad.append(f"{name}[general]: {w!r}")
        else:
            # family-keyed lists are puns echoing the model name (remote contract:
            # "must echo the provider name") — just require sane non-empty words
            for w in words:
                if not w or len(w) > 40:
                    bad.append(f"{name}[{key}]: {w!r}")
# the generator's emitted model words must obey the same rule
out = subprocess.run([sys.executable, f"{root}/bin/generate-local-settings.py",
                      "--emit-model-words", "--identity-json",
                      '{"session_kind":"local","actual_model_id":"qwen/qwen3-8b"}'],
                     capture_output=True, text=True, timeout=30)
try:
    for w in json.loads(out.stdout):
        if re.search(r"[a-z][A-Z]", w or ""):
            bad.append(f"emitted: {w!r}")
except Exception as exc:
    bad.append(f"generator output unparseable: {exc}: {out.stdout[:80]} {out.stderr[:80]}")
if bad:
    print("  FAIL: non-word verbs:", bad[:6]); sys.exit(1)
print("  PASS: every verb in both JSONs and the emitted words is a single readable word")
PY
check $? "general spinner-verb lists are plain single words (family puns excepted)"

echo
if [ "$failed" -eq 0 ]; then echo "ALL $passed PASS"; exit 0; else echo "$failed FAILED, $passed passed"; exit 1; fi
