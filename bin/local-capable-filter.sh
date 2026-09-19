#!/usr/bin/env bash
# local-capable-filter.sh — classify remote roster rows by whether they could reasonably run
# locally on this machine's 128 GB unified-memory stack.
#
# Usage:
#   local-capable-filter.sh --parse <policy.psv> [--roster <roster-file-or-->]
#     Reads the policy file and pipe-separated roster lines, prints a JSON report.
#   local-capable-filter.sh --report <policy.psv> [--roster <roster-file-or-->]
#     Prints a human-readable hidden-model report grouped by provider.
#   local-capable-filter.sh --help
#
# Policy file format (PSV, comments and blank lines allowed):
#   provider|remote_model_id|classification|local_artifact|runtime|estimated_size_gb|reason
#
#   classification must be one of:
#     local-capable | potentially-local-capable | remote-preferred | unknown
#   Rows with an unrecognized classification are treated as unknown (visible).
#   Duplicate (provider, remote_model_id) keys are rejected deterministically.
#   Missing/unknown classifications remain visible (fail-open).
#
# Roster input: one pipe-separated row per line:
#   alias|provider|model_id|display|tier|notes
#
# Exit:
#   0 — success
#   1 — malformed policy (prints filename:line diagnostics on stderr)
#   2 — usage error
set -uo pipefail

usage() {
  cat <<'EOF'
local-capable-filter.sh — local-capable filter for the remote roster.

Usage:
  local-capable-filter.sh --parse  <policy.psv> [--roster <file|->]   # JSON report
  local-capable-filter.sh --report <policy.psv> [--roster <file|->]   # human-readable report
  local-capable-filter.sh --help

Policy file format (PSV, comments/# and blank lines are skipped):
  provider|remote_model_id|classification|local_artifact|runtime|estimated_size_gb|reason

Roster input: pipe-separated lines from a file, or '-' to read stdin.
  alias|provider|model_id|display|tier|notes

Classifications:
  local-capable              — hidden by default in the remote picker
  potentially-local-capable  — visible, but marked: the footprint estimate lands near the
                               memory ceiling, so it needs real evidence before it is hidden
  remote-preferred           — visible
  unknown                    — visible (fail-open: uncertainty must never silently hide)

Exit codes:
  0 success
  1 malformed policy (filename:line diagnostics on stderr)
  2 usage error
EOF
}

die() { printf '%s\n' "$*" >&2; exit 2; }

[[ $# -ge 1 ]] || usage

MODE=""
POLICY_FILE=""
ROSTER_SOURCE=""
ROSTER_TMP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --parse|--report) MODE="$1"; MODE="${MODE#--}"; shift ;;
    --roster)
      [[ $# -ge 2 ]] || { echo "local-capable-filter: --roster requires a path or '-'" >&2; exit 2; }
      ROSTER_SOURCE="$2"; shift 2 ;;
    -)
      # Stdin sentinel for roster input.
      ROSTER_SOURCE="-"; shift ;;
    -*) die "unknown option: $1" ;;
    *)
      # Positional: first non-option arg is the policy file; second is optional roster file.
      if [[ -z "$POLICY_FILE" ]]; then
        POLICY_FILE="$1"
      elif [[ -z "$ROSTER_SOURCE" ]]; then
        ROSTER_SOURCE="$1"
      else
        die "unexpected extra argument: $1"
      fi
      shift ;;
  esac
done

[[ "$MODE" == "parse" || "$MODE" == "report" ]] || { echo "local-capable-filter: must specify --parse or --report" >&2; exit 2; }
[[ -n "$POLICY_FILE" ]] || { echo "local-capable-filter: --parse/--report requires a policy file" >&2; exit 2; }

if [[ -z "$ROSTER_SOURCE" ]]; then
  if [[ -t 0 ]]; then
    echo "local-capable-filter: roster input is required (pass --roster <file|->, or pipe via stdin)" >&2
    exit 2
  else
    ROSTER_TMP="$(mktemp)"
    cat > "$ROSTER_TMP"
    ROSTER_SOURCE="$ROSTER_TMP"
  fi
fi

# ---- policy parser ----------------------------------------------------------
declare -A POLICY_CLASSIFICATION POLICY_REASON POLICY_SEEN
_parse_policy() {
  local file="$1" lineno=0 provider="" model_id="" classification="" reason="" row
  while IFS= read -r row || [[ -n "$row" ]]; do
    lineno=$((lineno + 1))
    # Strip leading whitespace; skip blanks and comments.
    row="${row#"${row%%[![:space:]]*}"}"
    [[ -z "$row" || "$row" == \#* ]] && continue
    # Split on '|'. We only need fields 1,2,3,7 — the rest we ignore.
    local f1 f2 f3 f4 f5 f6 f7
    # shellcheck disable=SC2034  # f4-f6 intentionally unused, see comment above
    IFS='|' read -r f1 f2 f3 f4 f5 f6 f7 <<< "$row"
    provider="${f1:-}"; provider="${provider#"${provider%%[![:space:]]*}"}"; provider="${provider%"${provider##*[![:space:]]}"}"
    model_id="${f2:-}"; model_id="${model_id#"${model_id%%[![:space:]]*}"}"; model_id="${model_id%"${model_id##*[![:space:]]}"}"
    classification="${f3:-}"; classification="${classification#"${classification%%[![:space:]]*}"}"; classification="${classification%"${classification##*[![:space:]]}"}"
    reason="${f7:-}"; reason="${reason#"${reason%%[![:space:]]*}"}"; reason="${reason%"${reason##*[![:space:]]}"}"
    [[ -n "$provider" && -n "$model_id" && -n "$classification" ]] \
      || { echo "$file:$lineno: malformed row (need at least provider|model_id|classification): $row" >&2; return 1; }
    case "$classification" in
      local-capable|potentially-local-capable|remote-preferred|unknown) ;;
      *)
        echo "$file:$lineno: unrecognized classification '$classification'; must be local-capable|potentially-local-capable|remote-preferred|unknown" >&2
        return 1
        ;;
    esac
    local key="${provider}|${model_id}"
    [[ -z "${POLICY_SEEN[$key]+x}" ]] || {
      echo "$file:$lineno: duplicate key '$key' (first seen at line ${POLICY_SEEN[$key]})" >&2
      return 1
    }
    POLICY_SEEN[$key]="$lineno"
    POLICY_CLASSIFICATION[$key]="$classification"
    POLICY_REASON[$key]="${reason:-}"
  done < "$file"
  return 0
}
_parse_policy "$POLICY_FILE" || exit 1

# ---- join against roster ----------------------------------------------------
HIDDEN_COUNT=0
VISIBLE_COUNT=0

# _emit_json <alias> <provider> <model_id> <display> <tier>
# Prints one JSON object on stdout and updates VISIBLE_COUNT/HIDDEN_COUNT.
_emit_json() {
  local alias="$1" prov="$2" mid="$3" disp="$4" tier="$5"
  local key="${prov}|${mid}"
  local classification="${POLICY_CLASSIFICATION[$key]:-}"
  local reason="${POLICY_REASON[$key]:-}"
  local visible="true"
  if [[ "$classification" == "local-capable" ]]; then
    visible="false"
    HIDDEN_COUNT=$((HIDDEN_COUNT + 1))
  else
    VISIBLE_COUNT=$((VISIBLE_COUNT + 1))
  fi
  # Escape strings for JSON via python3 (avoids manual backslash/quote gymnastics).
  local esc
  esc="$(python3 -c '
import json,sys
parts = sys.argv[1:]
print(json.dumps({"alias":parts[0],"provider":parts[1],"remote_model_id":parts[2],"display":parts[3],"tier":parts[4],
                  "visible":parts[5]=="true","classification":parts[6],"reason":parts[7]}))
' "$alias" "$prov" "$mid" "$disp" "$tier" "$visible" "$classification" "$reason")"
  printf '%s\n' "$esc"
}

# ---- join + output ----------------------------------------------------------
# We inline the roster loop so VISIBLE_COUNT/HIDDEN_COUNT stay in the
# current shell (no subshell = no lost assignments with set -u).

_rows_tmp=""
_rows_write() {
  local line alias prov model_id disp tier
  if [[ "$ROSTER_SOURCE" == "-" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      IFS='|' read -r alias prov model_id disp tier _ <<< "$line"
      _emit_json "$alias" "$prov" "$model_id" "$disp" "$tier"
    done || true
  elif [[ -f "$ROSTER_SOURCE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      IFS='|' read -r alias prov model_id disp tier _ <<< "$line"
      _emit_json "$alias" "$prov" "$model_id" "$disp" "$tier"
    done < "$ROSTER_SOURCE" || true
  else
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" ]] && continue
      IFS='|' read -r alias prov model_id disp tier _ <<< "$line"
      _emit_json "$alias" "$prov" "$model_id" "$disp" "$tier"
    done <<< "$ROSTER_SOURCE" || true
  fi
}

if [[ "$MODE" == "parse" ]]; then
  _rows_tmp="$(mktemp)"
  _rows_write > "$_rows_tmp"
  # Join JSON rows with commas into a single-line array.
  rows_json="$(awk 'NR>1{printf ","} {printf "%s",$0} END{print ""}' < "$_rows_tmp")"
  rm -f "$_rows_tmp"
  printf '{"visible_count":%d,"hidden_count":%d,"rows":[%s]}\n' \
    "$VISIBLE_COUNT" "$HIDDEN_COUNT" "$rows_json"
else
  # Human-readable grouped report.
  _rows_tmp="$(mktemp)"
  _rows_write > "$_rows_tmp"
  echo "Hidden remote-model entries (local-capable), grouped by provider:"
  current_prov=""
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    prov="$(printf '%s' "$line" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read()).get("provider",""))')"
    classification="$(printf '%s' "$line" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read()).get("classification",""))')"
    reason="$(printf '%s' "$line" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read()).get("reason",""))')"
    alias="$(printf '%s' "$line" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read()).get("alias",""))')"
    model_id="$(printf '%s' "$line" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read()).get("remote_model_id",""))')"
    if [[ "$prov" != "$current_prov" ]]; then
      echo ""
      echo "  [$prov]"
      current_prov="$prov"
    fi
    printf '    - %-30s %s' "$alias" "$model_id"
    [[ -n "$classification" ]] && printf ' (%s)' "$classification"
    [[ -n "$reason" ]] && printf ' — %s' "$reason"
    echo
  done < "$_rows_tmp"
  rm -f "$_rows_tmp"
  echo ""
  echo "Visible: $VISIBLE_COUNT  Hidden: $HIDDEN_COUNT  (toggle with the local-capable switch in the remote menu)"
  echo "Entries marked potentially-local-capable stay VISIBLE: the estimate is near the memory"
  echo "ceiling, where its own error exceeds the headroom, so hiding them needs real evidence."
fi

# Clean up temp roster file if we created one.
if [[ -n "${ROSTER_TMP:-}" && -f "${ROSTER_TMP:-}" ]]; then rm -f "$ROSTER_TMP"; fi