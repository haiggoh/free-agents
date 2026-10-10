#!/usr/bin/env bash
# Every executable script in bin/, scripts/ and install/ must answer --help WITHOUT doing its work.
# 'Help' = exit 0, at least two lines of output, and NO stub marker. Each runs with a sandboxed HOME and a PATH whose brew/pipx/launchctl/curl/hf/claude/osascript/
# open/git-push stubs write a marker: any marker means the script RAN instead of printing help.
# Exemptions are listed (with reasons) in ALLOW below.   Usage: tests/test_every_script_has_help.sh [--help]
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,5p "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Libraries, sourced fragments and hooks that read stdin by contract (they have no CLI of their own).
ALLOW=" bin/emoji_constants.py bin/queue_marker.py bin/remote_provider_core.py bin/remote_http.py \
bin/session_picker_terminal.py bin/session_picker_model.py bin/session_menu_state.py bin/la_proxy_hooks.py \
bin/la-watch-filter.awk bin/la-hw-detect.sh bin/omlx-auto-prewarm-gate.sh bin/cloud_session_env.py \
bin/quota/providers.py bin/quota/telemetry.py bin/queue_replay.py bin/queue_state.py bin/acquisition_catalog.py \
bin/litellm-with-trust.py bin/free-agent-tool.py \
bin/session_picker.py install/managers/rapid_mlx.py install/local-stack-update-check.sh "
# session_picker.py: the module behind bin/session-picker (which IS checked). rapid_mlx.py: a manager
# class imported by manage-backend.py. local-stack-update-check.sh: retired, deleted on its own branch.
SB=$(mktemp -d); trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/stubs" "$SB/home"
for c in brew pipx launchctl curl wget hf huggingface-cli claude osascript open rapid-mlx litellm gh; do
  printf '#!/bin/sh\necho "%s $*" >> "%s/RAN"\nexit 0\n' "$c" "$SB" > "$SB/stubs/$c"; chmod +x "$SB/stubs/$c"
done
pass=0 fail=0 skip=0
while IFS= read -r f; do
  rel="${f#"$ROOT"/}"
  case "$ALLOW" in *" $rel "*) skip=$((skip+1)); continue ;; esac
  rm -f "$SB/RAN"
  out=$(cd "$SB" && HOME="$SB/home" PATH="$SB/stubs:$PATH" LA_API_KEYS_DIR="$SB/none" \
        perl -e 'alarm 15; exec @ARGV' "$f" --help </dev/null 2>&1); rc=$?
  if [ -f "$SB/RAN" ]; then fail=$((fail+1)); echo "  FAIL: $rel ran real work on --help: $(head -1 "$SB/RAN")"
  elif [ $rc -ne 0 ]; then fail=$((fail+1)); echo "  FAIL: $rel --help exit $rc: $(printf '%s' "$out" | head -1)"
  elif [ "$(printf '%s' "$out" | grep -c .)" -lt 2 ]; then fail=$((fail+1)); echo "  FAIL: $rel --help printed under 2 lines"
  else pass=$((pass+1)); fi
done < <(find "$ROOT/bin" "$ROOT/scripts" "$ROOT/install" -maxdepth 2 -type f -perm -u+x \
         ! -name '*.bak*' ! -path '*/__pycache__/*' ! -name '*.patch' ! -name '*.plist' | sort)
echo "every-script --help: $pass passed, $fail failed, $skip exempt"; [ "$fail" -eq 0 ]
