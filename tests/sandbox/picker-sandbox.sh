#!/bin/sh
# picker-sandbox.sh — run THIS checkout's session picker against fake inventories, for hand-testing.
#
# Nothing real changes: every install / remove / download / register / unregister / launch the picker
# would run is only appended to $SANDBOX/calls.log. Your registry, models, backend venvs, API keys and
# saved picker settings are never read or written (each run starts from an empty settings dir).
#
# What the fakes contain (edit the heredocs below to stage a different situation):
#   csl inventory    qwen-3.8-operator (normal), auto-dream-7b (on disk, NOT registered),
#                    gone-model / lost-model / devstral-2-123b (registered, weights MISSING);
#                    register/unregister are remembered, so the list changes like the real one
#   downloader       qwen-3.8-operator 16 GB, gemma-4-26b 14 GB (both absent), 500 GB free
#   backend manager  rapid-mlx 0.16.1 + 0.15.3 installed; releases 0.17.0 / 0.16.1 / 0.15.3
#   remote lane      two fake providers (Groq, Gemini); launches are only logged
#   rate limiter     a throwaway state file in $SANDBOX
#
# Usage: tests/sandbox/picker-sandbox.sh [home|local|remote|download] [--keep]
#        tests/sandbox/picker-sandbox.sh --help
#   --keep   reuse the previous run's sandbox (and its settings) instead of a fresh one
#
# Environment:
#   PICKER_SANDBOX   sandbox directory (default ${TMPDIR:-/tmp}/free-agents-picker-sandbox)
#   LA_PICKER_VENV   picker venv, as for bin/session-picker
#
# After a run: cat "$PICKER_SANDBOX/calls.log" shows what the picker WOULD have done.
set -eu
start=home keep=0
for a in "$@"; do
  case "$a" in
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --keep) keep=1 ;;
    home|local|remote|download) start=$a ;;
    *) echo "picker-sandbox.sh: unknown argument '$a' (try --help)" >&2; exit 2 ;;
  esac
done
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SB="${PICKER_SANDBOX:-${TMPDIR:-/tmp}/free-agents-picker-sandbox}"
SB="${SB%/}"
if [ "$keep" = 0 ]; then rm -rf "$SB"; fi
mkdir -p "$SB/cfg"
[ -f "$SB/calls.log" ] || : > "$SB/calls.log"

# One recorder for every fake: log the call, change nothing.
cat > "$SB/record" <<EOF
#!/bin/sh
echo "\$(date +%H:%M:%S) would run: \$(basename "\$0") \$*" >> "$SB/calls.log"
EOF

# The fake csl KEEPS STATE like the real one: --register-model / --unregister-model change what the
# next --inventory reports, so the picker's reload after a confirmation can be seen working.
cat > "$SB/csl" <<EOF
#!/bin/sh
REG="$SB/registered" UNREG="$SB/unregistered"
touch "\$REG" "\$UNREG"
case "\$1" in
  --help|-h) echo "sandbox csl: fake inventory that remembers register/unregister calls"; echo "see picker-sandbox.sh"; exit 0;;
  --register-model) . "$SB/record"; echo "\$2" >> "\$REG"; echo "registered \$2 (sandbox)"; exit 0;;
  --unregister-model) . "$SB/record"; shift; for a in "\$@"; do echo "\$a" >> "\$UNREG"; done; exit 0;;
esac
case "\$*" in
  *--inventory*)
    printf 'qwen-3.8-operator\trapid\thigh\t\t\n'
    if grep -qx auto-dream-7b "\$REG"; then printf 'auto-dream-7b\trapid\tmedium\t\t\n'
    else printf 'auto-dream-7b\trapid (auto)\tmedium\tauto-scanned\tauto-dream-7b\n'; fi
    for m in gone-model:gone-model-v2 lost-model:lost-model-v1 devstral-2-123b:Devstral-2-123B; do
      a=\${m%%:*}; grep -qx "\$a" "\$UNREG" || printf '#missing\t%s\t%s\n' "\$a" "\${m#*:}"
    done
    exit 0;;
esac
. "$SB/record"
EOF

cat > "$SB/download-models.sh" <<EOF
#!/bin/sh
case "\$*" in
  *--help*) echo "sandbox downloader: fake catalog"; echo "see picker-sandbox.sh"; exit 0;;
  *--inventory*) printf 'qwen-3.8-operator\tABSENT\t16\tsession\tq/q\ngemma-4-26b\tABSENT\t14\tsession\tg/g\n#disk\t500\t100\n'; exit 0;;
esac
. "$SB/record"
EOF

cat > "$SB/remote-session.sh" <<EOF
#!/bin/sh
case "\$*" in
  *--help*) echo "sandbox remote launcher: fake provider list"; echo "see picker-sandbox.sh"; exit 0;;
  *--inventory*) printf 'sandbox-groq\tgroq\tGroq Qwen (sandbox)\trenewing_free\t0\t1\nsandbox-gemini\tgemini\tGemini Flash (sandbox)\trenewing_free\t0\t1\n'; exit 0;;
esac
. "$SB/record"
EOF

cat > "$SB/manage-backend" <<EOF
#!/bin/sh
case "\$*" in
  *--help*) echo "sandbox backend manager: fake version lists"; echo "see picker-sandbox.sh"; exit 0;;
  *" installed"*) printf '0.16.1         complete   /sandbox/rapid-mlx-0.16.1\n0.15.3         complete   /sandbox/rapid-mlx-0.15.3\n'; exit 0;;
  *" releases"*) printf '0.17.0\n0.16.1\n0.15.3\n'; exit 0;;
esac
. "$SB/record"
EOF

chmod +x "$SB/record" "$SB/csl" "$SB/download-models.sh" "$SB/remote-session.sh" "$SB/manage-backend"
mkdir -p -m 700 "$SB/api_keys"
[ -f "$SB/limiter-state.json" ] || echo '{"sandbox": true}' > "$SB/limiter-state.json"
echo "picker sandbox: $SB   (calls.log records what would have run)" >&2

CSL_SELF="$SB/csl" LA_DOWNLOADER="$SB/download-models.sh" LA_MANAGE_BACKEND="$SB/manage-backend" \
CSL_REMOTE_LAUNCHER="$SB/remote-session.sh" LA_SESSION_MENU_CONFIG_DIR="$SB/cfg" \
LA_NVIDIA_THROTTLE_STATE="$SB/limiter-state.json" LA_API_KEYS_DIR="$SB/api_keys" \
  exec "$ROOT/bin/session-picker" "$start"
