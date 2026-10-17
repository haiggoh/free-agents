#!/usr/bin/env bash
# remote-provider-registry.sh — bash consumer of config/remote-registry.yaml
#
# Exposes:
#   _PROVIDER_IDS            — space-separated list of provider ids
#   _CLEAR_PROVIDER_ENV_VARS — space-separated list of provider env var names
#   _openai_base()           — get base URL for OpenAI-compatible provider
#   _catalog_request()       — GET the provider's /models endpoint
#   _available()             — check if provider is usable
#   _litellm_route()         — get litellm model string and API base
#   _clear_provider_env()    — unset all provider env vars
#
# This script is meant to be sourced, not executed.
# bash 3.2 compatible (no associative arrays).

_PROVIDER_IDS="cerebras cloudflare gemini github groq kilo llm7 mistral modelscope nvidia openrouter sambanova siliconflow streamlake vercel zai"

_CLEAR_PROVIDER_ENV_VARS="AI_GATEWAY_API_KEY CEREBRAS_API_KEY CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN GEMINI_API_KEY GITHUB_TOKEN GROQ_API_KEY KILO_API_KEY LLM7_API_KEY MISTRAL_API_KEY MODELSCOPE_API_KEY NVIDIA_API_KEY OPENROUTER_API_KEY SAMBANOVA_API_KEY SILICONFLOW_API_KEY STREAMLAKE_API_KEY ZAI_API_KEY"

_openai_base() {
    case "$1" in
        cerebras) echo 'https://api.cerebras.ai/v1';;
        cloudflare) echo 'https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1';;
        gemini) echo 'https://generativelanguage.googleapis.com/v1beta/openai';;
        github) echo 'https://models.github.ai/inference';;
        groq) echo 'https://api.groq.com/openai/v1';;
        kilo) echo 'https://api.kilo.ai/api/gateway';;
        llm7) echo 'https://api.llm7.io/v1';;
        mistral) echo 'https://api.mistral.ai/v1';;
        modelscope) echo 'https://api-inference.modelscope.cn/v1';;
        nvidia) echo 'https://integrate.api.nvidia.com/v1';;
        openrouter) echo 'https://openrouter.ai/api/v1';;
        sambanova) echo 'https://api.sambanova.ai/v1';;
        siliconflow) echo 'https://api.siliconflow.com/v1';;
        streamlake) echo 'https://api.streamlake.ai/v1';;
        vercel) echo 'https://ai-gateway.vercel.sh/v1';;
        zai) echo 'https://api.z.ai/api/paas/v4';;
        *) return 2 ;;
    esac
}

_litellm_route() {
    local prov="$1" model="$2"
    case "$prov" in
        gemini) echo "gemini/${model}|";;
        groq) echo "groq/${model}|";;
        nvidia) echo "nvidia_nim/${model}|";;
        openrouter) echo "openrouter/${model}|";;
        cerebras) echo "cerebras/${model}|";;
        cloudflare) echo "openai/${model}|https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1";;
        mistral) echo "openai/${model}|https://api.mistral.ai/v1";;
        zai) echo "openai/${model}|https://api.z.ai/api/paas/v4";;
        siliconflow) echo "openai/${model}|https://api.siliconflow.com/v1";;
        llm7) echo "openai/${model}|https://api.llm7.io/v1";;
        kilo) echo "openai/${model}|https://api.kilo.ai/api/gateway";;
        vercel) echo "openai/${model}|https://ai-gateway.vercel.sh/v1";;
        sambanova) echo "openai/${model}|https://api.sambanova.ai/v1";;
        modelscope) echo "openai/${model}|https://api-inference.modelscope.cn/v1";;
        streamlake) echo "openai/${model}|https://api.streamlake.ai/v1";;
        github) echo "openai/${model}|https://models.github.ai/inference";;
        *) return 1 ;;
    esac
}

_available() {
    [[ "$1" != github ]] || {
        echo 'remote-session: GitHub Models is retired (official docs; catalog HTTP 410 checked 2026-09-14). Select another provider directly; no generation or automatic fallback attempted. https://docs.github.com/en/github-models' >&2
        return 2
    }
}

_catalog_request() {
    local prov="$1" base envline key
    _available "$prov" || return 2
    case "$prov" in
        gemini) base='https://generativelanguage.googleapis.com/v1beta/openai/models';;
        groq) base='https://api.groq.com/openai/v1/models';;
        nvidia) base='https://integrate.api.nvidia.com/v1/models';;
        openrouter) base='https://openrouter.ai/api/v1/models';;
        cerebras) base='https://api.cerebras.ai/v1/models';;
        cloudflare) base='https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1/models';;
        mistral|llm7|kilo|vercel|sambanova) base="$( _openai_base "$prov" )/models";;
        siliconflow) base='https://api.siliconflow.com/v1/models?type=text';;
        streamlake) base='https://api.streamlake.ai/v1/models';;
        github) base='https://models.github.ai/catalog/models';;
        zai) echo 'remote-session: no documented ZAI catalog route; use --remote-model ID from https://docs.z.ai/guides/overview . This route uses general API billing, not the Coding Plan.' >&2; return 2 ;;
        modelscope) echo 'remote-session: no documented ModelScope catalog route; use --remote-model ID from the API-Inference model page: https://modelscope.cn/docs/model-service/API-Inference/intro' >&2; return 2 ;;
        *) echo "remote-session: no documented catalog route for $prov; choose an API model from its provider docs with --remote-model ID. Catalog verification unavailable." >&2; return 2 ;;
    esac
    envline="$("$KEYS" --env "$prov")" || return 1
    envline="${envline%%$'\n'*}"
    key="${envline#*=}"
    # curl, not python: it uses the system trust store, which survives the
    # corporate TLS-inspecting proxy that breaks python's default bundle.
    # Keep the credential out of argv; escape curl-config quotes/backslashes.
    key="${key//\\/\\\\}"; key="${key//\"/\\\"}"
    printf 'header = "Authorization: Bearer %s"\n' "$key" |
        curl --config - --fail --silent --max-time 25 "$base" || {
        printf '  model check : ? catalog request failed (network, authorization, or service error)\n' >&2;
        return 1
    }
}

_clear_provider_env() {
    local name
    for name in $_CLEAR_PROVIDER_ENV_VARS; do
        unset "$name"
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "remote-provider-registry.sh — source this script, do not run it"
    echo ""
    echo "Known provider IDs: $_PROVIDER_IDS"
    echo ""
    echo "Exposed variables/functions (for sourcing):"
    echo "  _PROVIDER_IDS            space-separated provider ids"
    echo "  _CLEAR_PROVIDER_ENV_VARS space-separated env var names to unset"
    echo "  _available <prov>        check if provider is available (retired providers return 2)"
    echo "  _openai_base <prov>      get base URL for OpenAI-compatible provider"
    echo "  _catalog_request <prov>  GET the provider's /models endpoint"
    echo "  _litellm_route <prov> <model>  get litellm model string and base"
    echo "  _clear_provider_env      unset all provider env vars"
    exit 0
fi
