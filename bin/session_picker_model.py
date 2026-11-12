#!/usr/bin/env python3
"""session_picker_model.py — the session picker's logic, with no terminal code.

Everything a screen shows and every key it answers come from ONE action table per screen
(`Screen.actions()`), so the label a user reads and the key that fires can never drift
apart, and `check_action_table` refuses a duplicate key. The Textual front end
(session_picker.py) only renders this model and forwards keys to it.

Navigation contract (docs/SESSION_PICKER.md):
  * HOME_OWNED  — reached from csl Home: shows `b` Back, never `q` Quit.
  * DIRECT_ROOT — started directly (csl local/remote, remote-session.sh, lowkey...):
                  shows `q` Quit, never a Back row.
  * `s` switches Local <-> Remote and keeps the owner; `l` / `r` jump to a lane; the key
    for the lane you are already in is an unlisted no-op.
  * Digits and uppercase letters are never shortcuts: a model is launched only by
    highlighting it and pressing Enter, so typing "11" can never launch model 1.
"""
from __future__ import annotations

import calendar
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

# Optional rich import for color swatches in theme config
try:
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    Text = None
    RICH_AVAILABLE = False

import session_menu_state as sms
import emoji_constants as ec
import cloud_session_env as cse
import model_names
import theme_colors as tc

# Session Launcher title - single source of truth
SESSION_LAUNCHER_TITLE = "Claude Code Free-Agents: Session Launcher"

HOME_OWNED = "home_owned"
DIRECT_ROOT = "direct_root"


@dataclass(frozen=True)
class Nav:
    target: str            # home | local | remote | rate_limiter
    owner: str = DIRECT_ROOT


QUIT = Nav("quit")
# A tool/settings screen opened FROM another screen (Home or a lane). Its nav row is
# "Back to <caller>", never Quit; the app keeps the caller stack and restores it.
SUB = "sub"
BACK = Nav("back")


def _drag_selects(start, end) -> bool:
    """0.50.0 F3: a long-click+drag of at least 2 characters (columns) selects the entry's
    text; a plain click (no drag) still activates the entry."""
    if start is None or end is None:
        return False
    return abs(end[0] - start[0]) + abs(end[1] - start[1]) >= 2


@dataclass
class Action:
    key: str
    label: str
    run: Callable[[], object]
    enabled: bool = True
    section: str = "settings"
    # Settings rows (toggle / multi-choice): step(+1|-1). Enter, click and Right call +1, Left
    # calls -1. None = a plain action (navigation, launch, tool) that only Enter/click runs.
    step: Callable[[int], object] | None = None
    # 0.55.4: the row's cycle metadata, for the per-row value column. `choices` is the raw
    # value set; `labels` the DISPLAY strings parallel to it (ON, not True). Empty tuples for
    # rows without them — the render then falls back to the current value's own width (safe
    # for rows whose label already self-pads, like the theme/lane rows).
    choices: tuple = ()
    labels: tuple = ()
    # 0.55.5: `head` is the explicit split point where the value begins (the classifier's
    # values contain ": " themselves, so the render's last-colon guess mangles them).
    # `value_prefix`/`value_suffix` are rich parts rendered OUTSIDE the cycle glyphs (the
    # theme accent rows: swatch, #hex and the border/shell slices — only the colour name
    # sits between the glyphs, user 2026-11-07).
    head: str | None = None
    value_prefix: object | None = None
    value_suffix: object | None = None


# The collapsed group rows in the lane pickers (user 2026-10-29/30): while folded, the
# rows render under these headers instead of as top-level rows. Key "0" never matches a
# letter hotkey, so it cannot collide with a real action. MODEL_GROUP holds the three
# model-setting rows (effort/temperature/thinking) — open by default, more users want
# those; SETTINGS_GROUP ("Advanced") holds the rest — collapsed by default. Nav rows
# always render last (user 2026-10-30).
SETTINGS_GROUP = Action("0", "Advanced", lambda: None, section="settings")
MODEL_GROUP = Action("0", "Model", lambda: None, section="model")


def setting(key: str, label: str, choices, get: Callable[[], object], put: Callable[[object], object],
            enabled: bool = True, section: str = "settings", labels=None, head=None,
            value_prefix=None, value_suffix=None) -> Action:
    """A row whose value cycles through `choices` in either direction (wraps). `labels` is the
    display string per choice, same order (0.55.4: sizes the row's value column — pass it
    whenever the display differs from str(choice), e.g. ON for True). `head` pins where the
    value starts inside the label; `value_prefix`/`value_suffix` are rich parts the render
    places outside the cycle glyphs (0.55.5)."""
    def step(d: int):
        try:
            i = list(choices).index(get())
        except ValueError:
            i = -1 if d > 0 else 0
        put(list(choices)[(i + d) % len(choices)])
    return Action(key, label, lambda: step(1), enabled=enabled, section=section, step=step,
                  choices=tuple(choices), labels=tuple(labels) if labels is not None else (),
                  head=head, value_prefix=value_prefix, value_suffix=value_suffix)


def check_action_table(actions) -> None:
    seen = {}
    for action in actions:
        if not (len(action.key) == 1 and action.key.islower() and action.key.isalpha()):
            raise ValueError(f"shortcut {action.key!r} must be one lowercase letter")
        if action.key in seen:
            raise ValueError(f"duplicate shortcut {action.key!r}: {seen[action.key]!r} and {action.label!r}")
        seen[action.key] = action.label


def thinking_constraint(provider: str, model_id: str, choice: bool | None) -> str | None:
    """The picker-side mirror of the launchers' thinking-constraint table (waypoint
    fa-thinking-toggle). Returns the notice to show when the remembered choice cannot be
    honored as-is, else None. Measured facts only (remote-session.sh _model_param_lines):
    GLM on NIM always runs its thinking template — chat_template_kwargs are inert
    (2026-10-13) — so OFF cannot disable it; reasoning_effort low (≈0 reasoning tokens,
    verified direct) is the closest the session gets. Nemotron honors enable_thinking both
    ways; Gemini is disabled unless asked; other families take the choice. None (no choice)
    never constrains: the default is ON and every model in the table runs with thinking."""
    if choice is False and (provider or "") == "nvidia" and "glm" in (model_id or "").lower():
        return ("GLM on NIM always runs its thinking template — the OFF choice cannot disable "
                "it. The session uses reasoning_effort low (≈0 reasoning tokens, measured "
                "2026-10-13); expect a short reasoning preamble.")
    return None


def thinking_row(s: Settings, key: str = "j", section: str = "model", get=None, put=None) -> Action:
    """The thinking toggle row: ON ↔ OFF. The row stores only the two states that differ
    behaviorally — None and True both launch with thinking ON and (0.55.5 user) read the
    same, plain "ON" — so cycling back from OFF returns to the launch default rather than
    to a second, indistinguishable ON. 0.56.5: get/put default to the shared
    thinking_session; lowkey passes its own lowkey_thinking pair (per-lane remembered value)."""
    if get is None:
        def get():
            return s.thinking_session
    if put is None:
        def put(v):
            s.thinking_session = v
            if s.on_thinking_saved:
                s.on_thinking_saved(s.thinking_session)

    def label():
        return "OFF" if get() is False else "ON"

    return setting(key, f"{ec.gap(ec.EMOJI_THINKING_STR)}Thinking: {label()}", (None, False),
                   get, put, section=section, labels=("ON", "OFF"))


def model_settings_rows(s: Settings, *, effort_get, effort_set, temp_get, temp_set,
                        think_get=None, think_set=None) -> list:
    """0.56.5 (user 2026-11-11): ONE template for the model-settings rows on every lane —
    same order (Effort, Temperature, Thinking), same hotkeys (e/o/j), same values
    (sms.EFFORT_TEMPLATE, TEMPERATURES, ON/OFF). Only the remembered value is per-lane,
    via the lane's own get/set callbacks."""
    return [
        setting("e", f"{ec.gap(ec.EMOJI_EFFORT_STR)}Effort: {_effort_label(effort_get())}",
                sms.EFFORT_TEMPLATE, effort_get, effort_set,
                labels=tuple(_effort_label(c) for c in sms.EFFORT_TEMPLATE), section="model"),
        setting("o", f"{ec.gap(ec.EMOJI_TEMPERATURE_STR)}Temperature: {temp_get() or 'not set'}",
                TEMPERATURES, temp_get, temp_set,
                labels=tuple("not set" if t == "" else t for t in TEMPERATURES), section="model"),
        thinking_row(s, get=think_get, put=think_set),
    ]


# --- data --------------------------------------------------------------------------------
@dataclass
class LocalModel:
    alias: str
    profile_id: str = ""      # unique profile identifier (for multiple profiles per alias)
    effort: str = ""
    roles: str = ""
    family: str = ""          # explicit family from registry metadata, when present
    backend: str = ""
    thinking: bool = False
    tool_parser: str = ""
    reasoning_parser: str = ""
    auto_scanned: bool = False   # on disk but not registered: selecting it offers registration
    folder: str = ""             # on-disk folder of an auto-scanned row (csl --inventory column 5)


@dataclass
class RemoteAgent:
    alias: str
    provider: str
    display: str
    tier: str
    local_capable: bool = False
    has_key: bool = True
    broken: bool = False      # listed in config/broken-nvidia-models.json
    model_id: str = ""        # provider model id (remote-session.sh --inventory column 8)
    old: bool = False         # catalog row older than the age cutoff (pinned rows: never)


@dataclass
class CatalogModel:
    """One row of a provider's live catalog (remote-session.sh --models)."""
    model_id: str
    tools: object = "unknown"     # True / "unknown" (False rows never reach the picker)
    pricing: object = "unknown"

    @classmethod
    def from_line(cls, line: str) -> "CatalogModel":
        ident, _, meta = line.partition("\t")
        try:
            data = json.loads(meta) if meta else {}
        except ValueError:
            data = {}
        return cls(ident, data.get("tools", "unknown"), data.get("pricing", "unknown"))


CATALOG_PREFIX = "cat:"     # catalog row ids: cat:<provider>:<model id>

# Loading animation frames — hourglass alternating between flowing sand (⏳) and done (⌛️).
# They live HERE (not in session_picker.py) because the per-group catalog placeholder renders
# in this module (0.50.8); the app aliases them so both sides share one source.
LOADING_FRAMES = [ec.EMOJI_LOADING_STR, ec.EMOJI_LOADING_DONE_STR]
LOADING_DOTS = (".  ", ".. ", "...")


def loading_label(tick: int, what: str = "loading") -> str:
    """Hourglass frame, then the label with 1-3 dots padded to a fixed width (nothing jumps)."""
    return f"{LOADING_FRAMES[(tick // 2) % len(LOADING_FRAMES)]} {what}{LOADING_DOTS[tick % len(LOADING_DOTS)]}"

# --- per-provider catalog cache (T2) ------------------------------------------------------
# The picker renders a provider's catalog from this cache instantly and revises it in the
# background once per session; a failed refresh keeps the cached list (never empties it).
CATALOG_CACHE_DIR_ENV = "LA_REMOTE_CATALOG_CACHE_DIR"


def catalog_cache_dir() -> Path:
    d = os.environ.get(CATALOG_CACHE_DIR_ENV)
    return Path(d) if d else Path.home() / ".local/state/local-agents/remote-catalogs"


def write_catalog_cache(provider: str, rows: list) -> None:
    """Persist one provider's catalog (file 0600, dir 0700, atomic replace). Best-effort:
    a cache failure must never fail the picker, so every error is swallowed."""
    try:
        d = catalog_cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        payload = {"provider": provider,
                   "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "rows": [[c.model_id, {"tools": c.tools, "pricing": c.pricing}] for c in rows]}
        p = d / f"{provider}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.chmod(tmp, 0o600)
        tmp.replace(p)
    except OSError:
        pass


def catalog_cache_fresh(provider: str, max_age_hours: float = 6.0) -> "list[CatalogModel] | None":
    """0.50.1: disk-cached rows when the cache file is fresh (LA_CATALOG_CACHE_MAX_AGE_H,
    default 6 h), else None. Lets the eager warm-up skip live catalog GETs when the cache
    is fresh — the per-session live refetch was the startup slowdown (69 s cold NVIDIA)."""
    rows = read_catalog_cache(provider)
    if not rows:
        return None
    try:
        age_h = (time.time() - (catalog_cache_dir() / f"{provider}.json").stat().st_mtime) / 3600.0
    except OSError:
        return None
    return rows if age_h <= max_age_hours else None


def read_catalog_cache(provider: str) -> "list[CatalogModel] | None":
    """Cached rows for a provider, or None when missing/corrupt — never raises. Rows whose
    id is not a string are dropped (a corrupt entry must not poison the whole file)."""
    try:
        data = json.loads((catalog_cache_dir() / f"{provider}.json").read_text())
    except (OSError, ValueError):
        return None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if isinstance(r, list) and r and isinstance(r[0], str):
            meta = r[1] if len(r) > 1 and isinstance(r[1], dict) else {}
            out.append(CatalogModel(r[0], meta.get("tools", "unknown"), meta.get("pricing", "unknown")))
    return out or None


# --- per-model lane (T3) ------------------------------------------------------------------
# native Anthropic (provider speaks /v1/messages AND this model accepted it), chat/completions
# (the translation lane), or undecided (provider has a native endpoint but the model was never
# probed). The verdicts are anthropic-route.py's; read through its own loader so the cache
# path, env override and format have exactly one definition.

_ROUTE_MOD = None
_ROUTE_CACHE_BY_PATH: dict = {}
_ANTHROPIC_BASE_PROVIDERS: "set | None" = None


def _route_module():
    global _ROUTE_MOD
    if _ROUTE_MOD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "anthropic_route", Path(__file__).resolve().parent / "anthropic-route.py")
        assert spec is not None and spec.loader is not None   # our own file always loads
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)     # stdlib-only module; defines cache_path/load_cache
        _ROUTE_MOD = mod
    return _ROUTE_MOD


def route_cache() -> dict:
    """The anthropic-route verdict cache, memoized per cache path (tests redirect the env
    var, so one path must never leak its data into another)."""
    try:
        mod = _route_module()
        path = Path(mod.cache_path()).resolve()
    except Exception:
        return {}
    if path not in _ROUTE_CACHE_BY_PATH:
        try:
            data = mod.load_cache()
        except Exception:
            data = {}
        _ROUTE_CACHE_BY_PATH[path] = data if isinstance(data, dict) else {}
    return _ROUTE_CACHE_BY_PATH[path]


def _anthropic_base_providers() -> set:
    global _ANTHROPIC_BASE_PROVIDERS
    if _ANTHROPIC_BASE_PROVIDERS is None:
        try:
            data = json.loads((Path(__file__).resolve().parent.parent
                               / "config" / "remote-registry.json").read_text())
            _ANTHROPIC_BASE_PROVIDERS = {
                pid for pid, e in (data.get("providers") or {}).items()
                if isinstance(e, dict) and e.get("anthropic_base")}
        except (OSError, ValueError):
            _ANTHROPIC_BASE_PROVIDERS = set()
    return _ANTHROPIC_BASE_PROVIDERS


def lane_for(provider: str, model_id: str) -> str:
    """"native Anthropic" | "chat/completions" | "undecided (not yet probed)". Providers
    without a native endpoint are chat/completions with no probe; everything else reads
    anthropic-route's cache."""
    if provider not in _anthropic_base_providers():
        return "chat/completions"
    hit = route_cache().get(f"{provider}|{model_id}")
    if isinstance(hit, dict) and hit.get("route") in ("anthropic", "chat"):
        return "native Anthropic" if hit["route"] == "anthropic" else "chat/completions"
    return "undecided (not yet probed)"


def lane_tag(provider: str, model_id: str) -> str:
    """Row suffix for a NON-default lane: "" for chat/completions AND for catalog rows
    (user 2026-10-29: "· catalog" on every catalog row is redundant noise — everything in
    that list is catalog by construction), so only genuinely non-default lanes stand out."""
    lane = lane_for(provider, model_id)
    return "" if lane in ("chat/completions", "catalog") else f"  · {lane}"


# --- model age (T6) ------------------------------------------------------------------------
# The user 2026-10-20: "some are quite old, we should have an additional filter set up for
# that." Catalog rows older than the cutoff hide until the picker toggle (or --show-old)
# shows them. The date is Hugging Face's createdAt, cached alongside the parameter count
# by the footprint estimator; a row with no date available stays VISIBLE (fail-open), and
# pinned registry rows — which have no dates — are never age-checked.
DEFAULT_MODEL_AGE_MONTHS = 18
MAX_MODEL_AGE_MONTHS_ENV = "LA_REMOTE_MAX_MODEL_AGE_MONTHS"


def max_model_age_months() -> int:
    """The age cutoff in months: LA_REMOTE_MAX_MODEL_AGE_MONTHS, default 18. An unreadable
    or non-positive value falls back to the default rather than disabling the filter."""
    try:
        v = int(os.environ.get(MAX_MODEL_AGE_MONTHS_ENV, DEFAULT_MODEL_AGE_MONTHS))
    except ValueError:
        return DEFAULT_MODEL_AGE_MONTHS
    return v if v > 0 else DEFAULT_MODEL_AGE_MONTHS


def age_cutoff_date(months: int, today=None):
    """`months` calendar months before `today` (the day clamped to the shorter month)."""
    t = today or datetime.date.today()
    y, m = t.year, t.month - int(months)
    y += (m - 1) // 12                       # floor division walks back over year ends
    m = (m - 1) % 12 + 1
    return datetime.date(y, m, min(t.day, calendar.monthrange(y, m)[1]))


def created_before_cutoff(created: str, months: int, today=None) -> bool:
    """True when a Hugging Face createdAt ISO stamp predates the cutoff. Anything
    unreadable (missing, empty, malformed) is NOT old: the filter must never hide what
    it cannot date."""
    try:
        d = datetime.date.fromisoformat(str(created)[:10])
    except (TypeError, ValueError):
        return False
    return d < age_cutoff_date(months, today)


def _model_identity(alias: str) -> str:
    """Alias with role/thinking suffixes stripped: qwen-3.8-operator / qwen-3.8-thinking /
    qwen-3.8 all map to qwen-3.8 (user 2026-10-30 — one row per model, toggle owns thinking)."""
    import re
    return re.sub(r"-(thinking|operator|reasoner|validator|utility)$", "", alias or "")


def status_line(sm: "Screen") -> str:
    """The nav-instructions line at the very bottom of EVERY screen — single source of
    truth (user 2026-10-30); pinned by a fixture test so it cannot drift per screen.

    0.54.0 (D5): the copy hint is terminal-honest. Apple Terminal never forwards ⌘C and
    ignores OSC 52, so there the line says "highlight text to copy it" (copy-on-select);
    ⌘-forwarding terminals (kitty keyboard protocol: kitty/Ghostty/WezTerm/iTerm2-CSI-u)
    also get the ⌘C note. The ⌘R (mouse-reporting) route is NOT taught."""
    import os
    term_program = (os.environ.get("TERM_PROGRAM") or "").lower()
    term = (os.environ.get("TERM") or "").lower()
    if term_program == "apple_terminal" or "apple_terminal" in term:
        return "  ↑↓ move · Enter/click select · ←→ change setting · highlight text to copy it"
    kitty = any(t in term or t in term_program for t in ("kitty", "ghostty", "wezterm", "iterm"))
    if kitty:
        return ("  ↑↓ move · Enter/click select · ←→ change setting · highlight text to copy it"
                " · ⌘C copies the highlighted row")
    return "  ↑↓ move · Enter/click select · ←→ change setting · drag selects text, ⌘C/Ctrl+C copies"


def matches_query(q: str, model_id: str, display: str = "") -> bool:
    """0.54.0: the ONE live-filter predicate (casefolded substring on id or display), shared by
    every model list screen — Remote/Local delegate here, Lowkey (D3) uses it too."""
    q = (q or "").casefold()
    return q in (model_id or "").casefold() or q in (display or "").casefold()


def catalog_param_size(settings, prov: str, model_id: str) -> "float | None":
    """0.54.0: the one parameter-count lookup for row labels and the size sort
    (Settings.model_param_count, set by the picker); None when unknown. RemoteScreen and
    LowkeyScreen both delegate here so the two lists can never sort differently."""
    fn = getattr(settings, "model_param_count", None)
    if fn is None:
        return None
    try:
        return fn(prov, model_id)
    except Exception:
        return None


def param_size_suffix(settings, prov: str, model_id: str, display: str) -> str:
    """0.54.0: '  · 1.05T' appended when the size is known and the name does not already state
    it (word-boundary match, shared by Remote and Lowkey rows)."""
    label = format_param_count(catalog_param_size(settings, prov, model_id))
    if not label or re.search(r"\b" + re.escape(label) + r"\b", display or "", re.IGNORECASE):
        return ""
    return f"  · {label}"


def size_sorted(rows, size_of):
    """Rows largest-first (user 2026-10-20: parameter size is a rough capability signal).

    Rows with a parameter count sort by it descending; rows with none sort after ALL
    sized rows, in their incoming order. The sort is stable, so equal sizes keep the
    list's own order — and it is display-order only: ids, launch argv and dedup logic
    are untouched."""
    def key(r):
        b = size_of(r)
        return (1, 0.0) if b is None else (0, -b)
    return sorted(rows, key=key)


def format_param_count(b) -> str:
    """Human-readable parameter count for row labels: 1.05T / 52B / 8.5B. "" when unknown."""
    if b is None:
        return ""
    try:
        b = float(b)
    except (TypeError, ValueError):
        return ""
    if b >= 1000:
        return f"{f'{b / 1000:.2f}'.rstrip('0').rstrip('.')}T"
    return f"{f'{b:.1f}'.rstrip('0').rstrip('.')}B"


# Order matters: identity markers first, so "deepseek-r1-distill-qwen" is DeepSeek (the
# model's identity), not Qwen (its base architecture).
_FAMILY_RULES = [
    ("DeepSeek", r"deepseek"),
    ("Mistral", r"mistral|codestral|devstral|magistral|ministral|mixtral"),
    # Nvidia BEFORE Qwen: "qwen-agentworld-4bit" is an agentworld (Nvidia) model on a
    # Qwen base — identity wins over architecture, same principle as DeepSeek distills.
    ("Nvidia", r"nemotron|minitron|agentworld"),
    ("Qwen", r"qwen|qwq"),
    ("Google Gemma", r"gemma"),
    ("Llama", r"llama"),
    ("GLM", r"glm"),
    ("Moonshot Kimi", r"kimi"),
    ("Phi", r"\bphi"),
    ("gpt-oss", r"gpt-oss"),
    ("Ornith", r"ornith"),
    ("Kat Coder", r"kat[-_ ]|\bkat\b"),
    ("Meta", r"muse|glimmer"),
    ("Granite", r"granite"),
    # Poolside Laguna AFTER the vendor rules above: "laguna" collides with nothing, but
    # the two variants (XS 33B, S 118B MoE) belong in ONE group (user 2026-10-15).
    ("Poolside Laguna", r"laguna"),
]


def _norm_token(s: str) -> str:
    """Lowercase alphanumerics only — the inventory's auto-scanned rows carry the
    DISPLAY name as their family (`granite-4.2-3b-MLX-4bit`) while the alias is the
    sanitized id (`granite-4-2-3b-MLX-4bit`); separator-identical strings are the
    same name, not grouping information."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def family_for(alias: str, explicit: str = "") -> str:
    # An explicit family that names the model itself is what `csl --inventory` emits
    # for its auto-scanned rows — it carries no grouping information, and honoring it
    # fragmented every rapid-discovered model into a one-model group (measured on the
    # live inventory 2026-10-13: "Qwen3.8-27B-MTP-4bit", "kat-v2.5-*", the Gemma and
    # Muse rows). "auto" is the same story from the registered profiles. All of them
    # mean "derive from the alias" (0.34.0).
    if explicit and explicit != "auto" and _norm_token(explicit) != _norm_token(alias):
        return explicit
    name = alias.lower()
    for family, pattern in _FAMILY_RULES:
        if re.search(pattern, name):
            return family
    return "Other"


PROVIDER_LABELS = {"nvidia": "NVIDIA", "gemini": "Gemini", "groq": "Groq",
                   "openrouter": "OpenRouter", "cloudflare": "Cloudflare", "cerebras": "Cerebras",
                   "mistral": "Mistral", "zai": "Z.ai", "siliconflow": "SiliconFlow",
                   "llm7": "LLM7", "kilo": "Kilo", "vercel": "Vercel", "sambanova": "SambaNova",
                   "modelscope": "ModelScope", "streamlake": "Streamlake",
                   "huggingface": "Hugging Face"}

# Provider-wide notes for every model's info box (catalog rows included), after the model note.
# Gemini (user 2026-10-29): the free daily quota cannot carry a full session — loading one eats
# most of it — so it is worth using only for stateless offload dispatches.
PROVIDER_ADVISORIES = {
    "gemini": "Recommended for offload dispatch only: the free daily quota is too small for full sessions.",
}

# Group-hover infobox texts (user 2026-10-30): provider-level facts live on the GROUP row,
# not inline with every model. Per-model NVIDIA rows get the short version appended.
NVIDIA_GROUP_NOTE = ("NVIDIA API key currently holds no daily cap — the only limitation is "
                     "40 requests per minute, but NO known daily maximum quota (yes, you read "
                     "that right). Note: on some days a specific model can be throttled or "
                     "blocked for a while when server load is very high; usually resolves after "
                     "24-48 hours. It never blocks the whole API key — you can always choose "
                     "another model if one is not reachable.")
NVIDIA_MODEL_QUOTA_SHORT = ("NVIDIA key: no daily cap, 40 req/min only; a model can be throttled "
                            "under high load for 24-48h, but the key is never blocked — pick "
                            "another model.")
PROVIDER_INFOS = {
    "nvidia": NVIDIA_GROUP_NOTE,
    "gemini": ("Gemini's free tier is recommended for offload dispatch only: its free daily "
               "quota is too small to carry a full session — a session's first prompt alone "
               "eats much of it. Every Gemini model's info box says the same."),
    "google": ("Google AI Studio free tier: recommended for offload dispatch only — the free "
               "daily quota is too small for full sessions."),
}


_MODEL_NOTES_PATH = Path(__file__).resolve().parent.parent / "config" / "remote-model-notes.json"


def model_note(alias: str, path: Path | None = None) -> str:
    """Short-form note for the remote info box, or "" when none is written. A "-thinking"
    variant shares its base model's note (same weights, different mode). A missing or
    malformed file yields "" — the box is an aid, never a reason the picker fails."""
    import json
    try:
        notes = json.loads((path or _MODEL_NOTES_PATH).read_text())
    except (OSError, ValueError):
        return ""
    note = notes.get(alias) or notes.get(alias.removesuffix("-thinking"))
    return note if isinstance(note, str) else ""


def strip_leading_name(display: str, note: str) -> str:
    """0.49.3 (H4): when the note repeats the display name at its start, drop the repetition —
    the infobox already prints the name on line 1 (Le Chonk's note did; user 2026-10-31)."""
    if display and note.lower().startswith(display.lower()):
        return note[len(display):].lstrip(" \t-—:,")
    return note


def tier_label(tier: str, provider: str) -> str:
    # Quota facts moved to the infobox (user 2026-10-30) — inline row tags are noise.
    if tier == "trial":
        return "trial — possible cost"
    return "" if tier == "unknown" else tier


# --- settings ----------------------------------------------------------------------------
AUTO_MODE_LABELS = {0: "blind-trust", 1: "classifier", 2: "off"}


def _default_local_classifier_alias() -> str:
    """The alias bin/local-classifier-default.sh would pick right now. Static
    fallback if the helper is missing or nothing is installed — the helper's own
    launch-time warning covers the nothing-installed case, so the label never
    names a model the machinery would not select."""
    import os
    import subprocess
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "local-classifier-default.sh")
    try:
        r = subprocess.run(["bash", helper, "--alias"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip() or "granite-4.2-30b"
    except Exception:
        pass
    return "granite-4.2-30b"


_DEFAULT_CLASSIFIER_ALIAS = _default_local_classifier_alias()

CLASSIFIER_SOURCE_LABELS = {
    0: f"NVIDIA API (fallback: {_DEFAULT_CLASSIFIER_ALIAS})",
    1: f"Local {_DEFAULT_CLASSIFIER_ALIAS} (always)",
    2: "Auto: local on local, remote on remote",
}

# Cloud-screen options that no cloud (Anthropic or gateway) session reads (audit 2026-10-08). Said
# in the menu, so a toggle cannot look as if it changed the cloud session it sits beside.
NOT_GATEWAY_NOTE = "[local/free-API only — not cloud]"


@dataclass
class Settings:
    local_effort: str = sms.DEFAULT_EFFORT["local_session"]
    remote_effort: str = sms.DEFAULT_EFFORT["remote_api_session"]
    auto_mode: int = 0
    telemetry: bool = False
    stop_hook: bool = False          # queued-prompt hook OFF by default (user, 0.22.3)
    watcher: bool = False
    enable_mcp: bool = True   # default ON (user 2026-10-15): new users missing MCPs they never disabled is worse than the reverse
    include_trials: bool = True      # interactive visibility only; direct CLI keeps its guard
    local_capable_shown: bool = False
    show_broken: bool = False        # unworking models; in-memory, like the CLI flag
    show_old: bool = False           # models over the age cutoff; in-memory, like the CLI flag
                                     # (--show-old seeds it; the picker filter toggle re-reveals)
    # One-shot hardware detection (bin/la-hw-detect.sh) wired in by the picker at startup;
    # the platform-aware subheadlines read these. None/"" = not detected, fall back to
    # platform.system() + /etc/os-release (user 2026-10-29).
    hw_os: "str | None" = None
    hw_distro: str = ""
    hw_hardware: "str | None" = None
    hw_ram_gb: "float | None" = None
    hw_gpu: str = ""
    deny_gh_release: bool = False    # re-add "Bash(gh release create:*)" to the blind-trust deny list (off since 0.32.0)
    remote_temperature: str = sms.DEFAULT_TEMPERATURE["remote_api_session"]
    local_temperature: str = sms.DEFAULT_TEMPERATURE["local_session"]
    lowkey_effort: str = sms.DEFAULT_EFFORT["lowkey"]
    lowkey_temperature: str = sms.DEFAULT_TEMPERATURE["lowkey"]
    lowkey_thinking: "bool | None" = None   # Lowkey's own thinking toggle (user 2026-10-30)
    # 0.54.0 (D3): opt-in — apply the Remote screen's filters to lowkey's API list. Persisted
    # via on_lowkey_filters_saved -> sms.save_lowkey_filters (schema 9 lowkey_filters section).
    lowkey_remote_filters: bool = False
    stats: dict = field(default_factory=dict)  # About-screen usage stats, gathered at picker start
    # Session thinking toggle (both lanes; waypoint fa-thinking-toggle). None = the launch
    # default, which is thinking ON for session launches; True/False = a remembered choice the
    # launchers apply via LA_THINKING_OVERRIDE (model constraints may override it, and the info
    # box says so). Persisted by the Picker through on_thinking_saved -> sms.save_thinking_session.
    thinking_session: bool | None = None
    rate_limiter: dict = field(default_factory=dict)
    # 0.50.0 (api-keys-provider-hide-toggle): provider slugs hidden from the remote
    # choose-model list; persisted via on_hidden_providers_saved. Keys stay on disk.
    hidden_providers: list = field(default_factory=list)
    memguard: dict = field(default_factory=lambda: dict(sms.DEFAULT_MEMGUARD))
    # 0.55.8 Task 8 (0541 plan Item G): the auto-sync setting lives HERE now, not in memguard.
    local_registry: dict = field(default_factory=lambda: {"auto_sync": False})
    runtime_backend: str = "rapid-mlx"   # backend manager selection; in-memory only
    # Classifier source for auto mode (0=NVIDIA API w/ fallback, 1=local default classifier always, 2=Auto)
    classifier_source: int = 2
    # Cloud sessions only: replace paid Sonnet subagents with free agents. None = undecided (the
    # hooks/cloud-agent-intercept.py hook asks once), "1" = free agents, "0" = native Sonnet. Kept in
    # Per-path routing modes (0.43.0): the legacy FA_REPLACE_AGENTS env key is mapped by
    # cloud_session_env.route(); free sessions (local, free-API) have no toggle: their
    # subagents already run on the free endpoint.
    # Bypass permissions mode for cloud sessions (blind-trust auto mode)
    cloud_bypass_permissions: bool = False
    # security-guidance LLM reviews in every cloud session: 0=default (opus-4-7), 1=sonnet-4-6,
    # 2=off, -1=custom model set by hand. Kept in ~/.claude/settings.json env (cloud_session_env).
    security_review: int = 0
    security_review_custom: str | None = None
    # Shown only when the plugin is installed+enabled, or an override of ours is still set.
    routes: dict = field(default_factory=dict)   # {"agent": mode, ...} — the per-path routing modes
    # The A2 default permission mode for sessions the user starts themselves (user settings).
    default_mode: str | None = None
    # A3: the cached eligibility verdict {"verdict", "evidence", "at"}; cost box only for not-eligible.
    eligibility: dict | None = None
    security_review_offered: bool = False
    # Called with {"security_review": n} or {"route": (path, mode)} when the user changes it; persists it.
    on_cloud_saved: Callable[[dict], object] | None = None
    # 0.55.8 Task 8: the auto-sync row persists through its OWN callback now (the setting
    # moved to the local_registry section; it was never a memory-guard setting).
    on_local_registry_saved: Callable[[dict], object] | None = None
    # Called with (lane, value) when the user CONFIRMS an effort change; persists it.
    on_effort_saved: Callable[[str, str], object] | None = None
    on_hidden_providers_saved: Callable[[list], object] | None = None
    # Called with (key, bool) when a lowkey picker toggle changes; persists it (0.54.0).
    on_lowkey_filters_saved: Callable[[str, bool], object] | None = None
    # 0.51.0: transient visible confirmation (toast) when a screen saves state — e.g. the
    # hide/show toggle. Wired to the app's notify; looked up at CALL time so tests can spy.
    on_flash: Callable[[str], object] | None = None
    # Called with (lane, value) when the user CONFIRMS a temperature change; persists it.
    on_temperature_saved: Callable[[str, str], object] | None = None
    # Called with (bool | None) when the user changes the session thinking toggle; persists it.
    on_thinking_saved: Callable[[bool | None], object] | None = None
    # Last launched model per lane (for R15: remember last launched)
    last_launched_model: dict = field(default_factory=lambda: {
        "local_session": None,
        "remote_api_session": None,
        "lowkey": None,
    })
    last_launched_mode: dict = field(default_factory=dict)   # 0.56.13: "<lane>:mode" keys from the store (lowkey convo/oneshot)
    # Remote tier of the last-launched alias (lets Go-last add --include-trials with no inventory).
    last_launched_tier: dict = field(default_factory=dict)
    # Called with (lane, alias, tier|None) when a launch request is made; persists it.
    on_last_launched: Callable[[str, str, str | None, str | None], object] | None = None
    # (provider, model id) -> True when a live-catalog row is local-capable (hidden by the filter).
    catalog_local_capable: Callable[[str, str], bool] | None = None
    # (provider, model id) -> parameter count in billions for the per-provider size sort, or
    # None when unknown (the row then sorts after the sized ones). Set by the picker; absent
    # (plain Settings()) means no data, and nothing sorts.
    model_param_count: Callable[[str, str], "float | None"] | None = None
    # (provider, model id) -> True when a live-catalog row predates the age cutoff (hidden
    # unless show_old). Set by the picker; absent means no dates known, so nothing hides.
    catalog_model_old: Callable[[str, str], bool] | None = None
    notes: list = field(default_factory=list)


# Same presets as main's bash menu (0.21.7). "" = leave it to the provider.
TEMPERATURES = ("", "0.0", "0.3", "0.7", "1.0", "1.5", "2.0")

def _load_key_providers():
    """The provider table of install/setup-api-keys.py — the ONE list of providers a key can be set
    up for. The picker used to keep two hand-copied tables, and a provider added to the wizard
    (Streamlake, 0.27.1) never reached them. Loaded by path because the file name is hyphenated."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "install" / "setup-api-keys.py"
    spec = importlib.util.spec_from_file_location("setup_api_keys", path)
    assert spec is not None and spec.loader is not None   # our own file always loads
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PROVIDERS


KEY_PROVIDERS = _load_key_providers()


def _registry_provider_note(slug: str) -> str:
    """config/remote-registry.json's notes for a key-setup slug ('' if none). The two slug sets
    differ only for vercel/huggingface key files, which share the provider id."""
    import json
    from pathlib import Path
    try:
        data = json.loads((Path(__file__).resolve().parent.parent / "config" / "remote-registry.json").read_text())
        return str(data.get("providers", {}).get(slug, {}).get("notes") or "").strip()
    except (OSError, ValueError):
        return ""


def _key_provider_order():
    """NVIDIA, Gemini, Groq first (the proven and most-used), then the rest alphabetically."""
    first = ("nvidia", "gemini", "groq")
    return sorted(KEY_PROVIDERS, key=lambda p: (first.index(p.slug) if p.slug in first else len(first),
                                                p.name.lower()))
PROVIDER_SIGNUP_URLS = {p.slug: p.url for p in KEY_PROVIDERS}


def api_keys_dir():
    import os
    from pathlib import Path
    return Path(os.environ.get("LA_API_KEYS_DIR", os.path.expanduser("~/.api_keys")))


def provider_has_key(slug: str) -> bool:
    """True when every key file this provider needs is saved. Presence only, never the value.
    A provider the wizard does not know (none today) falls back to a file named after the slug."""
    files = next((p.files for p in KEY_PROVIDERS if p.slug == slug), (slug,))
    d = api_keys_dir()
    return all((d / f).exists() for f in files)


def open_url(url: str) -> None:
    """Open url in the browser without blocking. LA_URL_OPENER overrides the macOS `open`
    command (tests point it at a recorder so no real browser window appears)."""
    import os
    opener = os.environ.get("LA_URL_OPENER") or "open"
    subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def effort_choices(lane: str) -> tuple:
    return sms.ALLOWED_EFFORT[lane]


def _next(choices, current, d: int = 1):
    """The choice `d` steps away from `current`, wrapping (0.55.6: d=-1 steps backward;
    the default keeps every existing 2-arg caller on the forward step)."""
    try:
        return choices[(choices.index(current) + d) % len(choices)]
    except ValueError:
        return choices[0]


def _effort_label(value: str) -> str:
    return "not set" if value == sms.PROVIDER_DEFAULT else value


# --- accordion ---------------------------------------------------------------------------
@dataclass
class Item:
    id: str
    label: str
    style: str = ""     # a Rich style for the whole row ("red" = registered, weights missing)


@dataclass
class Group:
    id: str
    label: str
    items: list
    style: str = ""     # a Rich style for the header; a styled empty group is an ACTION row


class Accordion:
    """Groups with at most one open; selection is by stable item id, never by row index.

    The one exception (T7, user 2026-10-20): while a model-name search is active the owner
    sets multi_open, and every group in it is open at once — a model several providers
    offer shows under each. None (the default) is the ordinary one-open accordion."""

    def __init__(self):
        self.groups: list[Group] = []
        self.open_group: str | None = None
        self.selected_id: str | None = None
        self.multi_open: set | None = None
        # In multi-open (search) mode: the groups the USER folded. A regroup recomputes
        # multi_open from this — never from what happened to be open last time.
        self.multi_open_folded: set = set()
        # 0.52.0: one nesting level (a Group inside a group's items — lowkey's "Local models"
        # wrapper). child group id -> parent group id; is_open() walks up so opening a nested
        # group also opens its ancestors.
        self._parents: dict = {}

    def is_open(self, group_id: str) -> bool:
        if self.multi_open is not None:
            return group_id in self.multi_open
        cur = self.open_group
        while cur is not None:
            if cur == group_id:
                return True
            cur = self._parents.get(cur)
        return False

    def set_groups(self, groups: list[Group]):
        self.groups = groups
        self._parents = {}
        for g in groups:
            for i in g.items:
                if isinstance(i, Group):
                    self._parents[i.id] = g.id
        ids = {g.id for g in groups} | set(self._parents)
        if self.open_group not in ids:
            self.open_group = None
        if self.selected_id and self._group_of(self.selected_id) is None:
            self.selected_id = None
        if self.selected_id:
            self.open_group = self._group_of(self.selected_id)
        if self.open_group is None and groups:
            # The first group that HAS rows: an action header (e.g. "Register new…") opens nothing.
            self.open_group = next((g.id for g in groups if g.items), groups[0].id)

    def _group_of(self, item_id):
        for g in self.groups:
            for i in g.items:
                if isinstance(i, Group):
                    if any(x.id == item_id for x in i.items):
                        return i.id
                elif i.id == item_id:
                    return g.id
        return None

    def expand(self, group_id: str) -> bool:
        all_ids = {g.id for g in self.groups} | set(self._parents)
        if group_id in all_ids:
            self.open_group = group_id
            if self.multi_open is not None:
                self.multi_open.add(group_id)
                self.multi_open_folded.discard(group_id)
            return True
        return False

    def collapse(self, group_id: str | None = None):
        """Close the open group; in multi-open mode close only `group_id` (all when None)."""
        if self.multi_open is not None:
            if group_id is None:
                self.multi_open.clear()
                self.multi_open_folded |= {g.id for g in self.groups}
            else:
                self.multi_open.discard(group_id)
                self.multi_open_folded.add(group_id)
            if group_id is None or group_id == self.open_group:
                self.open_group = None
            return
        self.open_group = None

    def select(self, item_id: str) -> bool:
        group = self._group_of(item_id)
        if group is None:
            return False
        self.open_group = group
        if self.multi_open is not None:
            self.multi_open.add(group)
        self.selected_id = item_id
        return True

    def rows(self):
        """Flattened visible rows: ('group', Group) headers and ('item', Item) for open ones.
        0.52.0: a Group nested in another group's items renders as a third-level header."""
        out = []
        for g in self.groups:
            out.append(("group", g))
            if self.is_open(g.id):
                for i in g.items:
                    if isinstance(i, Group):
                        out.append(("group", i))
                        if self.is_open(i.id):
                            out.extend(("item", x) for x in i.items)
                    else:
                        out.append(("item", i))
        return out


# --- screens -----------------------------------------------------------------------------
class Screen:
    title = ""
    lane_key = ""
    # 0.55.6 (user): when True, the render pads every stepper row's head to the widest head
    # and shares ONE value-cell width across the screen's cycle rows, so ❮ and ❯ each sit in
    # one column (the theme menu's four rows read as a single centered column). Other
    # screens keep the 0.55.4 per-row widths.
    align_cycle_values = False
    # Lane pickers fold their settings rows under one group header (user 2026-10-29,
    # waypoint picker-collapsed-settings-group); every other screen renders plain rows.
    settings_collapsed = True    # the "Advanced" group starts folded (user 2026-10-29)
    model_collapsed = True       # the "Model settings" group also starts folded (user 2026-10-30)
    # Rendered in its own colour below the policy line when a screen sets it (Home does).
    version_line = ""

    def toggle_settings(self):
        self.settings_collapsed = not self.settings_collapsed

    def toggle_model_group(self):
        self.model_collapsed = not self.model_collapsed

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        self.settings = settings
        self.owner = owner
        self.accordion = Accordion()

    def groups(self):
        return self.accordion.groups

    back_label = "Back"

    def nav_actions(self):
        acts = []
        if self.owner == SUB:
            acts.append(Action("b", self.back_label, lambda: BACK, section="nav"))
        elif self.owner == HOME_OWNED:
            acts.append(Action("b", f"{ec.EMOJI_HOME_STR} Back to Home", lambda: Nav("home", HOME_OWNED), section="nav"))
        else:
            # user 2026-11-09 (ported from the GUI): the quit entry carries the ⏻ icon
            acts.append(Action("q", f"{ec.gap(ec.quit_label())}Quit", lambda: QUIT, section="nav"))
        return acts

    def actions(self):
        raise NotImplementedError

    def handle_key(self, key: str):
        if key == "escape":
            if self.owner == SUB:
                return BACK
            return Nav("home", HOME_OWNED) if self.owner == HOME_OWNED else QUIT
        for action in self.actions():
            if action.key == key:
                if not action.enabled:
                    return None
                return action.run()
        return None

    def activate_selected(self) -> LaunchRequest | Nav | None:
        return None


def _toggle(s, attr):
    return (False, True), (lambda: getattr(s, attr)), (lambda v: setattr(s, attr, v))


def _common_toggles(s: Settings, include_watcher: bool):
    acts = [
        setting("a", f"{ec.EMOJI_AUTO_MODE_STR} Auto-mode: {AUTO_MODE_LABELS[s.auto_mode]}",
                (0, 1, 2), lambda: s.auto_mode, lambda v: setattr(s, "auto_mode", v),
                labels=tuple(AUTO_MODE_LABELS[c] for c in (0, 1, 2))),
    ]
    # Classifier source only relevant when auto_mode == 1 (classifier), not blind-trust or off
    if s.auto_mode == 1:
        acts.append(setting("z", f"Classifier: {CLASSIFIER_SOURCE_LABELS[s.classifier_source]}",
                (0, 1, 2), lambda: s.classifier_source, lambda v: setattr(s, "classifier_source", v),
                labels=tuple(CLASSIFIER_SOURCE_LABELS[c] for c in (0, 1, 2)),
                head="Classifier: "))   # the values contain ": " — split at the FIRST colon
    acts += [
        # Label says what it actually blocks (user 2026-10-30): LA_TELEMETRY=0 exports
        # CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 — stock Claude Code reporting off.
        setting("t", f"{ec.gap(ec.EMOJI_TELEMETRY_ON_STR)}optional Anthropic Telemetry calls: {'ON' if s.telemetry else 'OFF'}", *_toggle(s, "telemetry"), labels=("OFF", "ON")),
        setting("p", f"{ec.gap(ec.EMOJI_STOP_HOOK_STR)}Queued-prompt hook: {'ON' if s.stop_hook else 'OFF'}",
                *_toggle(s, "stop_hook"), labels=("OFF", "ON")),
        # Key 'd': 'g' is the dynamic Go-launch key and 'h' belongs to the remote filter
        # submenu (FilterSubmenuTests) — keep this row off both.
        setting("d", f"{ec.gap(ec.EMOJI_BLOCK_STR)}Always block Github releases in \"blind-trust\" mode: {'ON' if s.deny_gh_release else 'OFF'}",
                *_toggle(s, "deny_gh_release"), labels=("OFF", "ON")),
    ]
    if include_watcher:
        acts.append(setting("w", f"{ec.EMOJI_WATCHER_STR} Watcher: {'ON' if s.watcher else 'OFF'}",
                            *_toggle(s, "watcher"), labels=("OFF", "ON")))
    return acts


# User-facing name of the memory guard (user 2026-10-23): "memory guard" read like memory.md /
# auto-memory. Internal keys stay "memguard" / LA_MEMGUARD_* so saved settings keep working.
OOM_PROTECTION_NAME = "RAM usage out-of-memory protection"


def _tool_actions(backend: bool = True, local_session: bool = False, home: bool = False,
                  settings_keys: tuple = (), owner: str = DIRECT_ROOT):
    # settings_keys (user 2026-10-30): on lane screens these keys are SETTINGS and fold into
    # the "Advanced" group — k/n on Remote, u/v on Local.
    def sec(k):
        return "settings" if k in settings_keys else "tools"
    # The backend manager drives LOCAL runtimes (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM),
    # so the Remote lane does not offer it (user, 2026-10-04).
    # Local session picker (local_session=True) hides API keys and NVIDIA rate limiter.
    # Home screen (home=True) shows only API keys and Backend manager, no NVIDIA rate limiter.
    acts = [
        Action("v", f"{ec.EMOJI_TOOLS_STR} Backend manager (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM)",
               lambda: Nav("runtime_manager"), section=sec("v")),
    ]
    if local_session:
        # It protects LOCAL sessions only, so only the Local screen offers it — not Remote, and not
        # Home either, where it read as a global setting (user 2026-10-23).
        acts.append(Action("u", f"{ec.gap(ec.EMOJI_MEMORY_GUARD_STR)}{OOM_PROTECTION_NAME}", lambda: Nav("memguard"),
                           section=sec("u")))
    if not local_session:
        # 0.53.1: carry the owner so api keys opened from the csl home is HOME_OWNED (gets the
        # trailing Back-to-Home row); a bare Nav("api_keys") defaulted to DIRECT_ROOT (Quit).
        acts.insert(0, Action("k", f"{ec.EMOJI_KEY_STR} API keys — install / set up", lambda: Nav("api_keys", owner), section=sec("k")))
        if not home:
            acts.append(Action("n", f"{ec.EMOJI_NVIDIA_RATE_LIMITER_STR} NVIDIA rate limiter", lambda: Nav("rate_limiter"), section=sec("n")))
    return acts if backend else [a for a in acts if a.key != "v"]


_PLUGIN_VERSION = None


def plugin_version() -> str:
    """The running plugin's version, read from its manifest (never from the install path).
    Cached per process; "?" when the manifest is missing or unreadable — a version is
    never invented (user request 2026-10-28: csl had no way to show which version it ran)."""
    global _PLUGIN_VERSION
    if _PLUGIN_VERSION is None:
        try:
            data = json.loads((Path(__file__).resolve().parent.parent / ".claude-plugin/plugin.json").read_text())
            _PLUGIN_VERSION = str(data.get("version") or "?")
        except Exception:
            _PLUGIN_VERSION = "?"
    return _PLUGIN_VERSION


def _read_distro(os_release_path: str = "/etc/os-release") -> str:
    """PRETTY_NAME from os-release, or "" — never raises (missing/unreadable file is not
    an error, the caller falls back to the generic Linux wording)."""
    try:
        with open(os_release_path, encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("PRETTY_NAME="):
                    return ln.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _identity_subheadline(system: "str | None" = None, distro: "str | None" = None,
                          os_release_path: str = "/etc/os-release") -> str:
    """Platform-aware home identity line (user 2026-10-29): Linux users get the CUDA line,
    naming the distro. `system`/`distro` come from the canonical detector (la-hw-detect.sh)
    via Settings.hw_os/hw_distro when available; otherwise platform.system() + os-release.
    Never raises; non-Linux platforms keep the Mac wording."""
    if system is None:
        try:
            import platform
            system = platform.system()
        except Exception:
            system = "Darwin" if sys.platform == "darwin" else ""
    if (system or "").lower() == "linux":
        if not distro:
            distro = _read_distro(os_release_path) or "Linux"
        return (f"Claude Code on free inference — local CUDA models on this {distro} PC, "
                "or free cloud APIs")
    return "Claude Code on free inference — local MLX models on this Mac, or free cloud APIs"


def _local_backend_word(hw: "str | None" = None, system: "str | None" = None) -> str:
    """The local lane's backend word: LA_HARDWARE when the canonical detector supplied it
    (mlx|cuda|rocm|opencl|cpu-only), else MLX on macOS / CUDA elsewhere."""
    if hw:
        return "MLX" if hw == "mlx" else hw.upper()
    if system is None:
        system = sys.platform
    return "MLX" if system in ("darwin", "Darwin") else "CUDA"


class AboutScreen(Screen):
    """Version and the paths that matter when something misbehaves — read-only facts."""

    # The emoji comes from the canonical file (bin/emoji_constants.py), user 2026-10-29.
    # gap(): ℹ️ is narrow+VS16, drawn two columns — one space would be painted over.
    title = f"{ec.gap(ec.EMOJI_INFO_STR)}About — {SESSION_LAUNCHER_TITLE}"

    @property
    def version_line(self):
        # Bottom footer like Home (user 2026-10-30, option B) — not the first policy line.
        return f"free-agents v{plugin_version()}"

    @property
    def policy(self):
        # Structured readout, not a dim list of directories (user 2026-10-30): version
        # sentence on top, blank lines between groups, hardware + usage stats from
        # settings.stats (gathered in the background at picker start), paths last.
        import os
        s = self.settings
        root = Path(__file__).resolve().parent.parent
        hw = s.hw_hardware or "unknown"
        if getattr(s, "hw_distro", ""):
            hw += f" · {s.hw_distro}"
        if getattr(s, "hw_ram_gb", None):
            hw += f" · {s.hw_ram_gb:g} GB RAM"
        if getattr(s, "hw_gpu", ""):
            hw += f" · GPU {s.hw_gpu}"
        st = getattr(s, "stats", None) or {}
        # Visual hierarchy via a Rich Text (user 2026-10-30): section labels bold, values
        # plain — NOT all cyan. Each installed backend family gets its own line, pinned
        # version only, flagged when installed-but-unpinned.
        try:
            from rich.text import Text
        except ImportError:
            class Text:  # minimal stand-in so About renders without rich (tests, bare envs)
                def __init__(self): self.parts = []
                def append(self, t, style=None): self.parts.append(str(t))
                @property
                def plain(self): return "".join(self.parts)
                def __str__(self): return self.plain
        out = Text()
        out.append(f"This is free-agents v{plugin_version()}.\n\n", style="bold")
        out.append("OS / hardware: ", style="bold")
        out.append(f"{s.hw_os or 'unknown'} — {hw}\n")
        out.append("Backends installed:\n", style="bold")
        for b in (st.get("backends") or ["loading…"]):
            out.append(f"  {b}\n")
        out.append("\n")
        out.append("Local models: ", style="bold")
        out.append(f"{st.get('local_models', 'loading…')}"
                   + (f" (~{st['local_gb']:g} GB on disk)" if st.get("local_gb") else "") + "\n")
        out.append("API providers with a key: ", style="bold")
        out.append(f"{st.get('providers') or 'loading…'}\n")
        out.append("Remote models listed: ", style="bold")
        out.append(f"{st.get('remote_visible', 'loading…')}"
                   + (f" (hidden by filters: {st['remote_hidden']})" if st.get("remote_hidden") else "") + "\n")
        out.append("NVIDIA rate limiter: ", style="bold")
        out.append(f"{st.get('rate_limiter') or '—'}\n\n")
        out.append("Paths\n", style="bold")
        out.append(f"Installed at: {root}\n")
        out.append(f"Config: {os.environ.get('LA_CONFIG_SOURCE') or (root / 'config')}\n")
        out.append(f"Remote launcher: {os.environ.get('CSL_REMOTE_LAUNCHER') or (root / 'bin/remote-session.sh')}\n")
        out.append(f"Catalog cache: {catalog_cache_dir()}")
        return out

    def actions(self):
        return [Action("b", f"{ec.EMOJI_HOME_STR} Back to home", lambda: Nav("home", HOME_OWNED), section="nav")]


class HomeScreen(Screen):
    # The plugin's identity, not "home": both lanes' icons (user, 2026-10-04).
    # Remote (antenna) first, then local (robot arm) — user, 2026-10-28.
    title = f"{SESSION_LAUNCHER_TITLE}  {ec.SESSION_EMOJI_FREE_API_STR} {ec.SESSION_EMOJI_LOCAL_STR}"

    @property
    def version_line(self):
        # Its own widget and colour under the subheadline (user 2026-10-29): not the same
        # formatting directly below it, so it reads as a footer, not part of the subtitle.
        return f"free-agents v{plugin_version()}"

    @property
    def policy(self):
        # Identity subheadline (platform-aware), then the 0.21 menu's config line.
        import os
        line = _identity_subheadline(getattr(self.settings, "hw_os", None),
                                     getattr(self.settings, "hw_distro", None))
        src = os.environ.get("LA_CONFIG_SOURCE", "")
        if src:
            line += f"\nConfig: {src}"
        if os.environ.get("LA_FALLBACK_CONFIG") == "1":
            line += (f"\n{ec.EMOJI_WARNING_STR}  Using public fallback defaults. Private models "
                     "are not present in this installed copy.")
        return line

    def __init__(self, settings: Settings, local_count: int | None = None, remote_count: int | None = None,
                 catalog_total: "int | None" = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner=owner)
        self.local_count = local_count
        self.remote_count = remote_count
        # The full catalog sum over the providers the user has keys for (2026-10-27). The
        # roster count this label showed before was stale fiction — it never matched what
        # the provider groups actually list. None = not known yet ("?").
        self.catalog_total = catalog_total

    def _format_count(self, count: int | None, fallback: str) -> str:
        return str(count) if count is not None else fallback

    def actions(self):
        # Order (user 2026-10-30): the two session lanes + Lowkey grouped together first
        # (Lowkey above the blank line, with the session pickers, not with config items);
        # blank; API keys -> Download -> Backend manager -> Cloud config; blank; Theme +
        # About; Quit. The renderer drops a blank row on every section change.
        acts = [
            Action("l", f"{ec.SESSION_EMOJI_LOCAL_STR} Local sessions ({self._format_count(self.local_count, '?')} on disk)",
                   lambda: Nav("local", HOME_OWNED), section="lanes"),
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR} Remote free API sessions ({self._format_count(self.catalog_total, '?')} listed)",
                   lambda: Nav("remote", HOME_OWNED), section="lanes"),
            Action("o", f"{ec.gap(ec.LK_EMOJI_CONVO_STR)}Lowkey — model evaluation chat", lambda: Nav("tool:lowkey"), section="lanes"),
        ]
        tools = _tool_actions(home=True, owner=self.owner)          # [k API keys, v Backend manager]
        acts.append(tools[0])                     # API keys first …
        acts.append(Action("d", f"{ec.EMOJI_DOWNLOAD_STR} Download local models", lambda: Nav("tool:download"), section="tools"))  # … download directly after
        acts.append(tools[1])                     # Backend manager (blank line after it)
        acts.append(Action("c", f"{ec.gap(ec.EMOJI_CLOUD_CONFIG_STR)}Cloud session configuration…", lambda: Nav("cloud_config", HOME_OWNED), section="tools"))  # ☁️ only — 🔧 is the backend manager's, adjacent now (user 2026-10-30)
        # Theme last before About (user 2026-10-30).
        # Key i for About, not a: a is the lanes' Auto-mode key, and Home never reuses a lane key.
        acts.append(Action("h", f"{ec.EMOJI_THEME_STR} Theme colors", lambda: Nav("theme_config", HOME_OWNED), section="appearance"))
        acts.append(Action("i", f"{ec.gap(ec.EMOJI_INFO_STR)}About — version and paths", lambda: Nav("about", HOME_OWNED), section="appearance"))
        # Settings like NVIDIA rate limiter, auto-mode, prompt hook, watcher are in the lanes
        acts.append(Action("q", f"{ec.gap(ec.quit_label())}Quit", lambda: QUIT, section="nav"))  # user 2026-11-09: ⏻ icon (GUI port)
        check_action_table(acts)
        return acts


@dataclass
class LaunchRequest:
    lane: str
    argv: list
    env: dict
    selected_id: str
    # An auto-scanned row (on disk, not in the registry) cannot launch as-is: the launcher
    # refuses unregistered aliases. The driver turns this flag into an offer to append a
    # managed la_register block to config.local.sh, then launches the original request.
    needs_register: bool = False
    register_folder: str = ""   # the on-disk folder to register (may differ from the alias)


NEW_MODELS_GROUP = "__new_models__"   # the "Register new local models…" action header
HOME_ROW_GROUP = "__home__"            # 0.53.1 constant kept for the 0.54.0 removal path; no longer created (D1)


class LocalScreen(Screen):
    title = f"{ec.SESSION_EMOJI_LOCAL_STR} Local Session Picker"
    # Registered aliases whose weights are gone from disk (csl --inventory reports them as
    # "#missing<TAB>alias<TAB>subdir"). The picker offers ONCE per session to move them to the
    # removed-models tombstone; an empty list means nothing to offer.
    missing: list = None

    @property
    def policy(self):
        if self.search and getattr(self, "_models_loaded", False):
            n = sum(len(g.items) for g in self.accordion.groups if not g.id.startswith("__"))
            return f'{n} model(s) matching "{self.search}"  — the filter box above the list is live; empty shows all'
        # Restored 0.21 subheadline: "N model(s) on disk (session-capable)"; the backend
        # word is platform-aware (MLX on macOS, CUDA on Linux — user 2026-10-29).
        n = len(self.models) if getattr(self, "_models_loaded", False) and self.models else None
        backend = _local_backend_word(getattr(self.settings, "hw_hardware", None))
        return (f"{n} model(s) on disk (session-capable, {backend})" if n is not None
                else f"{backend} models on this machine")
    lane_key = "l"

    def __init__(self, settings: Settings, models: list[LocalModel] | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._models_loaded = False
        self._choose_model_visible = False  # Model list hidden by default
        self.search = ""                    # the live model filter, as on the Remote screen
        self._all_models: list[LocalModel] = []
        if models is not None:
            self.set_models(models)
        else:
            self.models = {}
            self.accordion.set_groups([])

    def set_models(self, models: list[LocalModel], missing: list[str] | None = None):
        # One row per model (user 2026-10-29/30): profiles on the SAME weights are one
        # model — "operator" vs "-thinking" variants differ only in the thinking flag,
        # which the `j` toggle owns now. Key by folder + serving config (backend/parsers),
        # NOT by effort/roles (those are profile choices, not model identity); the
        # non-thinking profile represents the model. First-seen order is preserved.
        groups: dict = {}
        for mdl in models:
            # One row per MODEL, period (user 2026-10-30): same weights = same model, even
            # across backends/parsers/roles/effort — those are launch choices, and the `j`
            # thinking toggle decides thinking. Registered aliases may carry no folder —
            # then the alias with role/thinking suffixes stripped is the identity.
            ident = mdl.folder or _model_identity(mdl.alias)
            key = (ident, mdl.auto_scanned)
            groups.setdefault(key, []).append(mdl)
        # deterministic representative, stable under reordering: the bare identity alias
        # wins, then non-thinking, then lexicographic
        deduped = []
        for (ident, _auto), grp in groups.items():
            deduped.append(min(grp, key=lambda x: (x.alias != ident, x.thinking, x.alias)))
        models = deduped
        # Key by profile_id for uniqueness (multiple profiles can share same alias)
        self.models = {mdl.profile_id or mdl.alias: mdl for mdl in models}
        # Registered but weights gone: still LISTED, in red, so a deletion never looks like
        # nothing happened (user 2026-10-16); never in self.models, so never launchable.
        self.missing = [a for a in (missing or []) if a not in self.models]
        self._all_models = list(models)
        self.unregistered = [x for x in models if x.auto_scanned]
        self._regroup()
        self._models_loaded = True

    def _regroup(self):
        """Build the family groups, narrowed by the live filter (user 2026-10-22: the same
        search as the Remote list). A search shows only matching rows, opens every group with
        a match, and hides the "register new models" action header -- the unregistered models
        themselves still match in their families."""
        q = self.search.casefold()
        buckets: dict[str, list] = {}
        for mdl in self._all_models:
            # The row reads as a NAME, not the kebab-case alias (user 2026-10-22, same
            # convention as the remote list); the alias stays the id and shows in the info box.
            name = model_names.readable(mdl.alias)
            if mdl.auto_scanned:
                # On disk but not registered: yellow, and it says so (user 2026-10-17).
                item = Item(mdl.profile_id or mdl.alias,
                            f"{name}  — on disk, not registered yet", style="yellow")
            else:
                item = Item(mdl.profile_id or mdl.alias,
                            f"{name}"
                            # No [roles] bracket here (user 2026-10-31): roles are a dispatch/council
                            # concept and mean nothing when choosing a session launch model.
                            + (f" ({mdl.backend}" + (", thinking" if mdl.thinking else "") + ")" if mdl.backend else ""))
            if q and q not in item.label.casefold() and q not in mdl.alias.casefold():
                continue
            buckets.setdefault(family_for(mdl.alias, mdl.family), []).append(item)
        for alias in self.missing:
            if q and q not in alias.casefold():
                continue
            buckets.setdefault(family_for(alias), []).append(
                Item(alias, f"{model_names.readable(alias)}  — weights missing from disk", style="red"))
        order = sorted(buckets, key=lambda f: (f == "Other", f.lower()))
        groups = [Group(f, f"{f} ({len(buckets[f])})", buckets[f]) for f in order]
        if q and not groups and (self._all_models or self.missing):
            groups = [Group("__nomatch__", f'No model matches "{self.search}"',
                            [Item("__nomatch__:x", "Edit the live filter above the list (empty shows every model)")])]
        if self.unregistered and not q:
            # Temporary entry ABOVE every group, so new models are seen without opening each one.
            groups.insert(0, Group(NEW_MODELS_GROUP,
                                   f"+ Register new local models found on disk ({len(self.unregistered)})",
                                   [], style="bold yellow"))
        self.accordion.set_groups(groups)
        if q:
            self.accordion.multi_open = {g.id for g in groups
                                         if g.id not in self.accordion.multi_open_folded}
        else:
            self.accordion.multi_open = None

    def set_search(self, query: str):
        """Filter the local models by name, live from the box above the list (same contract
        as RemoteScreen.set_search): empty restores the one-open accordion and forgets the
        headers folded under the previous query."""
        self.search = (query or "").strip()
        if not self.search:
            self.accordion.multi_open_folded = set()
            self.accordion.multi_open = None
            self.accordion.open_group = None
        keep, open_group = self.accordion.selected_id, self.accordion.open_group
        self._regroup()
        if keep and not self.accordion.select(keep):
            self.accordion.selected_id = None
        if open_group and not self.search:
            self.accordion.expand(open_group)

    def _toggle_choose_model(self):
        """Toggle the model list visibility."""
        self._choose_model_visible = not self._choose_model_visible
        if self._choose_model_visible and not self._models_loaded:
            # The picker will lazy-load the models
            pass

    def _choose_model_label(self):
        return "Choose model…" if not self._choose_model_visible else "Choose model (shown)"

    def info_text(self, item_id: str | None) -> str:
        """The info box under the open local list (0.41.0): the readable name over the exact
        alias -- the one `csl <alias>` takes -- and how the model is served."""
        mdl = self.models.get(item_id) if item_id else None
        if mdl is None and item_id in (self.missing or []):
            return f"{model_names.readable(item_id)}\nAlias: {item_id}\nWeights missing from disk"
        if mdl is None:
            return "Highlight a model to see its alias and backend."
        serve = (f"Backend: {mdl.backend}" + (", thinking" if mdl.thinking else "")
                 if mdl.backend else "Backend: not registered yet")
        # Roles omitted from the session picker's info box too (user 2026-10-31) — they only
        # matter for delegate dispatch, not for choosing a session launch model.
        return f"{model_names.readable(mdl.alias)}\nAlias: {mdl.alias}\n{serve}"

    def actions(self):
        s = self.settings
        acts = [
            *model_settings_rows(s, effort_get=lambda: s.local_effort, effort_set=self._set_effort,
                                 temp_get=lambda: s.local_temperature, temp_set=self._set_local_temp),
            Action("c", self._choose_model_label(), self._toggle_choose_model, section="models"),  # top-level, never folded into Settings
            Action("s", f"{ec.SESSION_EMOJI_FREE_API_STR}  Switch to Remote free API sessions",
                   lambda: Nav("remote", self.owner), section="nav"),   # grouped with Back/Quit at the end (user 2026-10-30)
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR}  Remote free API sessions", lambda: Nav("remote", self.owner),
                   section="hidden"),
            setting("m", f"{ec.gap(ec.EMOJI_MCP_STR)}Load MCPs in session: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                    *_toggle(s, "enable_mcp"), labels=("DISABLED", "ENABLED")),
            setting("y", f"{ec.gap(ec.EMOJI_REGISTRY_SYNC_STR)}Auto-sync local registry with the disk (no prompts): "
                        f"{'ON' if s.local_registry.get('auto_sync') else 'OFF'}",
                    (False, True), lambda: bool(s.local_registry.get("auto_sync")),
                    self._put_registry_auto_sync, labels=("OFF", "ON")),
        ]
        # Add "Launch last model" if we have a saved last model
        last_local = s.last_launched_model.get("local_session") if s.last_launched_model else None
        last_local = s.last_launched_model.get("local_session") if s.last_launched_model else None
        # No `in self.models` check: the list is lazy and usually not loaded yet, and launching
        # only needs alias + effort. A since-deleted model fails in the launcher, visibly.
        if last_local:
            acts.insert(0, Action("g", f"{ec.gap(ec.EMOJI_GO_LAUNCH_STR)}Go launch: {model_names.readable(last_local)} session",
                           lambda: self._launch_last("local_session"), section="launch"))
        acts += _common_toggles(s, include_watcher=True)
        acts += _tool_actions(backend=True, local_session=True, settings_keys=("u", "v"))  # RAM guard + backend manager are settings (user 2026-10-30)
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _put_registry_auto_sync(self, v):
        """The Local screen's auto-sync row (0.43.0; a cycle stepper since 0.55.6): same store
        key the guard screen used, persisted through the on_memguard_saved callback the Picker
        wires to sms.save_memguard. Sets the value it receives (direction-aware stepping)."""
        self.settings.local_registry["auto_sync"] = bool(v)
        if self.settings.on_local_registry_saved:
            self.settings.on_local_registry_saved({"auto_sync": bool(v)})

    def _launch_last(self, lane: str):
        """Launch the last used model for the given lane."""
        last_model = self.settings.last_launched_model.get(lane)
        if not last_model:
            return None
        # Find the model by alias if models are loaded
        mdl = None
        for m in self.models.values():
            if m.alias == last_model:
                mdl = m
                break
        if mdl:
            return self._request_for(mdl.profile_id or mdl.alias, mdl)
        # Fallback: models not loaded or model not in profiles (legacy alias)
        # Use the alias directly as before
        return self._request_for(last_model, LocalModel(alias=last_model))

    def _cycle_effort(self):
        self._set_effort(_next(effort_choices("local_session"), self.settings.local_effort))

    def _set_effort(self, value):
        s = self.settings
        s.local_effort = value
        if s.on_effort_saved:
            s.on_effort_saved("local_session", s.local_effort)

    def _set_local_temp(self, value):
        s = self.settings
        s.local_temperature = value
        if s.on_temperature_saved:
            s.on_temperature_saved("local_session", s.local_temperature)

    def activate_selected(self):
        sel = self.accordion.selected_id
        if sel and sel in (self.missing or []):
            # Not launchable: point at the cleanup offer instead, with this model pre-ticked.
            return Nav(f"confirm:missing:{sel}", self.owner)
        if not sel or sel not in self.models:
            return None
        mdl = self.models[sel]
        req = self._request_for(mdl.profile_id or mdl.alias, mdl)
        if mdl.auto_scanned:
            # The launcher refuses unregistered aliases; the driver asks first, registers, then
            # launches this same request (user 2026-10-15: offer registration in the picker).
            req.needs_register = True
            req.register_folder = mdl.folder or mdl.alias
        return req

    def _request_for(self, profile_id: str, mdl: LocalModel):
        s = self.settings
        if s.last_launched_model is not None:
            s.last_launched_model["local_session"] = mdl.alias
        if s.on_last_launched:
            s.on_last_launched("local_session", mdl.alias, None, None)   # 0.56.13: 4-arg callback (mode is lowkey-only)
        env = {"LA_AUTO_MODE": "0" if s.auto_mode == 2 else "1",
               "LA_BLIND_AUTO": "1" if s.auto_mode == 0 else "0",
               "LA_TELEMETRY": "1" if s.telemetry else "0",
               "LA_QUEUE_STOP_HOOK": "1" if s.stop_hook else "0",
               "LA_DENY_GH_RELEASE": "1" if s.deny_gh_release else "0",
               "LA_ENABLE_MCP": "1" if s.enable_mcp else "0",
               "LA_CLASSIFIER_SOURCE": str(s.classifier_source),
               "CSL_WATCH": "1" if s.watcher else "0"}
        # The thinking toggle always exports: None (the launch default) and True both mean ON,
        # so the launcher never has to guess the picker's intent (waypoint fa-thinking-toggle).
        env["LA_THINKING_OVERRIDE"] = "false" if s.thinking_session is False else "true"
        # Use profile_id for the launcher if available, else alias
        launch_alias = mdl.profile_id if mdl.profile_id else mdl.alias
        # 0.56.5: "not set" (provider_default) on a local lane = the launcher's own default,
        # so the effort arg is omitted entirely (csl <alias> [effort]).
        argv = ["local", launch_alias] + ([] if s.local_effort == sms.PROVIDER_DEFAULT else [s.local_effort])
        # Through the ENVIRONMENT, like every other toggle: csl --picker-launch execs the launcher
        # with "<alias> <effort>", and the launcher reads options only before the alias, so a
        # trailing --temperature would be silently dropped. Hotswap passes it to Rapid as
        # --default-temperature and counts it in the server-reuse identity.
        if s.local_temperature:
            env["LA_TEMPERATURE"] = s.local_temperature
        return LaunchRequest("local", argv, env, mdl.alias)


def is_provider_hidden(settings, provider: str) -> bool:
    """0.54.0: the ONE home for reading the hidden-provider list. Every other site calls this
    (pinned by the source scan in tests/test_row_visibility_single_source_0540.py)."""
    return provider in set(getattr(settings, "hidden_providers", None) or [])


def _agent_remote_filters(settings, agent) -> bool:
    """The Remote-screen FILTER predicates only (trial / local_capable / broken / old). The
    always-on rules (keyless, SELECT, hidden provider, dedupe) live in visible_agents, not
    here — they are not filters, they are launchability."""
    s = settings
    if agent.tier == "trial" and not s.include_trials:
        return False
    if agent.local_capable and not s.local_capable_shown:
        return False
    if agent.broken and not s.show_broken:
        return False
    if agent.old and not s.show_old:
        return False
    return True


def _agent_visible(settings, agent) -> bool:
    """Full predicate (kept for RemoteScreen._visible and the shared counts, 0.50.8):
    keyless rows can never launch, plus the remote filters."""
    if not agent.has_key:
        return False
    return _agent_remote_filters(settings, agent)


def visible_agents(settings, agents, *, remote_filters: bool = True) -> list:
    """0.54.0: ONE row-visibility + dedupe function for EVERY model list (Remote, Lowkey, any
    future one). ALWAYS applied (launchability, not a filter): SELECT placeholders and rows
    with neither model_id nor alias dropped, hidden providers dropped, keyless rows dropped
    (a keyless row cannot launch), and dedupe on (provider, model_id or alias) keeping the
    first non--thinking alias at the first-seen position. remote_filters=True additionally
    applies the Remote-screen filters (_agent_remote_filters); the Remote screen always passes
    True, lowkey's opt-in toggle (Task 2, D3) passes its value.

    A row with an EMPTY model_id is keyed on its alias, never silently dropped — that silent
    drop is why lowkey showed no API models at all (0.51.0)."""
    out = []
    pos = {}
    for a in (agents or []):
        if a.model_id == "SELECT" or not (a.model_id or a.alias):
            continue
        if is_provider_hidden(settings, a.provider) or not a.has_key:
            continue
        if remote_filters and not _agent_remote_filters(settings, a):
            continue
        key = (a.provider, a.model_id or a.alias)
        if key in pos:
            i = pos[key]
            if out[i].alias.endswith("-thinking") and not a.alias.endswith("-thinking"):
                out[i] = a                    # base alias represents the model, position kept
            continue
        pos[key] = len(out)
        out.append(a)
    return out


def api_agents_for(settings, agents, *, remote_filters: bool = False) -> list:
    """0.54.0: thin wrapper over visible_agents for lowkey's API chooser — the always-on rules
    apply, the Remote-screen filters are OPT-IN (Task 2's toggle; D3: OFF shows what the
    Remote screen would hide, keyless/hidden/duplicate rows stay absent)."""
    return visible_agents(settings, agents, remote_filters=remote_filters)


def _catalog_probe_agent(settings, prov: str, c, tier: str) -> RemoteAgent:
    """The probe agent a catalog row is judged and rendered through — ONE construction site,
    so counts can never disagree with rendered rows (0.50.8: module-level for sharing)."""
    def fn(name):
        f = getattr(settings, name, None)
        return bool(f and f(prov, c.model_id))
    return RemoteAgent(f"{CATALOG_PREFIX}{prov}:{c.model_id}", prov, c.model_id, tier,
                       local_capable=fn("catalog_local_capable"),
                       old=fn("catalog_model_old"),
                       model_id=c.model_id)


def remote_visibility_counts(settings, agents, catalogs) -> tuple[int, int]:
    """ONE counting function for the remote model list (0.50.8), shared by the RemoteScreen
    subheadline and the About stats so the two can never disagree: (visible, hidden), with
    catalog rows included. Parity must also hold with a PARTIALLY loaded catalog — both sides
    count the rows that have landed, not the whole roster."""
    # 0.54.0: the pinned side counts through visible_agents (the one home); hidden-provider
    # reads go through is_provider_hidden.
    vis = len(visible_agents(settings, agents, remote_filters=True))
    hidden = sum(1 for a in agents
                 if a.model_id != "SELECT" and (a.model_id or a.alias)
                 and not _agent_visible(settings, a))
    for prov, cat in (catalogs or {}).items():
        if not isinstance(cat, list) or not cat or is_provider_hidden(settings, prov):
            continue
        have = {a.model_id for a in agents if a.provider == prov
                and a.model_id != "SELECT" and _agent_visible(settings, a)}
        pinned0 = next((a for a in agents if a.provider == prov and a.model_id != "SELECT"), None)
        tier = pinned0.tier if pinned0 else "unknown"
        for c in cat:
            if c.model_id in have:
                continue
            if _agent_visible(settings, _catalog_probe_agent(settings, prov, c, tier)):
                vis += 1
            else:
                hidden += 1
    return vis, hidden


class RemoteScreen(Screen):
    title = f"{ec.SESSION_EMOJI_FREE_API_STR} Remote API Session Picker"
    lane_key = "r"

    @property
    def policy(self):
        # Restored 0.21 subheadline: "N model(s) visible (hidden: M)". The cloud/billing
        # disclaimer is gone by user decision (2026-10-04); per-row "trial — possible cost"
        # and "no key" labels still say what matters where it matters.
        if not self._models_loaded:
            return "Free/paid cloud API (provider quotas apply)"
        if self.search:
            n = sum(len(g.items) for g in self.accordion.groups if not g.id.startswith("__"))
            return f'{n} model(s) matching "{self.search}"  — the filter box above the list is live; empty shows all'
        # 0.50.8: the count comes from the ONE shared function the About stats use too —
        # the subheadline and About can never disagree (was an inline copy that drifted).
        vis, hidden = remote_visibility_counts(self.settings, self.agents, self.catalogs)
        return f"{vis} model(s) visible  (hidden: {hidden})"

    def __init__(self, settings: Settings, agents: list[RemoteAgent] | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._models_loaded = False
        self._choose_model_visible = False  # Model list hidden by default
        # provider -> list[CatalogModel] once loaded, or an error string; absent = not fetched.
        self.catalogs: dict = {}
        self._catalog_pending: set = set()
        # 0.50.8: set by the app on every render — the loading-animation tick (drives the
        # per-group placeholder label) and the providers whose catalog fetch is IN FLIGHT
        # right now (their groups show the placeholder even before they are opened).
        self.loading_frame = 0
        self._fetch_in_flight: set = set()
        # Providers whose rows came from the disk cache and still owe one background
        # revision this session; provider -> refresh error message, cleared on success.
        self._catalog_stale: set = set()
        self.catalog_refresh_error: dict = {}
        self.search = ""                     # the live model filter; "" = show every provider
        if agents is not None:
            self.agents = agents
            self._regroup()
        else:
            self.agents = []
            self.accordion.set_groups([])

    def _ensure_loaded(self):
        """Called to ensure agents are loaded. Override in picker to lazy-load."""
        pass

    def _regroup(self):
        buckets: dict[str, list] = {}
        order = []
        # 0.54.0: the -thinking dedupe + hidden/keyless rules come from the ONE function
        # (visible_agents) — but a provider whose ONLY row is a SELECT placeholder must still
        # get its group (the placeholder never renders; its catalog IS the roster, 2026-10-20),
        # so group EXISTENCE is computed from the full visibility predicate, rows from
        # visible_agents.
        for agent in self.agents:
            if not _agent_visible(self.settings, agent) or is_provider_hidden(self.settings, agent.provider):
                continue
            if agent.provider not in buckets:
                order.append(agent.provider)
            buckets.setdefault(agent.provider, [])
        visible = visible_agents(self.settings, self.agents, remote_filters=True)
        vis_by_prov: dict[str, list] = {}
        for agent in visible:
            vis_by_prov.setdefault(agent.provider, []).append(agent)
        groups = []
        for prov in order:
            # A "choose model" placeholder (registry model_id SELECT) is not a launchable row —
            # users cannot type the real id — so the provider's roster IS its catalog: the
            # placeholder never renders, only its real pinned rows and the catalog do (2026-10-20).
            # NVIDIA's pinned order is curated by hand (user 2026-10-20: "except nvidia we
            # have sorted those manually very deliberately") — every other provider's pins
            # sort size-descending, unknowns last in inventory order.
            pinned = vis_by_prov.get(prov, [])
            if prov != "nvidia":
                pinned = size_sorted(pinned, lambda a: self._catalog_size(prov, a.model_id))
            if self.search:
                pinned = [a for a in pinned if self._matches(a.model_id, a.display)]
            items = [Item(a.alias, self._pin_row_label(prov, a))
                     for a in pinned]
            items += self._catalog_items(prov, pinned)
            # 0.51.0 (user): when local-capable rows ARE shown, they sit at the very BOTTOM
            # of their provider group (by default they are filtered out entirely by
            # _visible — pinned rows get the verdict annotated in the app's _loaded).
            items = ([i for i in items if not self._row_capable(prov, i.id)]
                     + [i for i in items if self._row_capable(prov, i.id)])
            if self.search and not items:
                continue                       # a search shows only providers with a match
            label = f"{PROVIDER_LABELS.get(prov, prov)} ({len(items)})"
            if prov in self.catalog_refresh_error:
                label += f" · cached, refresh failed: {self.catalog_refresh_error[prov]}"
            groups.append(Group(prov, label, items))
        if self.search and not groups and any(self._visible(a) for a in self.agents):
            groups = [Group("__nomatch__", f'No model matches "{self.search}"',
                            [Item("__nomatch__:x", "Edit the live filter above the list (empty shows every provider)")])]
        # The no-keys instruction needs a REAL roster: an empty one means "still loading",
        # and reading it as "no keys" poisoned the screen — a search typed during the load
        # set _models_loaded here and both fill sites then refused to fill (2026-10-27).
        if not groups and self.agents and not any(a.has_key for a in self.agents):
            # No provider key at all: the list is an instruction, not a roster of unusable models.
            groups = [Group("__nokeys__", f"{ec.EMOJI_KEY_STR} No API key saved yet",
                            [Item("__nokeys__:k", "Press K (API keys) to set one up — every listed provider has a free tier")])]
        self.accordion.set_groups(groups)
        if self.search:
            # The one exception to the one-open rule: every matching provider is open, and a
            # group that appears later (its catalog just landed) opens too. Only a header the
            # USER folded stays folded. The previous _search_seen bookkeeping could not tell
            # "the user folded it" from "it left the list and came back", so under the live
            # filter a background catalog refresh folded EVERYTHING the moment one group's
            # match status flipped (measured 2026-10-22: typing a digit, rcat lands, the
            # no-match group returns already folded). The fold now lives in the accordion,
            # where collapse() records it and a regroup recomputes from it.
            self.accordion.multi_open = {g.id for g in groups
                                         if g.id not in self.accordion.multi_open_folded}
        else:
            self.accordion.multi_open = None
        # Only a landed roster may flip this: both fill sites gate on it, so a render of
        # an unlanded (empty) roster must not claim the models are loaded.
        self._models_loaded = bool(self.agents)

    def _matches(self, model_id: str, display: str = "") -> bool:
        # 0.54.0: delegates to the shared matches_query (Lowkey's filter uses it too)
        return matches_query(self.search, model_id, display)

    def set_search(self, query: str):
        """Filter model names across every provider. Driven LIVE by the filter box above the
        model list (user, 2026-10-21): every character narrows the list at once, no Enter.
        Empty clears it and restores the ordinary accordion (the first group open) — and
        forgets the headers folded under the previous query, so a new search starts fresh."""
        self.search = (query or "").strip()
        if not self.search:
            self.accordion.multi_open_folded = set()
            self.accordion.multi_open = None
            self.accordion.open_group = None
        self._refresh_keep_open()

    def load_cached_catalogs(self):
        """Render every provider that has a disk cache from it at once — the list is there
        on the first paint and the background revision (catalog_kind_needed) updates it
        after. Called once, when the agent roster lands. No network, never raises."""
        for prov in dict.fromkeys(a.provider for a in self.agents):
            rows = read_catalog_cache(prov)
            if rows:
                self.catalogs.setdefault(prov, rows)
                self._catalog_stale.add(prov)
        self._refresh_keep_open()

    # --- live provider catalogs (pinned aliases first, then everything the provider lists) ---
    def _catalog_probe(self, prov: str, c, tier: str) -> RemoteAgent:
        """The probe agent a catalog row is judged and rendered through — delegates to the
        module-level construction site (0.50.8) so counts can never disagree with rows."""
        return _catalog_probe_agent(self.settings, prov, c, tier)

    def _catalog_items(self, prov: str, pinned: list) -> list:
        cat = self.catalogs.get(prov)
        if (prov in self._catalog_pending or prov in getattr(self, "_fetch_in_flight", ())) and cat is None:
            # 0.50.8: the placeholder ANIMATES with the app's loading tick but its row id is
            # fixed — a per-tick id would re-introduce the DuplicateID crash class.
            tick = getattr(self, "loading_frame", 0)
            return [Item(f"{CATALOG_PREFIX}{prov}:__loading__",
                         "  " + loading_label(tick, "loading the provider's model list"), style="dim")]
        if isinstance(cat, str):
            return [Item(f"{CATALOG_PREFIX}{prov}:__error__", f"  model list unavailable: {cat}", style="red")]
        if not cat:
            return []
        have = {a.model_id for a in pinned if a.model_id}
        tier = pinned[0].tier if pinned else "unknown"
        rows = []
        # Largest first (T6), unknowns last in catalog order; the dedup below is by id
        # membership, so a display-only reordering cannot change which rows survive.
        for c in size_sorted(cat, lambda c: self._catalog_size(prov, c.model_id)):
            if c.model_id in have:
                continue                       # already a pinned row, with its own notes
            name = model_names.readable(c.model_id)
            if self.search and not self._matches(c.model_id, name):
                continue
            probe = self._catalog_probe(prov, c, tier)
            if not self._visible(probe):
                continue
            # The per-row "tools untested" flag is gone (user 2026-10-20: stale noise on
            # nearly every row); the row tags a NON-default lane instead. The row reads as
            # the model's own name with its MAKER, not the provider's raw id (user
            # 2026-10-22: the group header already names the provider); the info box keeps
            # the exact id.
            rows.append(Item(probe.alias, f"{name}{self._size_suffix(prov, c.model_id, name)}{lane_tag(prov, c.model_id)}"))
        return rows

    def _catalog_local_capable(self, prov: str, model_id: str) -> bool:
        """The local-capable verdict for a catalog row (Settings.catalog_local_capable, set by the picker)."""
        fn = getattr(self.settings, "catalog_local_capable", None)
        return bool(fn and fn(prov, model_id))

    def _row_capable(self, prov: str, item_id: str) -> bool:
        """0.51.0: the local-capable verdict for ANY row of this screen — pinned alias or
        catalog row — so shown local-capable rows can be sorted to the bottom of the group."""
        fn = getattr(self.settings, "catalog_local_capable", None)
        if fn is None:
            return False
        if item_id.startswith(CATALOG_PREFIX):
            model = item_id.split(":", 2)[2]
        else:
            agent = next((a for a in self.agents if a.alias == item_id), None)
            model = (agent.model_id if agent else "") or ""
        if not model:
            return False
        try:
            return bool(fn(prov, model))
        except Exception:
            return False

    def _pin_row_label(self, prov: str, a) -> str:
        # Tier and lane tags each carry their own "  · " separator, and BOTH can be empty
        # (unknown tier, default/catalog lane) — a glued-on separator then dangles behind the
        # parameter size (user 2026-10-31). Join only the tags that exist.
        tags = [t for t in (tier_label(a.tier, a.provider),
                            lane_tag(a.provider, a.model_id).lstrip(" ·")) if t]
        tail = f"  · {'  · '.join(tags)}" if tags else ""
        return f"{a.display}{self._size_suffix(prov, a.model_id, a.display)}{tail}"

    def _size_suffix(self, prov: str, model_id: str, display: str) -> str:
        """'  · 1.05T' appended to a row name when the size is known and the name does not
        already state it (user 2026-10-29). 0.54.0: delegates to the shared param_size_suffix."""
        return param_size_suffix(self.settings, prov, model_id, display)

    def _catalog_size(self, prov: str, model_id: str) -> "float | None":
        """The parameter count for the size sort. 0.54.0: delegates to the shared
        catalog_param_size (Lowkey sorts by the same rule)."""
        return catalog_param_size(self.settings, prov, model_id)

    def _catalog_old(self, prov: str, model_id: str) -> bool:
        """Whether a catalog row predates the age cutoff (Settings.catalog_model_old, set
        by the picker). Unknown (no callable / verdict error) means NOT old: fail-open."""
        fn = getattr(self.settings, "catalog_model_old", None)
        if fn is None:
            return False
        try:
            return bool(fn(prov, model_id))
        except Exception:
            return False

    def catalog_kind_needed(self) -> str | None:
        """The loader kind for the open provider group's catalog, once, then for each
        placeholder-only provider ("choose model" rows — the catalog IS their roster) even
        unopened; None when nothing to do."""
        prov = self.accordion.open_group
        if (prov and not prov.startswith("__") and prov not in self.catalogs
                and prov not in self._catalog_pending):
            pinned = next((a for a in self.agents if a.provider == prov and self._visible(a)), None)
            if pinned is not None:
                self._catalog_pending.add(prov)
                self._refresh_keep_open()           # show the loading row at once
                return f"rcat:{pinned.alias}"
        for agent in self.agents:               # placeholder rosters (and, under a search, every roster) load unprompted
            if (agent.model_id != "SELECT" and not self.search) or not self._visible(agent):
                continue
            if agent.provider in self.catalogs or agent.provider in self._catalog_pending:
                continue
            self._catalog_pending.add(agent.provider)
            self._refresh_keep_open()           # show the loading row at once
            return f"rcat:{agent.alias}"
        # Snapshot: the loop DISCARDS dead providers from _catalog_stale (below), and mutating a
        # set while iterating it raises "Set changed size during iteration" — measured as a
        # picker-wide crash (2026-10-25) whenever a re-render landed mid-load, because every
        # keystroke in the live search re-runs this path.
        for prov in list(self._catalog_stale):  # cached rosters: one background revision
            if prov in self._catalog_pending:
                continue
            anchor = next((a for a in self.agents if a.provider == prov and self._visible(a)), None)
            if anchor is None:
                self._catalog_stale.discard(prov)
                continue
            self._catalog_pending.add(prov)
            self._refresh_keep_open()
            return f"rcat:{anchor.alias}"
        return None

    def set_catalog(self, alias: str, rows, err: str | None):
        prov = next((a.provider for a in self.agents if a.alias == alias), None)
        if prov is None:
            return
        prev = self.catalogs.get(prov)
        self._catalog_pending.discard(prov)
        self._catalog_stale.discard(prov)      # revised once per session, success or not
        if err and isinstance(prev, list) and prev:
            # A failed refresh keeps the known list and says so in the group header —
            # a network/401/TLS failure must never empty the model list (T2).
            self.catalog_refresh_error[prov] = err.split(": ", 1)[-1]
            self._refresh_keep_open()
            return
        self.catalog_refresh_error.pop(prov, None)
        self.catalogs[prov] = (err.split(": ", 1)[-1] if err else list(rows))
        if not err and rows:
            write_catalog_cache(prov, rows)
        self._refresh_keep_open()

    def _refresh_keep_open(self):
        keep, open_group = self.accordion.selected_id, self.accordion.open_group
        self._regroup()
        if keep and not self.accordion.select(keep):
            self.accordion.selected_id = None
        if open_group:
            self.accordion.expand(open_group)

    def info_text(self, item_id: str | None) -> str:
        """The info box under the model list: the model's display name, its lane, and its
        short-form note (config/remote-model-notes.json), or a hint when nothing is pointed
        at. Only models get a box — group headers and the no-keys instruction get the hint.
        A model constraint on the thinking choice is appended, so the user learns BEFORE the
        launch that the model overrides their toggle (waypoint fa-thinking-toggle)."""
        if item_id and item_id.startswith(CATALOG_PREFIX):
            parts = item_id.split(":", 2)
            if len(parts) == 3 and not parts[2].startswith("__"):
                note = model_note(f"{parts[1]}:{parts[2]}") or ""
                if note:
                    note = strip_leading_name(model_names.readable(parts[2]), note)  # 0.49.3 (H4)
                if parts[1] == "nvidia":
                    note = (note + " " if note else "") + NVIDIA_MODEL_QUOTA_SHORT
                base = (f"{model_names.readable(parts[2])}\n{parts[2]} · "
                        f"{PROVIDER_LABELS.get(parts[1], parts[1])} catalog")
                notices = [thinking_constraint(parts[1], parts[2], self.settings.thinking_session),
                           PROVIDER_ADVISORIES.get(parts[1], ""), note]
                return "\n".join(filter(None, [base, *notices]))
        if item_id in PROVIDER_INFOS:
            return PROVIDER_INFOS[item_id]          # group-header hover: provider-level facts
        agent = next((a for a in self.agents if a.alias == item_id), None)
        if agent is None:
            return "Hover or highlight a model to see notes about it."
        # No "Lane:" line (user 2026-10-30): it held no decision value — the infobox exists to
        # help pick a model, and the lane is visible in the row itself.
        notices = [thinking_constraint(agent.provider, agent.model_id, self.settings.thinking_session),
                   PROVIDER_ADVISORIES.get(agent.provider, "")]
        note = model_note(agent.alias)
        if note:
            note = strip_leading_name(agent.display, note)   # 0.49.3 (H4): no name echo on line 2
        if agent.provider == "nvidia":
            note = (note + " " if note else "") + NVIDIA_MODEL_QUOTA_SHORT
        base = f"{agent.display}\n{note}" if note else f"{agent.display}\nNo notes for this model yet."
        return "\n".join([base, *filter(None, notices)])

    def _visible(self, agent: RemoteAgent) -> bool:
        # 0.50.8: delegates to the module-level _agent_visible (one source for the screen
        # and the shared counting function).
        return _agent_visible(self.settings, agent)

    def hidden_count(self) -> int:
        return sum(1 for a in self.agents if not self._visible(a) and a.model_id != "SELECT")

    def _toggle(self, attr):
        setattr(self.settings, attr, not getattr(self.settings, attr))
        self._regroup()

    def _filter(self, key, label, attr):
        s = self.settings
        def put(v):
            setattr(s, attr, v)
            self._regroup()
        return setting(key, f"{label}: {'SHOWN' if getattr(s, attr) else 'HIDDEN'}", (False, True),
                       lambda: getattr(s, attr), put, labels=("HIDDEN", "SHOWN"))

    def _toggle_choose_model(self):
        """Toggle the model list visibility."""
        self._choose_model_visible = not self._choose_model_visible
        if self._choose_model_visible and not self._models_loaded:
            # The picker will lazy-load the models
            pass

    def _choose_model_label(self):
        return "Choose model…" if not self._choose_model_visible else "Choose model (shown)"

    def actions(self):
        s = self.settings
        acts = [
            *model_settings_rows(s, effort_get=lambda: s.remote_effort, effort_set=self._set_effort,
                                 temp_get=lambda: s.remote_temperature, temp_set=self._set_remote_temp),
            Action("c", self._choose_model_label(), self._toggle_choose_model, section="models"),  # top-level, never folded into a group
            # No "find model" action row (user, 2026-10-21): the filter box at the top of
            # the open model list IS the search — typing there filters live from the first
            # character, so a separate menu entry would only duplicate it.
            Action("s", f"{ec.SESSION_EMOJI_LOCAL_STR}  Switch to Local sessions", lambda: Nav("local", self.owner), section="nav"),   # grouped with Back/Quit at the end (user 2026-10-30)
            Action("l", f"{ec.SESSION_EMOJI_LOCAL_STR}  Local sessions", lambda: Nav("local", self.owner), section="hidden"),
            # (the filter-settings row moves below — after the API keys / rate limiter tool
            #  actions — per user 2026-11-09; see the insert after _tool_actions below)
            setting("m", f"{ec.gap(ec.EMOJI_MCP_STR)}Load MCPs in session: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                    *_toggle(s, "enable_mcp"), labels=("DISABLED", "ENABLED")),
        ]
        # Add "Launch last model" if we have a saved last model
        last_remote = s.last_launched_model.get("remote_api_session") if s.last_launched_model else None
        # No visible-row check: the agent list is lazy. The tier needed for --include-trials is
        # remembered with the alias (user decision 2026-10-03: no 5.7 s inventory call on `g`).
        if last_remote:
            alias, _, rmodel = last_remote.partition("@")
            prov = alias.split("-", 1)[0]
            acts.insert(0, Action("g", f"{ec.gap(ec.EMOJI_GO_LAUNCH_STR)}Go launch: {PROVIDER_LABELS.get(prov, prov)} · {model_names.readable(rmodel or alias)}",
                           lambda: self._launch_last("remote_api_session"), section="launch"))
        acts += _common_toggles(s, include_watcher=False)
        acts += _tool_actions(backend=False, settings_keys=("k", "n"))  # API keys + rate limiter are settings (user 2026-10-30)
        # user 2026-11-09 (ported from the GUI): API keys setup comes BEFORE filter settings,
        # so filter settings lands on the second row together with the NVIDIA rate limiter
        acts.append(Action("y", f"{ec.gap(ec.EMOJI_FILTER_STR)}Filter settings…", lambda: Nav("remote_filters", self.owner), section="settings"))
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _launch_last(self, lane: str):
        """Launch the last used model for the given lane."""
        last_model = self.settings.last_launched_model.get(lane)
        if not last_model:
            return None
        alias, _, remote_model = last_model.partition("@")
        loaded = next((a for a in self.agents if a.alias == alias), None)
        tier = loaded.tier if loaded else self.settings.last_launched_tier.get(lane, "unknown")
        return self._request_for(alias, tier, remote_model=remote_model)

    def _cycle_temperature(self):
        s = self.settings
        s.remote_temperature = _next(TEMPERATURES, s.remote_temperature)

    def _cycle_effort(self):
        self._set_effort(_next(effort_choices("remote_api_session"), self.settings.remote_effort))

    def _set_effort(self, value):
        s = self.settings
        s.remote_effort = value
        if s.on_effort_saved:
            s.on_effort_saved("remote_api_session", s.remote_effort)

    def _set_remote_temp(self, value):
        s = self.settings
        s.remote_temperature = value
        if s.on_temperature_saved:
            s.on_temperature_saved("remote_api_session", s.remote_temperature)

    def activate_selected(self):
        sel = self.accordion.selected_id
        if sel == "__nokeys__:k":
            return Nav("api_keys", self.owner)
        if sel and sel.startswith(CATALOG_PREFIX):
            _, prov, model = sel.split(":", 2)
            if model.startswith("__"):
                return None                    # the loading / error row
            pinned = next((a for a in self.agents if a.provider == prov and self._visible(a)), None)
            if pinned is None:
                return None
            # Launch through the provider's first visible alias with the catalog model swapped in.
            return self._request_for(pinned.alias, pinned.tier, remote_model=model)
        agent = next((a for a in self.agents if a.alias == sel and self._visible(a)), None)
        if agent is None:
            return None
        return self._request_for(agent.alias, agent.tier)

    def _request_for(self, alias: str, tier: str, remote_model: str = ""):
        s = self.settings
        # A catalog pick is remembered as "<alias>@<model>" so Go launch repeats the same model.
        remembered = f"{alias}@{remote_model}" if remote_model else alias
        if s.last_launched_model is not None:
            s.last_launched_model["remote_api_session"] = remembered
        s.last_launched_tier["remote_api_session"] = tier
        if s.on_last_launched:
            s.on_last_launched("remote_api_session", remembered, tier, None)   # 0.56.13: 4-arg callback
        argv = ["remote"] + ["-a"] * s.auto_mode
        if s.telemetry:
            argv.append("-t")
        if s.local_capable_shown:
            argv.append("--local-capable-shown")
        if tier == "trial":
            argv.append("--include-trials")     # the user explicitly picked (or re-launched) a trial
        if s.enable_mcp:
            argv.append("--enable-mcp")
        effort = sms.effort_arg("remote_api_session", s.remote_effort)
        if effort:
            argv += ["--effort", effort]
        if s.remote_temperature:
            argv += ["--temperature", s.remote_temperature]
        if remote_model:
            argv += ["--remote-model", remote_model]
        argv.append(alias)
        env = {"LA_QUEUE_STOP_HOOK": "1" if s.stop_hook else "0",
               "LA_DENY_GH_RELEASE": "1" if s.deny_gh_release else "0",
               "LA_CLASSIFIER_SOURCE": str(s.classifier_source),
               "CLOUD_BYPASS_PERMISSIONS": "1" if s.cloud_bypass_permissions else "0"}
        # The thinking toggle always exports (both lanes): None/True = ON, False = OFF.
        env["LA_THINKING_OVERRIDE"] = "false" if s.thinking_session is False else "true"
        return LaunchRequest("remote", argv, env, alias)


def rendered_actions(sm: "Screen"):
    """The actions a lane picker renders (user 2026-10-29/30, waypoint
    picker-collapsed-settings-group). Lane screens get TWO harmonica groups: MODEL_GROUP
    (effort/temperature/thinking, open by default) and SETTINGS_GROUP ("Advanced", folded
    by default); nav rows always render LAST; every other section stays a top-level row.
    Returns (rendered, hidden_rows). Only the two lane screens fold — sub-screens and
    Home keep their plain rows."""
    if sm is None:
        return [], []
    acts = [a for a in sm.actions() if a.section != "hidden"]
    if isinstance(sm, (LocalScreen, RemoteScreen, LowkeyScreen)):
        nav = [a for a in acts if a.section == "nav"]
        lanes = [a for a in acts if a.section == "lanes"]
        model = [a for a in acts if a.section == "model"]
        adv = [a for a in acts if a.section == "settings"]
        top = [a for a in acts if a.section not in ("nav", "lanes", "model", "settings")]
        out, hidden = list(top), []
        if model:
            out.append(MODEL_GROUP)
            if sm.model_collapsed:
                hidden += model
            else:
                out += model
        if adv:
            out.append(SETTINGS_GROUP)
            if sm.settings_collapsed:
                hidden += adv
            else:
                out += adv
        out += lanes                     # pure navigation groups at the end, before back/quit
        out += nav                       # nav is always the last option (user 2026-10-30)
        return out, hidden
    return acts, []


class RemoteFiltersScreen(Screen):
    """Filter settings for Remote API Session Picker.

    Submenu showing filter options: limited trials, locally-runnable models,
    unworking models, old models (over the age cutoff), and hidden-model report.
    """
    title = f"{ec.EMOJI_FILTER_STR} Remote Filter Settings"

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        # No accordion needed - this is an action-only screen
        self.accordion = None

    def actions(self):
        s = self.settings
        acts = [
            setting("h", f"{ec.EMOJI_TRIALS_STR}  Limited trials: {'SHOWN' if s.include_trials else 'HIDDEN'}",
                    (False, True), lambda: s.include_trials, lambda v: self._put_filter("include_trials", v),
                    labels=("HIDDEN", "SHOWN")),
            setting("f", f"{ec.EMOJI_LOCAL_CAPABLE_STR}  Locally-runnable models: {'SHOWN' if s.local_capable_shown else 'HIDDEN'}",
                    (False, True), lambda: s.local_capable_shown, lambda v: self._put_filter("local_capable_shown", v),
                    labels=("HIDDEN", "SHOWN")),
            setting("u", f"{ec.EMOJI_BROKEN_MODELS_STR}  Unworking models: {'SHOWN' if s.show_broken else 'HIDDEN'}",
                    (False, True), lambda: s.show_broken, lambda v: self._put_filter("show_broken", v),
                    labels=("HIDDEN", "SHOWN")),
            setting("o", f"{ec.EMOJI_OLD_MODELS_STR}  Old models (over the age cutoff): "
                        f"{'SHOWN' if s.show_old else 'HIDDEN'}",
                    (False, True), lambda: s.show_old, lambda v: self._put_filter("show_old", v),
                    labels=("HIDDEN", "SHOWN")),
            Action("x", f"{ec.EMOJI_HIDDEN_REPORT_STR}  Hidden-model report", lambda: Nav("tool:report"), section="tools"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _put_filter(self, attr, v):
        """Direction-aware filter put (0.55.6): the stepper hands us the value to set."""
        setattr(self.settings, attr, bool(v))
        # Regroup the remote screen if it's loaded (lazy loading means we can't directly access it)
        # The change will take effect when remote screen is next opened
        self._regroup_remote_if_loaded()

    def _regroup_remote_if_loaded(self):
        # This is a best-effort; the remote screen will regroup when next opened
        pass

    def groups(self):
        """No accordion for this screen."""
        return []


class CloudConfigScreen(Screen):
    """Cloud session configuration settings.

    Reaches EVERY cloud session — native Anthropic login or an LLM gateway, any launcher — because
    it is kept in Claude Code's own ~/.claude/settings.json `env` (bin/cloud_session_env.py):
    - Security Review: security-guidance plugin reviews — default / sonnet-4-6 / off. Listed only
      when that plugin is installed and enabled (or an override is still set, so it can be cleared).

    - Replace subagents with free agents: FA_REPLACE_AGENTS, read by the cloud-agent-intercept
      PreToolUse hook. Opt-in; undecided = the hook asks once on the first Agent call.

    Does NOT reach cloud sessions (audit 2026-10-08; labelled so in the menu):
    - Bypass Permissions: blind-trust for free-API remote sessions (the gateway launcher hardcodes it)
    The Classifier Source row left this screen in 0.42.0 (user 2026-10-23): it only reaches free
    sessions and stays on the lane screens; a working cloud reroute would earn a row back.

    - Dispatch timeout tier: FA_DISPATCH_TIMEOUT_TIER, read by bin/free-agent-tool.py — snappy /
      balanced (default) / patient, cycled with key t; the infobox below lists each tier's
      pros/cons plus the dynamic rule (rate-limited lanes get patient timeouts while the limiter
      is in a cooldown).
    """
    title = f"{ec.EMOJI_CLOUD_CONFIG_STR}  {ec.EMOJI_TOOLS_STR} Cloud Session Configuration"

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        # No accordion needed - this is an action-only screen
        self.accordion = None

    def actions(self):
        s = self.settings
        acts = []
        if s.security_review_offered:
            label = (f"custom ({s.security_review_custom})" if s.security_review == -1
                     else cse.SECURITY_REVIEW_LABELS[s.security_review])
            acts.append(Action("s", f"Security Review (all cloud sessions): {label}",
                               lambda: self._cycle_security_review(),
                               step=lambda d: self._cycle_security_review(d),
                               labels=tuple(cse.SECURITY_REVIEW_LABELS[k]
                                            for k in cse.security_review_order())))
        bypass_reason = cse.bypass_blocked_by_policy()
        acts += [
            Action("x", f"{ec.EMOJI_SHIELD_STR}  Agent dispatch (paid sessions): "
                        f"{cse.ROUTE_LABELS[s.routes.get('agent', 'free-first')]}",
                   lambda: self._cycle_route("agent"),
                   step=lambda d: self._cycle_route("agent", d),
                   labels=tuple(cse.ROUTE_LABELS[k] for k in cse.ROUTE_MODES)),
            Action("t", f"Dispatch timeout tier (free-agent-tool.py): "
                        f"{cse.TIMEOUT_TIER_LABELS[cse.timeout_tier()]}",
                   lambda: self._cycle_timeout_tier(),
                   step=lambda d: self._cycle_timeout_tier(d),
                   labels=tuple(cse.TIMEOUT_TIER_LABELS[k] for k in cse.TIMEOUT_TIERS)),
            setting("p", f"{ec.EMOJI_AUTO_MODE_STR}  Bypass Permissions (blind-trust): "
                        f"{'ON' if s.cloud_bypass_permissions else 'OFF'}",
                    (False, True), lambda: s.cloud_bypass_permissions,
                    lambda v: setattr(s, "cloud_bypass_permissions", bool(v)),
                    labels=("OFF", "ON"), value_suffix=NOT_GATEWAY_NOTE),
            Action("d", self._bypass_default_label(bypass_reason),
                   lambda: self._cycle_default_mode(),
                   step=lambda d: self._cycle_default_mode(d),
                   labels=("auto", "bypassPermissions"), enabled=bypass_reason is None),
            Action("e", "Check whether auto mode's safety checks are free here "
                        "(sends one small request)", self._run_probe),
            # 0.56.14 (user, opt-in): in LOCAL sessions, Agent/Workflow use only local models.
            setting("l", f"{ec.SESSION_EMOJI_LOCAL_STR}  Local sessions: ONLY local agents (opt-in): "
                        f"{'ON' if s.local_registry.get('agents_local_only') else 'OFF'}",
                    (False, True), lambda: bool(s.local_registry.get("agents_local_only")),
                    self._put_agents_local_only, labels=("OFF", "ON"), section="settings"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _cycle_route(self, path: str, d: int = 1):
        """Cycle the path's routing mode through free-first -> free-only -> paid and persist.
        d=+1 (Enter/click/Right) steps forward, d=-1 (Left) backward (0.55.6)."""
        s = self.settings
        order = list(cse.ROUTE_MODES)
        current = s.routes.get(path, "free-first")
        s.routes[path] = order[(order.index(current) + d) % len(order)] if current in order else "free-only"
        if s.on_cloud_saved:
            s.on_cloud_saved({"route": (path, s.routes[path])})

    def _put_agents_local_only(self, v):
        """0.56.14 (user, opt-in): 'no remote API agents in local sessions' — stored in the
        local_registry section (free-form dict, no schema bump) via on_local_registry_saved."""
        self.settings.local_registry["agents_local_only"] = bool(v)
        if self.settings.on_local_registry_saved:
            self.settings.on_local_registry_saved({"agents_local_only": bool(v)})

    def _cycle_timeout_tier(self, d: int = 1):
        """Cycle the dispatch timeout tier snappy -> balanced -> patient (key t) and persist."""
        order = list(cse.TIMEOUT_TIERS)
        current = cse.timeout_tier()
        nxt = order[(order.index(current) + d) % len(order)] if current in order else "balanced"
        cse.save_timeout_tier(nxt)
        if self.settings.on_cloud_saved:
            self.settings.on_cloud_saved({"timeout_tier": nxt})

    def _timeout_tier_help(self) -> str:
        """The tier pros/cons for the infobox, derived from config/dispatch-preferences.json
        (one source — never a hand-copied copy of the values)."""
        try:
            cfg = json.loads((Path(__file__).resolve().parent.parent / "config"
                              / "dispatch-preferences.json").read_text(encoding="utf-8"))
            tiers = cfg.get("timeout_tiers") or {}
        except (OSError, ValueError):
            tiers = {}
        lines = ["", "Dispatch timeout tiers (key t — what free-agent-tool.py offloads wait for):"]
        for name in cse.TIMEOUT_TIERS:
            t = tiers.get(name) or {}
            vals = (f"wall {t.get('wall_clock_cap_s', '?') // 60:g}m / attempt "
                    f"{t.get('attempt_max_s', '?') // 60:g}m / stall {t.get('attempt_stall_s', '?')}s")
            lines.append(f"· {cse.TIMEOUT_TIER_LABELS[name]} ({vals})")
            if t.get("pro"):
                lines.append(f"    pro: {t['pro']}")
            if t.get("con"):
                lines.append(f"    con: {t['con']}")
        lines.append("Dynamic: while the NVIDIA rate limiter is in an active 429 cooldown, NVIDIA "
                     "attempts automatically get the patient timeout; non-rate-limited providers "
                     "(e.g. Google) always use the tier's own tighter values — no rate limit to wait out.")
        return "\n".join(lines)

    def _bypass_default_label(self, reason: str | None) -> str:
        if reason:
            return f"Default permission mode for sessions you start yourself: disabled — {reason}"
        registered = " — overruled by the registered cloud profile" if self._profile_overrides_mode() else ""
        return (f"Default permission mode for sessions you start yourself: "
                f"{self.settings.default_mode or cse.default_mode() or 'Auto'}{registered}")

    def _profile_overrides_mode(self) -> bool:
        # Release 2 registers the cloud profile; in Release 1 this reads a fixture store value.
        return bool(getattr(self.settings, "cloud_profile_default_mode", None))

    def _cycle_default_mode(self, d: int = 1):
        if cse.bypass_blocked_by_policy():
            return                      # csl never works around a managed policy (spec A2)
        order = ["auto", "bypassPermissions"]
        cur = self.settings.default_mode or cse.default_mode() or "auto"
        nxt = order[(order.index(cur) + d) % len(order)]
        self.settings.default_mode = nxt
        cse.save_default_mode(nxt)
        if self.settings.on_cloud_saved:
            self.settings.on_cloud_saved({"default_mode": nxt})

    def _run_probe(self):
        """Row e: the user CONFIRMED the small request (the ConfirmPanel path asks before this
        runs). The probe is cached; its verdict feeds the cost box. 0.51.0 (user: on a free lane
        the row looked like a no-op): the verdict is always SURFACED as a flash, so a refusal
        (free lane — nothing to measure) is visible instead of silent."""
        import subprocess as sp
        probe_py = Path(__file__).resolve().parent / "fa-eligibility-probe.py"
        r = sp.run([sys.executable, str(probe_py), "--json"],
                   capture_output=True, text=True, timeout=330)
        try:
            self.settings.eligibility = json.loads(r.stdout)
        except ValueError:
            self.settings.eligibility = {"verdict": "unknown", "evidence": (r.stderr or r.stdout)[:200]}
        verdict = (self.settings.eligibility or {}).get("verdict", "unknown")
        evidence = str((self.settings.eligibility or {}).get("evidence", ""))
        if self.settings.on_flash:
            self.settings.on_flash(f"Auto-mode probe: {verdict}" + (f" — {evidence[:80]}" if evidence else ""))

    def info_text(self, item_id: str | None) -> str:
        """The three routing modes, each with its one-line contract (plain Action rows pass
        item_id=None, so the box always shows the whole table); the gateway cost box appears
        ONLY when the cached verdict is not-eligible (A3: facts, only for users it applies to)."""
        extra = ""
        el = self.settings.eligibility
        if el and el.get("verdict") == "refused":
            extra = ("\n\nProbe refused: this shell is a free lane — nothing to measure "
                     "(cached verdict; run it from a paid/cloud shell to measure).")
        if el and el.get("verdict") == "not-eligible":
            extra = ("\n\nCost note: on this setup auto mode's safety checks are billed as normal "
                     "token usage. Bypass with allow/deny rules avoids those calls; the lasting fix "
                     "is a gateway that passes the safeguards fields through unchanged.")
        return (
            "How the Agent tool is routed on cloud sessions:\n"
            "· Free first, paid as last resort — every Agent call is redirected to a free model; "
            "after this session's free attempts are honestly exhausted "
            "(\"paid_fallback_allowed\": true in the dispatch result) AND a reason is stated, ONE "
            "native paid call is let through (exhaustion alone never permits paid).\n"
            "· Free only — every Agent call is redirected and the hook never falls back to paid.\n"
            "· Paid (free-agents stays out) — the native Agent tool runs paid subagents as usual; "
            "free models only when you ask."
            + extra + self._timeout_tier_help()
        )

    def _cycle_security_review(self, d: int = 1):
        """Direction-aware cycle (0.55.6) through the same ring next_state walks."""
        s = self.settings
        order = cse.security_review_order()
        cur = s.security_review
        if cur in order:
            s.security_review = order[(order.index(cur) + d) % len(order)]
        else:                            # e.g. the custom (-1) value set elsewhere
            s.security_review = order[0]
        s.security_review_custom = None
        if s.on_cloud_saved:
            s.on_cloud_saved({"security_review": s.security_review})

    def groups(self):
        """No accordion for this screen."""
        return []


# Theme accent presets, ordered cool to warm (user 2026-10-14). Green Yellow closes the
# list: it is the remote lane's shipped default and must stay reachable by cycling. ONE
# copy on purpose — the 0.34.9 duplicate-Fire-Red bug came from this list living inline
# in three methods and drifting.
THEME_PRESETS = [
    ("#EE2B2B", "Fire Red"), ("#00BFFF", "Sky Blue"), ("#00FFFF", "Cyan"),
    ("#66F298", "Mint Green"), ("#990000", "Deep Red"), ("#F55F50", "Coral Red"),
    ("#FF4500", "Orange Red"), ("#FFD700", "Yellow"), ("#FF00FF", "Magenta"),
    ("#9370DB", "Medium Purple"), ("#00FA9A", "Spring Green"), ("#ADFF2F", "Lime"),
]


class ThemeConfigScreen(Screen):
    """Theme color configuration for local and remote sessions.

    The four value rows (two accents, two hues) share one centered value column
    (align_cycle_values, 0.55.6 user).

    Two settings rows, one per lane, behaving exactly like the effort/temperature rows
    (user 2026-10-15): left/right arrows step the accent, Enter and mouse click step
    forward, wrapping through THEME_PRESETS; `d` restores the shipped defaults. The
    border is re-derived on every change as the accent at the same lightness, 55%
    saturation. Theme files live in the plugin's config/ dir; LA_THEME_CONFIG_DIR
    overrides that location so tests can cycle colors without ever writing into the
    repo (the 0.34.13 release shipped a cycled-over default otherwise).
    """
    title = f"{ec.EMOJI_THEME_STR} Theme Colors"

    align_cycle_values = True   # 0.55.6 (user): ❮ and ❯ in one column across all four rows

    @staticmethod
    def _user_theme_path(name: str) -> Path:
        """The stable user store: survives version installs, shared by every plugin copy."""
        return Path.home() / ".claude" / "free-agents" / "themes" / name

    @classmethod
    def _theme_path(cls, name: str) -> Path:
        """Theme config resolution (user 2026-10-15 — colours must survive plugin version
        installs and be shared by every copy; the version-keyed cache dir is wiped on
        update): 1) LA_THEME_CONFIG_DIR (test escape hatch), 2) the stable user store,
        3) this copy's shipped config/. WRITES go to the env dir when set, else the user
        store (_write_path). MUST MATCH theme_config_path() in generate-local/remote-
        settings.py — the launcher's session theme must read the colours this picker writes."""
        env = os.environ.get("LA_THEME_CONFIG_DIR")
        if env:
            return Path(env) / name
        user = cls._user_theme_path(name)
        return user if user.exists() else Path(__file__).parent.parent / "config" / name

    @classmethod
    def _write_path(cls, name: str) -> Path:
        env = os.environ.get("LA_THEME_CONFIG_DIR")
        return Path(env) / name if env else cls._user_theme_path(name)

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        # Load current theme colors from config files
        import json
        self.local_theme = json.loads(self._theme_path("local-theme.json").read_text())
        self.remote_theme = json.loads(self._theme_path("remote-theme.json").read_text())

    def _lane_label(self, which: str):
        """Row label per user 2026-10-15, restructured 0.55.5 (user): the label carries the
        lane word and the colour NAME only — the name is what sits between the cycle glyphs;
        the #hex, swatch block and border/shell slices moved OUTSIDE them (_lane_suffix).
        The lane word is padded ("Local" vs "Remote" differ by one cell) so BOTH rows' hex,
        swatch and slice share a column (user 2026-10-15); the glyph cell's fixed name width
        (the palette's widest name, via the row's labels) keeps that alignment."""
        theme = self.local_theme if which == "local" else self.remote_theme
        emoji = ec.SESSION_EMOJI_LOCAL_STR if which == "local" else ec.SESSION_EMOJI_FREE_API_STR
        accent = theme["theme"]["accent_color"]
        name = dict(THEME_PRESETS).get(accent.upper(), "custom")
        lane_pad = " " * (len("Remote") - len(which.capitalize()))
        if RICH_AVAILABLE:
            return Text.assemble(f"{emoji} {which.capitalize()}:{lane_pad} ", (name, "bold"))
        return f"{emoji} {which.capitalize()}:{lane_pad} {name}"

    def _lane_suffix(self, which: str):
        """The parts of an accent row that render OUTSIDE the cycle glyphs (0.55.5 user): the
        #hex in the colour it names, the swatch block, and the thin border/shell slices
        previewing accent + border together (0.50.0: shell-mode slice)."""
        theme = self.local_theme if which == "local" else self.remote_theme
        accent = theme["theme"]["accent_color"]
        border = theme["theme"].get("border_color", "")
        if RICH_AVAILABLE:
            return Text.assemble(
                " ",
                (accent, accent),
                "  ",
                Text("    ", style=f"on {accent}"),
                Text(" ", style=f"on {border}"),
                Text(" ", style=f"on {tc.shell_mode_color(accent)}"),
            )
        return f" {accent} + border {border} + shell {tc.shell_mode_color(accent)}"

    def _set_accent(self, which: str, color: str):
        """setting() put: store the accent, re-derive the border, persist."""
        theme = self.local_theme if which == "local" else self.remote_theme
        theme["theme"]["accent_color"] = color
        self._recompute_border(theme)
        self._save_theme(which)

    def _recompute_border(self, theme_dict: dict):
        """Recalculate border_color from accent_color at same lightness, 55% saturation."""
        import colorsys
        accent = theme_dict["theme"]["accent_color"].lstrip("#")
        r, g, b = [int(accent[i:i+2], 16) / 255 for i in (0, 2, 4)]
        h, light, sat = colorsys.rgb_to_hls(r, g, b)
        r2, g2, b2 = colorsys.hls_to_rgb(h, light, 0.55)
        border = "#%02X%02X%02X" % tuple(round(c * 255) for c in (r2, g2, b2))
        theme_dict["theme"]["border_color"] = border

    def _save_theme(self, which: str):
        """Write the lane's theme file back to disk (border re-derived from the accent).
        Always to the stable user store (or the LA_THEME_CONFIG_DIR escape hatch) — never
        the version-keyed cache, which the next update wipes."""
        import json
        if which == "local":
            self._recompute_border(self.local_theme)
            path = self._write_path("local-theme.json")
        else:
            self._recompute_border(self.remote_theme)
            path = self._write_path("remote-theme.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            self.local_theme if which == "local" else self.remote_theme,
            indent=2, ensure_ascii=False) + "\n")

    def _hue_deg(self, which: str) -> int:
        import colorsys
        accent = (self.local_theme if which == "local" else self.remote_theme)["theme"]["accent_color"]
        r, g, b = [int(accent.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return round(colorsys.rgb_to_hls(r, g, b)[0] * 360)  # noqa: E741 -- hls tuple unpack below

    def _shift_hue(self, which: str, deg: int):
        """0.50.0 (theme-hue-shift-shell-swatch): rotate the lane accent's hue by deg —
        arrows step ±5°, Enter/click +20° — re-derive the border, persist. The shell-mode
        colour follows automatically (it is derived from the accent at +20° hue)."""
        import colorsys
        theme = self.local_theme if which == "local" else self.remote_theme
        accent = theme["theme"]["accent_color"].lstrip("#")
        r, g, b = [int(accent[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        hue, light, sat = colorsys.rgb_to_hls(r, g, b)
        hue = (hue + deg / 360.0) % 1.0
        r2, g2, b2 = colorsys.hls_to_rgb(hue, light, sat)
        theme["theme"]["accent_color"] = "#%02X%02X%02X" % tuple(round(c * 255) for c in (r2, g2, b2))
        self._recompute_border(theme)
        self._save_theme(which)

    def _restore_defaults(self):
        self.local_theme["theme"]["accent_color"] = "#FFD700"
        self.remote_theme["theme"]["accent_color"] = "#ADFF2F"
        self._save_theme("local")
        self._save_theme("remote")

    def actions(self):
        presets = [color for color, _ in THEME_PRESETS]
        acts = [
            setting("l", self._lane_label("local"), presets,
                    lambda: self.local_theme["theme"]["accent_color"],
                    lambda v: self._set_accent("local", v),
                    labels=[n for _, n in THEME_PRESETS],
                    value_suffix=self._lane_suffix("local")),
            setting("r", self._lane_label("remote"), presets,
                    lambda: self.remote_theme["theme"]["accent_color"],
                    lambda v: self._set_accent("remote", v),
                    labels=[n for _, n in THEME_PRESETS],
                    value_suffix=self._lane_suffix("remote")),
            Action("y", f"Hue local: {self._hue_deg('local')}°",     # ❮ ❯ render around the value now
                   lambda: self._shift_hue("local", 20),
                   step=lambda d: self._shift_hue("local", 5 * d),
                   labels=tuple(f"{d}°" for d in range(0, 360, 5))),
            Action("u", f"Hue remote: {self._hue_deg('remote')}°",
                   lambda: self._shift_hue("remote", 20),
                   step=lambda d: self._shift_hue("remote", 5 * d),
                   labels=tuple(f"{d}°" for d in range(0, 360, 5))),
            Action("d", "Restore defaults", lambda: self._restore_defaults()),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def groups(self):
        return []



class RateLimiterScreen(Screen):
    """NVIDIA rate limiter settings, persisted in session-menu.local.json (rate_limiter).

    Proxies read the saved values through rate_limiter._setting(); an explicit LA_NVIDIA_*
    environment variable still wins, so nothing a user exported is overridden. Defaults and
    every cycle's steps include rate_limiter's own defaults, so the screen never shows a value
    the proxies do not use.
    """
    title = f"{ec.EMOJI_NVIDIA_RATE_LIMITER_STR} NVIDIA Rate Limiter"
    RPM_STEPS = (10, 20, 30, 40)
    CAP_STEPS = (2, 4, 6, 10)
    WAIT_STEPS = (30, 60, 120, 300)
    COOLDOWN_STEPS = (5, 10, 30, 60)
    MAX_COOLDOWN_STEPS = (120, 300, 600, 900)
    MULTIPLIER_STEPS = (1.5, 2.0, 3.0)
    RETRY_STEPS = (0, 3, 5, 10)

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT,
                 on_save: Callable[[dict], object] | None = None):
        super().__init__(settings, owner)
        self.on_save = on_save

    def value(self, key, default):
        return self.settings.rate_limiter.get(key, default)

    def _set(self, key, value):
        self.settings.rate_limiter[key] = value
        if self.on_save:
            self.on_save({key: value})

    def _cycle(self, key, steps, default, d: int = 1):
        self._set(key, _next(steps, self.value(key, default), d))

    def actions(self):
        mode = self.value("mode", "sliding_window")
        acts = [
            Action("o", f"Mode: {mode}",
                   lambda: self._set("mode", _next(sms.RATE_LIMITER_MODES, mode)),
                   step=lambda d: self._set("mode", _next(sms.RATE_LIMITER_MODES,
                                                          self.value("mode", "sliding_window"), d)),
                   labels=tuple(sms.RATE_LIMITER_MODES)),
            Action("r", f"Requests per minute: {self.value('rpm', 40)}",
                   lambda: self._cycle("rpm", self.RPM_STEPS, 40),
                   step=lambda d: self._cycle("rpm", self.RPM_STEPS, 40, d),
                   labels=tuple(str(v) for v in self.RPM_STEPS)),
            Action("c", (f"Bucket capacity: {self.value('bucket_capacity', 6)}"
                         if mode == "smooth_bucket" else "Bucket capacity: unavailable (smooth_bucket only)"),
                   lambda: self._cycle("bucket_capacity", self.CAP_STEPS, 6),
                   step=lambda d: self._cycle("bucket_capacity", self.CAP_STEPS, 6, d),
                   labels=tuple(str(v) for v in self.CAP_STEPS),
                   enabled=mode == "smooth_bucket"),
            Action("w", f"Max wait: {self.value('max_wait', 300)}s",
                   lambda: self._cycle("max_wait", self.WAIT_STEPS, 300),
                   step=lambda d: self._cycle("max_wait", self.WAIT_STEPS, 300, d),
                   labels=tuple(f"{v}s" for v in self.WAIT_STEPS)),
            Action("d", f"429 base cooldown: {self.value('cooldown', 30)}s",
                   lambda: self._cycle("cooldown", self.COOLDOWN_STEPS, 30),
                   step=lambda d: self._cycle("cooldown", self.COOLDOWN_STEPS, 30, d),
                   labels=tuple(f"{v}s" for v in self.COOLDOWN_STEPS)),
            Action("u", f"429 max cooldown: {self.value('max_cooldown', 300)}s",
                   lambda: self._cycle("max_cooldown", self.MAX_COOLDOWN_STEPS, 300),
                   step=lambda d: self._cycle("max_cooldown", self.MAX_COOLDOWN_STEPS, 300, d),
                   labels=tuple(f"{v}s" for v in self.MAX_COOLDOWN_STEPS)),
            Action("y", f"429 backoff multiplier: ×{self.value('backoff_multiplier', 2.0)}",
                   lambda: self._cycle("backoff_multiplier", self.MULTIPLIER_STEPS, 2.0),
                   step=lambda d: self._cycle("backoff_multiplier", self.MULTIPLIER_STEPS, 2.0, d),
                   labels=tuple(f"×{v}" for v in self.MULTIPLIER_STEPS)),
            Action("z", f"429 max retries: {self.value('max_retries', 5)}",
                   lambda: self._cycle("max_retries", self.RETRY_STEPS, 5),
                   step=lambda d: self._cycle("max_retries", self.RETRY_STEPS, 5, d),
                   labels=tuple(str(v) for v in self.RETRY_STEPS)),
            Action("i", "Show live limiter status", lambda: Nav("tool:rl-status"), section="tools"),
            Action("x", "Reset shared limiter state file", lambda: Nav("tool:rl-reset"), section="tools"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts


class MemoryGuardScreen(Screen):
    """RAM usage out-of-memory protection settings (internally "memguard"), persisted in
    session-menu.local.json (memguard, schema 5).

    The launchers export these as LA_MEMGUARD_* (session_menu_state.py memguard-env); a variable
    the user exported still wins. Every cycle includes sms.DEFAULT_MEMGUARD, so the screen never
    shows a value the guard does not use.
    """
    title = f"{ec.gap(ec.EMOJI_MEMORY_GUARD_STR)}{OOM_PROTECTION_NAME} (local sessions)"
    SOFT_STEPS = (75, 80, 85, 90)
    HARD_STEPS = (90, 93, 95, 97)
    QUIET_STEPS = (2, 3, 5, 10)

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT,
                 on_save: Callable[[dict], object] | None = None):
        super().__init__(settings, owner)
        self.on_save = on_save
        self.accordion = None

    def value(self, key):
        return self.settings.memguard.get(key, sms.DEFAULT_MEMGUARD[key])

    def _set(self, key, value):
        self.settings.memguard[key] = value
        if self.on_save:
            self.on_save({key: value})

    def _cycle(self, key, steps, d: int = 1):
        """Nearest step in direction d that keeps soft below hard; a pair-breaking step is
        skipped, never saved (0.55.6: d=-1 walks backward)."""
        cur = self.value(key)
        order = list(steps)
        start = order.index(cur) + d if cur in order else (0 if d > 0 else len(order) - 1)
        for i in range(len(order)):
            cand = order[(start + i) % len(order)]
            soft = cand if key == "soft_pct" else self.value("soft_pct")
            hard = cand if key == "hard_pct" else self.value("hard_pct")
            if soft < hard:
                self._set(key, cand)
                return

    def groups(self):
        return []

    def actions(self):
        on = self.value("enabled")
        def onoff(v):
            return "ON" if v else "OFF"
        acts = [
            setting("g", f"{OOM_PROTECTION_NAME}: {onoff(on)}",
                    (False, True), lambda: self.value("enabled"),
                    lambda v: self._set("enabled", bool(v)), labels=("OFF", "ON")),
            setting("t", f"Start sessions inside tmux (lets the guard type /compact): {onoff(self.value('tmux'))}",
                    (False, True), lambda: self.value("tmux"),
                    lambda v: self._set("tmux", bool(v)), labels=("OFF", "ON"), enabled=on),
            Action("s", f"Compact idle sessions at RAM: ≥{self.value('soft_pct')}%",
                   lambda: self._cycle("soft_pct", self.SOFT_STEPS),
                   step=lambda d: self._cycle("soft_pct", self.SOFT_STEPS, d),
                   labels=tuple(f"≥{v}%" for v in self.SOFT_STEPS), enabled=on),
            Action("h", f"Compact the largest session at once at RAM: ≥{self.value('hard_pct')}%",
                   lambda: self._cycle("hard_pct", self.HARD_STEPS),
                   step=lambda d: self._cycle("hard_pct", self.HARD_STEPS, d),
                   labels=tuple(f"≥{v}%" for v in self.HARD_STEPS), enabled=on),
            Action("w", f"Quiet time before the guard types into a session: {self.value('quiet_s')}s",
                   lambda: self._cycle("quiet_s", self.QUIET_STEPS),
                   step=lambda d: self._cycle("quiet_s", self.QUIET_STEPS, d),
                   labels=tuple(f"{v}s" for v in self.QUIET_STEPS), enabled=on),
            setting("c", f"After a memory-limit abort, continue by itself: {onoff(self.value('auto_continue'))}",
                    (False, True), lambda: self.value("auto_continue"),
                    lambda v: self._set("auto_continue", bool(v)), labels=("OFF", "ON"), enabled=on),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts


# install/manage-backend.py --backend choices; tests compare this against its registry.
# ★ Keep in step with install/manage-backend.py's manager imports: adding a backend manager
# without adding it here leaves the picker unable to name it, and the contract test below is the
# only guard (it caught `moespresso` missing here for three releases — 0.45.0/0.45.1/0.45.5).
RUNTIME_BACKENDS = ("rapid-mlx", "vllm-mlx", "omlx", "llama-cpp", "litellm", "moespresso",
                    "cuda-vllm", "cuda-llama-cpp", "cuda-exllamav2", "cuda-sglang")
# The CUDA four (0.49.0 Task 14) cycle only on NVIDIA hardware; elsewhere the Backend manager
# shows them as ONE dim "not applicable on this hardware" row, never silently absent.
CUDA_BACKENDS = ("cuda-vllm", "cuda-llama-cpp", "cuda-exllamav2", "cuda-sglang")
MAC_ONLY_BACKENDS = tuple(b for b in RUNTIME_BACKENDS if b not in CUDA_BACKENDS)


def _backend_choices(hw: str) -> tuple:
    """0.50.0 F2: the backend list is hardware-gated — on CUDA hardware the CUDA backends
    list FIRST (as MLX does on Mac); elsewhere only the Mac backends. A 'not applicable
    on this hardware' row is never shown (that was the 0.49.x dim row)."""
    cuda_ok = (hw or "") == "cuda"
    return CUDA_BACKENDS + MAC_ONLY_BACKENDS if cuda_ok else MAC_ONLY_BACKENDS
# Per-version operations beyond install/validate/info, by backend: only the ones that backend's
# manager actually implements (the base class raises "not supported"), so the menu never offers
# a row that can only fail. Tests compare this against the managers' real overrides.
# Equal to each manager's VERSION_OPS declaration (0.49.0; contract-tested). LiteLLM offers remove
# only: pipx owns the install the remote sessions run, the manager's venvs are leftovers.
BACKEND_VERSION_OPS = {"rapid-mlx": ("snapshot", "promote", "remove", "repair"),
                       "vllm-mlx": ("snapshot", "promote", "remove", "repair"),
                       "litellm": ("remove",),
                       **{b: ("snapshot", "promote", "remove", "repair") for b in CUDA_BACKENDS}}
# Backends whose manager keeps at least one COMPLETE version (REMOVE_KEEPS_ONE).
KEEP_ONE_BACKENDS = ("rapid-mlx", "vllm-mlx")


@dataclass
class VersionEntry:
    version: str
    status: str = ""          # installed: complete/incomplete; releases: "installed" or ""
    pinned: bool = False      # 0.50.0 F1: the promoted version (live-config pin) gets a pin icon


CORE_REFUSAL = ("{version} is the only installed {backend} version — not removed. {backend} is a "
                "core dependency of free-agents (it runs local models).")
RAPID_CORE_REFUSAL = CORE_REFUSAL.replace("{backend}", "Rapid-MLX")   # kept for existing importers


class VersionsScreen(Screen):
    """Pick a version from a LIST (arrows/click, like the model list), then run one operation
    on it. Replaces the typed-version prompts: the old manage-rapid-mlx.py menu listed the
    versions to act on, and 0.22's picker port asked the user to type them instead."""
    OPS = {   # op -> (label, which list it picks from)
        "install": ("Install a release", "releases"),
        "validate": ("Validate an installed version", "installed"),
        "info": ("Show info for an installed version", "installed"),
        "snapshot": ("Snapshot an installed version (recreation receipt)", "installed"),
        "promote": ("Promote pins to an installed version", "installed"),
        "remove": ("Remove an installed version", "installed"),
        "repair": ("Repair an installed version (rebuild in place)", "installed"),
    }
    load_error: str | None = None   # set by the app when the list failed to load (not empty)

    def __init__(self, settings: Settings, op: str, entries: list[VersionEntry] | None = None,
                 owner: str = SUB):
        super().__init__(settings, owner)
        self.op = op
        self.backend = settings.runtime_backend
        self.title = f"{ec.gap(VERSION_OP_EMOJI[op])}{self.OPS[op][0]} — {self.backend}"
        self.entries: list[VersionEntry] | None = None
        self.promote_after_install = True
        if entries is not None:
            self.set_entries(entries)

    @property
    def inventory_kind(self) -> str:
        return f"versions:{self.backend}:{self.OPS[self.op][1]}"

    @property
    def policy(self):
        if self.entries is None:
            return ""
        return "↑↓ choose a version · Enter/click runs it" if self.entries else ""

    def set_entries(self, entries: list[VersionEntry]):
        self.entries = entries
        source = self.OPS[self.op][1]
        if source == "releases":
            items = [Item(e.version, e.version + ("  (installed)" if e.status == "installed" else ""))
                     for e in entries]
            label = f"Releases incl. prereleases ({len(items)})"
        else:
            # A version that cannot run is marked where the user chooses (0.49.0: health is real).
            # F1 (0.50.0): the promoted version carries a real pin icon.
            items = [Item(e.version, (f"{ec.EMOJI_PROMOTE_STR} " if e.pinned else "")
                          + f"{e.version:14} {e.status}"
                          + (f"  {ec.gap(ec.EMOJI_WARNING_STR)}cannot run — Repair rebuilds it" if e.status in ("broken", "incomplete") else ""))
                     for e in entries]
            label = f"Installed versions ({len(items)})"
        self.accordion.set_groups([Group(source, label, items)] if items else [])
        # The list IS the screen: start on the first version, never on the group header,
        # where Enter would collapse the only group instead of choosing a version.
        if items and self.accordion.selected_id is None:
            self.accordion.select(items[0].id)

    def activate_selected(self):
        v = self.accordion.selected_id
        if not v:
            return None
        if self.op == "remove":
            # The picker's own guard, before any prompt: the last complete Rapid-MLX version is
            # never offered for removal (the manager refuses it too — this is the friendly half).
            if self.backend in KEEP_ONE_BACKENDS and not any(
                    e.version != v and e.status == "complete" for e in (self.entries or [])):
                return Nav("warn:" + CORE_REFUSAL.format(version=v, backend=self.backend), self.owner)
            return Nav("prompt:rt-remove", self.owner)       # yes/no, then "are you sure"
        return Nav(f"tool:rt-op:{self.op}:{v}", self.owner)

    def argv_for(self, version: str) -> list[str]:
        """manage-backend.py arguments (after --backend) for running this op on `version`."""
        if self.op == "install":
            return ["install", version] + ([] if self.promote_after_install or self.backend != "rapid-mlx"
                                           else ["--skip-pin-update"])
        if self.op == "remove":
            # --yes only AFTER the picker's own typed confirmation; the manager still enforces
            # its gates (inactive, unpinned, reproducible) and refuses otherwise.
            return ["remove", version, "--yes"]
        return [self.op, version]

    def actions(self):
        acts = []
        if self.op == "install" and self.backend == "rapid-mlx":
            acts.append(setting("p", f"Promote pins after install: {'ON' if self.promote_after_install else 'OFF'}",
                                (True, False), lambda: self.promote_after_install,
                                lambda v: setattr(self, "promote_after_install", v),
                                labels=("ON", "OFF")))
        acts.append(Action("b", f"{ec.EMOJI_BACK_STR}  Back to Backend manager", lambda: BACK, section="nav"))
        check_action_table(acts)
        return acts

    def handle_key(self, key: str):
        if key == "escape":
            return BACK
        return super().handle_key(key)


VERSION_OP_EMOJI = {"install": ec.EMOJI_INSTALL_STR, "validate": ec.EMOJI_VALIDATE_STR,
                    "info": ec.EMOJI_INFO_STR, "snapshot": ec.EMOJI_SNAPSHOT_STR,
                    "promote": ec.EMOJI_PROMOTE_STR, "remove": ec.EMOJI_REMOVE_STR,
                    "repair": ec.EMOJI_REPAIR_STR}


def runtime_actions(screen) -> list:
    """The Backend manager rows, shared by both entry screens (Home `v` and Launchd's Back).
    0.50.0 item 5: no standalone list-releases row (the install flow lists the same
    releases) and no cycling backend row — the backend is the chooser accordion on
    RuntimeManagerScreen. F2: no 'not applicable on this hardware' row either."""
    b = screen.settings.runtime_backend
    choices = _backend_choices(getattr(screen.settings, "hw_hardware", ""))
    if b not in choices:
        b = choices[0]
        screen.settings.runtime_backend = b
    pick = lambda op: (lambda: Nav(f"runtime_versions:{op}", screen.owner))   # noqa: E731
    acts = [
        Action("i", f"{ec.EMOJI_INSTALL_STR} Install a release (choose from the list)", pick("install"), section="tools"),
        Action("t", f"{ec.EMOJI_VALIDATE_STR} Validate an installed version", pick("validate"), section="tools"),
        Action("f", f"{ec.EMOJI_INFO_STR}  Show info for an installed version", pick("info"), section="tools"),
    ]
    ops = BACKEND_VERSION_OPS.get(b, ())
    for key, op, label in (("s", "snapshot", "Snapshot an installed version"),
                           ("p", "promote", "Promote pins to an installed version"),
                           ("x", "remove", "Remove an installed version"),
                           ("e", "repair", "Repair an installed version (rebuild in place)")):
        if op in ops:
            acts.append(Action(key, f"{ec.gap(VERSION_OP_EMOJI[op])}{label}", pick(op), section="tools"))
    acts.append(Action("l", f"{ec.EMOJI_LAUNCHD_STR} Launchd update checks", lambda: Nav("runtime_launchd", screen.owner),
                       section="tools"))
    if type(screen).__name__ == "RuntimeManagerScreen":
        # the launch-prompt's settings live in the backend manager (not the launchd page)
        acts += _upgrade_pref_rows()
    acts += screen.nav_actions()
    check_action_table(acts)
    return acts


def _bu_state_path() -> Path:
    """The launch-time upgrade state file — same resolution as install/backend_upgrades.py,
    read directly as JSON (executing that module per screen build is slow enough to shift
    PTY-test timing, and the state is a flat dict)."""
    import os as _os
    return Path(_os.environ.get("LA_BACKEND_UPGRADES_STATE")
                or Path.home() / ".claude" / "free-agents" / "backend-upgrades.json")


def _bu_pref(key, default):
    import json as _json
    try:
        return bool(_json.loads(_bu_state_path().read_text()).get(key, default))
    except Exception:
        return default


def _set_bu_pref(key, value):
    import json as _json
    p = _bu_state_path()
    data = {}
    try:
        data = _json.loads(p.read_text())
    except Exception:
        pass
    if not isinstance(data, dict):
        data = {}
    data[key] = bool(value)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, indent=2) + "\n")
    tmp.replace(p)


def _upgrade_pref_rows():
    """Backend-manager toggles backing the launch prompt's notice text (user 2026-11-06):
    silencing the notification and installing upgrades automatically in the background."""
    notify = lambda: _bu_pref("notify", True)          # noqa: E731
    auto = lambda: _bu_pref("auto_install", False)     # noqa: E731
    return [
        setting("n", f"Notify about backend upgrades at launch: {'ON' if notify() else 'OFF'}",
                (False, True), notify, lambda v: _set_bu_pref("notify", v), labels=("OFF", "ON")),
        setting("a", f"Install backend upgrades automatically in the background: {'ON' if auto() else 'OFF'}",
                (False, True), auto, lambda v: _set_bu_pref("auto_install", v), labels=("OFF", "ON")),
    ]


def about_backend_lines(home: Path, repo: Path) -> list[str]:
    """About-screen backend lines: one per REGISTERED backend family with >=1 venv under
    <home>/.venvs — never a bare glob of ~/.venvs, which also listed the user's own unrelated
    venvs (hf-py312). Health comes from the managers' real installed_versions() (runs the
    interpreter), and the pin is read from the LIVE config (config-lib's own resolution) — the
    old code read backend-pin.txt, a file no code ever writes, so About could never show a pin.
    Format: "<fam>  <pin-emoji> pinned <ver> (<health>)" (0.50.1: the promoted version
    carries the pin emoji, not the warning triangle) or "<fam>  <n> installed, not pinned
    (auto-discovery: <ver>)"; broken / pinned-keg versions are flagged with the warning emoji.
    Emoji spacing goes through ec.gap() (narrow+VS16 emoji are drawn 2 cols wide).
    """
    warn = ec.EMOJI_WARNING_STR
    pin = ec.EMOJI_PROMOTE_STR
    try:
        install = str(repo / "install")
        if install not in sys.path:
            sys.path.insert(0, install)
        import managers  # noqa: E402
        import managers.llama_cpp  # noqa: F401,E402  (fills the registry, as manage-backend.py does)
        import managers.litellm  # noqa: F401,E402
        import managers.moespresso  # noqa: F401,E402
        import managers.omlx  # noqa: F401,E402
        import managers.rapid_mlx  # noqa: F401,E402
        import managers.vllm_mlx  # noqa: F401,E402
        from managers.base import live_config_file  # noqa: E402
        from managers.durability import venv_durability  # noqa: E402
    except Exception:
        return []
    try:
        live = live_config_file(repo)
        live_text = live.read_text(encoding="utf-8", errors="replace") if live.is_file() else ""
    except Exception:
        live_text = ""

    def pins_in_live(path: Path) -> bool:
        # comment lines are not pins (same rule as _referenced_paths): a commented-out
        # LA_RAPID_BIN line must not make About report a stale pin
        for line in live_text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"(?<![\w.-])" + re.escape(path.name) + r"(?![\w.-])", line) or str(path) in line:
                return True
        return False

    lines = []
    for name in managers.list_managers():
        cls = managers.get_manager(name)
        if not getattr(cls, "VENV_PREFIX", ""):
            continue
        mgr = managers.get_manager_instance(name, home)
        if mgr is None:
            continue
        try:
            rows = mgr.installed_versions()
        except Exception:
            rows = []
        if not rows:
            continue
        broken = [v for v, _p, s in rows if s == "broken"]
        fragile = [v for v, p, s in rows
                   if s == "complete" and (p / "pyvenv.cfg").is_file()
                   and venv_durability(p) == "pinned-keg"]
        flags = []
        if broken:
            flags.append(f"{ec.gap(warn)}broken: {', '.join(broken)}")
        if fragile:
            flags.append(f"{ec.gap(warn)}pinned-keg: {', '.join(fragile)}")
        pinned = next(((v, s) for v, p, s in rows if pins_in_live(p)), None)
        if pinned:
            ver, state = pinned
            health = "runs" if state == "complete" else state
            lines.append(f"{name}  {ec.gap(pin)}pinned {ver} ({health})" + (f" {' '.join(flags)}" if flags else ""))
        else:
            best = next((v for v, _p, s in rows if s != "broken"), rows[0][0])
            lines.append(f"{name}  {len(rows)} installed, not pinned (auto-discovery: {best})"
                         + (f" {' '.join(flags)}" if flags else ""))
    return lines


class RuntimeManagerScreen(Screen):
    """Backend Manager - manages versioned runtime environments.

    Supports multiple backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm).
    Drives install/manage-backend.py with backend selection.
    """
    title = f"{ec.EMOJI_TOOLS_STR} Backend Manager"

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._rebuild_backend_chooser()

    def _rebuild_backend_chooser(self):
        """0.50.0 item 5: the backend picker is a chooser (accordion group), not a cycling
        first row. F2: the list is hardware-gated (CUDA first on CUDA hardware)."""
        choices = _backend_choices(getattr(self.settings, "hw_hardware", ""))
        cur = self.settings.runtime_backend
        if cur not in choices:
            cur = choices[0]
            self.settings.runtime_backend = cur
        self.accordion.set_groups([Group(
            "backends", "Choose backend",
            # 0.51.0 (user 2026-11-03): "selected", not "current" — this menu is about which
            # backend to MODIFY (update/delete/pin), not which one is in use.
            [Item(b, f"{b}" + ("  ← selected" if b == cur else "")) for b in choices])])
        self.accordion.open_group = "backends"     # the chooser stays open while picking

    def groups(self):
        return self.accordion.groups

    def activate_selected(self):
        sel = self.accordion.selected_id
        if sel and sel in _backend_choices(getattr(self.settings, "hw_hardware", "")):
            self.settings.runtime_backend = sel
            self._rebuild_backend_chooser()
        return None

    def actions(self):
        return runtime_actions(self)


class APIKeysScreen(Screen):
    """API Keys Setup - manage remote API provider credentials.

    Shows provider status (saved/missing) by checking actual key files.
    Each provider is an expandable group with two contextual actions:
    - Open provider signup page
    - Add provider key (launches wizard)
    Only one provider's actions are visible at a time.
    """
    title = f"{ec.EMOJI_KEY_STR} API Keys Setup"

    # Provider list: NVIDIA first, then Google, then Groq, then alphabetical — built from the key
    # wizard's table, so a provider added there appears here with no second edit.
    # (slug, name, shortcut, signup_url, key_files); the shortcut is unused (rows are accordion items).
    PROVIDERS = [(p.slug, p.name, "", p.url, tuple(p.files)) for p in _key_provider_order()]

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._show_all_providers = True   # initial view lists ALL providers (as before 0.56.5)
        self._rebuild_groups()   # builds providers + separator + more + blank rows + Back row

    # 0.54.0 (D1): NO _with_home_row / hidden re-sectioning here — Back is the last item of the
    # actions pane like every other screen (the nav-only pane renders below the provider
    # accordion on this screen, which is exactly the D1 placement for API keys).

    # Account requirements a user must meet BEFORE a key can work, shown on the provider's row so
    # they are seen while choosing, not discovered as a 403 inside the first session.
    ACCOUNT_WARNINGS = {"vercel": f"{ec.EMOJI_WARNING_STR}  needs a credit card on file, even for free credits"}

    def _get_provider_status(self, slug: str, key_files: tuple) -> str:
        """Get status of a provider by checking actual key files in ~/.api_keys"""
        status = f"{ec.EMOJI_OK_STR} Saved" if provider_has_key(slug) else f"{ec.EMOJI_MISSING_STR} Missing"
        warning = self.ACCOUNT_WARNINGS.get(slug)
        if warning:
            status = f"{status}  {warning}"
        # 0.56.5 (user): the keys list must NOT change when a provider is hidden from the
        # model list — the Hide/Show item inside the provider's group states the toggle.
        return status

    def _hide_item(self, slug: str) -> tuple:
        """(item_id, label) for the hide/show toggle — the id flips with the state so
        activating it again reverses the toggle, and the label STATES the current state
        (0.51.0, user 2026-11-03: the toggle must reveal its state, not just the action)."""
        hidden = is_provider_hidden(self.settings, slug)
        verb = "Show" if hidden else "Hide"
        state = "currently hidden" if hidden else "currently shown"
        return f"{'show' if hidden else 'hide'}:{slug}", f"{verb} {slug} in the choose-model list ({state})"

    def info_text(self, item_id: str | None) -> str:
        """The info box under the list: what the highlighted provider needs BEFORE a key works
        (its setup steps, incl. account requirements such as Vercel's credit card) plus the
        registry's measured notes. Shown for a provider row and for its Open/Add items."""
        slug = item_id.split(":", 1)[-1] if item_id else None
        if item_id and (item_id.startswith("hide:") or item_id.startswith("show:")):
            hidden = is_provider_hidden(self.settings, slug or "")
            return (f"{'Show' if hidden else 'Hide'} this provider in the choose-model list. "
                    "The key stays on disk — only the remote model list hides the provider's group. "
                    "Unlike 'Open signup page' (browser) and 'Add key' (credential wizard), this "
                    "changes no account state; toggle it any time to reverse.")
        prov = next((p for p in KEY_PROVIDERS if p.slug == slug), None)
        if prov is None:
            return "Highlight a provider to see what its account needs before a key works."
        lines = [prov.name, prov.steps]
        note = _registry_provider_note(slug)
        # Skip the note when the steps already say it (Vercel's card requirement is in both).
        if note and not ("credit card" in note.lower() and "credit card" in prov.steps.lower()):
            lines.append("Measured: " + note)
        return "\n".join(lines)

    def actions(self):
        # No static actions - all actions are via accordion items
        # Add Back at bottom (matching other menus - Back to Home for HOME_OWNED, BACK for SUB)
        if self.owner == HOME_OWNED:
            acts = [Action("b", f"{ec.EMOJI_HOME_STR} Back to Home", lambda: Nav("home", HOME_OWNED), section="nav")]
        else:
            acts = [Action("b", f"{ec.EMOJI_HOME_STR} Back", lambda: BACK, section="nav")]
        check_action_table(acts)
        return acts

    def _rebuild_groups(self):
        """Rebuild accordion groups based on _show_all_providers flag."""
        groups = []
        for i, (slug, name, _shortcut, _url, key_files) in enumerate(self.PROVIDERS):
            # Skip providers after NVIDIA if not showing all
            if i > 0 and not getattr(self, '_show_all_providers', False):
                continue
            status = self._get_provider_status(slug, key_files)
            label = f"{name}: {status}"
            # Visual separator AFTER NVIDIA (before Google): a Group with no items
            # and an empty label renders as a blank row. Placed at index 1 (after NVIDIA).
            if i == 1:
                groups.append(Group("__sep__", "", []))
            # Each provider group has three items: Open page, Add key, Hide/Show
            # (0.50.0 api-keys-provider-hide-toggle).
            groups.append(Group(
                slug,
                label,
                [
                    Item(f"open:{slug}", f"Open {name} signup page"),
                    Item(f"add:{slug}", f"Add {name} key"),
                    Item(*self._hide_item(slug)),
                ]
            ))
        # Add "More providers" item if not showing all
        if not getattr(self, '_show_all_providers', False):
            groups.append(Group(
                "more",
                "More providers…",
                [
                    Item("more:show", "Show all providers"),
                ]
            ))
        self.accordion.set_groups(groups)
        # Default expand NVIDIA (first group)
        if groups:
            self.accordion.open_group = groups[0].id

    def activate_selected(self):
        """Handle activation of accordion items (Open page / Add key / More).
        Returns LaunchRequest to launch the appropriate command.
        """
        sel = self.accordion.selected_id
        if not sel:
            return None
        # sel format: "open:slug" or "add:slug" or "more:show"
        if sel.startswith("open:"):
            slug = sel[5:]
            # Open browser directly without suspending - macOS 'open' command works async
            url = PROVIDER_SIGNUP_URLS.get(slug)
            if url:
                open_url(url)
            return None
        elif sel.startswith("add:"):
            # Inline key entry in the picker (the app opens a hidden Input), not the old wizard
            return Nav(f"tool:keys:wizard:{sel[4:]}", self.owner)
        elif sel.startswith("more:"):
            # Toggle more providers visibility
            self._show_all_providers = not getattr(self, '_show_all_providers', False)
            self._rebuild_groups()
            return None
        elif sel.startswith("hide:") or sel.startswith("show:"):
            # 0.50.0: hide/show this provider in the remote choose-model list. Reversible;
            # the key on disk is untouched. Persisted via the save callback.
            slug = sel.split(":", 1)[1]
            hidden = list(self.settings.hidden_providers or [])
            if sel.startswith("hide:") and slug not in hidden:
                hidden.append(slug)
            elif sel.startswith("show:") and slug in hidden:
                hidden.remove(slug)
            self.settings.hidden_providers = hidden
            if self.settings.on_hidden_providers_saved:
                self.settings.on_hidden_providers_saved(hidden)
            self._rebuild_groups()
            # 0.51.0 (user 2026-11-03): the save must be VISIBLE, not a silent rebuild —
            # a flash message names the provider and the new state.
            if self.settings.on_flash:
                self.settings.on_flash(
                    f"Saved — {slug} is now "
                    f"{'hidden from' if slug in hidden else 'shown in'} the choose-model list")
            return None
        return None


class RuntimeScreen(Screen):
    """Front end for install/manage-backend.py (0.21.0's unified backend manager).

    Every row maps to a subcommand that main's parser accepts AND dispatches. Version-taking
    rows open a VersionsScreen (pick from a list); snapshot/promote/remove are offered only for
    backends whose manager implements them (BACKEND_VERSION_OPS).
    """
    title = f"{ec.EMOJI_TOOLS_STR} Backend manager"

    def _cycle_backend(self):
        cuda_ok = getattr(self.settings, "hw_hardware", "") == "cuda"
        choices = RUNTIME_BACKENDS if cuda_ok else MAC_ONLY_BACKENDS
        self.settings.runtime_backend = _next(choices, self.settings.runtime_backend)

    def actions(self):
        return runtime_actions(self)


class LaunchdScreen(Screen):
    """Weekly backend update check (manage-backend.py launchd ...). Install/uninstall change a
    LaunchAgent, so both go through a typed confirmation; Back returns to the backend manager."""
    title = f"{ec.EMOJI_LAUNCHD_STR} Launchd update checks"

    def handle_key(self, key: str):
        if key == "escape":
            return BACK if self.owner == SUB else Nav("runtime", self.owner)
        return super().handle_key(key)

    def actions(self):
        acts = [
            Action("s", f"{ec.EMOJI_LAUNCHD_STATUS_STR} Status", lambda: Nav("tool:rt-launchd-status"), section="tools"),
            Action("o", f"{ec.EMOJI_LAUNCHD_RUN_ONCE_STR}  Run the update check once now",
                   lambda: Nav("tool:rt-launchd-run-once"), section="tools"),
            Action("i", f"{ec.EMOJI_LAUNCHD_INSTALL_STR} Install the weekly check (LaunchAgent)",
                   lambda: Nav("prompt:rt-launchd-install"), section="tools"),
            Action("u", f"{ec.EMOJI_LAUNCHD_UNINSTALL_STR} Uninstall the weekly check",
                   lambda: Nav("prompt:rt-launchd-uninstall"), section="tools"),
            Action("b", f"{ec.EMOJI_BACK_STR}  Back to Backend manager",
                   lambda: BACK if self.owner == SUB else Nav("runtime", self.owner), section="nav"),
        ]
        check_action_table(acts)
        return acts


class LowkeyScreen(Screen):
    """Lowkey — a minimalist model-evaluation chat that can run on ANY model, local or a
    free API provider (user 2026-10-30; was "local dispatch" only). `s` here means Set
    session name, never lane switch — Lowkey is a tool screen, not a lane picker."""
    title = f"{ec.gap(ec.LK_EMOJI_CONVO_STR)}Lowkey — model evaluation chat"

    def __init__(self, settings: Settings, models: "list[LocalModel] | None" = None,
                 owner: str = DIRECT_ROOT, api_agents: "list | None" = None):
        super().__init__(settings, owner)
        self.session_name = ""
        # convo | oneshot; remembered across sessions from the last launch (user 2026-11-12)
        self.mode = (settings.last_launched_mode or {}).get("lowkey") or "convo"
        self._choose_model_visible = False     # local model list is NOT open by default
        self._models_loaded = False
        # 0.50.0 item 8 (lowkey-any-model-api): the API chooser is REAL — a second accordion
        # group fed from the remote inventory; picking a row selects it for lowkey.
        # 0.54.0: store the RAW roster; set_models filters through visible_agents (the one
        # home) so the opt-in Remote-filter toggle (Task 2) can re-filter without a second copy.
        self.api_agents = list(api_agents or [])
        self.api_catalogs: dict = {}           # provider -> list[CatalogModel], set_api_catalogs
        self.search = ""                       # live filter (D3): same #search mechanism as Remote
        self._remote_filters = bool(getattr(settings, "lowkey_remote_filters", False))  # D3 toggle
        self.api_selection = None              # RemoteAgent once an API row is picked (0.54.0)
        self._api_by_id = {}                   # item id -> RemoteAgent, built in set_models
        self.set_models(models or [])

    def info_text(self, item_id: str | None) -> str:
        """The info box under the list (0.54.0 D3: lowkey is a model-list screen now)."""
        if item_id and item_id in self._api_by_id:
            a = self._api_by_id[item_id]
            model = a.model_id or "(provider default model)"
            return (f"{a.display or a.model_id or a.alias}\nProvider: {a.provider}\n"
                    f"Upstream model id: {model}")
        if item_id and item_id in self.models:
            return f"Local model {item_id} (MLX)"
        return "Highlight a row for its provider and model id."

    def set_api_catalogs(self, catalogs: dict) -> None:
        """0.54.0 Task 2 (D3): feed lowkey's API chooser the provider catalogs (pinned rows first,
        then catalog rows inside each provider group). Called by the app's cache->screen sync."""
        self.api_catalogs = dict(catalogs or {})
        self.set_models(list(self.models.values()))

    def set_search(self, query: str):
        """Live filter for the model list — the session pickers' mechanism (the #search Input
        drives this; 0.54.0 D3 adds the bar to lowkey too). Empty clears and restores all."""
        self.search = (query or "").strip()
        if not self.search:
            self.accordion.multi_open_folded = set()
            self.accordion.multi_open = None
            self.accordion.open_group = None
        self.set_models(list(self.models.values()))

    def _put_remote_filters(self, v):
        """D3 opt-in (a cycle stepper since 0.55.6): apply the Remote screen's filters to
        lowkey's API list. Sets the value it receives; persisted like the other lowkey
        settings (on_lowkey_filters_saved -> sms.save_lowkey_filters)."""
        self._remote_filters = bool(v)
        s = self.settings
        s.lowkey_remote_filters = self._remote_filters
        if s.on_lowkey_filters_saved:
            s.on_lowkey_filters_saved("remote_filters", self._remote_filters)
        self.set_models(list(self.models.values()))

    def set_models(self, models: list[LocalModel]):
        self.models = {mdl.alias: mdl for mdl in models}
        buckets: dict[str, list] = {}
        for mdl in models:
            buckets.setdefault(family_for(mdl.alias, mdl.family), []).append(mdl)
        order = sorted(buckets, key=lambda f: (f == "Other", f.lower()))
        # 0.52.0 (user): ONE combined model list — the per-family groups nest as a third
        # level under a single "Local models" group, next to the API group.
        family_groups = [
            Group(f, f"  {f} ({len(buckets[f])})", [Item(x.alias, model_names.readable(x.alias)) for x in buckets[f]])
            for f in order]
        groups = []
        # 0.54.0 Task 2 (D3): the API section is one collapsible group PER PROVIDER ("NVIDIA (n)",
        # "Mistral (n)", …), pinned rows first then catalog rows, same size sort as the Remote
        # screen, hidden providers excluded. Visibility/dedupe come from visible_agents (the
        # one home, Task 4); the Remote-screen filters are opt-in via self._remote_filters.
        deduped = api_agents_for(self.settings, self.api_agents,
                                 remote_filters=self._remote_filters)
        prov_groups = []
        # resolve the selected item id back to the agent OBJECT from the SAME id expression as
        # the Item — an empty model_id keys on the alias, and passing that alias as --api-model
        # sent the wrong upstream id (0.54.0 Task 1).
        self._api_by_id = {f"{a.provider}:{a.model_id or a.alias}": a for a in deduped}
        provs = list(dict.fromkeys([a.provider for a in deduped] + list(self.api_catalogs)))
        for prov in provs:
            if is_provider_hidden(self.settings, prov):
                continue
            pinned = [a for a in deduped if a.provider == prov]
            if prov != "nvidia":
                pinned = size_sorted(pinned, lambda a: catalog_param_size(self.settings, prov, a.model_id or a.alias))
            if self.search:
                pinned = [a for a in pinned
                          if matches_query(self.search, a.model_id or a.alias, a.display)]
            items = [Item(f"{a.provider}:{a.model_id or a.alias}",
                          f"{a.display or a.model_id or a.alias} ({PROVIDER_LABELS.get(prov, prov)})"
                          f"{param_size_suffix(self.settings, prov, a.model_id or a.alias, a.display)}")
                     for a in pinned]
            have = {a.model_id for a in pinned if a.model_id}
            tier = pinned[0].tier if pinned else "unknown"
            cat = self.api_catalogs.get(prov) or []
            for c in size_sorted(list(cat), lambda c: catalog_param_size(self.settings, prov, c.model_id)):
                if c.model_id in have:
                    continue                     # already a pinned row
                probe = _catalog_probe_agent(self.settings, prov, c, tier)
                if not _agent_visible(self.settings, probe):
                    continue
                self._api_by_id[f"{prov}:{c.model_id}"] = probe
                name = model_names.readable(c.model_id)
                if self.search and not matches_query(self.search, c.model_id, name):
                    continue
                items.append(Item(f"{CATALOG_PREFIX}{prov}:{c.model_id}",
                                  f"  {name}{param_size_suffix(self.settings, prov, c.model_id, name)}"))
            if self.search and not items:
                continue                         # a search shows only providers with a match
            if items:
                prov_groups.append(Group(prov, f"{PROVIDER_LABELS.get(prov, prov)} ({len(items)})", items))
        if prov_groups:
            total = sum(len(g.items) for g in prov_groups)
            groups.append(Group("api", f"API models {ec.SESSION_EMOJI_FREE_API_STR} ({total})", prov_groups))
        if family_groups:
            groups.append(Group(
                "local", f"Local models {ec.SESSION_EMOJI_LOCAL_STR} ({len(models)})", family_groups))
        self.accordion.set_groups(groups)
        self.accordion.collapse()              # nothing open until "Choose model…"
        self._models_loaded = True

    def refresh_keep_open(self, models: list[LocalModel] | None = None):
        """0.55.8 Task 3 (0541 plan Item B): the REFRESH path — rebuild exactly like
        set_models but without the first-population collapse, then restore the selection and
        every open level (the accordion's single open_group covers the nested provider/
        family group via its ancestor walk, so one expand restores both levels, R16)."""
        keep_id = self.accordion.selected_id
        keep_open = self.accordion.open_group
        keep_multi = (set(self.accordion.multi_open)
                      if self.accordion.multi_open is not None else None)  # collapse() clears IN PLACE
        keep_folded = set(self.accordion.multi_open_folded)
        self.set_models(models if models is not None else list(self.models.values()))
        if keep_multi is not None:
            self.accordion.multi_open = keep_multi
            self.accordion.multi_open_folded = keep_folded
        if keep_id and not self.accordion.select(keep_id):
            self.accordion.selected_id = None
        if keep_open and keep_open in {g.id for g in self.accordion.groups} | set(self.accordion._parents):
            self.accordion.expand(keep_open)

    def _cycle_effort(self):
        s = self.settings
        s.lowkey_effort = _next(effort_choices("lowkey"), s.lowkey_effort)
        if s.on_effort_saved:
            s.on_effort_saved("lowkey", s.lowkey_effort)

    def _set_lowkey_effort(self, v):
        """Direction-aware effort put (0.55.6): the stepper hands us the value to set; the
        persist path is the one _cycle_effort has always used (on_effort_saved)."""
        s = self.settings
        s.lowkey_effort = v
        if s.on_effort_saved:
            s.on_effort_saved("lowkey", v)

    def _cycle_temperature(self):
        s = self.settings
        s.lowkey_temperature = _next(TEMPERATURES, s.lowkey_temperature)
        if s.on_temperature_saved:
            s.on_temperature_saved("lowkey", s.lowkey_temperature)

    def _set_lowkey_temperature(self, v):
        """Direction-aware temperature put (0.55.6, review focus 2): the stepper hands us the
        value to set; the persist path is the one _cycle_temperature has always used."""
        s = self.settings
        s.lowkey_temperature = v
        if s.on_temperature_saved:
            s.on_temperature_saved("lowkey", v)

    def _set_thinking(self, v):
        s = self.settings
        s.lowkey_thinking = v
        if s.on_thinking_saved:
            s.on_thinking_saved(v)

    def _toggle_choose_model(self):
        self._choose_model_visible = not self._choose_model_visible

    def _toggle_choose_api(self):
        # open/close the API-models group
        g = next((g for g in self.accordion.groups if g.id == "api"), None)
        if g is None:
            return
        if self.accordion.open_group == "api":
            self.accordion.collapse()
        else:
            self.accordion.open_group = "api"

    def _thinking_label(self):
        v = self.settings.lowkey_thinking
        return "OFF" if v is False else "ON"

    def actions(self):
        s = self.settings
        # Order (user 2026-10-30, 0.50.0 waypoint lowkey-menu-drop-the-advanced):
        # C) Choose model FIRST, M) Mode, G) Launch (chosen model, then remembered
        # last), S) Set session name, and the Model settings group LAST. The old
        # "advanced" group is dropped entirely — no action sits in a "settings" section.
        acts = [
            # 0.52.0 (user): ONE "Choose model 📡 🦾" row opens the combined list (api group +
            # "Local models" wrapper) — the separate "Choose API model…" row is gone (it was a
            # no-op unless the local list had been opened first). 0.55.8 (user): the LEADING
            # model emoji is redundant with the trailing lane pair and is dropped, and the
            # ellipsis is dropped too (it overlapped the trailing emojis).
            Action("c", f"Choose model {ec.SESSION_EMOJI_FREE_API_STR} {ec.SESSION_EMOJI_LOCAL_STR}"
                        + (" (shown)" if self._choose_model_visible else ""),
                   self._toggle_choose_model, section="models"),
        ]
        # 0.56.13 (user): each mode is its OWN action item (reusing the mode emojis); the
        # rocket stays only on Go-launch-last, which sits BEFORE Choose model and shows
        # whether the last launch was a convo or a one-shot (the more recent one wins).
        last = s.last_launched_model.get("lowkey") if s.last_launched_model else None
        last_mode = (s.last_launched_mode or {}).get("lowkey")
        if last:
            mode_tag = f" ({'convo' if last_mode == 'convo' else 'one-shot'})" if last_mode else ""
            acts.insert(0, Action("g", f"{ec.gap(ec.EMOJI_GO_LAUNCH_STR)}Go launch last: "
                                       f"{model_names.readable(last)}{mode_tag}",
                                  self._launch_last, section="launch"))
        acts += [
            Action("p", f"{ec.gap(ec.LK_EMOJI_CONVO_STR)}Start convo",
                   lambda: self._start("convo"), section="launch"),
            Action("m", f"{ec.gap(ec.LK_EMOJI_ONESHOT_STR)}Start one-shot",
                   lambda: self._start("oneshot"), section="launch"),
        ]
        acts.append(Action("s", f"{ec.gap(ec.LK_EMOJI_SESSION_STR)}Set session name: {self.session_name or '(ephemeral)'}",
                           lambda: Nav("prompt:session"), section="main"))
        # 0.56.5 (user): the same model-settings template as local/remote — order e/o/j,
        # same values; lowkey keeps its own remembered lowkey_* values via its setters.
        acts += model_settings_rows(
            s, effort_get=lambda: s.lowkey_effort, effort_set=self._set_lowkey_effort,
            temp_get=lambda: s.lowkey_temperature, temp_set=self._set_lowkey_temperature,
            think_get=lambda: s.lowkey_thinking, think_set=self._set_thinking)
        # 0.54.0 (D3): opt-in Remote-screen filters for the API list; default OFF.
        acts += [
            setting("f", f"{ec.gap(ec.SESSION_EMOJI_FREE_API_STR)}Apply Remote-screen filters: "
                        f"{'ON' if self._remote_filters else 'OFF'}",
                    (False, True), lambda: self._remote_filters, self._put_remote_filters,
                    labels=("OFF", "ON"), section="model"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _base(self):
        if self.api_selection is not None:
            a = self.api_selection
            argv = ["lowkey", "--provider", a.provider]
            # 0.54.0: an alias-only row (empty model_id) launches the provider DEFAULT model —
            # never pass the alias as --api-model.
            if a.model_id:
                argv += ["--api-model", a.model_id]
            return argv + ["--effort", self.settings.lowkey_effort]
        sel = self.accordion.selected_id
        if not sel or sel not in self.models:
            return None
        return ["lowkey", "--model", sel, "--effort", self.settings.lowkey_effort]

    def _launch_key(self) -> str | None:
        """The last-launch key: the API item id (provider:model) for an API selection, else the
        local alias. 0.54.0: `g` (Go launch last) rebuilds from this pair."""
        if self.api_selection is not None:
            a = self.api_selection
            return f"{a.provider}:{a.model_id or a.alias}"
        return self.accordion.selected_id

    def _remember_launch(self, key: str):
        s = self.settings
        if s.last_launched_model is not None:
            s.last_launched_model["lowkey"] = key
        if s.on_last_launched:
            s.on_last_launched("lowkey", key, None, self.mode)   # 0.56.13: mode rides along

    def _env(self):
        v = self.settings.lowkey_thinking
        return {"LA_THINKING_OVERRIDE": "false" if v is False else "true"}

    def _launch_last(self):
        last = self.settings.last_launched_model.get("lowkey")
        if not last:
            return None
        # 0.56.13: relaunch in the remembered mode (convo vs one-shot), not always --convo
        mode = (self.settings.last_launched_mode or {}).get("lowkey") or "convo"
        argv = ["lowkey", "--convo"] if mode == "convo" else ["lowkey"]
        if ":" in last:
            # 0.54.0: an API launch is stored as provider:model — rebuild --provider/--api-model
            # (a bare --model <provider:model> relaunched a nonsense local alias before).
            prov, rest = last.split(":", 1)
            argv += ["--provider", prov]
            agent = self._api_by_id.get(last)
            model_id = agent.model_id if agent is not None else (rest if "/" in rest else "")
            if model_id:
                argv += ["--api-model", model_id]
        else:
            argv += ["--model", last]
        argv += ["--effort", self.settings.lowkey_effort]
        if self.session_name:
            argv += ["--session", self.session_name]
        return LaunchRequest("lowkey", argv, self._env(), last)

    def _start(self, mode: str):
        # 0.56.13 (user): each mode is its own action item; the mode is remembered with the
        # launch (last_launched_mode), so Go-launch-last can show which one it was.
        self.mode = mode
        if mode == "convo":
            return self._convo()
        return Nav("prompt:oneshot")

    def _convo(self):
        base = self._base()
        if base is None:
            return None
        argv = [base[0], "--convo"] + base[1:]
        if self.session_name:
            argv += ["--session", self.session_name]
        key = self._launch_key()
        if key:
            self._remember_launch(key)
        return LaunchRequest("lowkey", argv, self._env(), key or "")

    def activate_selected(self):
        # Click/Enter on a model row only SELECTS it (user 2026-10-30: highlight-to-choose
        # is finicky); launching is the explicit `p`/`g` actions. An API row selects the
        # RemoteAgent for lowkey (0.50.0 item 8); a LOCAL row clears it (0.54.0: the selection
        # used to leak, so a later local pick still launched the API model).
        sel = self.accordion.selected_id
        if sel and sel in self._api_by_id:
            self.api_selection = self._api_by_id[sel]
        elif sel and sel in self.models:
            self.api_selection = None
        return None

    def one_shot(self, prompt: str):
        base = self._base()
        if base is None or not prompt.strip():
            return None
        key = self._launch_key()
        if key:
            self._remember_launch(key)
        return LaunchRequest("lowkey", base + ["--prompt", prompt], self._env(), key or "")


@dataclass
class CatalogEntry:
    alias: str
    state: str          # COMPLETE | PRESENT | METADATA | ABSENT (the engine's own words)
    size_gb: float
    groups: str
    repo: str


@dataclass
class Review:
    aliases: list
    total_gb: float
    fits: bool


class DownloadScreen(Screen):
    """Queue-then-confirm front end for install/download-models.sh.

    Enter/Space only toggle the queue; `c` shows the review; only an explicit YES in the
    review produces the one engine call, `download-models.sh --select A --select B`. The
    engine's own disk preflight, auth checks and acquisition markers stay in charge — the
    picker never passes --all, --force or --allow-tight."""
    title = "Download local models"

    def __init__(self, settings: Settings, entries: list[CatalogEntry], free_gb: float,
                 headroom_gb: float, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self.entries = {e.alias: e for e in entries}
        self.free_gb = free_gb
        self.headroom_gb = headroom_gb
        self.queue: list[str] = []
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e.state, []).append(e)
        order = [st for st in ("ABSENT", "METADATA", "PRESENT", "COMPLETE") if st in groups]
        # "Absent" read like a mistake (user 2026-10-30); it means: in a catalog, not on disk.
        state_labels = {"ABSENT": "Available for download from existing catalogs"}
        self.accordion.set_groups([
            Group(st, f"{state_labels.get(st, st.title())} ({len(groups[st])})",
                  [Item(e.alias, f"{model_names.readable(e.alias)}  ~{e.size_gb:g} GB  {e.groups}") for e in groups[st]])
            for st in order])

    def toggle_selected(self):
        sel = self.accordion.selected_id
        if not sel:
            return None
        if sel in self.queue:
            self.queue.remove(sel)
        else:
            self.queue.append(sel)
        return None

    def activate_selected(self):
        return self.toggle_selected()

    def review(self) -> Review:
        total = sum(self.entries[a].size_gb for a in self.queue if self.entries[a].state != "COMPLETE")
        return Review(list(self.queue), total, total <= max(0.0, self.free_gb - self.headroom_gb))

    def confirm(self, yes: bool):
        if not yes or not self.queue:
            return None
        argv = ["download"]
        for alias in self.queue:
            argv += ["--select", alias]
        return LaunchRequest("download", argv, {}, self.queue[-1])

    def actions(self):
        acts = [Action("c", f"Review queue and confirm ({len(self.queue)} queued)",
                       lambda: Nav("prompt:download-review"), enabled=bool(self.queue)),
                Action("x", "Clear queue", lambda: self.queue.clear())]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts


