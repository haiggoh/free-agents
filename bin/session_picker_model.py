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
from dataclasses import dataclass, field
from typing import Callable

import session_menu_state as sms

HOME_OWNED = "home_owned"
DIRECT_ROOT = "direct_root"


@dataclass(frozen=True)
class Nav:
    target: str            # home | local | remote | rate_limiter
    owner: str = DIRECT_ROOT


QUIT = Nav("quit")


@dataclass
class Action:
    key: str
    label: str
    run: Callable[[], object]
    enabled: bool = True
    section: str = "settings"


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
    effort: str = ""
    roles: str = ""
    family: str = ""          # explicit family from registry metadata, when present
    backend: str = ""


@dataclass
class RemoteAgent:
    alias: str
    provider: str
    display: str
    tier: str
    local_capable: bool = False
    has_key: bool = True


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


@dataclass
class Settings:
    local_effort: str = sms.DEFAULT_EFFORT["local_session"]
    remote_effort: str = sms.DEFAULT_EFFORT["remote_api_session"]
    auto_mode: int = 0
    telemetry: bool = False
    stop_hook: bool = True
    watcher: bool = False
    enable_mcp: bool = False
    include_trials: bool = True      # interactive visibility only; direct CLI keeps its guard
    local_capable_shown: bool = False
    lowkey_effort: str = sms.DEFAULT_EFFORT["lowkey"]
    rate_limiter: dict = field(default_factory=dict)
    # Called with (lane, value) when the user CONFIRMS an effort change; persists it.
    on_effort_saved: Callable[[str, str], object] | None = None
    notes: list = field(default_factory=list)


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

    def nav_actions(self):
        acts = []
        if self.owner == HOME_OWNED:
            acts.append(Action("b", "Back to Home", lambda: Nav("home", HOME_OWNED), section="nav"))
        else:
            acts.append(Action("q", "Quit", lambda: QUIT, section="nav"))
        return acts

    def actions(self):
        raise NotImplementedError

    def handle_key(self, key: str):
        if key == "escape":
            return Nav("home", HOME_OWNED) if self.owner == HOME_OWNED else QUIT
        for action in self.actions():
            if action.key == key:
                if not action.enabled:
                    return None
                return action.run()
        return None

    def activate_selected(self) -> LaunchRequest | None:
        return None


def _common_toggles(s: Settings, include_watcher: bool):
    acts = [
        Action("a", f"Auto-mode: {AUTO_MODE_LABELS[s.auto_mode]}",
               lambda: setattr(s, "auto_mode", (s.auto_mode + 1) % 3)),
        Action("t", f"Telemetry: {'ON' if s.telemetry else 'OFF'}",
               lambda: setattr(s, "telemetry", not s.telemetry)),
        Action("p", f"Queued-prompt hook: {'ON' if s.stop_hook else 'OFF'}",
               lambda: setattr(s, "stop_hook", not s.stop_hook)),
    ]
    if include_watcher:
        acts.append(Action("w", f"Watcher: {'ON' if s.watcher else 'OFF'}",
                           lambda: setattr(s, "watcher", not s.watcher)))
    return acts


def _tool_actions():
    return [Action("k", "API keys — install / set up", lambda: Nav("tool:keys"), section="tools"),
            Action("v", "Rapid-MLX runtime manager", lambda: Nav("tool:runtime"), section="tools"),
            Action("n", "NVIDIA rate limiter", lambda: Nav("rate_limiter"), section="tools")]


class HomeScreen(Screen):
    title = "Claude Code Free-Agents: Session Launcher"

    def __init__(self, settings: Settings, local_count: int = 0, remote_count: int = 0, owner: str = DIRECT_ROOT):
        super().__init__(settings, owner=owner)
        self.local_count = local_count
        self.remote_count = remote_count

    def actions(self):
        acts = [
            Action("l", f"Local sessions ({self.local_count} on disk)",
                   lambda: Nav("local", HOME_OWNED), section="lanes"),
            Action("r", f"Remote free API sessions ({self.remote_count} listed)",
                   lambda: Nav("remote", HOME_OWNED), section="lanes"),
            Action("d", "Download local models", lambda: Nav("tool:download"), section="tools"),
            Action("o", "Lowkey — local dispatch chat", lambda: Nav("tool:lowkey"), section="tools"),
        ]
        acts += _tool_actions()
        acts += _common_toggles(self.settings, include_watcher=True)
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
    title = "Local Session Picker"
    lane_key = "l"

    def __init__(self, settings: Settings, models: list[LocalModel], owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self.set_models(models)

    def set_models(self, models: list[LocalModel]):
        self.models = {mdl.alias: mdl for mdl in models}
        buckets: dict[str, list] = {}
        for mdl in models:
            buckets.setdefault(family_for(mdl.alias, mdl.family), []).append(mdl)
        order = sorted(buckets, key=lambda f: (f == "Other", f.lower()))
        self.accordion.set_groups([
            Group(f, f"{f} ({len(buckets[f])})",
                  [Item(x.alias, f"{x.alias}" + (f"  [{x.roles}]" if x.roles else "")) for x in buckets[f]])
            for f in order])

    def actions(self):
        s = self.settings
        mcp_ok = s.auto_mode == 0
        acts = [
            Action("e", f"Effort: {s.local_effort}", self._cycle_effort),
            Action("s", "Switch to Remote free API sessions",
                   lambda: Nav("remote", self.owner), section="lanes"),
            Action("r", "Remote free API sessions", lambda: Nav("remote", self.owner),
                   section="hidden"),
            Action("m", (f"MCPs: {'ENABLED' if s.enable_mcp else 'DISABLED'}" if mcp_ok else
                         "MCPs: unavailable (local MCP allowlisting needs blind-trust auto-mode)"),
                   lambda: setattr(s, "enable_mcp", not s.enable_mcp), enabled=mcp_ok),
        ]
        acts += _common_toggles(s, include_watcher=True)
        acts += [a for a in _tool_actions()]
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _cycle_effort(self):
        s = self.settings
        s.local_effort = _next(effort_choices("local_session"), s.local_effort)
        if s.on_effort_saved:
            s.on_effort_saved("local_session", s.local_effort)

    def activate_selected(self):
        sel = self.accordion.selected_id
        if not sel or sel not in self.models:
            return None
        s = self.settings
        env = {"LA_AUTO_MODE": "0" if s.auto_mode == 2 else "1",
               "LA_BLIND_AUTO": "1" if s.auto_mode == 0 else "0",
               "LA_TELEMETRY": "1" if s.telemetry else "0",
               "LA_QUEUE_STOP_HOOK": "1" if s.stop_hook else "0",
               "LA_ENABLE_MCP": "1" if s.enable_mcp else "0",
               "CSL_WATCH": "1" if s.watcher else "0"}
        return LaunchRequest("local", ["local", sel, s.local_effort], env, sel)


class RemoteScreen(Screen):
    title = "Remote Free API Session Picker"
    lane_key = "r"
    policy = ("Prompts and file contents leave this machine. A saved key does not prove a "
              "free quota or no billing; trial rows may cost money. Effort is a request, "
              "not a guarantee.")

    def __init__(self, settings: Settings, agents: list[RemoteAgent], owner: str = DIRECT_ROOT):
        super().__init__(settings, owner)
        self.agents = agents
        self._regroup()

    def _visible(self, agent: RemoteAgent) -> bool:
        s = self.settings
        if agent.tier == "trial" and not s.include_trials:
            return False
        if agent.local_capable and not s.local_capable_shown:
            return False
        return True

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

    def hidden_count(self) -> int:
        return sum(1 for a in self.agents if not self._visible(a))

    def _toggle(self, attr):
        setattr(self.settings, attr, not getattr(self.settings, attr))
        self._regroup()

    def actions(self):
        s = self.settings
        acts = [
            Action("e", f"Effort: {_effort_label(s.remote_effort)}", self._cycle_effort),
            Action("s", "Switch to Local sessions", lambda: Nav("local", self.owner), section="lanes"),
            Action("l", "Local sessions", lambda: Nav("local", self.owner), section="hidden"),
            Action("h", f"Limited trials: {'SHOWN' if s.include_trials else 'HIDDEN'}",
                   lambda: self._toggle("include_trials")),
            Action("f", f"Locally-runnable models: {'SHOWN' if s.local_capable_shown else 'HIDDEN'}",
                   lambda: self._toggle("local_capable_shown")),
            Action("x", "Hidden-model report", lambda: Nav("tool:report"), section="tools"),
            Action("m", f"MCPs: {'ENABLED' if s.enable_mcp else 'DISABLED'}",
                   lambda: setattr(s, "enable_mcp", not s.enable_mcp)),
        ]
        acts += _common_toggles(s, include_watcher=False)
        acts += _tool_actions()
        acts += self.nav_actions()
        check_action_table(acts)
        return acts

    def _cycle_effort(self):
        s = self.settings
        s.remote_effort = _next(effort_choices("remote_api_session"), s.remote_effort)
        if s.on_effort_saved:
            s.on_effort_saved("remote_api_session", s.remote_effort)

    def activate_selected(self):
        sel = self.accordion.selected_id
        agent = next((a for a in self.agents if a.alias == sel and self._visible(a)), None)
        if agent is None:
            return None
        s = self.settings
        argv = ["remote"] + ["-a"] * s.auto_mode
        if s.telemetry:
            argv.append("-t")
        if s.local_capable_shown:
            argv.append("--local-capable-shown")
        if agent.tier == "trial":
            argv.append("--include-trials")     # the user explicitly picked a visible trial row
        if s.enable_mcp:
            argv.append("--enable-mcp")
        effort = sms.effort_arg("remote_api_session", s.remote_effort)
        if effort:
            argv += ["--effort", effort]
        argv.append(agent.alias)
        env = {"LA_QUEUE_STOP_HOOK": "1" if s.stop_hook else "0"}
        return LaunchRequest("remote", argv, env, agent.alias)


class RateLimiterScreen(Screen):
    """NVIDIA rate limiter settings, persisted in session-menu.local.json (rate_limiter).

    Proxies read the saved values through rate_limiter.effective_settings(); an explicit
    LA_NVIDIA_* environment variable still wins, so nothing a user exported is overridden.
    """
    title = "NVIDIA Rate Limiter"
    RPM_STEPS = (10, 20, 30, 40)
    CAP_STEPS = (2, 4, 6, 10)
    WAIT_STEPS = (30, 60, 120, 300)
    COOLDOWN_STEPS = (5, 10, 30, 60)

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
            Action("w", f"Max wait: {self.value('max_wait', 120)}s",
                   lambda: self._cycle("max_wait", self.WAIT_STEPS, 120)),
            Action("d", f"429 cooldown: {self.value('cooldown', 10)}s",
                   lambda: self._cycle("cooldown", self.COOLDOWN_STEPS, 10)),
            Action("i", "Show live limiter status", lambda: Nav("tool:rl-status"), section="tools"),
            Action("x", "Reset shared limiter state file", lambda: Nav("tool:rl-reset"), section="tools"),
        ]
        acts += self.nav_actions()
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

    def actions(self):
        s = self.settings
        acts = [Action("e", f"Effort: {s.lowkey_effort} (sent as reasoning_effort)", self._cycle_effort),
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


