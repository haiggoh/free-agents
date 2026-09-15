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

  # ════════════════════════════════════════════════════════════════════════════════
  # ★ TIER 1 — NVIDIA NIM: THE PREFERRED REMOTE LANE. Listed first, and first is the
  # Enter-default in every picker that reads this roster in order.
  #
  # WHY IT LEADS, and why this is evidence and not enthusiasm: the rate limits are very
  # generous and there is no known DAILY quota — the constraint other providers hit first
  # and hardest. The operator has run several successful sessions here across different
  # models (reported 2026-09-15).
  #
  # The one real downside was NIM reasoning models returning `reasoning_content`, which is
  # not an Anthropic thinking block — the translation layer died with "Content block is not
  # a thinking block" and took the session's endpoint with it. FIXED: write_proxy_config now
  # sends chat_template_kwargs.enable_thinking=false for nvidia unless thinking is asked for.
  # That fix is a PREREQUISITE of this promotion; without it the default lane breaks sessions.
  #
  # ⚠️ Tier stays `unknown` deliberately — NVIDIA throttles unpredictably and publishes no
  # per-account quota, so `renewing_free` would be a promise the provider does not make.
  # Leading the roster is about PREFERENCE ORDER, not about a billing guarantee.
  # ⚠️ NVIDIA's catalog reports tools:"unknown" for EVERY row, so tool capability cannot be
  # read from the catalog — a new row here is catalog-only until a real tool session proves it.
  # ════════════════════════════════════════════════════════════════════════════════
  "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|NVIDIA Nemotron 3 Super 120B-A12B|unknown|★ PREFERRED DEFAULT. VERIFIED chat+tools, and sessions run successfully on this lane. Reasoning disabled at the backend so the thinking-block failure cannot recur."
  "nvidia-nemotron-ultra|nvidia|nvidia/nemotron-3-ultra-550b-a55b|NVIDIA Nemotron 3 Ultra 550B-A55B|unknown|Largest NIM model. Catalog-listed; reasoning disabled like its Super sibling."
  "nvidia-kimi-k3|nvidia|moonshotai/kimi-k3|NVIDIA Kimi K3 (Moonshot)|unknown|Catalog-listed 2026-09-15. Strong coding/agentic reputation; tool use unproven on this route."
  "nvidia-kimi-k26|nvidia|moonshotai/kimi-k2.6|NVIDIA Kimi K2.6 (Moonshot)|unknown|Catalog-listed 2026-09-15. Predecessor to K3, kept for A/B."
  "nvidia-deepseek-v4|nvidia|deepseek-ai/deepseek-v4-flash-0731|NVIDIA DeepSeek V4 Flash|unknown|Catalog-listed 2026-09-15. Fast DeepSeek lane; tool use unproven."
  "nvidia-glm53|nvidia|z-ai/glm-5.3-flash|NVIDIA GLM 5.3 Flash|unknown|Catalog-listed 2026-09-15. GLM via NIM — distinct from the direct z.ai route and its billing."
  "nvidia-lightning|nvidia|nvidia/nemotron-3.5-lightning-30b-a3b|NVIDIA Nemotron 3.5 Lightning 30B-A3B|unknown|Catalog-listed 2026-09-15. Small/fast MoE for utility work."
  "nvidia-nano3|nvidia|nvidia/nemotron-nano-3-30b-a3b|NVIDIA Nemotron Nano 3 30B-A3B|unknown|Catalog-listed 2026-09-15. Cheapest Nemotron tier."
  "nvidia-gemma4|nvidia|google/gemma-4-31b-it|NVIDIA Gemma 4 31B|unknown|Catalog-listed 2026-09-15. Gemma weights without a Gemini daily quota."
  "nvidia-muse-glimmer|nvidia|meta/muse-glimmer-30b|NVIDIA Muse Glimmer 30B (Meta)|unknown|Catalog-listed 2026-09-15. Tool use unproven."
  "nvidia-laguna|nvidia|poolside/laguna-xs-2.1|NVIDIA Laguna XS 2.1 (Poolside)|unknown|Catalog-listed 2026-09-15. Code-oriented; tool use unproven."
  "nvidia-gptoss|nvidia|openai/gpt-oss-20b|NVIDIA gpt-oss-20b|unknown|Second NIM lane when Nemotron is throttled."

  # --- TIER 2 — Gemini: still VERIFIED for tool-calling, but DEMOTED from the default.
  # The free quota is reached annoyingly fast, leaving little headroom to get real work
  # done in one sitting (operator, 2026-09-15) — a lane you cannot finish a task on is not
  # the right Enter-default however good its tool-calling is. Kept high because when it has
  # quota it is the best-behaved of the free lanes.
  # ★ 3.8 FIRST, preferred over 3.6 for as long as it stays available (operator, 2026-09-15).
  # If 3.8 is withdrawn or starts erroring, 3.6 below is the fallback and is still VERIFIED.
  "gemini-3.8-flash|gemini|gemini-3.8-flash|Gemini 3.8 Flash|renewing_free|★ PREFERRED Gemini. Catalog-listed; newer than 3.6. Thinking off for latency."
  "gemini-3.8-flash-thinking|gemini|gemini-3.8-flash|Gemini 3.8 Flash (thinking)|renewing_free|Preferred Gemini, thinking ON — slower, stronger reasoning."
  "gemini-flash|gemini|gemini-3.6-flash|Gemini 3.6 Flash|renewing_free|FALLBACK. VERIFIED chat+tools over multiple sessions; use when 3.8 is unavailable."
  "gemini-flash-thinking|gemini|gemini-3.6-flash|Gemini 3.6 Flash (thinking)|renewing_free|VERIFIED weights; thinking ON — slower, stronger reasoning."
  "gemini-flash-lite|gemini|gemini-3.1-flash-lite|Gemini 3.1 Flash-Lite|renewing_free|VERIFIED in catalogue. Utility tier: cheapest/fastest, weaker tool use."

  # --- Groq: fastest tokens/sec of any free lane
  "groq-oss120|groq|openai/gpt-oss-120b|Groq gpt-oss-120b|renewing_free|VERIFIED chat+tools. Very fast; best once Gemini quota is spent."
  "groq-oss20|groq|openai/gpt-oss-20b|Groq gpt-oss-20b|renewing_free|Smaller/faster sibling for utility work."
  "groq-qwen36|groq|qwen/qwen3.6-27b|Groq Qwen 3.6 27B|renewing_free|Catalog only; account quotas apply."
  "groq-qwen38|groq|qwen/qwen3.8-27b|Groq Qwen 3.8 27B|renewing_free|Catalog only; account quotas apply."

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
