# remote-agents.sh — the REMOTE cloud-API session roster.
#
# Deliberately a SEPARATE list from the local MLX aliases in config.local.sh:
# a remote agent runs on someone else's hardware over the network, so it is
# selected on its own menu and never mixed into the local model list.
#
# Each entry:  alias|provider|model-id|display|tier|notes
# provider must have a key mapping and a remote-session.sh proxy route.
# tier: renewing_free | trial | unknown
# SELECT requires an explicit --remote-model ID or an interactive model choice.
#
# Recheck pinned ids with remote-session.sh --verify <alias> (catalog GET only).
# Launch does not consume a separate generation probe. A catalog listing is not
# evidence of working tool calls, remaining quota, or an account billing limit.

LA_REMOTE_AGENTS=(
  # Catalogs checked 2026-09-14. Newly added rows are CATALOG ONLY: no generation
  # probes were spent. Historical tool checks are identified individually below.

  # --- Gemini: primary free lane; largest renewing quota, best tool-calling
  "gemini-flash|gemini|gemini-3.6-flash|Gemini 3.6 Flash|renewing_free|VERIFIED. Primary free lane, fast, solid tool use. Thinking off for latency."
  "gemini-flash-thinking|gemini|gemini-3.6-flash|Gemini 3.6 Flash (thinking)|renewing_free|VERIFIED weights; thinking ON — slower, stronger reasoning."
  "gemini-3.8-flash|gemini|gemini-3.8-flash|Gemini 3.8 Flash|renewing_free|Catalog only. Separate selection from 3.6; account quotas apply. Thinking off."
  "gemini-3.8-flash-thinking|gemini|gemini-3.8-flash|Gemini 3.8 Flash (thinking)|renewing_free|Catalog only. Provider default thinking enabled."
  "gemini-flash-lite|gemini|gemini-3.1-flash-lite|Gemini 3.1 Flash-Lite|renewing_free|VERIFIED in catalogue. Utility tier: cheapest/fastest, weaker tool use."

  # --- Groq: fastest tokens/sec of any free lane
  "groq-oss120|groq|openai/gpt-oss-120b|Groq gpt-oss-120b|renewing_free|VERIFIED chat+tools. Very fast; best once Gemini quota is spent."
  "groq-oss20|groq|openai/gpt-oss-20b|Groq gpt-oss-20b|renewing_free|Smaller/faster sibling for utility work."
  "groq-qwen36|groq|qwen/qwen3.6-27b|Groq Qwen 3.6 27B|renewing_free|Catalog only; account quotas apply."
  "groq-qwen38|groq|qwen/qwen3.8-27b|Groq Qwen 3.8 27B|renewing_free|Catalog only; account quotas apply."

  # --- NVIDIA hosted NIM: broad catalogue, limits dynamic
  "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|NVIDIA Nemotron 3 Super 120B-A12B|unknown|VERIFIED chat+tools. Largest verified free lane; NVIDIA throttles unpredictably."
  "nvidia-gptoss|nvidia|openai/gpt-oss-20b|NVIDIA gpt-oss-20b|unknown|Second NIM lane when Nemotron is throttled."
  "nvidia-nemotron-ultra|nvidia|nvidia/nemotron-3-ultra-550b-a55b|NVIDIA Nemotron 3 Ultra|unknown|Catalog only; account quota and billing not verified."

  # --- OpenRouter: explicit free router or zero-priced, tool-capable catalog rows
  "openrouter-free|openrouter|openrouter/free|OpenRouter free router|renewing_free|Catalog only. Routes among free models; account limits still apply."
  "openrouter-nemotron|openrouter|nvidia/nemotron-3-super-120b-a12b:free|OpenRouter Nemotron 3 Super (free)|renewing_free|Catalog lists zero token prices and tools; no generation probe."
  "openrouter-code|openrouter|cohere/north-mini-code:free|OpenRouter North Mini Code (free)|renewing_free|Catalog lists zero token prices and tools; no generation probe."

  # --- Cloudflare Workers AI: account-specific OpenAI-compatible endpoint
  "cloudflare-oss20|cloudflare|@cf/openai/gpt-oss-20b|Cloudflare gpt-oss-20b|unknown|Catalog only. Requires cloudflare token and cloudflare-account-id; account billing applies."
  "cloudflare-qwen38|cloudflare|@cf/qwen/qwen3.8-27b|Cloudflare Qwen 3.8 27B|unknown|Catalog only; free allocation and paid overage depend on the account."

  # --- Cerebras: retain explicit trial opt-in until this account is requalified.
  "cerebras-oss120|cerebras|gpt-oss-120b|Cerebras gpt-oss-120b|trial|Catalog only. Use --include-trials; account billing not verified."
  "cerebras-qwen38|cerebras|qwen-3.8-27b|Cerebras Qwen 3.8 27B|trial|Catalog only. Use --include-trials; account billing not verified."

  # Provider selections intentionally pin no speculative default model. General
  # account API endpoints; ZAI Coding Plan is a distinct endpoint, not this route.
  "mistral|mistral|SELECT|Mistral (choose model)|unknown|Select a catalog model; free experiment and paid plans differ. Tool session untested."
  "zai|zai|SELECT|ZAI general API (choose model)|unknown|Explicit model required. General API billing; not the GLM Coding Plan endpoint. Tool session untested."
  "siliconflow|siliconflow|SELECT|SiliconFlow (choose model)|unknown|International .com endpoint. Free and paid models differ; select explicitly. Tool session untested."
  "llm7|llm7|SELECT|LLM7 (choose model)|unknown|Catalog contains free and paid models; inspect pricing and tools. Tool session untested."
  "kilo|kilo|SELECT|Kilo Gateway (choose model)|unknown|Free and paid models differ; inspect catalog pricing. Tool session untested."
  "vercel|vercel|SELECT|Vercel Gateway (choose model)|unknown|Credits and paid account billing apply; select explicitly. Tool session untested."
  "sambanova|sambanova|SELECT|SambaNova (choose model)|unknown|Account trial, credit and paid billing vary. Tool session untested."
  "modelscope|modelscope|SELECT|ModelScope (choose model)|unknown|Explicit API-Inference model required; account quota and tool use untested."
)

# Which spoofed Claude ids the proxy should answer to. Claude Code asks for these
# names; the proxy maps them onto the chosen remote model.
LA_REMOTE_SPOOF_IDS="claude-opus-5,claude-opus-4-8,claude-sonnet-5,claude-haiku-4-5-20251001"

# Port range scanned for a free proxy port (kept clear of the local 8000-8010 band).
LA_REMOTE_PROXY_PORT_MIN="${LA_REMOTE_PROXY_PORT_MIN:-4141}"
LA_REMOTE_PROXY_PORT_MAX="${LA_REMOTE_PROXY_PORT_MAX:-4151}"
