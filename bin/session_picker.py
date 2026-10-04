#!/usr/bin/env python3
"""session_picker.py — the Textual front end of the Free Agents session picker.

Renders session_picker_model screens and forwards keys to them; all behaviour (which rows
exist, what a key does, what argv a launch uses) lives in the model, so the tests there are
the contract. This file owns only terminal concerns:

  * arrows move the highlight; Enter/Right on a group header expands it (closing the
    previous one), Left collapses; Enter on a row activates it;
  * a child program (a session, the key wizard, the runtime manager, the downloader) gets
    the NORMAL terminal via App.suspend() and the picker resumes the SAME screen, with the
    same selection and settings, when the child exits — success or failure alike;
  * mouse clicks select/activate rows when the terminal reports them (optional; the
    keyboard is always sufficient).

Invoked through the bin/session-picker wrapper (never by typing a python path):
  session-picker [home|local|remote|lowkey|download|rate-limiter] [--include-trials]
                 [--exclude-trials] [--local-capable-shown] [--help]
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

import emoji_constants as ec  # noqa: E402
import session_menu_state as sms  # noqa: E402
import session_picker_model as m  # noqa: E402

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Input, OptionList, Static
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover - the wrapper checks first; keep a clear message anyway
    sys.stderr.write("session-picker: the UI needs its venv — run install/setup-session-picker.sh\n")
    sys.exit(3)

# Apple Terminal compatibility: suppress DECRQM 2048 query that causes stray 'p'
# Only activates when TERM_PROGRAM=Apple_Terminal on Unix-like systems
try:
    from session_picker_terminal import get_driver_class
    _DRIVER_CLASS = get_driver_class()
except ImportError:
    _DRIVER_CLASS = None

# Loading indicator delay threshold (200ms) - applies to LAZY LOADING operations only
# (e.g., model list loading). Startup shows loading immediately with NO delay.
LOADING_INDICATOR_DELAY_S = 0.2

# Loading animation frames - hourglass alternating between flowing sand (⏳) and done (⌛️)
LOADING_FRAMES = [ec.EMOJI_LOADING_STR, ec.EMOJI_LOADING_DONE_STR]
# One beat = 0.4 s: the hourglass flips every 2 beats (0.8 s) and the dots grow . .. ... every
# beat. At 0.15 s the two hourglass glyphs (nearly identical at terminal size) blurred into
# one, so the animation looked static (measured: frames did alternate; user saw no motion).
LOADING_INTERVAL = 0.4
LOADING_DOTS = (".  ", ".. ", "...")


def loading_label(tick: int, what: str = "loading") -> str:
    """Hourglass frame, then the label with 1-3 dots padded to a fixed width (nothing jumps)."""
    return f"{LOADING_FRAMES[(tick // 2) % len(LOADING_FRAMES)]} {what}{LOADING_DOTS[tick % len(LOADING_DOTS)]}"

REPO = BIN.parent
NO_COLOR = bool(os.environ.get("NO_COLOR"))

# Import emoji constants from config/emoji.sh (single source of truth)
import emoji_constants as ec

# --- inventories (read-only; never eval shell) ---------------------------------------------
def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def load_local_models() -> list[m.LocalModel]:
    # Use the new profile-based loader
    try:
        r = _run(["bash", str(BIN / "load-profile-models.py")], timeout=10)
        import json
        models_data = json.loads(r.stdout)
        models = []
        for d in models_data:
            models.append(m.LocalModel(
                alias=d["alias"],
                profile_id=d["profile_id"],
                effort=d["effort"],
                roles=d["roles"],
                family=d["family"],
                backend=d["backend"],
                thinking=d["thinking"],
                tool_parser=d["tool_parser"],
                reasoning_parser=d["reasoning_parser"],
            ))
        return models
    except Exception as e:
        # Fallback to old method if profile loader fails
        csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
        r = _run(["bash", csl, "--inventory"])
        models = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and parts[0]:
                models.append(m.LocalModel(alias=parts[0], backend=parts[1], effort=parts[2],
                                           roles=parts[3], family=parts[4] if len(parts) > 4 else ""))
        return models


def load_remote_agents() -> list[m.RemoteAgent]:
    rs = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
    r = _run(["bash", rs, "--inventory"])
    agents = []
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) in (6, 7):     # 7th column (broken) arrived with main's 0.21.9 filter
            agents.append(m.RemoteAgent(p[0], p[1], p[2], p[3], local_capable=p[4] == "1",
                                        has_key=p[5] == "1", broken=len(p) == 7 and p[6] == "1"))
    return agents


def load_catalog():
    dl = os.environ.get("LA_DOWNLOADER") or str(REPO / "install/download-models.sh")
    r = _run(["bash", dl, "--inventory"])
    entries, free, headroom = [], 0.0, 100.0
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if p[0] == "#disk" and len(p) == 3:
            free, headroom = float(p[1] or 0), float(p[2] or 100)
        elif len(p) == 5:
            try:
                size = float(p[2])
            except ValueError:
                size = 0.0
            entries.append(m.CatalogEntry(p[0], p[1], size, p[3], p[4]))
    return entries, free, headroom


# --- child commands ---------------------------------------------------------------------------
def command_for(req: m.LaunchRequest) -> list[str]:
    head, rest = req.argv[0], req.argv[1:]
    if head == "local":
        # Through csl, so the session profile and the watcher (following csl's pid, which the
        # launcher inherits via exec) behave exactly as before.
        return [os.environ.get("CSL_SELF") or str(BIN / "csl"), "--picker-launch", *rest]
    if head == "remote":
        # When running as a child of csl (HOME_OWNED), pass --csl-owner and --csl-nav-file
        # so remote-session.sh returns via nav file instead of exec'ing claude directly.
        base = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
        if getattr(req, "owner", "direct_root") == "home_owned":
            import tempfile
            navfile = os.path.join(tempfile.gettempdir(), f"_csl_nav.{os.getpid()}")
            return [base, "--csl-owner", "--csl-nav-file", navfile, *rest]
        return [base, *rest]
    if head == "lowkey":
        return [sys.executable if os.environ.get("LOWKEY_USE_PICKER_PY") else "python3",
                str(BIN / "lowkey-cli.py"), *rest]
    if head == "download":
        return [os.environ.get("LA_DOWNLOADER") or str(REPO / "install/download-models.sh"), *rest]
    raise ValueError(head)


def _mb(backend: str, *args: str) -> list[str]:
    return ["python3", str(REPO / "install/manage-backend.py"), "--backend", backend, *args]


TOOLS = {
    "tool:keys": lambda: ["python3", str(REPO / "install/setup-api-keys.py")],
    "tool:rl-status": lambda: ["python3", str(BIN / "rate_limiter.py"), "status"],
    "tool:report": lambda: ["bash", str(BIN / "local-capable-filter.sh"), "--report",
                            str(REPO / "config/local-capable-remote-models.psv"), "--roster",
                            str(REPO / "config/remote-agents.sh")],
    # Backend manager (install/manage-backend.py): (backend[, version]) -> argv. "tool:rl-reset" is
    # the RATE LIMITER state reset; it is handled as a typed-confirm prompt, never run directly.
    "tool:rt-releases": lambda b: _mb(b, "releases", "--pre"),
    "tool:rt-install": lambda b: _mb(b, "install"),
    "tool:rt-validate": lambda b, v: _mb(b, "validate", v),
    "tool:rt-info": lambda b, v: _mb(b, "info", v),
    "tool:rt-launchd-status": lambda b: _mb(b, "launchd", "status"),
    "tool:rt-launchd-run-once": lambda b: _mb(b, "launchd", "run-once"),
    "tool:rt-launchd-install": lambda b: _mb(b, "launchd", "install"),
    "tool:rt-launchd-uninstall": lambda b: _mb(b, "launchd", "uninstall"),
    "tool:keys:open": lambda: ["python3", "-c", "import webbrowser; webbrowser.open('https://github.com/haiggoh/free-agents/blob/main/docs/PROVIDER_SIGNUP.md')"],
    "tool:keys:add": lambda slug: ["python3", str(REPO / "install/setup-api-keys.py"), slug],
}


class Picker(App):
    # R5: no painted background. The `textual-ansi` theme maps background/foreground to the
    # terminal's own defaults (ANSI 39/49) and ansi_color=True keeps them ANSI instead of
    # converting to truecolor, so light, dark and custom terminal themes all show through.
    # R9: the Remote policy line is neutral (muted), not the orange warning colour.
    CSS = """
    Screen { layout: vertical; background: ansi_default; color: ansi_default; }
    #title { text-style: bold; padding: 0 1; }
    #policy { color: ansi_bright_black; padding: 0 1; }
    #actions { height: auto; padding: 0 1; }
    #rows { height: 1fr; display: none; margin-top: 1; }   /* a blank line before the model list */
    #rows.visible { display: block; }
    /* Secondary text is grey (ANSI bright-black): readable on light AND dark terminals.
       Only background and main text are the terminal's own defaults. */
    #status { height: auto; padding: 0 1; color: ansi_bright_black; }
    #prompt { display: none; }
    OptionList { background: ansi_default; color: ansi_default; border: none; }
    OptionList:focus { background: ansi_default; color: ansi_default; border: none; }
    /* Highlight and hover never paint a block: soft-blue text (ANSI bright-blue, which every
       light/dark terminal theme tunes to be readable) on the terminal's own background. */
    OptionList { background-tint: ansi_default 0%; }
    OptionList > .option-list--option-highlighted { background: ansi_default; color: ansi_bright_blue; text-style: none; }
    OptionList:focus > .option-list--option-highlighted { background: ansi_default; color: ansi_bright_blue; text-style: bold; }
    OptionList > .option-list--option-hover { background: ansi_default; color: ansi_bright_blue; }
    Input { background: ansi_default; color: ansi_default; }
    Static { background: ansi_default; color: ansi_default; }
    """
    # Ctrl+C copies the mouse selection when there is one and quits otherwise; Cmd+C (super+c,
    # where the terminal forwards it) always copies. Title/subheadline/status are selectable
    # Static text, and the terminal's own selection (Option-drag in Terminal.app) still works.
    BINDINGS = [Binding("ctrl+c", "copy_or_quit", "Copy / Quit", show=False, priority=True),
                Binding("super+c", "copy_selection", "Copy", show=False, priority=True)]

    def __init__(self, start: str, flags: argparse.Namespace):
        # Through the constructor, never a class attribute: Textual 3.7.1's App.__init__ does
        # `self.driver_class = driver_class or self.get_driver_class()`, which overwrote the
        # class attribute 786d487 set, so Apple Terminal still got LinuxDriver (stray `p`).
        # None = let Textual pick its default driver.
        super().__init__(driver_class=_DRIVER_CLASS, ansi_color=True)
        self.theme = "textual-ansi"
        state = sms.load(Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR))
        self.state_warnings = list(state.warnings)
        self.settings = m.Settings(
            local_effort=state.effort["local_session"],
            remote_effort=state.effort["remote_api_session"],
            lowkey_effort=state.effort["lowkey"],
            rate_limiter=dict(state.rate_limiter),
            auto_mode=int(os.environ.get("CSL_AUTO_MODE_STATE", "0") or 0) % 3,
            telemetry=os.environ.get("CSL_TELEMETRY", "0") == "1",
            stop_hook=os.environ.get("CSL_STOP_HOOK", "0") == "1",   # OFF by default (0.22.3)
            watcher=os.environ.get("CSL_WATCH", "0") == "1",
            enable_mcp=os.environ.get("LA_ENABLE_MCP", "0") == "1",
            include_trials=not flags.exclude_trials,
            local_capable_shown=flags.local_capable_shown or os.environ.get("CSL_LOCAL_CAPABLE") == "1",
            on_effort_saved=self._save_effort,
            # "Go last": restored from the store; written when a launch request is made.
            last_launched_model={"local_session": None, "remote_api_session": None, "lowkey": None,
                                 **state.last_launched},
            last_launched_tier=dict(state.last_launched_tier),
            on_last_launched=self._save_last_launched)
        self.start = start
        self.nav_stack: list = []          # callers of sub-screens (Back returns to them)
        self.screen_model: m.Screen | None = None
        self.cache: dict = {}
        self.pending_prompt = None
        # Loading indicator state
        self._pending_op_timer: float | None = None
        self._pending_op_id: int = 0
        self._loading_frame: int = 0
        self._startup_loading: bool = True  # Show loading immediately on startup

    # --- persistence -----------------------------------------------------------------------
    def _config_dir(self):
        return Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR)

    def _save_effort(self, lane, value):
        warning = sms.save_effort(self._config_dir(), lane, value)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    def _save_last_launched(self, lane, alias, tier):
        try:
            warning = sms.save_last_launched(self._config_dir(), lane, alias, tier)
        except ValueError as exc:          # an alias the store refuses: never block the launch
            warning = f"{exc}; Go last not remembered"
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    def _save_rl(self, values):
        warning = sms.save_rate_limiter(self._config_dir(), values)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    # --- loading indicator with animation ---------------------------------------------------------------------
    def _start_pending_op(self) -> int:
        """Start a pending operation timer. Returns operation ID."""
        self._pending_op_id += 1
        self._pending_op_timer = time.perf_counter()
        self._loading_frame = 0
        return self._pending_op_id

    def _check_pending_op(self, op_id: int) -> bool:
        """Check if pending operation has exceeded delay threshold.
        Returns True if should show loading indicator."""
        if self._pending_op_timer is None or self._pending_op_id != op_id:
            return False
        elapsed = time.perf_counter() - self._pending_op_timer
        return elapsed >= LOADING_INDICATOR_DELAY_S

    def _clear_pending_op(self, op_id: int) -> None:
        """Clear pending operation timer."""
        if self._pending_op_id == op_id:
            self._pending_op_timer = None

    def _update_loading_animation(self) -> str:
        """Get the next loading animation frame."""
        if self._pending_op_timer is None:
            return ""
        elapsed = time.perf_counter() - self._pending_op_timer
        if elapsed < LOADING_INDICATOR_DELAY_S:
            return ""
        # After 200ms, show animated loading indicator
        frame_idx = int((elapsed - LOADING_INDICATOR_DELAY_S) / LOADING_INTERVAL) % len(LOADING_FRAMES)
        return LOADING_FRAMES[frame_idx] + " "

    def _get_loading_text(self) -> str:
        """Get loading text - shows immediately on startup, then after delay for lazy operations."""
        if self._startup_loading:
            # On startup, show loading immediately
            return LOADING_FRAMES[0] + " "
        if self._pending_op_timer is None:
            return ""
        elapsed = time.perf_counter() - self._pending_op_timer
        if elapsed < LOADING_INDICATOR_DELAY_S:
            return ""
        frame_idx = int((elapsed - LOADING_INDICATOR_DELAY_S) / LOADING_INTERVAL) % len(LOADING_FRAMES)
        return LOADING_FRAMES[frame_idx] + " "

    # --- layout ----------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="title")
            yield Static("", id="policy")
            yield OptionList(id="actions")
            yield OptionList(id="rows")
            yield Input(id="prompt")
            yield Static("", id="status")

    def on_mount(self):
        self.goto(m.Nav(self.start, m.DIRECT_ROOT))
        # Menu first, then warm the model lists in the background (plan R13/R14): by the time
        # the user presses `c` the slow remote inventory (~1-6 s) is usually already cached.
        self.call_after_refresh(self._prefetch, ("local", "remote"))
        self.set_interval(LOADING_INTERVAL, self._tick_loading)

    # --- background inventory ----------------------------------------------------------------
    LOADERS = {"local": "load_local_models", "remote": "load_remote_agents"}

    def _prefetch(self, kinds):
        import threading
        if not hasattr(self, "_loading"):
            self._loading = set()
        for kind in kinds:
            if kind in self.cache or kind in self._loading:
                continue                      # cached, or a load is already running (coalesce)
            self._loading.add(kind)
            threading.Thread(target=self._load_in_thread, args=(kind,), daemon=True).start()

    def _load_in_thread(self, kind):
        try:
            data, err = globals()[self.LOADERS[kind]](), None
        except Exception as exc:              # surfaced as a status warning, never swallowed
            data, err = [], f"{kind} model list failed to load: {exc}"
        self.call_from_thread(self._loaded, kind, data, err)

    def _loaded(self, kind, data, err):
        self._loading.discard(kind)
        self.cache[kind] = data
        if err and err not in self.state_warnings:
            self.state_warnings.append(err)
        sm = self.screen_model
        # Fill an already-open list in place; otherwise just refresh counts/labels.
        if isinstance(sm, m.RemoteScreen) and kind == "remote" and not sm._models_loaded:
            sm.agents = data
            sm._regroup()
        elif isinstance(sm, (m.LocalScreen, m.LowkeyScreen)) and kind == "local" and not getattr(sm, "_models_loaded", False):
            sm.set_models(data)
            sm._models_loaded = True
        elif isinstance(sm, m.HomeScreen):
            if kind == "local":
                sm.local_count = len(data)
            else:
                sm.remote_count = len(data)
        if not getattr(self, "pending_prompt", None):
            self._rerender_keep_focus()

    def _startup_tick(self):
        return False

    def _waiting_for(self):
        """The inventory the visible screen is waiting on, if any (drives the loading line)."""
        sm = self.screen_model
        loading = getattr(self, "_loading", set())
        if isinstance(sm, m.RemoteScreen) and sm._choose_model_visible and "remote" in loading:
            return "remote"
        if isinstance(sm, (m.LocalScreen, m.LowkeyScreen)) and getattr(sm, "_choose_model_visible", True) and "local" in loading:
            return "local"
        return None

    def _tick_loading(self):
        if self._waiting_for():
            self._loading_frame += 1
            self.query_one("#status", Static).update("  " + loading_label(self._loading_frame, "loading models"))
        elif self._startup_tick():
            pass

    def _rerender_keep_focus(self):
        actions_list = self.query_one("#actions", OptionList)
        idx = actions_list.highlighted
        if self.focused is actions_list and idx is not None and idx < len(getattr(self, "action_ids", [])):
            self._rerender_keep_action(idx)
        else:
            self.render_model(keep=(lambda h: f"{'g' if h[0] == 'group' else 'i'}:{h[1]}" if h else None)(self._highlighted()))

    # --- navigation ------------------------------------------------------------------------
    def build(self, nav: m.Nav) -> m.Screen:
        s = self.settings
        if nav.target == "home":
            # Pass counts as None initially; they'll be loaded lazily
            return m.HomeScreen(s, local_count=None, remote_count=None, owner=nav.owner)
        if nav.target == "local":
            return m.LocalScreen(s, models=None, owner=nav.owner)
        if nav.target == "remote":
            return m.RemoteScreen(s, agents=None, owner=nav.owner)
        if nav.target == "lowkey":
            return m.LowkeyScreen(s, models=None, owner=nav.owner)
        if nav.target == "download":
            return m.DownloadScreen(s, entries=None, free_gb=None, headroom_gb=None, owner=nav.owner)
        if nav.target == "rate_limiter":
            return m.RateLimiterScreen(s, nav.owner, on_save=self._save_rl)
        if nav.target == "runtime_manager":
            return m.RuntimeManagerScreen(s, nav.owner)
        if nav.target == "api_keys":
            return m.APIKeysScreen(s, nav.owner)
        raise ValueError(nav.target)

    def goto(self, nav: m.Nav):
        # Build screen WITHOUT loading inventory (lazy loading)
        # Inventory will be loaded on-demand when user toggles "Choose model"
        s = self.settings
        if nav.target == "home":
            local = self.cache.get("local")
            remote = self.cache.get("remote")
            self.screen_model = m.HomeScreen(s, len(local) if local else None, len(remote) if remote else None, nav.owner)
        elif nav.target == "local":
            # Pass models only if already cached; otherwise pass None for lazy loading
            local = self.cache.get("local")
            self.screen_model = m.LocalScreen(s, local, nav.owner)
        elif nav.target == "remote":
            remote = self.cache.get("remote")
            self.screen_model = m.RemoteScreen(s, remote, nav.owner)
        elif nav.target == "lowkey":
            local = self.cache.get("local")
            self.screen_model = m.LowkeyScreen(s, local, nav.owner)
        elif nav.target == "download":
            catalog_data = self.cache.get("catalog")
            if catalog_data:
                entries, free, head = catalog_data
            else:
                entries, free, head = [], 0.0, 100.0
            self.screen_model = m.DownloadScreen(s, entries, free, head, nav.owner)
        elif nav.target == "rate_limiter":
            self.screen_model = m.RateLimiterScreen(s, nav.owner, on_save=self._save_rl)
        elif nav.target == "runtime_manager":
            self.screen_model = m.RuntimeManagerScreen(s, nav.owner)
        elif nav.target == "runtime":
            self.screen_model = m.RuntimeScreen(s, nav.owner)
        elif nav.target == "runtime_launchd":
            self.screen_model = m.LaunchdScreen(s, nav.owner)
        elif nav.target == "api_keys":
            self.screen_model = m.APIKeysScreen(s, nav.owner)
        else:
            raise ValueError(nav.target)

        self.nav = nav
        self.render_model()

    def render_model(self, keep: str | None = None):
        sm = self.screen_model
        self.query_one("#title", Static).update(Text(sm.title, style="bold"))
        policy = getattr(sm, "policy", "")
        # Show loading text - immediately on startup, after delay for lazy operations
        if self._startup_loading:
            policy += "  " + loading_label(self._loading_frame)
            self._startup_loading = False  # Only show immediate loading on first render
        self.query_one("#policy", Static).update(policy)
        rows = self.query_one("#rows", OptionList)
        rows.clear_options()
        self.row_ids = []

        # Handle lazy loading when "Choose model" is toggled
        self._handle_lazy_load()

        # Conditionally show model list based on _choose_model_visible
        show_models = True
        if isinstance(sm, (m.LocalScreen, m.RemoteScreen)):
            show_models = getattr(sm, '_choose_model_visible', False)

        # Show/hide the rows container
        rows.display = show_models

        # Check if screen has accordion with groups (RuntimeManagerScreen has accordion but no groups)
        has_groups = hasattr(sm, 'accordion') and sm.accordion is not None and len(sm.accordion.groups) > 0

        if show_models and has_groups:
            for kind, obj in sm.accordion.rows():
                if kind == "group":
                    mark = "▾" if obj.id == sm.accordion.open_group else "▸"
                    rows.add_option(Option(f"{mark} {obj.label}", id=f"g:{obj.id}"))
                    self.row_ids.append(("group", obj.id))
                else:
                    queued = isinstance(sm, m.DownloadScreen) and obj.id in sm.queue
                    box = "[x] " if queued else ("[ ] " if isinstance(sm, m.DownloadScreen) else "")
                    rows.add_option(Option(f"    {box}{obj.label}", id=f"i:{obj.id}"))
                    self.row_ids.append(("item", obj.id))

        if not self.row_ids and not isinstance(sm, (m.HomeScreen, m.RateLimiterScreen, m.RuntimeManagerScreen, m.APIKeysScreen)):
            rows.add_option(Option("  (nothing to show here yet)", id="empty", disabled=True))
        target = keep or (f"i:{sm.accordion.selected_id}" if sm.accordion and sm.accordion.selected_id else None)
        if target:
            for idx in range(rows.option_count):
                if rows.get_option_at_index(idx).id == target:
                    rows.highlighted = idx
                    break

        # Build actions list in the actions OptionList
        actions_list = self.query_one("#actions", OptionList)
        actions_list.clear_options()
        self.action_ids = []  # Track action IDs for keyboard navigation
        for a in sm.actions():
            if a.section == "hidden":
                continue
            label = a.label if a.enabled else f"{a.label}"
            # Store action object reference
            self.action_ids.append(a)
            enabled_str = "" if a.enabled else "  —"
            # Display shortcut as uppercase per R8 requirement
            display_key = a.key.upper()
            actions_list.add_option(Option(f"  {display_key}) {label}{enabled_str}", id=f"a:{len(self.action_ids)-1}"))

        status = "  ↑↓ move · Enter/click select · ←→ change setting · drag selects text, ⌘C/Ctrl+C copies"
        waiting = self._waiting_for() if hasattr(self, "_loading") else None
        if waiting:
            status = "  " + loading_label(self._loading_frame, "loading models")
        if isinstance(sm, m.DownloadScreen):
            status = "  ↑↓ move · Space/Enter queue · c review"
        if self.state_warnings:
            status += f"\n  {ec.EMOJI_WARNING_STR} " + self.state_warnings[-1]
        self.query_one("#status", Static).update(status)

    # --- keys ------------------------------------------------------------------------------
    def _highlighted(self):
        rows = self.query_one("#rows", OptionList)
        idx = rows.highlighted
        if idx is None or idx >= len(self.row_ids):
            return None
        return self.row_ids[idx]

    def on_option_list_option_highlighted(self, event):
        # Handle both rows and actions highlighting
        if event.option_list.id == "rows":
            h = self._highlighted()
            if h and h[0] == "item":
                self.screen_model.accordion.selected_id = h[1]
        elif event.option_list.id == "actions":
            # Update status to show action description
            pass

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "rows":
            self.enter()
        elif event.option_list.id == "actions":
            self._activate_action(event.option_index)

    def _activate_action(self, action_index: int):
        """Activate an action from the actions list."""
        if not hasattr(self, 'action_ids') or action_index >= len(self.action_ids):
            return
        action = self.action_ids[action_index]
        if not action.enabled:
            return
        if action.step is not None:
            self._step_action(action_index, +1)
            return
        result = action.run()
        if result:
            self.dispatch(result, None)
        else:
            self._rerender_keep_action(action_index)

    def _step_action(self, action_index: int, direction: int):
        action = self.action_ids[action_index]
        if not action.enabled or action.step is None:
            return
        action.step(direction)
        self._rerender_keep_action(action_index)

    def _rerender_keep_action(self, action_index: int):
        """Redraw with the highlight left on the same action row (settings change in place)."""
        actions_list = self.query_one("#actions", OptionList)
        key = self.action_ids[action_index].key if action_index < len(self.action_ids) else None
        self.render_model()
        for i, a in enumerate(self.action_ids):
            if a.key == key:
                actions_list.highlighted = i
                break

    def _maybe_load_inventory(self, screen_type: str) -> None:
        """Never blocks the UI: a cache miss starts (or joins) the background load, and
        _loaded() fills the list when it lands while the loading line animates."""
        kind = "remote" if screen_type == "remote" else "local"
        if kind not in self.cache:
            self._prefetch((kind,))

    def _handle_lazy_load(self) -> bool:
        """Check if current screen needs lazy loading and trigger it.
        Returns True if lazy loading was triggered."""
        sm = self.screen_model
        # Check LocalScreen
        if isinstance(sm, m.LocalScreen) and getattr(sm, '_choose_model_visible', False):
            if not getattr(sm, '_models_loaded', False):
                self._maybe_load_inventory("local")
                if "local" in self.cache:
                    sm.set_models(self.cache["local"])
                    sm._models_loaded = True
                return True
        # Check RemoteScreen
        if isinstance(sm, m.RemoteScreen) and getattr(sm, '_choose_model_visible', False):
            if not getattr(sm, '_models_loaded', False):
                self._maybe_load_inventory("remote")
                if "remote" in self.cache:
                    sm.agents = self.cache["remote"]
                    sm._regroup()  # Re-group with loaded agents
                    sm._models_loaded = True
                return True
        return False

    def enter(self):
        h = self._highlighted()
        if not h:
            return
        acc = self.screen_model.accordion
        if h[0] == "group":
            if acc.open_group == h[1]:
                acc.collapse()
            else:
                acc.expand(h[1])
            self.render_model(keep=f"g:{h[1]}")
            return
        acc.select(h[1])
        if isinstance(self.screen_model, m.DownloadScreen):
            self.screen_model.toggle_selected()
            self.render_model(keep=f"i:{h[1]}")
            return
        req = self.screen_model.activate_selected()
        if req:
            self.run_child(command_for(req), env=req.env)

    def on_key(self, event):
        prompt = self.query_one("#prompt", Input)
        if prompt.display:
            if event.key == "escape":
                self._close_prompt()
                event.stop()
            return
        key = event.key
        sm = self.screen_model
        # Seamless navigation between actions and rows
        if key == "down" or key == "up":
            actions_list = self.query_one("#actions", OptionList)
            rows_list = self.query_one("#rows", OptionList)
            actions_count = actions_list.option_count
            rows_count = rows_list.option_count

            # Check if actions list is focused
            if self.focused is actions_list:
                if key == "down" and actions_list.highlighted == actions_count - 1:
                    # Move from last action to first row (if rows visible)
                    if rows_count > 0 and rows_list.display:
                        rows_list.focus()
                        rows_list.highlighted = 0
                        event.stop()
                        return
                elif key == "up" and actions_list.highlighted == 0:
                    # At top of actions, could wrap or stay - let default handle
                    pass
            # Check if rows list is focused
            elif self.focused is rows_list:
                if key == "up" and rows_list.highlighted == 0:
                    # Move from first row to last action
                    if actions_count > 0:
                        actions_list.focus()
                        actions_list.highlighted = actions_count - 1
                        event.stop()
                        return
                elif key == "down" and rows_list.highlighted == rows_count - 1:
                    # At bottom of rows, could wrap or stay - let default handle
                    pass

        if key in ("left", "right"):
            actions_list = self.query_one("#actions", OptionList)
            if self.focused is actions_list and actions_list.highlighted is not None:
                idx = actions_list.highlighted
                if idx < len(self.action_ids) and self.action_ids[idx].step is not None:
                    self._step_action(idx, +1 if key == "right" else -1)
                    event.stop()
                    return
        # Handle group expand/collapse when on a group row (only when rows list is focused)
        if key in ("left", "right"):
            rows_list = self.query_one("#rows", OptionList)
            if self.focused is rows_list:
                h = self._highlighted()
                if h and h[0] == "group" and sm.accordion is not None:
                    if key == "right":
                        sm.accordion.expand(h[1])
                        self.render_model(keep=f"g:{h[1]}")
                        event.stop()
                        return
                    elif key == "left":
                        sm.accordion.collapse()
                        self.render_model(keep=f"g:{h[1]}")
                        event.stop()
                        return
                elif h and h[0] == "item" and sm.accordion is not None and sm.accordion.open_group:
                    # Left arrow on item with open group: collapse the group
                    if key == "left":
                        g = sm.accordion.open_group
                        sm.accordion.collapse()
                        self.render_model(keep=f"g:{g}")
                        event.stop()
                        return

        if key == "escape" or (len(key) == 1 and key.isalpha() and key.islower()):
            keep = self._highlighted()
            idx = next((i for i, a in enumerate(getattr(self, "action_ids", [])) if a.key == key), None)
            result = sm.handle_key(key)
            event.stop()
            if result is None and idx is not None:
                self._rerender_keep_action(idx)
                return
            self.dispatch(result, keep)

    def dispatch(self, result, keep=None):
        keep_id = f"{'g' if keep and keep[0] == 'group' else 'i'}:{keep[1]}" if keep else None
        if result is None:
            self.render_model(keep=keep_id)
            return
        if result == m.QUIT:
            self.exit(0)
            return
        if isinstance(result, m.LaunchRequest):     # a shortcut that launches (Go last `g`)
            self.run_child(command_for(result), env=result.env)
            return
        target = result.target
        if target.startswith("tool:"):
            self.run_tool(target)
            return
        if target.startswith("prompt:"):
            self._open_prompt(target)
            return
        if result is m.BACK or target == "back":
            self._go_back()
            return
        owner = result.owner
        # Tool/settings screens are sub-screens of whatever opened them: Back returns there
        # (Home or the exact lane), never Quit — even when the lane itself was a direct root.
        if target in self.SUB_TARGETS:
            self.nav_stack.append(self.nav)
            caller = self.screen_model
            self.goto(m.Nav(target, m.SUB))
            self.screen_model.back_label = (
                f"{ec.EMOJI_HOME_STR} Back to Home" if isinstance(caller, m.HomeScreen)
                else f"{ec.EMOJI_BACK_STR} Back to " + (caller.title.split(" ", 1)[-1] if getattr(caller, "title", "") else "previous"))
            self.render_model()
            return
        if target == "home":
            self.goto(m.Nav("home", m.DIRECT_ROOT))
            return
        self.goto(m.Nav(target, owner))

    SUB_TARGETS = ("rate_limiter", "runtime_manager", "runtime", "api_keys", "runtime_launchd")

    def _go_back(self):
        prev = self.nav_stack.pop() if self.nav_stack else m.Nav("home", m.DIRECT_ROOT)
        self.goto(prev)

    def run_tool(self, target):
        if target == "tool:download":
            self.goto(m.Nav("download", m.HOME_OWNED if isinstance(self.screen_model, m.HomeScreen) else self.nav.owner))
            return
        if target == "tool:lowkey":
            self.goto(m.Nav("lowkey", m.HOME_OWNED))
            return
        if target == "tool:rl-reset":
            self._open_prompt("prompt:rl-reset")
            return
        if target.startswith("tool:rt-"):
            self.run_child(TOOLS[target](self.settings.runtime_backend), pause=True)
            return
        if target.startswith("tool:keys:"):
            # API keys with specific provider
            slug = target.split(":", 2)[2]
            self.run_child(TOOLS["tool:keys:add"](slug), pause=True)
            return
        self.run_child(TOOLS[target](), pause=True)

    # --- inline prompts (text entry, confirmations) ------------------------------------------
    PROMPTS = {"prompt:session": "Session name (letters, digits, - _; empty = ephemeral):",
               "prompt:oneshot": "One-shot prompt:",
               "prompt:download-review": "",
               "prompt:rl-reset": "Really delete the shared limiter state file? type yes:",
               "prompt:rt-validate": "Version to validate (e.g. 0.15.3; empty cancels):",
               "prompt:rt-info": "Version to show (e.g. 0.15.3; empty cancels):",
               "prompt:rt-launchd-install": "Install the weekly LaunchAgent update check? type yes:",
               "prompt:rt-launchd-uninstall": "Remove the weekly LaunchAgent update check? type yes:"}

    def _open_prompt(self, kind):
        self.pending_prompt = kind
        prompt = self.query_one("#prompt", Input)
        label = self.PROMPTS[kind]
        if kind == "prompt:download-review":
            rv = self.screen_model.review()
            fit = "fits the disk reserve" if rv.fits else "BREACHES the disk reserve — the downloader will refuse"
            label = (f"Queue: {', '.join(rv.aliases)} · ~{rv.total_gb:g} GB · {fit}. "
                     "Type yes to download, anything else cancels:")
        prompt.placeholder = label
        prompt.value = ""
        prompt.display = True
        self.query_one("#status", Static).update("  " + label)
        prompt.focus()

    def _close_prompt(self):
        prompt = self.query_one("#prompt", Input)
        prompt.display = False
        self.pending_prompt = None
        self.query_one("#rows", OptionList).focus()
        self.render_model()

    def on_input_submitted(self, event):
        kind, value = self.pending_prompt, event.value.strip()
        self._close_prompt()
        sm = self.screen_model
        if kind == "prompt:session":
            import re
            if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                self.state_warnings.append("session name rejected: use letters, digits, - or _")
            else:
                sm.session_name = value
            self.render_model()
        elif kind == "prompt:oneshot":
            req = sm.one_shot(value)
            if req:
                self.run_child(command_for(req), pause=True)
        elif kind == "prompt:download-review":
            req = sm.confirm(value.lower() == "yes")
            if req:
                self.run_child(command_for(req), pause=True)
                self.goto(m.Nav("download", self.nav.owner))
            else:
                self.state_warnings.append("download cancelled — nothing was started")
                self.render_model()
        elif kind == "prompt:rl-reset":
            if value.lower() == "yes":
                self.run_child(["python3", "-c",
                                "import rate_limiter as r,os;p=r.DEFAULT_STATE if not os.environ.get('LA_NVIDIA_THROTTLE_STATE') else r.Path(os.environ['LA_NVIDIA_THROTTLE_STATE']);p.unlink(missing_ok=True);print('state file reset')"],
                               pause=True, cwd=str(BIN))

        elif kind in ("prompt:rt-validate", "prompt:rt-info"):
            import re
            if not value:
                self.render_model()
            elif not re.fullmatch(r"[0-9][0-9A-Za-z.]{0,31}", value):
                self.state_warnings.append("version rejected: digits, letters and dots only")
                self.render_model()
            else:
                tool = "tool:rt-" + kind.rsplit("-", 1)[1]
                self.run_child(TOOLS[tool](self.settings.runtime_backend, value), pause=True)
        elif kind in ("prompt:rt-launchd-install", "prompt:rt-launchd-uninstall"):
            if value.lower() == "yes":
                self.run_child(TOOLS["tool:" + kind.split(":", 1)[1]](self.settings.runtime_backend), pause=True)
            else:
                self.state_warnings.append("launchd unchanged — nothing was run")
                self.render_model()

    # --- children ------------------------------------------------------------------------------
    def run_child(self, cmd, env=None, pause=False, cwd=None):
        """Hand the real terminal to `cmd`, then resume this exact screen."""
        full_env = dict(os.environ)
        full_env.update(env or {})
        full_env.pop("TEXTUAL", None)
        keep = self._highlighted()
        with self.suspend():
            try:
                rc = subprocess.call(cmd, env=full_env, cwd=cwd)
            except (OSError, KeyboardInterrupt) as exc:
                rc = getattr(exc, "errno", 130) or 130
                print(f"\n[picker] could not run {Path(cmd[0]).name}: {exc}")
            if pause:
                try:
                    input(f"\n[picker] finished (exit {rc}). Press Enter to return…")
                except (EOFError, KeyboardInterrupt):
                    pass
        self.state_warnings = [w for w in self.state_warnings if not w.startswith("last child")]
        if rc not in (0, None):
            self.state_warnings.append(f"last child exited {rc}")
        self.cache.pop("local", None) if isinstance(self.screen_model, m.DownloadScreen) else None
        self.render_model(keep=(f"{'g' if keep[0] == 'group' else 'i'}:{keep[1]}" if keep else None))

    def copy_to_clipboard(self, text: str) -> None:
        # Textual copies via OSC 52, which macOS Terminal.app ignores (Textual's own docs say
        # so) — that is why copying silently did nothing. Also hand the text to the system
        # clipboard: pbcopy on macOS, wl-copy/xclip elsewhere when present. Never fatal.
        super().copy_to_clipboard(text)
        import shutil
        for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, input=text, text=True, timeout=3, check=False)
                except (OSError, subprocess.SubprocessError):
                    continue
                break

    def action_copy_selection(self):
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.screen.clear_selection()
            self.notify("Copied", timeout=1.5)
        return bool(text)

    def action_copy_or_quit(self):
        if not self.action_copy_selection():
            self.action_quit_now()

    def action_quit_now(self):
        self.exit(130)


def main(argv):
    ap = argparse.ArgumentParser(prog="session-picker",
                                 description="Free Agents session picker (arrow keys, letter shortcuts).")
    ap.add_argument("screen", nargs="?", default="home",
                    choices=["home", "local", "remote", "lowkey", "download", "rate-limiter"])
    ap.add_argument("--include-trials", action="store_true", help="show trial rows (the default)")
    ap.add_argument("--exclude-trials", action="store_true", help="start with trial rows hidden")
    ap.add_argument("--local-capable-shown", action="store_true",
                    help="start with locally-runnable remote models shown")
    args = ap.parse_args(argv)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("session-picker: needs an interactive terminal (use a direct alias or --list instead)",
              file=sys.stderr)
        return 2
    start = "rate_limiter" if args.screen == "rate-limiter" else args.screen
    rc = Picker(start, args).run()
    return rc or 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv[1:]))
