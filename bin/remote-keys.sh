#!/usr/bin/env bash
# remote-keys.sh — resolve provider credentials from ~/.api_keys/<provider> files
# into the child-process environment variable each provider expects.
#
# The plan treats $HOME/.api_keys as a PRIVATE DIRECTORY, never a sourceable
# shell file: one file per provider, each holding only the secret. This script
# reads the file for ONE provider and exports only that provider's variable, so
# a dispatch never carries credentials it does not need.
#
# Usage:
#   remote-keys.sh --check <provider>     # exit 0 if the credential file exists and is non-empty
#   remote-keys.sh --list                # print every provider and configured/missing (no secrets)
#   remote-keys.sh --env <provider>      # literal NAME=value lines (SECRET: read/export, never eval/log)
#   remote-keys.sh --names <provider>    # print just the env var NAMES this provider needs
#   remote-keys.sh --resolve <provider>  # print the credential FILE chosen for the provider (0.52.0 aliases)
#
# 0.52.0 multi-key aliases: $KEYS_DIR/aliases.json may map a provider to an ordered list of
# credential file names, tried in order — the first that exists and reads OK wins, so one
# provider can be tried against several keys (e.g. {"nvidia": ["nvidia-backup", "nvidia"]}).
# The store stays one file per key; key material is never copied anywhere. A missing alias
# file falls through to the next; an alias that EXISTS but is refused (symlink, not owned,
# empty) or a malformed aliases.json aborts loudly naming it. No alias entry -> the direct
# file, exactly the old behavior.
#
# Environment:
#   LA_API_KEYS_DIR   credential directory (default: $HOME/.api_keys)
set -euo pipefail

KEYS_DIR="${LA_API_KEYS_DIR:-$HOME/.api_keys}"

# provider|env-var-name|credential-file[,extra-env:extra-file]...
# Session routes include providers beyond the emergency-fallback Python registry.
_provider_spec() {
    case "$1" in
        gemini)     echo "GEMINI_API_KEY:gemini" ;;
        groq)       echo "GROQ_API_KEY:groq" ;;
        openrouter) echo "OPENROUTER_API_KEY:openrouter" ;;
        cloudflare) echo "CLOUDFLARE_API_TOKEN:cloudflare CLOUDFLARE_ACCOUNT_ID:cloudflare-account-id" ;;
        github|github-models) echo "GITHUB_MODELS_TOKEN:github-models" ;;
        cerebras)   echo "CEREBRAS_API_KEY:cerebras" ;;
        nvidia)     echo "NVIDIA_API_KEY:nvidia" ;;
        mistral)    echo "MISTRAL_API_KEY:mistral" ;;
        zai)        echo "ZAI_API_KEY:zai" ;;
        siliconflow) echo "SILICONFLOW_API_KEY:siliconflow" ;;
        llm7)       echo "LLM7_API_KEY:llm7" ;;
        kilo)       echo "KILO_API_KEY:kilo" ;;
        vercel)     echo "AI_GATEWAY_API_KEY:vercel" ;;
        sambanova)  echo "SAMBANOVA_API_KEY:sambanova" ;;
        modelscope) echo "MODELSCOPE_API_KEY:modelscope" ;;
        streamlake) echo "STREAMLAKE_API_KEY:streamlake" ;;
        huggingface) echo "HF_TOKEN:huggingface" ;;
        *)          return 1 ;;
    esac
}

ALL_PROVIDERS="gemini groq openrouter cloudflare cerebras nvidia mistral zai siliconflow llm7 kilo vercel sambanova modelscope streamlake huggingface"

usage() {
    sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
}

# Read one credential file, refusing anything that is not a plain, user-owned file.
_read_secret() {
    local path="$1"
    [[ -e "$path" ]] || return 1
    [[ -L "$path" ]] && { echo "remote-keys: refusing symlinked credential: $path" >&2; return 1; }
    [[ -f "$path" ]] || { echo "remote-keys: not a regular file: $path" >&2; return 1; }
    [[ -O "$path" ]] || { echo "remote-keys: not owned by current user: $path" >&2; return 1; }
    local val
    val="$(tr -d '\r\n' < "$path")"
    [[ -n "$val" ]] || return 1
    printf '%s' "$val"
}

# 0.52.0: print the alias file names for a provider in priority order (empty when the map
# has no entry for it). A malformed aliases.json or a non-list entry exits 3 AFTER naming
# the file and the problem on stderr — an explicit override must fail loudly, never fall
# through silently to the default.
_alias_files() {
    local prov="$1" map="$KEYS_DIR/aliases.json"
    [ -f "$map" ] || return 0
    python3 - "$map" "$prov" <<'PY'
import json, sys
map_path, prov = sys.argv[1], sys.argv[2]
try:
    with open(map_path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as exc:
    sys.stderr.write(f"remote-keys: cannot parse {map_path}: {exc}\n")
    sys.exit(3)
if not isinstance(data, dict):
    sys.stderr.write(f"remote-keys: {map_path} must be a JSON object: provider -> [file names]\n")
    sys.exit(3)
entry = data.get(prov)
if entry is None:
    sys.exit(0)
if not isinstance(entry, list) or not all(isinstance(x, str) and x for x in entry):
    sys.stderr.write(f"remote-keys: {map_path}: entry for '{prov}' must be a list of file-name strings\n")
    sys.exit(3)
for name in entry:
    print(name)
PY
}

# _resolve_file <provider> <default-file>: the first alias that exists and reads OK, else
# the default file (whose absence is the caller's normal "no credential" error). An alias
# that exists but is refused aborts loudly (exit 3) naming the entry.
_resolve_file() {
    local prov="$1" default="$2" aliases f
    if ! aliases="$(_alias_files "$prov")"; then
        echo "remote-keys: invalid alias map for $prov in $KEYS_DIR/aliases.json (see above)" >&2
        exit 3
    fi
    if [ -n "$aliases" ]; then
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            [ -e "$KEYS_DIR/$f" ] || continue          # missing alias -> try the next
            _read_secret "$KEYS_DIR/$f" >/dev/null || {
                echo "remote-keys: alias '$f' for $prov exists but is refused (see above); fix $KEYS_DIR/aliases.json" >&2
                exit 3
            }
            printf '%s' "$f"
            return 0
        done <<EOF2
$aliases
EOF2
    fi
    printf '%s' "$default"
}

cmd="${1:---list}"
case "$cmd" in
    -h|--help) usage; exit 0 ;;
    --list)
        printf '%-12s %-24s %s\n' PROVIDER ENV_VAR STATUS
        for p in $ALL_PROVIDERS; do
            first=1
            for pair in $(_provider_spec "$p"); do
                name="${pair%%:*}"; file="${pair##*:}"
                if [ "$first" -eq 1 ]; then file="$(_resolve_file "$p" "$file")"; first=0; fi
                if _read_secret "$KEYS_DIR/$file" >/dev/null 2>&1; then st="configured"; else st="missing"; fi
                printf '%-12s %-24s %s\n' "$p" "$name" "$st"
            done
        done
        ;;
    --check)
        prov="${2:?--check needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        first=1
        for pair in $spec; do
            file="${pair##*:}"
            if [ "$first" -eq 1 ]; then file="$(_resolve_file "$prov" "$file")"; first=0; fi
            _read_secret "$KEYS_DIR/$file" >/dev/null 2>&1 || exit 1
        done
        exit 0
        ;;
    --names)
        prov="${2:?--names needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        for pair in $spec; do echo "${pair%%:*}"; done
        ;;
    --resolve)
        prov="${2:?--resolve needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        first_pair="${spec%% *}"
        # assign first: with set -e a failing substitution in an ARGUMENT would be ignored
        resolved="$(_resolve_file "$prov" "${first_pair##*:}")"
        printf '%s\n' "$resolved"
        ;;
    --env)
        prov="${2:?--env needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        first=1
        for pair in $spec; do
            name="${pair%%:*}"; file="${pair##*:}"
            if [ "$first" -eq 1 ]; then file="$(_resolve_file "$prov" "$file")"; first=0; fi
            val="$(_read_secret "$KEYS_DIR/$file")" || {
                echo "remote-keys: no credential for $prov at $KEYS_DIR/$file" >&2; exit 1; }
            printf '%s=%s\n' "$name" "$val"
        done
        ;;
    *) usage >&2; exit 2 ;;
esac
