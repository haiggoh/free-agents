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
        *)          return 1 ;;
    esac
}

ALL_PROVIDERS="gemini groq openrouter cloudflare cerebras nvidia mistral zai siliconflow llm7 kilo vercel sambanova modelscope"

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

cmd="${1:---list}"
case "$cmd" in
    -h|--help) usage; exit 0 ;;
    --list)
        printf '%-12s %-24s %s\n' PROVIDER ENV_VAR STATUS
        for p in $ALL_PROVIDERS; do
            for pair in $(_provider_spec "$p"); do
                name="${pair%%:*}"; file="${pair##*:}"
                if _read_secret "$KEYS_DIR/$file" >/dev/null 2>&1; then st="configured"; else st="missing"; fi
                printf '%-12s %-24s %s\n' "$p" "$name" "$st"
            done
        done
        ;;
    --check)
        prov="${2:?--check needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        for pair in $spec; do
            _read_secret "$KEYS_DIR/${pair##*:}" >/dev/null 2>&1 || exit 1
        done
        exit 0
        ;;
    --names)
        prov="${2:?--names needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        for pair in $spec; do echo "${pair%%:*}"; done
        ;;
    --env)
        prov="${2:?--env needs a provider}"
        spec="$(_provider_spec "$prov")" || { echo "remote-keys: unknown provider: $prov" >&2; exit 2; }
        for pair in $spec; do
            name="${pair%%:*}"; file="${pair##*:}"
            val="$(_read_secret "$KEYS_DIR/$file")" || {
                echo "remote-keys: no credential for $prov at $KEYS_DIR/$file" >&2; exit 1; }
            printf '%s=%s\n' "$name" "$val"
        done
        ;;
    *) usage >&2; exit 2 ;;
esac
