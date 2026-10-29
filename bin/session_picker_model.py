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


# The one collapsed group row in the lane pickers (user 2026-10-29): while folded, the
# settings rows render under this header instead of as top-level rows. Key "0" never
# matches a letter hotkey, so it cannot collide with a real action.
SETTINGS_GROUP = Action("0", "Settings", lambda: None)


def setting(key: str, label: str, choices, get: Callable[[], object], put: Callable[[object], object],
            enabled: bool = True) -> Action:
    """A row whose value cycles through `choices` in either direction (wraps)."""
    def step(d: int):
        try:
            i = list(choices).index(get())
        except ValueError:
            i = -1 if d > 0 else 0
        put(list(choices)[(i + d) % len(choices)])
    return Action(key, label, lambda: step(1), enabled=enabled, step=step)


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


def thinking_row(s: Settings, key: str = "j") -> Action:
    """The thinking toggle row: ON (default) ↔ OFF. The row stores only the two states that
    differ behaviorally — None and True both launch with thinking ON — so cycling back from
    OFF returns to the launch default rather than to a second, indistinguishable ON."""
    def label():
        return "ON (default)" if s.thinking_session is None else "ON" if s.thinking_session else "OFF"

    def put(v):
        s.thinking_session = v
        if s.on_thinking_saved:
            s.on_thinking_saved(s.thinking_session)

    return setting(key, f"{ec.EMOJI_THINKING_STR} Thinking: {label()}", (None, False),
                   lambda: s.thinking_session, put)


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


def tier_label(tier: str, provider: str) -> str:
    if provider == "nvidia" and tier == "unknown":
        return "no daily cap/40/min"
    if tier == "trial":
        return "trial — possible cost"
    return tier


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
    deny_gh_release: bool = False    # re-add "Bash(gh release create:*)" to the blind-trust deny list (off since 0.32.0)
    remote_temperature: str = sms.DEFAULT_TEMPERATURE["remote_api_session"]
    local_temperature: str = sms.DEFAULT_TEMPERATURE["local_session"]
    lowkey_effort: str = sms.DEFAULT_EFFORT["lowkey"]
    lowkey_temperature: str = sms.DEFAULT_TEMPERATURE["lowkey"]
    # Session thinking toggle (both lanes; waypoint fa-thinking-toggle). None = the launch
    # default, which is thinking ON for session launches; True/False = a remembered choice the
    # launchers apply via LA_THINKING_OVERRIDE (model constraints may override it, and the info
    # box says so). Persisted by the Picker through on_thinking_saved -> sms.save_thinking_session.
    thinking_session: bool | None = None
    rate_limiter: dict = field(default_factory=dict)
    memguard: dict = field(default_factory=lambda: dict(sms.DEFAULT_MEMGUARD))
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
    # Called with {"registry_auto_sync": v} when the Local screen's auto-sync row toggles; persists it.
    on_memguard_saved: Callable[[dict], object] | None = None
    # Called with (lane, value) when the user CONFIRMS an effort change; persists it.
    on_effort_saved: Callable[[str, str], object] | None = None
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
    # Remote tier of the last-launched alias (lets Go-last add --include-trials with no inventory).
    last_launched_tier: dict = field(default_factory=dict)
    # Called with (lane, alias, tier|None) when a launch request is made; persists it.
    on_last_launched: Callable[[str, str, str | None], object] | None = None
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


def _next(choices, current):
    try:
        return choices[(choices.index(current) + 1) % len(choices)]
    except ValueError:
        return choices[0]


def _effort_label(value: str) -> str:
    return "Provider default" if value == sms.PROVIDER_DEFAULT else value


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

    def is_open(self, group_id: str) -> bool:
        if self.multi_open is not None:
            return group_id in self.multi_open
        return group_id == self.open_group

    def set_groups(self, groups: list[Group]):
        self.groups = groups
        ids = {g.id for g in groups}
        if self.open_group not in ids:
            self.open_group = None
        if self.selected_id and not any(i.id == self.selected_id for g in groups for i in g.items):
            self.selected_id = None
        if self.selected_id:
            self.open_group = self._group_of(self.selected_id)
        if self.open_group is None and groups:
            # The first group that HAS rows: an action header (e.g. "Register new…") opens nothing.
            self.open_group = next((g.id for g in groups if g.items), groups[0].id)

    def _group_of(self, item_id):
        for g in self.groups:
            if any(i.id == item_id for i in g.items):
                return g.id
        return None

    def expand(self, group_id: str) -> bool:
        if any(g.id == group_id for g in self.groups):
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
        """Flattened visible rows: ('group', Group) headers and ('item', Item) for open ones."""
        out = []
        for g in self.groups:
            out.append(("group", g))
            if self.is_open(g.id):
                out.extend(("item", i) for i in g.items)
        return out


# --- screens -----------------------------------------------------------------------------
class Screen:
    title = ""
    lane_key = ""
    # Lane pickers fold their settings rows under one group header (user 2026-10-29,
    # waypoint picker-collapsed-settings-group); every other screen renders plain rows.
    settings_collapsed = True
    # Rendered in its own colour below the policy line when a screen sets it (Home does).
    version_line = ""

    def toggle_settings(self):
        self.settings_collapsed = not self.settings_collapsed

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
            acts.append(Action("q", "Quit", lambda: QUIT, section="nav"))
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
                (0, 1, 2), lambda: s.auto_mode, lambda v: setattr(s, "auto_mode", v)),
    ]
    # Classifier source only relevant when auto_mode == 1 (classifier), not blind-trust or off
    if s.auto_mode == 1:
        acts.append(setting("z", f"Classifier: {CLASSIFIER_SOURCE_LABELS[s.classifier_source]}",
                (0, 1, 2), lambda: s.classifier_source, lambda v: setattr(s, "classifier_source", v)))
    acts += [
        setting("t", f"{ec.EMOJI_TELEMETRY_ON_STR} Telemetry: {'ON' if s.telemetry else 'OFF'}", *_toggle(s, "telemetry")),
        setting("p", f"{ec.EMOJI_STOP_HOOK_STR} Queued-prompt hook: {'ON' if s.stop_hook else 'OFF'}",
                *_toggle(s, "stop_hook")),
        # Key 'd': 'g' is the dynamic Go-launch key and 'h' belongs to the remote filter
        # submenu (FilterSubmenuTests) — keep this row off both.
        setting("d", f"Deny 'gh release create' in blind-trust: {'ON' if s.deny_gh_release else 'OFF'}",
                *_toggle(s, "deny_gh_release")),
    ]
    if include_watcher:
        acts.append(setting("w", f"{ec.EMOJI_WATCHER_STR} Watcher: {'ON' if s.watcher else 'OFF'}",
                            *_toggle(s, "watcher")))
    return acts


# User-facing name of the memory guard (user 2026-10-23): "memory guard" read like memory.md /
# auto-memory. Internal keys stay "memguard" / LA_MEMGUARD_* so saved settings keep working.
OOM_PROTECTION_NAME = "RAM usage out-of-memory protection"


def _tool_actions(backend: bool = True, local_session: bool = False, home: bool = False):
    # The backend manager drives LOCAL runtimes (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM),
    # so the Remote lane does not offer it (user, 2026-10-04).
    # Local session picker (local_session=True) hides API keys and NVIDIA rate limiter.
    # Home screen (home=True) shows only API keys and Backend manager, no NVIDIA rate limiter.
    acts = [
        Action("v", f"{ec.EMOJI_TOOLS_STR} Backend manager (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM)",
               lambda: Nav("runtime_manager"), section="tools"),
    ]
    if local_session:
        # It protects LOCAL sessions only, so only the Local screen offers it — not Remote, and not
        # Home either, where it read as a global setting (user 2026-10-23).
        acts.append(Action("u", f"{ec.gap(ec.EMOJI_MEMORY_GUARD_STR)}{OOM_PROTECTION_NAME}", lambda: Nav("memguard"),
                           section="tools"))
    if not local_session:
        acts.insert(0, Action("k", f"{ec.EMOJI_KEY_STR} API keys — install / set up", lambda: Nav("api_keys"), section="tools"))
        if not home:
            acts.append(Action("n", f"{ec.EMOJI_NVIDIA_RATE_LIMITER_STR} NVIDIA rate limiter", lambda: Nav("rate_limiter"), section="tools"))
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
    def policy(self):
        import os
        root = Path(__file__).resolve().parent.parent
        lines = [f"free-agents v{plugin_version()}",
                 f"Installed at: {root}",
                 f"Config: {os.environ.get('LA_CONFIG_SOURCE') or (root / 'config')}",
                 f"Remote launcher: {os.environ.get('CSL_REMOTE_LAUNCHER') or (root / 'bin/remote-session.sh')}",
                 f"Catalog cache: {catalog_cache_dir()}"]
        return "\n".join(lines)

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
        acts = [
            Action("l", f"{ec.SESSION_EMOJI_LOCAL_STR} Local sessions ({self._format_count(self.local_count, '?')} on disk)",
                   lambda: Nav("local", HOME_OWNED), section="lanes"),
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR} Remote free API sessions ({self._format_count(self.catalog_total, '?')} listed)",
                   lambda: Nav("remote", HOME_OWNED), section="lanes"),
            Action("c", f"{ec.EMOJI_CLOUD_CONFIG_STR}  {ec.EMOJI_TOOLS_STR} Cloud session configuration…", lambda: Nav("cloud_config", HOME_OWNED), section="tools"),
            Action("d", f"{ec.EMOJI_DOWNLOAD_STR} Download local models", lambda: Nav("tool:download"), section="tools"),
            Action("o", f"{ec.LK_EMOJI_CONVO_STR} Lowkey — local dispatch chat", lambda: Nav("tool:lowkey"), section="tools"),
        ]
        acts.append(Action("h", f"{ec.EMOJI_THEME_STR} Theme colors", lambda: Nav("theme_config", HOME_OWNED), section="tools"))
        acts += _tool_actions(home=True)
        # Which version am I running, and where does it live — user request 2026-10-28.
        # Key i, not a: a is the lanes' Auto-mode key, and Home never reuses a lane setting key.
        acts.append(Action("i", "About — version and paths", lambda: Nav("about", HOME_OWNED), section="tools"))
        # Settings like NVIDIA rate limiter, auto-mode, prompt hook, watcher are in the lanes
        acts.append(Action("q", "Quit", lambda: QUIT, section="nav"))
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
                            + (f" ({mdl.backend}" + (", thinking" if mdl.thinking else "") + ")" if mdl.backend else "")
                            + (f"  [{mdl.roles}]" if mdl.roles else ""))
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
        roles = f" · Roles: {mdl.roles}" if mdl.roles else ""
        return f"{model_names.readable(mdl.alias)}\nAlias: {mdl.alias}\n{serve}{roles}"

    def actions(self):
        s = self.settings
        acts = [
            setting("e", f"{ec.EMOJI_EFFORT_STR} Effort: {s.local_effort}", effort_choices("local_session"),
                    lambda: s.local_effort, lambda v: self._set_effort(v)),
            setting("o", f"{ec.EMOJI_TEMPERATURE_STR}  Temperature: {s.local_temperature or '<provider default>'}",
                    TEMPERATURES, lambda: s.local_temperature, lambda v: self._set_local_temp(v)),
            thinking_row(s),
            Action("c", self._choose_model_label(), self._toggle_choose_model, section="models"),  # top-level, never folded into Settings
            Action("s", f"{ec.SESSION_EMOJI_FREE_API_STR}  Switch to Remote free API sessions",
                   lambda: Nav("remote", self.owner), section="lanes"),
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR}  Remote free API sessions", lambda: Nav("remote", self.owner),
                   section="hidden"),
            setting("m", f"{ec.EMOJI_MCP_STR}  MCPs: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                    *_toggle(s, "enable_mcp")),
            Action("y", f"Auto-sync local registry with the disk (no prompts): "
                        f"{'ON' if s.memguard.get('registry_auto_sync') else 'OFF'}",
                   self._toggle_registry_auto_sync),
        ]
        # Add "Launch last model" if we have a saved last model
        last_local = s.last_launched_model.get("local_session") if s.last_launched_model else None
        last_local = s.last_launched_model.get("local_session") if s.last_launched_model else None
        # No `in self.models` check: the list is lazy and usually not loaded yet, and launching
        # only needs alias + effort. A since-deleted model fails in the launcher, visibly.
        if last_local:
            acts.insert(0, Action("g", f"{ec.EMOJI_GO_LAUNCH_STR} Go launch: {model_names.readable(last_local)} session",
                           lambda: self._launch_last("local_session"), section="launch"))
        acts += _common_toggles(s, include_watcher=True)
        acts += _tool_actions(backend=True, local_session=True)
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _toggle_registry_auto_sync(self):
        """The Local screen's auto-sync row (0.43.0): same store key the guard screen used,
        persisted through the on_memguard_saved callback the Picker wires to sms.save_memguard."""
        v = not self.settings.memguard.get("registry_auto_sync")
        self.settings.memguard["registry_auto_sync"] = v
        if self.settings.on_memguard_saved:
            self.settings.on_memguard_saved({"registry_auto_sync": v})

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
            s.on_last_launched("local_session", mdl.alias, None)
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
        argv = ["local", launch_alias, s.local_effort]
        # Through the ENVIRONMENT, like every other toggle: csl --picker-launch execs the launcher
        # with "<alias> <effort>", and the launcher reads options only before the alias, so a
        # trailing --temperature would be silently dropped. Hotswap passes it to Rapid as
        # --default-temperature and counts it in the server-reuse identity.
        if s.local_temperature:
            env["LA_TEMPERATURE"] = s.local_temperature
        return LaunchRequest("local", argv, env, mdl.alias)


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
        vis = sum(1 for a in self.agents if self._visible(a) and a.model_id != "SELECT")
        hidden = self.hidden_count()
        for prov, cat in self.catalogs.items():
            if not isinstance(cat, list) or not cat:
                continue
            # Same tier/dedupe rules as _catalog_items, so this count can never disagree
            # with the rendered rows (2026-10-27: the subheadline under the title counted
            # the agent roster only and read as fiction next to the real lists).
            have = {a.model_id for a in self.agents if a.provider == prov
                    and a.model_id != "SELECT" and self._visible(a)}
            pinned0 = next((a for a in self.agents if a.provider == prov and a.model_id != "SELECT"), None)
            tier = pinned0.tier if pinned0 else "unknown"
            for c in cat:
                if c.model_id in have:
                    continue
                if self._visible(self._catalog_probe(prov, c, tier)):
                    vis += 1
                else:
                    hidden += 1
        return f"{vis} model(s) visible  (hidden: {hidden})"

    def __init__(self, settings: Settings, agents: list[RemoteAgent] | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._models_loaded = False
        self._choose_model_visible = False  # Model list hidden by default
        # provider -> list[CatalogModel] once loaded, or an error string; absent = not fetched.
        self.catalogs: dict = {}
        self._catalog_pending: set = set()
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
        for agent in self.agents:
            if not self._visible(agent):
                continue
            if agent.provider not in buckets:
                order.append(agent.provider)
            buckets.setdefault(agent.provider, []).append(agent)
        groups = []
        for prov in order:
            # A "choose model" placeholder (registry model_id SELECT) is not a launchable row —
            # users cannot type the real id — so the provider's roster IS its catalog: the
            # placeholder never renders, only its real pinned rows and the catalog do (2026-10-20).
            # NVIDIA's pinned order is curated by hand (user 2026-10-20: "except nvidia we
            # have sorted those manually very deliberately") — every other provider's pins
            # sort size-descending, unknowns last in inventory order.
            pinned = [a for a in buckets[prov] if a.model_id != "SELECT"]
            if prov != "nvidia":
                pinned = size_sorted(pinned, lambda a: self._catalog_size(prov, a.model_id))
            if self.search:
                pinned = [a for a in pinned if self._matches(a.model_id, a.display)]
            items = [Item(a.alias, f"{a.display}{self._size_suffix(prov, a.model_id, a.display)}  · {tier_label(a.tier, a.provider)}{lane_tag(a.provider, a.model_id)}")
                     for a in pinned]
            items += self._catalog_items(prov, buckets[prov])
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
        q = self.search.casefold()
        return q in (model_id or "").casefold() or q in (display or "").casefold()

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
        """The probe agent a catalog row is judged and rendered through — ONE construction
        site, so the subheadline counts can never disagree with the rendered rows."""
        return RemoteAgent(f"{CATALOG_PREFIX}{prov}:{c.model_id}", prov, c.model_id, tier,
                           local_capable=self._catalog_local_capable(prov, c.model_id),
                           old=self._catalog_old(prov, c.model_id),
                           model_id=c.model_id)

    def _catalog_items(self, prov: str, pinned: list) -> list:
        cat = self.catalogs.get(prov)
        if prov in self._catalog_pending and cat is None:
            return [Item(f"{CATALOG_PREFIX}{prov}:__loading__", "  … loading the provider's model list", style="dim")]
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

    def _size_suffix(self, prov: str, model_id: str, display: str) -> str:
        """'  · 1.05T' appended to a row name when the size is known and the name does not
        already state it (user 2026-10-29: param size in the name by default, not a separate
        column, and never duplicated when the name already carries it)."""
        label = format_param_count(self._catalog_size(prov, model_id))
        if not label or label.lower() in (display or "").lower():
            return ""
        return f"  · {label}"

    def _catalog_size(self, prov: str, model_id: str) -> "float | None":
        """The parameter count for the size sort (Settings.model_param_count, set by the
        picker), or None when unknown — the row then sorts after the sized ones. A broken
        verdict degrades to None, never to a render failure."""
        fn = getattr(self.settings, "model_param_count", None)
        if fn is None:
            return None
        try:
            v = fn(prov, model_id)
        except Exception:
            return None
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

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
                base = (f"{model_names.readable(parts[2])}\n{parts[2]} · "
                        f"{PROVIDER_LABELS.get(parts[1], parts[1])} catalog · "
                        f"Lane: {lane_for(parts[1], parts[2])}")
                notices = [thinking_constraint(parts[1], parts[2], self.settings.thinking_session),
                           PROVIDER_ADVISORIES.get(parts[1], "")]
                return "\n".join([base, *filter(None, notices)])
        agent = next((a for a in self.agents if a.alias == item_id), None)
        if agent is None:
            return "Hover or highlight a model to see notes about it."
        lane_line = f"Lane: {lane_for(agent.provider, agent.model_id)}"
        notices = [thinking_constraint(agent.provider, agent.model_id, self.settings.thinking_session),
                   PROVIDER_ADVISORIES.get(agent.provider, "")]
        note = model_note(agent.alias)
        base = (f"{agent.display}\n{lane_line}\n{note}" if note
                else f"{agent.display}\n{lane_line}\nNo notes for this model yet.")
        return "\n".join([base, *filter(None, notices)])

    def _visible(self, agent: RemoteAgent) -> bool:
        s = self.settings
        # A model whose provider has no saved key cannot launch, so it is not listed at all.
        if not agent.has_key:
            return False
        if agent.tier == "trial" and not s.include_trials:
            return False
        if agent.local_capable and not s.local_capable_shown:
            return False
        if agent.broken and not s.show_broken:
            return False
        # The age filter (T6): only catalog probes carry old=True — pinned registry rows
        # have no dates and are never hidden by it.
        if agent.old and not s.show_old:
            return False
        return True

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
                       lambda: getattr(s, attr), put)

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
            setting("e", f"{ec.EMOJI_EFFORT_STR} Effort: {_effort_label(s.remote_effort)}",
                    effort_choices("remote_api_session"), lambda: s.remote_effort, lambda v: self._set_effort(v)),
            setting("o", f"{ec.EMOJI_TEMPERATURE_STR}  Temperature: {s.remote_temperature or '<provider default>'}",
                    TEMPERATURES, lambda: s.remote_temperature, lambda v: self._set_remote_temp(v)),
            thinking_row(s),
            Action("c", self._choose_model_label(), self._toggle_choose_model, section="models"),  # top-level, never folded into Settings
            # No "find model" action row (user, 2026-10-21): the filter box at the top of
            # the open model list IS the search — typing there filters live from the first
            # character, so a separate menu entry would only duplicate it.
            Action("s", f"{ec.SESSION_EMOJI_LOCAL_STR}  Switch to Local sessions", lambda: Nav("local", self.owner), section="lanes"),
            Action("l", f"{ec.SESSION_EMOJI_LOCAL_STR}  Local sessions", lambda: Nav("local", self.owner), section="hidden"),
            Action("y", f"{ec.EMOJI_FILTER_STR}  Filter settings…", lambda: Nav("remote_filters", self.owner), section="tools"),
            setting("m", f"{ec.EMOJI_MCP_STR}  MCPs: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                    *_toggle(s, "enable_mcp")),
        ]
        # Add "Launch last model" if we have a saved last model
        last_remote = s.last_launched_model.get("remote_api_session") if s.last_launched_model else None
        # No visible-row check: the agent list is lazy. The tier needed for --include-trials is
        # remembered with the alias (user decision 2026-10-03: no 5.7 s inventory call on `g`).
        if last_remote:
            acts.insert(0, Action("g", f"{ec.EMOJI_GO_LAUNCH_STR} Go launch: {last_remote} session",
                           lambda: self._launch_last("remote_api_session"), section="launch"))
        acts += _common_toggles(s, include_watcher=False)
        acts += _tool_actions(backend=False)
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
            s.on_last_launched("remote_api_session", remembered, tier)
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
    """The actions a lane picker renders, with settings rows folded under one group header
    while collapsed (user 2026-10-29, waypoint picker-collapsed-settings-group). Returns
    (rendered, hidden_settings): SETTINGS_GROUP replaces the settings rows when folded;
    every other section stays a top-level row. Only the two lane screens fold — sub-screens
    (rate limiter, memguard, filters, …) keep their plain rows, and so does Home."""
    if sm is None:
        return [], []
    acts = [a for a in sm.actions() if a.section != "hidden"]
    if isinstance(sm, (LocalScreen, RemoteScreen)):
        settings = [a for a in acts if a.section == "settings"]
        if settings:
            top = [a for a in acts if a.section != "settings"]
            if sm.settings_collapsed:
                return top + [SETTINGS_GROUP], settings
            # expanded: the header stays, so Enter on it folds the group again
            return top + [SETTINGS_GROUP] + settings, []
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
            Action("h", f"{ec.EMOJI_TRIALS_STR}  Limited trials: {'SHOWN' if s.include_trials else 'HIDDEN'}",
                   lambda: self._toggle("include_trials")),
            Action("f", f"{ec.EMOJI_LOCAL_CAPABLE_STR}  Locally-runnable models: {'SHOWN' if s.local_capable_shown else 'HIDDEN'}",
                   lambda: self._toggle("local_capable_shown")),
            Action("u", f"{ec.EMOJI_BROKEN_MODELS_STR}  Unworking models: {'SHOWN' if s.show_broken else 'HIDDEN'}",
                   lambda: self._toggle("show_broken")),
            Action("o", f"{ec.EMOJI_OLD_MODELS_STR}  Old models (over the age cutoff): "
                        f"{'SHOWN' if s.show_old else 'HIDDEN'}",
                   lambda: self._toggle("show_old")),
            Action("x", f"{ec.EMOJI_HIDDEN_REPORT_STR}  Hidden-model report", lambda: Nav("tool:report"), section="tools"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _toggle(self, attr):
        setattr(self.settings, attr, not getattr(self.settings, attr))
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
                               lambda: self._cycle_security_review()))
        bypass_reason = cse.bypass_blocked_by_policy()
        acts += [
            Action("x", f"{ec.EMOJI_SHIELD_STR}  Agent dispatch (paid sessions): "
                        f"{cse.ROUTE_LABELS[s.routes.get('agent', 'free-first')]}",
                   lambda: self._cycle_route("agent")),
            Action("p", f"{ec.EMOJI_AUTO_MODE_STR}  Bypass Permissions (blind-trust): {'ON' if s.cloud_bypass_permissions else 'OFF'}"
                        f"  {NOT_GATEWAY_NOTE}",
                   lambda: self._toggle("cloud_bypass_permissions")),
            Action("d", self._bypass_default_label(bypass_reason),
                   self._cycle_default_mode, enabled=bypass_reason is None),
            Action("e", "Check whether auto mode's safety checks are free here "
                        "(sends one small request)", self._run_probe),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _toggle(self, attr):
        setattr(self.settings, attr, not getattr(self.settings, attr))

    def _cycle_route(self, path: str):
        """Cycle the path's routing mode through free-first -> free-only -> paid and persist."""
        s = self.settings
        order = list(cse.ROUTE_MODES)
        current = s.routes.get(path, "free-first")
        s.routes[path] = order[(order.index(current) + 1) % len(order)] if current in order else "free-only"
        if s.on_cloud_saved:
            s.on_cloud_saved({"route": (path, s.routes[path])})

    def _bypass_default_label(self, reason: str | None) -> str:
        if reason:
            return f"Default permission mode for sessions you start yourself: disabled — {reason}"
        registered = " — overruled by the registered cloud profile" if self._profile_overrides_mode() else ""
        return (f"Default permission mode for sessions you start yourself: "
                f"{self.settings.default_mode or cse.default_mode() or 'Auto'}{registered}")

    def _profile_overrides_mode(self) -> bool:
        # Release 2 registers the cloud profile; in Release 1 this reads a fixture store value.
        return bool(getattr(self.settings, "cloud_profile_default_mode", None))

    def _cycle_default_mode(self):
        if cse.bypass_blocked_by_policy():
            return                      # csl never works around a managed policy (spec A2)
        order = ["auto", "bypassPermissions"]
        cur = self.settings.default_mode or cse.default_mode() or "auto"
        nxt = order[(order.index(cur) + 1) % len(order)]
        self.settings.default_mode = nxt
        cse.save_default_mode(nxt)
        if self.settings.on_cloud_saved:
            self.settings.on_cloud_saved({"default_mode": nxt})

    def _run_probe(self):
        """Row e: the user CONFIRMED the small request (the ConfirmPanel path asks before this
        runs). The probe is cached; its verdict feeds the cost box."""
        import subprocess as sp
        probe_py = Path(__file__).resolve().parent / "fa-eligibility-probe.py"
        r = sp.run([sys.executable, str(probe_py), "--json"],
                   capture_output=True, text=True, timeout=330)
        try:
            self.settings.eligibility = json.loads(r.stdout)
        except ValueError:
            self.settings.eligibility = {"verdict": "unknown", "evidence": (r.stderr or r.stdout)[:200]}

    def info_text(self, item_id: str | None) -> str:
        """The three routing modes, each with its one-line contract (plain Action rows pass
        item_id=None, so the box always shows the whole table); the gateway cost box appears
        ONLY when the cached verdict is not-eligible (A3: facts, only for users it applies to)."""
        extra = ""
        el = self.settings.eligibility
        if el and el.get("verdict") == "not-eligible":
            extra = ("\n\nCost note: on this setup auto mode's safety checks are billed as normal "
                     "token usage. Bypass with allow/deny rules avoids those calls; the lasting fix "
                     "is a gateway that passes the safeguards fields through unchanged.")
        return (
            "How the Agent tool is routed on cloud sessions:\n"
            "· Free first, paid as last resort — every Agent call is redirected to a free model; "
            "after this session's free attempts are honestly exhausted "
            "(\"paid_fallback_allowed\": true in the dispatch result), ONE native paid call is "
            "let through automatically.\n"
            "· Free only — every Agent call is redirected and the hook never falls back to paid.\n"
            "· Paid (free-agents stays out) — the native Agent tool runs paid subagents as usual; "
            "free models only when you ask."
            + extra
        )

    def _cycle_security_review(self):
        s = self.settings
        s.security_review = cse.next_state(s.security_review)
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

    Two settings rows, one per lane, behaving exactly like the effort/temperature rows
    (user 2026-10-15): left/right arrows step the accent, Enter and mouse click step
    forward, wrapping through THEME_PRESETS; `d` restores the shipped defaults. The
    border is re-derived on every change as the accent at the same lightness, 55%
    saturation. Theme files live in the plugin's config/ dir; LA_THEME_CONFIG_DIR
    overrides that location so tests can cycle colors without ever writing into the
    repo (the 0.34.13 release shipped a cycled-over default otherwise).
    """
    title = f"{ec.EMOJI_THEME_STR} Theme Colors"

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
        """Row label per user 2026-10-15: the color name spelled out (accessibility), the
        hex rendered in the colour it names, and a swatch block that holds a FIXED column —
        the name field is padded to the palette's widest name so cycling never shifts it —
        plus a thin slice in the derived border colour, previewing accent + border together.
        The lane word is padded ("Local" vs "Remote" differ by one cell) so BOTH rows' hex,
        swatch and slice share a column (user 2026-10-15)."""
        theme = self.local_theme if which == "local" else self.remote_theme
        emoji = ec.SESSION_EMOJI_LOCAL_STR if which == "local" else ec.SESSION_EMOJI_FREE_API_STR
        accent = theme["theme"]["accent_color"]
        name = dict(THEME_PRESETS).get(accent.upper(), "custom")
        width = max(len(n) for _, n in THEME_PRESETS)
        lane_pad = " " * (len("Remote") - len(which.capitalize()))
        border = theme["theme"].get("border_color", "")
        if RICH_AVAILABLE:
            return Text.assemble(
                f"{emoji} {which.capitalize()}:{lane_pad} ",
                (f"{name:<{width}}", "bold"),
                " ",
                (accent, accent),
                "  ",
                Text("    ", style=f"on {accent}"),
                Text(" ", style=f"on {border}"),
            )
        return f"{emoji} {which.capitalize()}:{lane_pad} {name:<{width}} {accent} + border {border}"

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
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        r2, g2, b2 = colorsys.hls_to_rgb(h, l, 0.55)
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
                    lambda v: self._set_accent("local", v)),
            setting("r", self._lane_label("remote"), presets,
                    lambda: self.remote_theme["theme"]["accent_color"],
                    lambda v: self._set_accent("remote", v)),
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

    def _cycle(self, key, steps, default):
        self._set(key, _next(steps, self.value(key, default)))

    def actions(self):
        mode = self.value("mode", "sliding_window")
        acts = [
            Action("o", f"Mode: {mode}", lambda: self._set(
                "mode", _next(sms.RATE_LIMITER_MODES, mode))),
            Action("r", f"Requests per minute: {self.value('rpm', 40)}",
                   lambda: self._cycle("rpm", self.RPM_STEPS, 40)),
            Action("c", (f"Bucket capacity: {self.value('bucket_capacity', 6)}"
                         if mode == "smooth_bucket" else "Bucket capacity: unavailable (smooth_bucket only)"),
                   lambda: self._cycle("bucket_capacity", self.CAP_STEPS, 6),
                   enabled=mode == "smooth_bucket"),
            Action("w", f"Max wait: {self.value('max_wait', 300)}s",
                   lambda: self._cycle("max_wait", self.WAIT_STEPS, 300)),
            Action("d", f"429 base cooldown: {self.value('cooldown', 30)}s",
                   lambda: self._cycle("cooldown", self.COOLDOWN_STEPS, 30)),
            Action("u", f"429 max cooldown: {self.value('max_cooldown', 300)}s",
                   lambda: self._cycle("max_cooldown", self.MAX_COOLDOWN_STEPS, 300)),
            Action("y", f"429 backoff multiplier: ×{self.value('backoff_multiplier', 2.0)}",
                   lambda: self._cycle("backoff_multiplier", self.MULTIPLIER_STEPS, 2.0)),
            Action("z", f"429 max retries: {self.value('max_retries', 5)}",
                   lambda: self._cycle("max_retries", self.RETRY_STEPS, 5)),
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

    def _toggle(self, key):
        self._set(key, not self.value(key))

    def _cycle(self, key, steps):
        """Next step that keeps soft below hard; a pair-breaking step is skipped, never saved."""
        cur = self.value(key)
        order = list(steps)
        start = order.index(cur) + 1 if cur in order else 0
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
            Action("g", f"{OOM_PROTECTION_NAME}: {onoff(on)}", lambda: self._toggle("enabled")),
            Action("t", f"Start sessions inside tmux (lets the guard type /compact): {onoff(self.value('tmux'))}",
                   lambda: self._toggle("tmux"), enabled=on),
            Action("s", f"Compact at RAM ≥ {self.value('soft_pct')}% (when the session is idle)",
                   lambda: self._cycle("soft_pct", self.SOFT_STEPS), enabled=on),
            Action("h", f"Compact the largest session at once at RAM ≥ {self.value('hard_pct')}%",
                   lambda: self._cycle("hard_pct", self.HARD_STEPS), enabled=on),
            Action("w", f"Wait for {self.value('quiet_s')}s of no typing before typing into a session",
                   lambda: self._cycle("quiet_s", self.QUIET_STEPS), enabled=on),
            Action("c", f"After a memory-limit abort, continue by itself: {onoff(self.value('auto_continue'))}",
                   lambda: self._toggle("auto_continue"), enabled=on),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts


# install/manage-backend.py --backend choices; tests compare this against its registry.
# ★ Keep in step with install/manage-backend.py's manager imports: adding a backend manager
# without adding it here leaves the picker unable to name it, and the contract test below is the
# only guard (it caught `moespresso` missing here for three releases — 0.45.0/0.45.1/0.45.5).
RUNTIME_BACKENDS = ("rapid-mlx", "vllm-mlx", "omlx", "llama-cpp", "litellm", "moespresso")
# Per-version operations beyond install/validate/info, by backend: only the ones that backend's
# manager actually implements (the base class raises "not supported"), so the menu never offers
# a row that can only fail. Tests compare this against the managers' real overrides.
BACKEND_VERSION_OPS = {"rapid-mlx": ("snapshot", "promote", "remove")}


@dataclass
class VersionEntry:
    version: str
    status: str = ""          # installed: complete/incomplete; releases: "installed" or ""


RAPID_CORE_REFUSAL = ("{version} is the only installed Rapid-MLX version — not removed. Rapid-MLX is a "
                      "core dependency of free-agents (it runs local models).")


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
            items = [Item(e.version, f"{e.version:14} {e.status}") for e in entries]
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
            if self.backend == "rapid-mlx" and not any(
                    e.version != v and e.status == "complete" for e in (self.entries or [])):
                return Nav("warn:" + RAPID_CORE_REFUSAL.format(version=v), self.owner)
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
                                lambda v: setattr(self, "promote_after_install", v)))
        acts.append(Action("b", f"{ec.EMOJI_BACK_STR}  Back to Backend manager", lambda: BACK, section="nav"))
        check_action_table(acts)
        return acts

    def handle_key(self, key: str):
        if key == "escape":
            return BACK
        return super().handle_key(key)


VERSION_OP_EMOJI = {"install": ec.EMOJI_INSTALL_STR, "validate": ec.EMOJI_VALIDATE_STR,
                    "info": ec.EMOJI_INFO_STR, "snapshot": ec.EMOJI_SNAPSHOT_STR,
                    "promote": ec.EMOJI_PROMOTE_STR, "remove": ec.EMOJI_REMOVE_STR}


def runtime_actions(screen) -> list:
    """The Backend manager rows, shared by both entry screens (Home `v` and Launchd's Back)."""
    b = screen.settings.runtime_backend
    pick = lambda op: (lambda: Nav(f"runtime_versions:{op}", screen.owner))   # noqa: E731
    acts = [
        Action("c", f"{ec.EMOJI_BACKEND_STR} Backend: {b}", screen._cycle_backend),
        Action("r", f"{ec.EMOJI_RELEASES_STR} List installable releases (incl. prereleases)", lambda: Nav("tool:rt-releases"), section="tools"),
        Action("i", f"{ec.EMOJI_INSTALL_STR} Install a release (choose from the list)", pick("install"), section="tools"),
        Action("t", f"{ec.EMOJI_VALIDATE_STR} Validate an installed version", pick("validate"), section="tools"),
        Action("f", f"{ec.EMOJI_INFO_STR}  Show info for an installed version", pick("info"), section="tools"),
    ]
    ops = BACKEND_VERSION_OPS.get(b, ())
    for key, op, label in (("s", "snapshot", "Snapshot an installed version"),
                           ("p", "promote", "Promote pins to an installed version"),
                           ("x", "remove", "Remove an installed version")):
        if op in ops:
            acts.append(Action(key, f"{ec.gap(VERSION_OP_EMOJI[op])}{label}", pick(op), section="tools"))
    acts.append(Action("l", f"{ec.EMOJI_LAUNCHD_STR} Launchd update checks", lambda: Nav("runtime_launchd", screen.owner),
                       section="tools"))
    acts += screen.nav_actions()
    check_action_table(acts)
    return acts


class RuntimeManagerScreen(Screen):
    """Backend Manager - manages versioned runtime environments.

    Supports multiple backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm).
    Drives install/manage-backend.py with backend selection.
    """
    title = f"{ec.EMOJI_TOOLS_STR} Backend Manager"
    # This screen has no accordion (action-only screen)
    _has_accordion = False

    def _cycle_backend(self):
        self.settings.runtime_backend = _next(RUNTIME_BACKENDS, self.settings.runtime_backend)

    def actions(self):
        return runtime_actions(self)

    def groups(self):
        """No accordion for this screen."""
        return []


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
        # Build accordion with providers as groups
        groups = []
        for i, (slug, name, _shortcut, _url, key_files) in enumerate(self.PROVIDERS):
            status = self._get_provider_status(slug, key_files)
            label = f"{name}: {status}"
            # Visual separator AFTER NVIDIA (before Google): a Group with no items
            # and an empty label renders as a blank row. Placed at index 1 (after NVIDIA).
            if i == 1:
                groups.append(Group("__sep__", "", []))
            # Each provider group has two items: Open page and Add key
            groups.append(Group(
                slug,
                label,
                [
                    Item(f"open:{slug}", f"Open {name} signup page"),
                    Item(f"add:{slug}", f"Add {name} key"),
                ]
            ))
        self.accordion.set_groups(groups)
        # Default expand NVIDIA (first group)
        if groups:
            self.accordion.open_group = groups[0].id

    # Account requirements a user must meet BEFORE a key can work, shown on the provider's row so
    # they are seen while choosing, not discovered as a 403 inside the first session.
    ACCOUNT_WARNINGS = {"vercel": f"{ec.EMOJI_WARNING_STR}  needs a credit card on file, even for free credits"}

    def _get_provider_status(self, slug: str, key_files: tuple) -> str:
        """Get status of a provider by checking actual key files in ~/.api_keys"""
        status = f"{ec.EMOJI_OK_STR} Saved" if provider_has_key(slug) else f"{ec.EMOJI_MISSING_STR} Missing"
        warning = self.ACCOUNT_WARNINGS.get(slug)
        return f"{status}  {warning}" if warning else status

    def info_text(self, item_id: str | None) -> str:
        """The info box under the list: what the highlighted provider needs BEFORE a key works
        (its setup steps, incl. account requirements such as Vercel's credit card) plus the
        registry's measured notes. Shown for a provider row and for its Open/Add items."""
        slug = item_id.split(":", 1)[-1] if item_id else None
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
            # Each provider group has two items: Open page and Add key
            groups.append(Group(
                slug,
                label,
                [
                    Item(f"open:{slug}", f"Open {name} signup page"),
                    Item(f"add:{slug}", f"Add {name} key"),
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
        return None


class RuntimeScreen(Screen):
    """Front end for install/manage-backend.py (0.21.0's unified backend manager).

    Every row maps to a subcommand that main's parser accepts AND dispatches. Version-taking
    rows open a VersionsScreen (pick from a list); snapshot/promote/remove are offered only for
    backends whose manager implements them (BACKEND_VERSION_OPS).
    """
    title = f"{ec.EMOJI_TOOLS_STR} Backend manager"

    def _cycle_backend(self):
        self.settings.runtime_backend = _next(RUNTIME_BACKENDS, self.settings.runtime_backend)

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
    """Lowkey chat/one-shot against a local model. `s` here means Session NAME (Lowkey's own
    menu), never lane switch — Lowkey is a tool screen, not a lane picker."""
    title = "Lowkey — local dispatch"

    def __init__(self, settings: Settings, models: list[LocalModel], owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self.session_name = ""
        self.models = {mdl.alias: mdl for mdl in models}
        buckets: dict[str, list] = {}
        for mdl in models:
            buckets.setdefault(family_for(mdl.alias, mdl.family), []).append(mdl)
        order = sorted(buckets, key=lambda f: (f == "Other", f.lower()))
        self.accordion.set_groups([
            Group(f, f"{f} ({len(buckets[f])})", [Item(x.alias, model_names.readable(x.alias)) for x in buckets[f]])
            for f in order])

    def _cycle_effort(self):
        s = self.settings
        s.lowkey_effort = _next(effort_choices("lowkey"), s.lowkey_effort)
        if s.on_effort_saved:
            s.on_effort_saved("lowkey", s.lowkey_effort)

    def _cycle_temperature(self):
        s = self.settings
        s.lowkey_temperature = _next(TEMPERATURES, s.lowkey_temperature)
        if s.on_temperature_saved:
            s.on_temperature_saved("lowkey", s.lowkey_temperature)

    def actions(self):
        s = self.settings
        acts = [
            Action("e", f"Effort: {s.lowkey_effort} (sent as reasoning_effort)", self._cycle_effort),
            Action("t", f"Temperature: {s.lowkey_temperature or '<provider default>'}", self._cycle_temperature),
            Action("s", f"Session name: {self.session_name or '(ephemeral)'}",
                   lambda: Nav("prompt:session")),
            Action("o", "One-shot prompt with the highlighted model", lambda: Nav("prompt:oneshot"))]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _base(self):
        sel = self.accordion.selected_id
        if not sel or sel not in self.models:
            return None
        return ["lowkey", "--model", sel, "--effort", self.settings.lowkey_effort]

    def activate_selected(self):
        base = self._base()
        if base is None:
            return None
        argv = [base[0], "--convo"] + base[1:]
        if self.session_name:
            argv += ["--session", self.session_name]
        return LaunchRequest("lowkey", argv, {}, self.accordion.selected_id)

    def one_shot(self, prompt: str):
        base = self._base()
        if base is None or not prompt.strip():
            return None
        return LaunchRequest("lowkey", base + ["--prompt", prompt], {}, self.accordion.selected_id)


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
        self.accordion.set_groups([
            Group(st, f"{st.title()} ({len(groups[st])})",
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


