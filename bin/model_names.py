#!/usr/bin/env python3
"""model_names.py — turn a provider model id into the name a person reads.

`deepseek-ai/deepseek-v4.1-flash` -> `DeepSeek V4.1 Flash`, `z-ai/glm-5.3` -> `Z.ai GLM 5.3`,
`@cf/qwen/qwen3.8-27b` -> `Alibaba Qwen 3.8 27B`, `openai/gpt-oss-20b` -> `OpenAI GPT-OSS 20B`.

The name leads with the model's MANUFACTURER when it is known, never the API provider it is
served from (user 2026-10-22: the picker's group header already names the provider, and only
the Nemotron models are actually NVIDIA's). Unknown makers get no prefix rather than a guess.
Used by the session picker's model lists (remote and local) and by the session title.

Usage:  model_names.py <model-id> [...]     print the readable name of each id
        model_names.py --help
"""
from __future__ import annotations

import re
import sys

# Vendor path segment (lower-cased) -> manufacturer.
VENDORS = {
    "z-ai": "Z.ai", "zai": "Z.ai", "zai-org": "Z.ai", "thudm": "Z.ai",
    "nvidia": "NVIDIA", "nv-mistralai": "NVIDIA",
    "deepseek": "DeepSeek", "deepseek-ai": "DeepSeek",
    "moonshotai": "Moonshot", "moonshot": "Moonshot",
    "meta": "Meta", "meta-llama": "Meta", "meta-models": "Meta",
    "google": "Google", "openai": "OpenAI", "anthropic": "Anthropic",
    "qwen": "Alibaba", "alibaba": "Alibaba",
    "mistral": "Mistral", "mistralai": "Mistral",
    "poolside": "Poolside", "cohere": "Cohere", "coherelabs": "Cohere",
    "microsoft": "Microsoft", "ibm": "IBM", "ibm-granite": "IBM",
    "minimax": "MiniMax", "minimaxai": "MiniMax",
    "x-ai": "xAI", "xai": "xAI", "spacexai": "xAI",
    "xiaomi": "Xiaomi", "xiaomimimo": "Xiaomi",
    "bytedance": "ByteDance", "bytedance-seed": "ByteDance",
    "amazon": "Amazon", "tencent": "Tencent", "stepfun": "StepFun", "stepfun-ai": "StepFun",
    "meituan": "Meituan", "meituan-longcat": "Meituan", "ai21labs": "AI21", "upstage": "Upstage",
    "arcee-ai": "Arcee", "liquid": "Liquid AI", "inclusionai": "inclusionAI",
    "01-ai": "01.AI", "snowflake": "Snowflake", "databricks": "Databricks", "writer": "Writer",
    # KAT Coder's team names itself Kwaipilot on HuggingFace (Kuaishou is the parent company;
    # the list shows the team's own name — user 2026-10-23).
    "kwaipilot": "Kwaipilot", "openrouter": "OpenRouter",   # its own free router
    "stepfun-ai": "StepFun",
}

# Model-family word (the name's first word) -> manufacturer, for ids with no vendor segment
# (`gpt-oss-120b` on Cerebras, `gemini-3.8-flash` on the Gemini API, `codestral-2508`).
FAMILIES = {
    "gpt": "OpenAI", "o3": "OpenAI", "o4": "OpenAI",
    "gemini": "Google", "gemma": "Google",
    "glm": "Z.ai", "deepseek": "DeepSeek", "kimi": "Moonshot",
    "nemotron": "NVIDIA", "minitron": "NVIDIA",
    "llama": "Meta", "muse": "Meta",
    "qwen": "Alibaba", "qwq": "Alibaba",
    "mistral": "Mistral", "codestral": "Mistral", "devstral": "Mistral",
    "magistral": "Mistral", "ministral": "Mistral", "pixtral": "Mistral",
    "laguna": "Poolside", "granite": "IBM", "phi": "Microsoft",
    "claude": "Anthropic", "grok": "xAI", "minimax": "MiniMax", "mimo": "Xiaomi",
    "kat": "Kwaipilot", "seed": "ByteDance", "command": "Cohere", "north": "Cohere",
}

# Words whose spelling is not plain capitalisation. An empty value DROPS the token: "it"
# (instruct — every chat model is) and "mlx" (the format — the row's info box names the
# backend) read as noise in a picker line (user-approved preview 2026-10-23).
# Quantisation/profile tokens spell brand + bits (grounded in the model folders' own
# quantization configs: oq6e/oq8e/oq6 = OptiQ 6/8-bit; 6xl = the 6-bit XL profile).
WORDS = {
    "glm": "GLM", "gpt": "GPT", "oss": "OSS", "gpt-oss": "GPT-OSS", "it": "", "vl": "VL",
    "fp8": "FP8", "fp4": "FP4", "awq": "AWQ", "gguf": "GGUF", "mlx": "", "moe": "MoE",
    "deepseek": "DeepSeek", "minimax": "MiniMax", "mimo": "MiMo", "qwq": "QwQ",
    "kat": "KAT", "xs": "XS", "ai": "AI", "r1": "R1", "o3": "o3", "o4": "o4",
    "optiq": "OptiQ", "oq6e": "OptiQ 6-bit", "oq8e": "OptiQ 8-bit", "oq6": "OptiQ 6-bit",
    "6xl": "6-bit XL", "bf16": "BF16",
}

_SIZE = re.compile(r"^(\d+(?:\.\d+)?)([bmkt])$", re.I)        # 120b, 1.5b, 8x7b handled below
_ACTIVE = re.compile(r"^a(\d+(?:\.\d+)?)b$", re.I)           # a12b -> A12B
_LETTER_VER = re.compile(r"^([a-z])(\d+(?:\.\d+)*)$", re.I)  # v4.1 -> V4.1, k2.6 -> K2.6
_NUM_SUFFIX = re.compile(r"^(\d+(?:\.\d+)*)([a-np-z])$", re.I)  # 4.5v -> 4.5V (not 4o)
_WORD_VER = re.compile(r"^([a-z]{3,})(\d+(?:\.\d+)*)$", re.I)  # qwen3.8 -> Qwen 3.8


def _word(tok: str) -> str:
    low = tok.lower()
    if low in WORDS:
        return WORDS[low]
    if m := _SIZE.match(tok):
        return m.group(1) + m.group(2).upper()
    if m := _ACTIVE.match(tok):
        return f"A{m.group(1)}B"
    if m := _LETTER_VER.match(tok):
        return m.group(1).upper() + m.group(2)
    if m := re.fullmatch(r"(\d+)bit", tok, re.I):
        return f"{m.group(1)}-bit"                    # 4bit -> 4-bit (quantisation)
    if m := _NUM_SUFFIX.match(tok):
        return m.group(1) + m.group(2).upper()
    if m := _WORD_VER.match(tok):
        return f"{_word(m.group(1))} {m.group(2)}"
    if tok[:1].isdigit() or any(c.isupper() for c in tok[1:]):
        return tok                                    # 2508, 3.8, MiniMax-style casing
    return tok[:1].upper() + tok[1:]


def manufacturer(model_id: str) -> str:
    """The model's maker, or "" when not known."""
    mid = (model_id or "").removeprefix("@cf/")
    parts = mid.split("/")
    if len(parts) > 1:
        maker = VENDORS.get(parts[-2].lower())
        if maker:
            return maker
    first = re.split(r"[-_.:\d]", parts[-1].lower(), maxsplit=1)[0]
    return FAMILIES.get(first, "")


def readable(model_id: str, with_maker: bool = True) -> str:
    """`vendor/some-model-1.5-30b-a3b:free` -> `Maker Some Model 1.5 30B-A3B (free)`."""
    mid = (model_id or "").strip()
    if not mid:
        return ""
    tail = ""
    if ":" in mid:
        mid, tag = mid.rsplit(":", 1)
        if tag.lower() == "free":
            tail = " (free)"
        elif _SIZE.match(tag):
            mid = f"{mid}-{tag}"                      # ollama-style gemma4:31b
        elif tag:
            tail = f" ({tag})"
    name = mid.rsplit("/", 1)[-1]
    name = re.sub(r"(?i)gpt-oss", "gptoss", name)     # one word, written GPT-OSS
    toks = [t for t in re.split(r"[-_ ]+", name) if t]
    words = [WORDS["gpt-oss"] if t.lower() == "gptoss" else _word(t) for t in toks]
    words = [w for w in words if w]          # dropped tokens (it, mlx) leave no double space
    out: list[str] = []
    for w in words:
        if out and re.fullmatch(r"A\d+(?:\.\d+)?B", w) and re.fullmatch(r"[\d.]+B", out[-1]):
            out[-1] = f"{out[-1]}-{w}"                # size + active params: 30B-A3B
        elif out and re.fullmatch(r"\d{1,2}", w) and re.fullmatch(r"\d{1,2}(?:\.\d+)*", out[-1]):
            out[-1] = f"{out[-1]}.{w}"                # dash-written version: sonnet-4-6 -> 4.6
        elif out and out[-1] == "GPT" and re.fullmatch(r"\d[\w.]*", w):
            out[-1] = f"GPT-{w}"                      # OpenAI's own spelling: GPT-5.4, GPT-4o
        else:
            out.append(w)
    pretty = " ".join(out) + tail
    maker = manufacturer(model_id) if with_maker else ""
    if maker and not pretty.lower().startswith(maker.lower()):
        pretty = f"{maker} {pretty}"
    return pretty


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0 if argv else 2
    unknown = [a for a in argv if a.startswith("-")]
    if unknown:
        print(f"model_names.py: unrecognised option {unknown[0]}\nTry 'model_names.py --help'.",
              file=sys.stderr)
        return 2
    for a in argv:
        print(readable(a))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
