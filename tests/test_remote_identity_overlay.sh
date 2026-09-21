#!/usr/bin/env bash
# test_remote_identity_overlay.sh — free-API session identity: port range, provider family,
# and the settings overlay.
#
# Guards three defects measured live on 2026-09-20, all of which shipped GREEN in 0.17.3/4:
#
#  1. The resolver matched ONLY port 4141 while remote-session.sh scans 4141-4151 and takes
#     the first FREE port. A session launched while an older proxy was alive resolved to
#     session_kind=unknown, which collapsed theme, emoji, spinner AND banner at once.
#     The range cases below deliberately probe a NON-FIRST port: a 4141-only fixture
#     cannot catch this, which is exactly why the old suite missed it.
#
#  2. get_provider_family() compared a DISPLAY string ("Free API (NVIDIA)") for equality
#     against bare identifiers ("nvidia"), so every provider fell through to 'general' —
#     a key that did not exist in remote-spinner-verbs.json — and the verb list came back
#     EMPTY on every real session.
#
#  3. An empty verb list must never be emitted as mode=replace: that strips Claude Code's
#     own vocabulary and blanks the spinner.

set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0; FAIL=0

check() { # check <actual> <expected> <description>
    if [ "$1" = "$2" ]; then echo "✅ PASS: $3"; PASS=$((PASS+1))
    else echo "❌ FAIL: $3"; echo "     expected: $2"; echo "     actual:   $1"; FAIL=$((FAIL+1)); fi
}

kind_for() { ANTHROPIC_BASE_URL="$1" "$ROOT/bin/la-session-identity.sh" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_kind"])'; }

# --- 1. the whole launcher scan range resolves as free_api ---------------------------
for p in 4141 4142 4145 4151; do
    check "$(kind_for "http://127.0.0.1:$p")" "free_api" "port $p resolves as free_api"
done
check "$(kind_for http://localhost:4142)" "free_api" "localhost form of a non-first port resolves too"
# outside the range must NOT be claimed
check "$(kind_for http://127.0.0.1:4152)" "unknown" "port 4152 (outside 4141-4151) is not claimed as free_api"
check "$(kind_for http://127.0.0.1:8003)" "local" "local range still resolves as local"

# --- 2. provider family is matched from the DISPLAY string ---------------------------
fam() { python3 - "$ROOT" "$1" <<'PY'
import importlib.util, sys
root, prov = sys.argv[1], sys.argv[2]
s = importlib.util.spec_from_file_location("g", f"{root}/bin/generate-remote-settings.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.get_provider_family(prov))
PY
}
check "$(fam 'Free API (NVIDIA)')" "nvidia" "display string 'Free API (NVIDIA)' -> nvidia"
check "$(fam 'Free API (Google Gemini)')" "gemini" "display string with vendor prefix -> gemini"
check "$(fam 'Free API (Groq)')" "groq" "display string -> groq"
check "$(fam 'Free API (NVIDIA Nemotron)')" "nemotron" "model family beats serving provider"
check "$(fam 'Free API (unknown provider)')" "general" "unidentifiable provider -> general"

# --- 3. every family, including the fallback, yields NON-EMPTY verbs ------------------
verbs_len() { python3 - "$ROOT" "$1" <<'PY'
import importlib.util, json, sys
root, fam = sys.argv[1], sys.argv[2]
s = importlib.util.spec_from_file_location("g", f"{root}/bin/generate-remote-settings.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
data = json.load(open(f"{root}/config/remote-spinner-verbs.json"))
print(len(m.select_spinner_verbs(fam, data)))
PY
}
for fam in nvidia gemini groq nemotron general; do
    [ "$(verbs_len "$fam")" -gt 0 ] && r=ok || r=empty
    check "$r" "ok" "family '$fam' has at least one spinner verb"
done

# --- 4. the overlay a real session gets ----------------------------------------------
overlay() { # overlay <port> [provider]
    local id
    id=$(ANTHROPIC_BASE_URL="http://127.0.0.1:$1" LA_REMOTE_PROVIDER="${2:-}" "$ROOT/bin/la-session-identity.sh" 2>/dev/null)
    LAUNCH_DIR="$ROOT/bin" python3 "$ROOT/bin/generate-remote-settings.py" --identity-json "$id"
}
nv=$(overlay 4142 nvidia)
check "$(printf '%s' "$nv" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["spinnerVerbs"]["verbs"]))')" "3" \
      "a non-first-port NVIDIA session gets its 3 provider verbs"
check "$(printf '%s' "$nv" | python3 -c 'import json,sys; print(json.load(sys.stdin)["spinnerVerbs"]["mode"])')" "replace" \
      "overlay uses mode=replace so the session stays visibly distinct"

# a cloud session must get an EMPTY overlay, never a remote theme
cloud=$(printf '{"session_kind":"cloud","provider_display":"Anthropic (cloud)","session_emoji":"x"}' \
        | xargs -0 -I{} python3 "$ROOT/bin/generate-remote-settings.py" --identity-json {})
check "$cloud" "{}" "a cloud session gets an empty overlay"

# NEVER emit mode=replace with zero verbs — that would blank the native spinner
empty_ok=$(python3 - "$ROOT" <<'PY'
import importlib.util, json, subprocess, sys
root = sys.argv[1]
ident = json.dumps({"session_kind": "free_api", "provider_display": "Free API (nonexistent)",
                    "session_emoji": "🌐", "theme_identifier": "free-lime",
                    "actual_model_id": "unknown", "spinner_profile_id": "free-api"})
out = subprocess.run([sys.executable, f"{root}/bin/generate-remote-settings.py",
                      "--identity-json", ident], capture_output=True, text=True).stdout
d = json.loads(out or "{}")
sv = d.get("spinnerVerbs")
print("ok" if (sv is None or sv.get("verbs")) else "blank-spinner")
PY
)
check "$empty_ok" "ok" "an unresolvable provider never emits mode=replace with zero verbs"

# --- the lime accent must now actually be EMITTED ------------------------------
#
# HISTORY, so this is not "fixed" back to a guess later: 0.17.6 deliberately did NOT emit a
# theme, because a probe of a `themes` SETTINGS MAP could not be confirmed -- a bogus key
# succeeded just as silently, so acceptance was not evidence. That reasoning was right about
# the key and wrong about the MECHANISM. Custom themes are FILES: the CLI reads
# ~/.claude/themes/<slug>.json (<=256KB, shape {name, base, overrides}) and a session selects
# one with the ordinary `theme` setting. Confirmed three ways against CLI 2.1.278: the loader
# path userConfigDir("themes",[slug]) in the binary, a "[theme] watcher" for hot reload, and
# `claude --help` naming "custom themes" among the customizations --safe-mode disables.
#
# So a remote session must carry theme=<slug>, and the overlay must NOT invent a colour: the
# accent has to come from remote-theme.json so the data file stays the single source of truth.
lime=$(overlay 4142 nvidia)
check "$(printf '%s' "$lime" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("theme","MISSING"))')" \
      "free-lime" "a remote session selects the custom lime theme by slug"

# The theme FILE must be installable and must carry the accent from the data file, not a
# hardcoded literal -- assert they agree rather than asserting the colour twice.
theme_ok=$(python3 - "$ROOT" <<'PY2'
import json, subprocess, sys
root = sys.argv[1]
accent = json.load(open(f"{root}/config/remote-theme.json"))["theme"]["accent_color"]
out = subprocess.run([sys.executable, f"{root}/bin/generate-remote-settings.py",
                      "--emit-theme-file"], capture_output=True, text=True).stdout
d = json.loads(out or "{}")
ov = d.get("overrides", {})
print("ok" if (d.get("base") and ov and all(v == accent for v in ov.values()) and "claude" in ov)
      else f"bad:{out[:120]}")
PY2
)
check "$theme_ok" "ok" "--emit-theme-file writes a valid theme whose overrides use the data-file accent"

# A cloud session must NOT be themed lime -- same scoping discipline as the spinner verbs.
check "$(printf '%s' "$cloud" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("theme","none"))')" \
      "none" "a cloud session is never given the remote theme"

echo
echo "=== Summary ==="
echo "Pass: $PASS  Fail: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "remote identity overlay: PASS"
