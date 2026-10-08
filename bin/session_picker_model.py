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

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

import session_menu_state as sms
import emoji_constants as ec
import cloud_session_env as cse

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


@dataclass
class RemoteAgent:
    alias: str
    provider: str
    display: str
    tier: str
    local_capable: bool = False
    has_key: bool = True
    broken: bool = False      # listed in config/broken-nvidia-models.json


# Order matters: identity markers first, so "deepseek-r1-distill-qwen" is DeepSeek (the
# model's identity), not Qwen (its base architecture).
_FAMILY_RULES = [
    ("DeepSeek", r"deepseek"),
    ("Mistral", r"mistral|codestral|devstral|magistral|ministral|mixtral"),
    ("Qwen", r"qwen|qwq"),
    ("Gemma", r"gemma"),
    ("Llama", r"llama"),
    ("GLM", r"glm"),
    ("Kimi", r"kimi"),
    ("Phi", r"\bphi"),
    ("Nemotron", r"nemotron|minitron"),
    ("gpt-oss", r"gpt-oss"),
    ("Ornith", r"ornith"),
]


def family_for(alias: str, explicit: str = "") -> str:
    if explicit:
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
                   "modelscope": "ModelScope"}


def tier_label(tier: str, provider: str) -> str:
    if provider == "nvidia" and tier == "unknown":
        return "no daily cap/40/min"
    if tier == "trial":
        return "trial — possible cost"
    return tier


# --- settings ----------------------------------------------------------------------------
AUTO_MODE_LABELS = {0: "blind-trust", 1: "classifier", 2: "off"}

CLASSIFIER_SOURCE_LABELS = {
    0: "NVIDIA API (fallback: Devstral)",
    1: "Local Devstral (always)",
    2: "Auto: local on local, remote on remote",
}

# Cloud-screen options that no gateway launcher reads (audit 2026-10-08). Said in the menu, so a
# toggle cannot look as if it changed the gateway session it sits beside.
NOT_GATEWAY_NOTE = "[local/free-API only — not gateway]"


@dataclass
class Settings:
    local_effort: str = sms.DEFAULT_EFFORT["local_session"]
    remote_effort: str = sms.DEFAULT_EFFORT["remote_api_session"]
    auto_mode: int = 0
    telemetry: bool = False
    stop_hook: bool = False          # queued-prompt hook OFF by default (user, 0.22.3)
    watcher: bool = False
    enable_mcp: bool = False
    include_trials: bool = True      # interactive visibility only; direct CLI keeps its guard
    local_capable_shown: bool = False
    show_broken: bool = False        # unworking models; in-memory, like the CLI flag
    remote_temperature: str = sms.DEFAULT_TEMPERATURE["remote_api_session"]
    local_temperature: str = sms.DEFAULT_TEMPERATURE["local_session"]
    lowkey_effort: str = sms.DEFAULT_EFFORT["lowkey"]
    lowkey_temperature: str = sms.DEFAULT_TEMPERATURE["lowkey"]
    rate_limiter: dict = field(default_factory=dict)
    runtime_backend: str = "rapid-mlx"   # backend manager selection; in-memory only
    # Classifier source for auto mode (0=NVIDIA API w/ fallback, 1=Local Devstral always, 2=Auto)
    classifier_source: int = 2
    # Intercept Agents toggle (Deny & Replace): True = ON (FreeAgent), False = OFF (Native Agent)
    intercept_agents: bool = True
    # Bypass permissions mode for cloud sessions (blind-trust auto mode)
    cloud_bypass_permissions: bool = False
    # security-guidance LLM reviews on GATEWAY sessions: 0=default (opus-4-7), 1=sonnet-4-6, 2=off.
    # Persisted to config/cloud-session.local.env, which claude-cloud-lean sources.
    security_review: int = 0
    # Called with the full cloud-env dict whenever a gateway-reaching cloud setting changes.
    on_cloud_saved: Callable[[dict], object] | None = None
    # Called with (lane, value) when the user CONFIRMS an effort change; persists it.
    on_effort_saved: Callable[[str, str], object] | None = None
    # Called with (lane, value) when the user CONFIRMS a temperature change; persists it.
    on_temperature_saved: Callable[[str, str], object] | None = None
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
    notes: list = field(default_factory=list)


# Same presets as main's bash menu (0.21.7). "" = leave it to the provider.
TEMPERATURES = ("", "0.0", "0.3", "0.7", "1.0", "1.5", "2.0")

# Provider signup URLs from install/setup-api-keys.py
PROVIDER_SIGNUP_URLS = {
    "nvidia": "https://build.nvidia.com/settings/api-keys",
    "cerebras": "https://cloud.cerebras.ai/platform/",
    "cloudflare": "https://dash.cloudflare.com/profile/api-tokens",
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
    "kilo": "https://app.kilo.ai",
    "llm7": "https://dash.llm7.io",
    "mistral": "https://console.mistral.ai/home?profile_dialog=api-keys",
    "modelscope": "https://modelscope.cn/my/myaccesstoken",
    "openrouter": "https://openrouter.ai/settings/keys",
    "sambanova": "https://cloud.sambanova.ai/dashboard",
    "siliconflow": "https://cloud.siliconflow.com/account/ak",
    "vercel": "https://vercel.com/d?title=AI+Gateway+API+Keys&to=%2F%5Bteam%5D%2F~%2Fai-gateway%2Fapi-keys",
    "zai": "https://z.ai/manage-apikey/apikey-list",
}


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


@dataclass
class Group:
    id: str
    label: str
    items: list


class Accordion:
    """Groups with at most one open; selection is by stable item id, never by row index."""

    def __init__(self):
        self.groups: list[Group] = []
        self.open_group: str | None = None
        self.selected_id: str | None = None

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
            self.open_group = groups[0].id

    def _group_of(self, item_id):
        for g in self.groups:
            if any(i.id == item_id for i in g.items):
                return g.id
        return None

    def expand(self, group_id: str) -> bool:
        if any(g.id == group_id for g in self.groups):
            self.open_group = group_id
            return True
        return False

    def collapse(self):
        self.open_group = None

    def select(self, item_id: str) -> bool:
        group = self._group_of(item_id)
        if group is None:
            return False
        self.open_group = group
        self.selected_id = item_id
        return True

    def rows(self):
        """Flattened visible rows: ('group', Group) headers and ('item', Item) for the open one."""
        out = []
        for g in self.groups:
            out.append(("group", g))
            if g.id == self.open_group:
                out.extend(("item", i) for i in g.items)
        return out


# --- screens -----------------------------------------------------------------------------
class Screen:
    title = ""
    lane_key = ""

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
    ]
    if include_watcher:
        acts.append(setting("w", f"{ec.EMOJI_WATCHER_STR} Watcher: {'ON' if s.watcher else 'OFF'}",
                            *_toggle(s, "watcher")))
    return acts


def _tool_actions(backend: bool = True, local_session: bool = False, home: bool = False):
    # The backend manager drives LOCAL runtimes (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM),
    # so the Remote lane does not offer it (user, 2026-10-04).
    # Local session picker (local_session=True) hides API keys and NVIDIA rate limiter.
    # Home screen (home=True) shows only API keys and Backend manager, no NVIDIA rate limiter.
    acts = [
        Action("v", f"{ec.EMOJI_TOOLS_STR} Backend manager (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, LiteLLM)",
               lambda: Nav("runtime_manager"), section="tools"),
    ]
    if not local_session:
        acts.insert(0, Action("k", f"{ec.EMOJI_KEY_STR} API keys — install / set up", lambda: Nav("api_keys"), section="tools"))
        if not home:
            acts.append(Action("n", f"{ec.EMOJI_NVIDIA_RATE_LIMITER_STR} NVIDIA rate limiter", lambda: Nav("rate_limiter"), section="tools"))
    return acts if backend else [a for a in acts if a.key != "v"]


class HomeScreen(Screen):
    # The plugin's identity, not "home": both lanes' icons (user, 2026-10-04).
    title = f"{SESSION_LAUNCHER_TITLE}  {ec.SESSION_EMOJI_LOCAL_STR} {ec.SESSION_EMOJI_FREE_API_STR}"

    @property
    def policy(self):
        # Identity subheadline, then the 0.21 menu's config line.
        import os
        line = "Claude Code on free inference — local MLX models on this Mac, or free cloud APIs"
        src = os.environ.get("LA_CONFIG_SOURCE", "")
        if src:
            line += f"\nConfig: {src}"
        if os.environ.get("LA_FALLBACK_CONFIG") == "1":
            line += (f"\n{ec.EMOJI_WARNING_STR} Using public fallback defaults. Private models "
                     "are not present in this installed copy.")
        return line

    def __init__(self, settings: Settings, local_count: int | None = None, remote_count: int | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner=owner)
        self.local_count = local_count
        self.remote_count = remote_count

    def _format_count(self, count: int | None, fallback: str) -> str:
        return str(count) if count is not None else fallback

    def actions(self):
        acts = [
            Action("l", f"{ec.SESSION_EMOJI_LOCAL_STR} Local sessions ({self._format_count(self.local_count, '?')} on disk)",
                   lambda: Nav("local", HOME_OWNED), section="lanes"),
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR} Remote free API sessions ({self._format_count(self.remote_count, '?')} listed)",
                   lambda: Nav("remote", HOME_OWNED), section="lanes"),
            Action("c", f"{ec.EMOJI_CLOUD_CONFIG_STR} {ec.EMOJI_TOOLS_STR} Cloud session configuration…", lambda: Nav("cloud_config", HOME_OWNED), section="tools"),
            Action("d", f"{ec.EMOJI_DOWNLOAD_STR} Download local models", lambda: Nav("tool:download"), section="tools"),
            Action("o", f"{ec.LK_EMOJI_CONVO_STR} Lowkey — local dispatch chat", lambda: Nav("tool:lowkey"), section="tools"),
        ]
        acts += _tool_actions(home=True)
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


class LocalScreen(Screen):
    title = f"{ec.SESSION_EMOJI_LOCAL_STR} Local Session Picker"

    @property
    def policy(self):
        # Restored 0.21 subheadline: "N model(s) on disk (session-capable)".
        n = len(self.models) if getattr(self, "_models_loaded", False) and self.models else None
        return (f"{n} model(s) on disk (session-capable)" if n is not None
                else "MLX models on this machine")
    lane_key = "l"

    def __init__(self, settings: Settings, models: list[LocalModel] | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._models_loaded = False
        self._choose_model_visible = False  # Model list hidden by default
        if models is not None:
            self.set_models(models)
        else:
            self.models = {}
            self.accordion.set_groups([])

    def set_models(self, models: list[LocalModel]):
        # Key by profile_id for uniqueness (multiple profiles can share same alias)
        self.models = {mdl.profile_id or mdl.alias: mdl for mdl in models}
        buckets: dict[str, list] = {}
        for mdl in models:
            buckets.setdefault(family_for(mdl.alias, mdl.family), []).append(mdl)
        order = sorted(buckets, key=lambda f: (f == "Other", f.lower()))
        self.accordion.set_groups([
            Group(f, f"{f} ({len(buckets[f])})",
                  [Item(
                       x.profile_id or x.alias,
                       f"{x.alias}"
                       + (f" ({x.backend}" + (", thinking" if x.thinking else "") + ")" if x.backend else "")
                       + (f"  [{x.roles}]" if x.roles else "")
                   ) for x in buckets[f]])
            for f in order])
        self._models_loaded = True

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
            setting("e", f"{ec.EMOJI_EFFORT_STR} Effort: {s.local_effort}", effort_choices("local_session"),
                    lambda: s.local_effort, lambda v: self._set_effort(v)),
            setting("o", f"{ec.EMOJI_TEMPERATURE_STR}  Temperature: {s.local_temperature or '<provider default>'}",
                    TEMPERATURES, lambda: s.local_temperature, lambda v: self._set_local_temp(v)),
            Action("c", self._choose_model_label(), self._toggle_choose_model),
            Action("s", f"{ec.SESSION_EMOJI_FREE_API_STR}  Switch to Remote free API sessions",
                   lambda: Nav("remote", self.owner), section="lanes"),
            Action("r", f"{ec.SESSION_EMOJI_FREE_API_STR}  Remote free API sessions", lambda: Nav("remote", self.owner),
                   section="hidden"),
            setting("m", f"{ec.EMOJI_MCP_STR}  MCPs: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                    *_toggle(s, "enable_mcp")),
        ]
        # Add "Launch last model" if we have a saved last model
        last_local = s.last_launched_model.get("local_session") if s.last_launched_model else None
        # No `in self.models` check: the list is lazy and usually not loaded yet, and launching
        # only needs alias + effort. A since-deleted model fails in the launcher, visibly.
        if last_local:
            acts.insert(0, Action("g", f"{ec.EMOJI_GO_LAUNCH_STR} Go launch: {last_local} session",
                           lambda: self._launch_last("local_session"), section="launch"))
        acts += _common_toggles(s, include_watcher=True)
        acts += _tool_actions(backend=True, local_session=True)
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

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
        if not sel or sel not in self.models:
            return None
        mdl = self.models[sel]
        return self._request_for(mdl.profile_id or mdl.alias, mdl)

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
               "LA_ENABLE_MCP": "1" if s.enable_mcp else "0",
               "LA_CLASSIFIER_SOURCE": str(s.classifier_source),
               "CSL_WATCH": "1" if s.watcher else "0",
               "INTERCEPT_AGENTS": "1" if s.intercept_agents else "0"}
        # Use profile_id for the launcher if available, else alias
        launch_alias = mdl.profile_id if mdl.profile_id else mdl.alias
        argv = ["local", launch_alias, s.local_effort]
        if s.local_temperature:
            argv += ["--temperature", s.local_temperature]
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
        vis = sum(1 for a in self.agents if self._visible(a))
        return f"{vis} model(s) visible  (hidden: {self.hidden_count()})"

    def __init__(self, settings: Settings, agents: list[RemoteAgent] | None = None, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self._models_loaded = False
        self._choose_model_visible = False  # Model list hidden by default
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
            items = [Item(a.alias, f"{a.display}  · {tier_label(a.tier, a.provider)}"
                          + ("" if a.has_key else "  · no key")) for a in buckets[prov]]
            groups.append(Group(prov, f"{PROVIDER_LABELS.get(prov, prov)} ({len(items)})", items))
        self.accordion.set_groups(groups)
        self._models_loaded = True

    def _visible(self, agent: RemoteAgent) -> bool:
        s = self.settings
        if agent.tier == "trial" and not s.include_trials:
            return False
        if agent.local_capable and not s.local_capable_shown:
            return False
        if agent.broken and not s.show_broken:
            return False
        return True

    def hidden_count(self) -> int:
        return sum(1 for a in self.agents if not self._visible(a))

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
            Action("c", self._choose_model_label(), self._toggle_choose_model),
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
        loaded = next((a for a in self.agents if a.alias == last_model), None)
        tier = loaded.tier if loaded else self.settings.last_launched_tier.get(lane, "unknown")
        return self._request_for(last_model, tier)

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
        agent = next((a for a in self.agents if a.alias == sel and self._visible(a)), None)
        if agent is None:
            return None
        return self._request_for(agent.alias, agent.tier)

    def _request_for(self, alias: str, tier: str):
        s = self.settings
        if s.last_launched_model is not None:
            s.last_launched_model["remote_api_session"] = alias
        s.last_launched_tier["remote_api_session"] = tier
        if s.on_last_launched:
            s.on_last_launched("remote_api_session", alias, tier)
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
        argv.append(alias)
        env = {"LA_QUEUE_STOP_HOOK": "1" if s.stop_hook else "0",
               "LA_CLASSIFIER_SOURCE": str(s.classifier_source),
               "INTERCEPT_AGENTS": "1" if s.intercept_agents else "0",
               "CLOUD_BYPASS_PERMISSIONS": "1" if s.cloud_bypass_permissions else "0"}
        return LaunchRequest("remote", argv, env, alias)


class RemoteFiltersScreen(Screen):
    """Filter settings for Remote API Session Picker.

    Submenu showing filter options: limited trials, locally-runnable models,
    unworking models, and hidden-model report.
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

    Reaches GATEWAY sessions (persisted to config/cloud-session.local.env, sourced by
    ~/.claude/scripts/claude-cloud-lean):
    - Security Review: security-guidance plugin reviews — default / sonnet-4-6 / off

    Does NOT reach gateway sessions (audit 2026-10-08; labelled so in the menu):
    - Intercept Agents: replace native Agent tool with FreeAgent (local launcher only)
    - Classifier Source: NVIDIA API / Local Devstral / Auto (local + free-API only)
    - Bypass Permissions: blind-trust for free-API remote sessions (the gateway launcher hardcodes it)
    """
    title = f"{ec.EMOJI_CLOUD_CONFIG_STR} {ec.EMOJI_TOOLS_STR} Cloud Session Configuration"

    def __init__(self, settings: Settings, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        # No accordion needed - this is an action-only screen
        self.accordion = None

    def actions(self):
        s = self.settings
        acts = [
            Action("s", f"Security Review (gateway): {cse.SECURITY_REVIEW_LABELS[s.security_review]}",
                   lambda: self._cycle_security_review()),
            Action("x", f"{ec.EMOJI_SHIELD_STR}  Intercept Agents: {'ON (FreeAgent)' if s.intercept_agents else 'OFF (Native Agent)'}"
                        f"  {NOT_GATEWAY_NOTE}",
                   lambda: self._toggle("intercept_agents")),
            Action("y", f"Classifier: {CLASSIFIER_SOURCE_LABELS[s.classifier_source]}  {NOT_GATEWAY_NOTE}",
                   lambda: self._cycle_classifier()),
            Action("p", f"{ec.EMOJI_AUTO_MODE_STR}  Bypass Permissions (blind-trust): {'ON' if s.cloud_bypass_permissions else 'OFF'}"
                        f"  {NOT_GATEWAY_NOTE}",
                   lambda: self._toggle("cloud_bypass_permissions")),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _toggle(self, attr):
        setattr(self.settings, attr, not getattr(self.settings, attr))

    def _cycle_classifier(self):
        s = self.settings
        s.classifier_source = (s.classifier_source + 1) % 3

    def _cycle_security_review(self):
        s = self.settings
        s.security_review = (s.security_review + 1) % len(cse.SECURITY_REVIEW_LABELS)
        if s.on_cloud_saved:
            s.on_cloud_saved({"security_review": s.security_review})

    def groups(self):
        """No accordion for this screen."""
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


# install/manage-backend.py --backend choices; tests compare this against its registry.
RUNTIME_BACKENDS = ("rapid-mlx", "vllm-mlx", "omlx", "llama-cpp", "litellm")


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
        b = self.settings.runtime_backend
        acts = [
            Action("c", f"{ec.EMOJI_BACKEND_STR} Backend: {b}", self._cycle_backend),
            Action("r", f"{ec.EMOJI_RELEASES_STR} List installable releases (incl. prereleases)", lambda: Nav("tool:rt-releases"), section="tools"),
            Action("i", f"{ec.EMOJI_INSTALL_STR} Install a release (choose from the list)", lambda: Nav("tool:rt-install"), section="tools"),
            Action("t", f"{ec.EMOJI_VALIDATE_STR} Validate an installed version", lambda: Nav("prompt:rt-validate"), section="tools"),
            Action("f", f"{ec.EMOJI_INFO_STR} Show info for an installed version", lambda: Nav("prompt:rt-info"), section="tools"),
            Action("l", f"{ec.EMOJI_LAUNCHD_STR} Launchd update checks", lambda: Nav("runtime_launchd", self.owner),
                   section="tools"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

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

    # Provider list: NVIDIA first, then Google, then Groq, then alphabetical
    # (slug, name, shortcut, signup_url, key_files)
    PROVIDERS = [
        ("nvidia", "NVIDIA", "n", "https://build.nvidia.com/settings/api-keys", ("nvidia",)),
        # --- gap ---
        ("gemini", "Google Gemini", "g", "https://aistudio.google.com/apikey", ("gemini",)),
        ("groq", "Groq", "u", "https://console.groq.com/keys", ("groq",)),
        ("cerebras", "Cerebras", "e", "https://cloud.cerebras.ai/platform/", ("cerebras",)),
        ("cloudflare", "Cloudflare Workers AI", "c", "https://dash.cloudflare.com/profile/api-tokens", ("cloudflare", "cloudflare-account-id")),
        ("kilo", "Kilo", "k", "https://app.kilo.ai", ("kilo",)),
        ("llm7", "LLM7", "l", "https://dash.llm7.io", ("llm7",)),
        ("mistral", "Mistral", "m", "https://console.mistral.ai/home?profile_dialog=api-keys", ("mistral",)),
        ("modelscope", "ModelScope", "x", "https://modelscope.cn/my/myaccesstoken", ("modelscope",)),
        ("openrouter", "OpenRouter", "r", "https://openrouter.ai/settings/keys", ("openrouter",)),
        ("sambanova", "SambaNova", "y", "https://cloud.sambanova.ai/dashboard", ("sambanova",)),
        ("siliconflow", "SiliconFlow", "s", "https://cloud.siliconflow.com/account/ak", ("siliconflow",)),
        ("vercel", "Vercel AI Gateway", "v", "https://vercel.com/d?title=AI+Gateway+API+Keys&to=%2F%5Bteam%5D%2F~%2Fai-gateway%2Fapi-keys", ("vercel",)),
        ("zai", "Z.AI", "z", "https://z.ai/manage-apikey/apikey-list", ("zai",)),
    ]

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

    def _get_provider_status(self, slug: str, key_files: tuple) -> str:
        """Get status of a provider by checking actual key files in ~/.api_keys"""
        import os
        from pathlib import Path
        api_keys_dir = Path(os.environ.get("LA_API_KEYS_DIR", os.path.expanduser("~/.api_keys")))
        if not api_keys_dir.exists():
            return f"{ec.EMOJI_MISSING_STR} Missing"
        # Check if all required key files exist
        all_exist = all((api_keys_dir / name).exists() for name in key_files)
        return f"{ec.EMOJI_OK_STR} Saved" if all_exist else f"{ec.EMOJI_MISSING_STR} Missing"

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

    Every row maps to a subcommand that main's parser accepts AND dispatches; the old
    manage-rapid-mlx.py rows smoke/snapshot/reset/inspect/installed no longer exist there, and
    promote/remove/check-updates parse but are not dispatched, so they are not offered.
    """
    title = f"{ec.EMOJI_TOOLS_STR} Backend manager"

    def _cycle_backend(self):
        self.settings.runtime_backend = _next(RUNTIME_BACKENDS, self.settings.runtime_backend)

    def actions(self):
        b = self.settings.runtime_backend
        acts = [
            Action("c", f"{ec.EMOJI_BACKEND_STR} Backend: {b}", self._cycle_backend),
            Action("r", f"{ec.EMOJI_RELEASES_STR} List installable releases (incl. prereleases)", lambda: Nav("tool:rt-releases"), section="tools"),
            Action("i", f"{ec.EMOJI_INSTALL_STR} Install a release (choose from the list)", lambda: Nav("tool:rt-install"), section="tools"),
            Action("t", f"{ec.EMOJI_VALIDATE_STR} Validate an installed version", lambda: Nav("prompt:rt-validate"), section="tools"),
            Action("f", f"{ec.EMOJI_INFO_STR} Show info for an installed version", lambda: Nav("prompt:rt-info"), section="tools"),
            Action("l", f"{ec.EMOJI_LAUNCHD_STR} Launchd update checks", lambda: Nav("runtime_launchd", self.owner),
                   section="tools"),
        ]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts


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
            Action("o", f"{ec.EMOJI_LAUNCHD_RUN_ONCE_STR} Run the update check once now",
                   lambda: Nav("tool:rt-launchd-run-once"), section="tools"),
            Action("i", f"{ec.EMOJI_LAUNCHD_INSTALL_STR} Install the weekly check (LaunchAgent)",
                   lambda: Nav("prompt:rt-launchd-install"), section="tools"),
            Action("u", f"{ec.EMOJI_LAUNCHD_UNINSTALL_STR} Uninstall the weekly check",
                   lambda: Nav("prompt:rt-launchd-uninstall"), section="tools"),
            Action("b", f"{ec.EMOJI_BACK_STR} Back to Backend manager",
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
            Group(f, f"{f} ({len(buckets[f])})", [Item(x.alias, x.alias) for x in buckets[f]])
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
                  [Item(e.alias, f"{e.alias}  ~{e.size_gb:g} GB  {e.groups}") for e in groups[st]])
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


