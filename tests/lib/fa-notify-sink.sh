#!/bin/sh
# tests/lib/fa-notify-sink.sh — source me: the notification fence for shell tests
# (0.55.8 Task 0, 0541 plan Item H + R1). Sets FA_NOTIFY_SINK to a per-run temp file and
# prepends a shim dir of no-op osascript/terminal-notifier/afplay/say/open stubs, so a shell
# test that notifies is RECORDED and the real user sees nothing. Idempotent; also sourced
# by tests/lib/sandbox.sh so its whole copy inherits the fence.

_FA_NOTIFY_SHIM="${FA_NOTIFY_SHIM_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/fa-notify-shim.XXXXXX")}"
FA_NOTIFY_SHIM_DIR="$_FA_NOTIFY_SHIM"
export FA_NOTIFY_SHIM_DIR
for _n in osascript terminal-notifier afplay say open; do
  _s="$_FA_NOTIFY_SHIM/$_n"
  if [ ! -x "$_s" ]; then
    cat > "$_s" <<STUB
#!/bin/sh
printf '%s\n' "\$(date +%s) $_n \$*" >> "$_FA_NOTIFY_SHIM/calls.log"
exit 0
STUB
    chmod +x "$_s"
  fi
done
if [ -z "${FA_NOTIFY_SINK:-}" ]; then
  FA_NOTIFY_SINK="$_FA_NOTIFY_SHIM/sink.jsonl"
  export FA_NOTIFY_SINK
fi
case ":$PATH:" in
  *":$_FA_NOTIFY_SHIM:"*) ;;
  *) PATH="$_FA_NOTIFY_SHIM:$PATH"; export PATH ;;
esac
unset _n _s
