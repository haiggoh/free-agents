# remote-agents.sh — the REMOTE cloud-API session roster.
#
# Deliberately a SEPARATE list from the local MLX aliases in config.local.sh:
# a remote agent runs on someone else's hardware over the network, so it is
# selected on its own menu and never mixed into the local model list.
#
# Each entry:  alias|provider|model-id|display|tier|notes
# provider must exist in bin/remote-keys.sh and bin/remote_provider_core.py.
# tier: renewing_free | trial | unknown   (matches remote_provider_core.py)
#
# Model ids are RESOLVED LIVE where a provider rotates them — never trust a
# pinned id: Gemini retired gemini-2.5-flash for new keys mid-2026 while the
# repo still pinned it, which is why remote-session.sh verifies the id against
# the provider's own /models before launching.

LA_REMOTE_AGENTS=(
  # Every model id below was verified LIVE against the provider's own catalogue
  # AND a real tool-calling round-trip on 2026-09-14. Re-verify with
  # `bin/remote-session.sh --verify <alias>` — providers retire ids without notice
  # (Gemini dropped gemini-2.5-flash for new keys while this repo still pinned it).

  # --- Gemini: primary free lane; largest renewing quota, best tool-calling
  "gemini-flash|gemini|gemini-3.6-flash|Gemini 3.6 Flash|renewing_free|VERIFIED. Primary free lane, fast, solid tool use. Thinking off for latency."
  "gemini-flash-thinking|gemini|gemini-3.6-flash|Gemini 3.6 Flash (thinking)|renewing_free|VERIFIED weights; thinking ON — slower, stronger reasoning."
  "gemini-flash-lite|gemini|gemini-3.1-flash-lite|Gemini 3.1 Flash-Lite|renewing_free|VERIFIED in catalogue. Utility tier: cheapest/fastest, weaker tool use."

  # --- Groq: fastest tokens/sec of any free lane
  "groq-oss120|groq|openai/gpt-oss-120b|Groq gpt-oss-120b|renewing_free|VERIFIED chat+tools. Very fast; best once Gemini quota is spent."
  "groq-oss20|groq|openai/gpt-oss-20b|Groq gpt-oss-20b|renewing_free|Smaller/faster sibling for utility work."

  # --- NVIDIA hosted NIM: broad catalogue, limits dynamic
  "nvidia-nemotron3|nvidia|nvidia/nemotron-3-super-120b-a12b|NVIDIA Nemotron 3 Super 120B-A12B|unknown|VERIFIED chat+tools. Largest verified free lane; NVIDIA throttles unpredictably."
  "nvidia-gptoss|nvidia|openai/gpt-oss-20b|NVIDIA gpt-oss-20b|unknown|Second NIM lane when Nemotron is throttled."

  # --- OpenRouter: only :free-suffixed models are actually free
  "openrouter-free|openrouter|openrouter/auto|OpenRouter auto (free variants)|unknown|NOT verified — router picks per call; confirm the model is :free before trusting."

  # --- Cerebras: LEGACY TRIAL — never recommended for new setup (revisions plan §2)
  "cerebras-legacy|cerebras|llama-3.3-70b|Cerebras Llama 3.3 70B (legacy trial)|trial|TRIAL, not free. Offered only behind --include-trials."
)

# Which spoofed Claude ids the proxy should answer to. Claude Code asks for these
# names; the proxy maps them onto the chosen remote model.
LA_REMOTE_SPOOF_IDS="claude-opus-5,claude-opus-4-8,claude-sonnet-5,claude-haiku-4-5-20251001"

# Port range scanned for a free proxy port (kept clear of the local 8000-8010 band).
LA_REMOTE_PROXY_PORT_MIN="${LA_REMOTE_PROXY_PORT_MIN:-4141}"
LA_REMOTE_PROXY_PORT_MAX="${LA_REMOTE_PROXY_PORT_MAX:-4151}"
