#!/usr/bin/env python3
"""Turn a provider's model-catalog JSON (stdin) into chat/tool candidate rows (stdout).

Usage: remote-catalog-parse.py [--help] < catalog.json
Output: one line per candidate, "<model id><TAB><json metadata>", metadata keys
        pricing, tier, tools. Exit 1 (with a message on stderr) when the body is
        empty, malformed, an error, or holds no chat/tool candidate.
Environment: none.

Shapes understood (all measured against live catalogs, 2026-10-18):
  OpenAI-style {"data": [...]}           most providers
  Cloudflare {"result": [...]} by "name"  /models/search
  Gemini ids "models/<id>"                prefix stripped
  Mistral rows type "base" + capabilities completion_chat/function_calling
  OpenRouter/Kilo supported_parameters    "tools" in the list = tool-capable
  Hugging Face router providers[]         any provider supports_tools = tool-capable

One unusable row (an id we cannot safely pass on, e.g. OpenRouter's "~openai/..."
aliases) is skipped; it never blanks the whole list.
"""
import json
import re
import sys

ID_RE = re.compile(r"[a-zA-Z0-9@][a-zA-Z0-9_./:@+-]*")
CHAT_TYPES = ("chat", "language", "text", "model", "chat-completion")
EMPTY_MSG = "remote-session: empty, malformed, error, or no chat/tool candidates in catalog."


def _tools(row, caps):
    """True / False / "unknown" from whichever signal the catalog carries."""
    if "function_calling" in caps:
        return caps["function_calling"]
    if "tools_calling" in row:
        return row["tools_calling"]
    providers = row.get("providers")
    if isinstance(providers, list) and providers:
        return any(isinstance(p, dict) and p.get("supports_tools") is True for p in providers)
    params = row.get("supported_parameters")
    if isinstance(params, list):
        return "tools" in params
    return "unknown"


def _pricing(row):
    if "pricing" in row:
        return row["pricing"]
    providers = row.get("providers")
    if isinstance(providers, list):
        priced = [p["pricing"] for p in providers if isinstance(p, dict) and p.get("pricing")]
        if priced:
            return priced[0]
    return "unknown"


def candidates(payload):
    """Rows as (id, metadata dict). Raises ValueError on an unusable catalog body."""
    by_name = isinstance(payload, dict) and "result" in payload
    rows = payload.get("data", payload.get("result")) if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and payload.get("success") is False:
        raise ValueError("catalog reports success=false")
    if not isinstance(rows, list):
        raise ValueError("no row list")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = row.get("name", row.get("id")) if by_name else row.get("id")
        if not isinstance(ident, str):
            continue
        if ident.startswith("models/"):
            ident = ident.split("/", 1)[1]
        if not ID_RE.fullmatch(ident):
            continue
        caps = row.get("capabilities") or {}
        if not isinstance(caps, dict):
            caps = {}
        if caps.get("completion_chat") is False or caps.get("function_calling") is False \
                or row.get("tools_calling") is False:
            continue
        # Mistral labels every chat model type "base"; its capability flag is the real signal.
        if caps.get("completion_chat") is not True and \
                row.get("model_type", row.get("type", "chat")) not in CHAT_TYPES:
            continue
        tools = _tools(row, caps)
        if tools is False:
            continue
        out.append((ident, {"pricing": _pricing(row), "tier": row.get("tier", "unknown"),
                            "tools": tools}))
    if not out:
        raise ValueError("no candidates")
    return out


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0
    if argv:
        print("usage: remote-catalog-parse.py [--help] < catalog.json", file=sys.stderr)
        return 2
    try:
        rows = candidates(json.load(sys.stdin))
    except (ValueError, TypeError, AttributeError):
        print(EMPTY_MSG, file=sys.stderr)
        return 1
    # JSON serialization escapes terminal control characters in metadata.
    print("\n".join(i + "\t" + json.dumps(m, ensure_ascii=True) for i, m in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
