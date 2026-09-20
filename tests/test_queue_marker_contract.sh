#!/usr/bin/env bash
# test_queue_marker_contract.sh — the QUEUE_ANSWERED marker contract and FIFO drain pairing.
#
# Two defects are guarded here, both measured live on 2026-09-20:
#
#  1. build_queue_groups() paired each drain with the most RECENT enqueue (LIFO) and
#     ignored the content the drain op carries. With 2+ queued prompts every drain got the
#     wrong content, so the hash the hook computed could never match a correct marker.
#     A 1-prompt fixture CANNOT catch this — LIFO and FIFO coincide there — so the
#     ordering cases below use THREE prompts.
#
#  2. The hash, marker syntax and parse regex were restated independently in the hook and
#     in the helper. They agreed by luck; nothing enforced it. The seam test asserts the
#     helper's output is parseable by the hook and round-trips to the same hash.

set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0; FAIL=0
WORK=$(mktemp -d "${TMPDIR:-/tmp}/test_queue_marker.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

check() { # check <actual> <expected> <description>
    if [ "$1" = "$2" ]; then
        echo "✅ PASS: $3"; PASS=$((PASS+1))
    else
        echo "❌ FAIL: $3"; echo "     expected: $2"; echo "     actual:   $1"; FAIL=$((FAIL+1))
    fi
}

# --- fixture: three prompts, drained in the order they were queued (FIFO) -------------
cat > "$WORK/fifo.jsonl" <<'EOF'
{"type":"queue-operation","operation":"enqueue","content":"P1"}
{"type":"queue-operation","operation":"enqueue","content":"P2"}
{"type":"queue-operation","operation":"enqueue","content":"P3"}
{"type":"queue-operation","operation":"remove","content":"P1"}
{"type":"queue-operation","operation":"remove","content":"P2"}
{"type":"queue-operation","operation":"remove","content":"P3"}
EOF

# A drain that names a prompt out of order must still be attributed correctly.
cat > "$WORK/outoforder.jsonl" <<'EOF'
{"type":"queue-operation","operation":"enqueue","content":"FIRST prompt"}
{"type":"queue-operation","operation":"enqueue","content":"SECOND prompt"}
{"type":"queue-operation","operation":"remove","content":"SECOND prompt"}
{"type":"queue-operation","operation":"remove","content":"FIRST prompt"}
EOF

# A drain with NO content field must fall back to FIFO, never LIFO.
cat > "$WORK/nocontent.jsonl" <<'EOF'
{"type":"queue-operation","operation":"enqueue","content":"OLDEST"}
{"type":"queue-operation","operation":"enqueue","content":"NEWEST"}
{"type":"queue-operation","operation":"remove"}
EOF

cat > "$WORK/popall.jsonl" <<'EOF'
{"type":"queue-operation","operation":"enqueue","content":"A"}
{"type":"queue-operation","operation":"enqueue","content":"B"}
{"type":"queue-operation","operation":"enqueue","content":"C"}
{"type":"queue-operation","operation":"popAll"}
EOF

pair() { # pair <fixture> -> "content,content,..." in group order
    python3 - "$ROOT" "$1" <<'PY'
import importlib.util, sys
root, fixture = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("h", f"{root}/bin/local-queue-stop-hook.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(",".join(g["content"] for g in m.build_queue_groups(fixture)))
PY
}

check "$(pair "$WORK/fifo.jsonl")" "P1,P2,P3" "three prompts drained FIFO keep their own content"
check "$(pair "$WORK/outoforder.jsonl")" "SECOND prompt,FIRST prompt" "drain is matched by the content it carries, not by position"
check "$(pair "$WORK/nocontent.jsonl")" "OLDEST" "a drain with no content falls back to the OLDEST pending (FIFO), not the newest"
check "$(pair "$WORK/popall.jsonl")" "A,B,C" "popAll yields groups oldest-first"

# --- the hash the hook computes must be the hash the helper prints -------------------
for content in "P1" "/run-to-completion" "fix the thing" "continue"; do
    helper_out=$(python3 "$ROOT/bin/queue-marker-helper.py" "$content")
    hook_hash=$(python3 - "$ROOT" "$content" <<'PY'
import importlib.util, sys
root, content = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("h", f"{root}/bin/local-queue-stop-hook.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m._content_hash(content))
PY
)
    check "$helper_out" "[[QUEUE_ANSWERED:$hook_hash]]" "helper marker matches the hook's own hash for ${content}"

    # and the hook must PARSE what the helper emitted, back to that same hash
    parsed=$(python3 - "$ROOT" "$helper_out" <<'PY'
import importlib.util, sys
root, text = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("h", f"{root}/bin/local-queue-stop-hook.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(",".join(sorted(m._extract_answered_markers([text]))))
PY
)
    check "$parsed" "$hook_hash" "hook parses the helper's marker for ${content}"
done

# --- an answered marker must actually clear the prompt it names ----------------------
verdict=$(python3 - "$ROOT" <<'PY'
import importlib.util, sys
root = sys.argv[1]
spec = importlib.util.spec_from_file_location("h", f"{root}/bin/local-queue-stop-hook.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# P2's marker must address P2 — under the old LIFO pairing this is where it broke
marker = m._content_hash("P2")
print("addressed" if m._text_addresses_prompt(
    [f"some reply [[QUEUE_ANSWERED:{marker}]]"], "P2", {marker}) else "missed")
PY
)
check "$verdict" "addressed" "a marker for the 2nd queued prompt addresses that prompt"

# --- the helper must answer --help rather than doing work -----------------------------
# Must print USAGE and must NOT hash the flag itself. Asserting on the presence of
# "QUEUE_ANSWERED" alone would be vacuous — the docstring contains that word too. So
# assert the usage line is present AND that no line is a bare marker, which is what the
# script emits when it treats an unrecognised flag as content.
help_out=$(python3 "$ROOT/bin/queue-marker-helper.py" --help 2>&1)
help_has_usage=no; case "$help_out" in *"USAGE:"*) help_has_usage=yes ;; esac
check "$help_has_usage" "yes" "helper --help prints its usage block"

flag_marker=$(python3 "$ROOT/bin/queue-marker-helper.py" --help 2>&1 \
    | grep -cE '^\[\[QUEUE_ANSWERED:[a-f0-9]{8}\]\]$')
check "$flag_marker" "0" "helper --help does not hash the flag and emit a marker"

# The real marker path still works, and is exactly one bare marker line.
real_marker=$(python3 "$ROOT/bin/queue-marker-helper.py" "P1" \
    | grep -cE '^\[\[QUEUE_ANSWERED:[a-f0-9]{8}\]\]$')
check "$real_marker" "1" "helper with real content emits exactly one marker line"

echo
echo "=== Summary ==="
echo "Pass: $PASS  Fail: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "queue marker contract: PASS"
