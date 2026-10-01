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

# Loading indicator delay threshold (200ms per performance amendment)
LOADING_INDICATOR_DELAY_S = 0.2

REPO = BIN.parent
NO_COLOR = bool(os.environ.get("NO_COLOR"))


# --- inventories (read-only; never eval shell) ---------------------------------------------
def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def load_local_models() -> list[m.LocalModel]:
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
        if len(p) == 6:
            agents.append(m.RemoteAgent(p[0], p[1], p[2], p[3], local_capable=p[4] == "1",
                                        has_key=p[5] == "1"))
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


TOOLS = {
    "tool:keys": lambda: ["python3", str(REPO / "install/setup-api-keys.py")],
    "tool:runtime": lambda: ["python3", str(REPO / "install/manage-rapid-mlx.py")],
    "tool:rl-status": lambda: ["python3", str(BIN / "rate_limiter.py"), "status"],
    "tool:report": lambda: ["bash", str(BIN / "local-capable-filter.sh"), "--report",
                            str(REPO / "config/local-capable-remote-models.psv"), "--roster",
                            str(REPO / "config/remote-agents.sh")],
}


class Picker(App):
    # Use Apple Terminal safe driver when available (TERM_PROGRAM=Apple_Terminal on Unix)
    driver_class = _DRIVER_CLASS
    CSS = """
    Screen { layout: vertical; }
    #title { text-style: bold; padding: 0 1; }
    #policy { color: $warning; padding: 0 1; }
    #rows { height: 1fr; }
    #actions { height: auto; padding: 0 1; }
    #status { height: auto; padding: 0 1; color: $text-muted; }
    #prompt { display: none; }
    """
    BINDINGS = [Binding("ctrl+c", "quit_now", "Quit", show=False, priority=True)]

    def __init__(self, start: str, flags: argparse.Namespace):
        super().__init__()
        state = sms.load(Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR))
        self.state_warnings = list(state.warnings)
        self.settings = m.Settings(
            local_effort=state.effort["local_session"],
            remote_effort=state.effort["remote_api_session"],
            lowkey_effort=state.effort["lowkey"],
            rate_limiter=dict(state.rate_limiter),
            auto_mode=int(os.environ.get("CSL_AUTO_MODE_STATE", "0") or 0) % 3,
            telemetry=os.environ.get("CSL_TELEMETRY", "0") == "1",
            stop_hook=os.environ.get("CSL_STOP_HOOK", "1") != "0",
            watcher=os.environ.get("CSL_WATCH", "0") == "1",
            enable_mcp=os.environ.get("LA_ENABLE_MCP", "0") == "1",
            include_trials=not flags.exclude_trials,
            local_capable_shown=flags.local_capable_shown or os.environ.get("CSL_LOCAL_CAPABLE") == "1",
            on_effort_saved=self._save_effort)
        self.start = start
        self.screen_model: m.Screen | None = None
        self.cache: dict = {}
        self.pending_prompt = None
        # Loading indicator state
        self._pending_op_timer: float | None = None
        self._pending_op_id: int = 0

    # --- persistence -----------------------------------------------------------------------
    def _config_dir(self):
        return Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR)

    def _save_effort(self, lane, value):
        warning = sms.save_effort(self._config_dir(), lane, value)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    def _save_rl(self, values):
        warning = sms.save_rate_limiter(self._config_dir(), values)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    # --- loading indicator ---------------------------------------------------------------------
    def _start_pending_op(self) -> int:
        """Start a pending operation timer. Returns operation ID."""
        self._pending_op_id += 1
        self._pending_op_timer = time.perf_counter()
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

    # --- layout ----------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="title")
            yield Static("", id="policy")
            yield OptionList(id="rows")
            yield Static("", id="actions")
            yield Input(id="prompt")
            yield Static("", id="status")

    def on_mount(self):
        self.goto(m.Nav(self.start, m.DIRECT_ROOT))

    # --- navigation ------------------------------------------------------------------------
    def build(self, nav: m.Nav) -> m.Screen:
        s = self.settings
        if nav.target == "home":
            local = self.cache.setdefault("local", load_local_models())
            remote = self.cache.setdefault("remote", load_remote_agents())
            return m.HomeScreen(s, len(local), len(remote), nav.owner)
        if nav.target == "local":
            return m.LocalScreen(s, self.cache.setdefault("local", load_local_models()), nav.owner)
        if nav.target == "remote":
            return m.RemoteScreen(s, self.cache.setdefault("remote", load_remote_agents()), nav.owner)
        if nav.target == "lowkey":
            return m.LowkeyScreen(s, self.cache.setdefault("local", load_local_models()), nav.owner)
        if nav.target == "download":
            entries, free, head = load_catalog()
            return m.DownloadScreen(s, entries, free, head, nav.owner)
        if nav.target == "rate_limiter":
            return m.RateLimiterScreen(s, nav.owner, on_save=self._save_rl)
        raise ValueError(nav.target)

    def goto(self, nav: m.Nav):
        self.screen_model = self.build(nav)
        self.nav = nav
        self.render_model()

    def render_model(self, keep: str | None = None):
        sm = self.screen_model
        self.query_one("#title", Static).update(Text(sm.title, style="bold"))
        policy = getattr(sm, "policy", "")
        if isinstance(sm, m.RemoteScreen) and sm.hidden_count():
            policy += f"  ({sm.hidden_count()} hidden by filters)"
        # Check if any pending operation has exceeded the loading indicator delay
        if self._pending_op_timer is not None:
            elapsed = time.perf_counter() - self._pending_op_timer
            if elapsed >= LOADING_INDICATOR_DELAY_S:
                policy += "  ⏳ loading…"
        self.query_one("#policy", Static).update(policy)
        rows = self.query_one("#rows", OptionList)
        rows.clear_options()
        self.row_ids = []
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
        if not self.row_ids and not isinstance(sm, (m.HomeScreen, m.RateLimiterScreen)):
            rows.add_option(Option("  (nothing to show here yet)", id="empty", disabled=True))
        target = keep or (f"i:{sm.accordion.selected_id}" if sm.accordion.selected_id else None)
        if target:
            for idx in range(rows.option_count):
                if rows.get_option_at_index(idx).id == target:
                    rows.highlighted = idx
                    break
        lines = []
        for a in sm.actions():
            if a.section == "hidden":
                continue
            label = a.label if a.enabled else f"{a.label}"
            lines.append(f"  {a.key}) {label}" + ("" if a.enabled else "  —"))
        self.query_one("#actions", Static).update("\n".join(lines))
        status = "  ↑↓ move · Enter open/launch · ← collapse"
        if isinstance(sm, m.DownloadScreen):
            status = "  ↑↓ move · Space/Enter queue · c review"
        if self.state_warnings:
            status += "\n  ⚠ " + self.state_warnings[-1]
        self.query_one("#status", Static).update(status)

    # --- keys ------------------------------------------------------------------------------
    def _highlighted(self):
        rows = self.query_one("#rows", OptionList)
        idx = rows.highlighted
        if idx is None or idx >= len(self.row_ids):
            return None
        return self.row_ids[idx]

    def on_option_list_option_highlighted(self, event):
        h = self._highlighted()
        if h and h[0] == "item":
            self.screen_model.accordion.selected_id = h[1]

    def on_option_list_option_selected(self, event):
        self.enter()

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
        if key == "right":
            h = self._highlighted()
            if h and h[0] == "group":
                sm.accordion.expand(h[1])
                self.render_model(keep=f"g:{h[1]}")
            event.stop()
            return
        if key == "left":
            if sm.accordion.open_group:
                g = sm.accordion.open_group
                sm.accordion.collapse()
                self.render_model(keep=f"g:{g}")
            event.stop()
            return
        if key == "space" and isinstance(sm, m.DownloadScreen):
            h = self._highlighted()
            if h and h[0] == "item":
                sm.accordion.select(h[1])
                sm.toggle_selected()
                self.render_model(keep=f"i:{h[1]}")
            event.stop()
            return
        if key == "escape" or (len(key) == 1 and key.isalpha() and key.islower()):
            keep = self._highlighted()
            result = sm.handle_key(key)
            event.stop()
            self.dispatch(result, keep)

    def dispatch(self, result, keep=None):
        keep_id = f"{'g' if keep and keep[0] == 'group' else 'i'}:{keep[1]}" if keep else None
        if result is None:
            self.render_model(keep=keep_id)
            return
        if result == m.QUIT:
            self.exit(0)
            return
        target = result.target
        if target.startswith("tool:"):
            self.run_tool(target)
            return
        if target.startswith("prompt:"):
            self._open_prompt(target)
            return
        # Tool screens reached from Home get Back; reached from a lane they keep the lane's owner.
        owner = result.owner
        if target in ("rate_limiter",) and isinstance(self.screen_model, m.HomeScreen):
            owner = m.HOME_OWNED
        if target == "home":
            self.goto(m.Nav("home", m.DIRECT_ROOT))
            return
        self.goto(m.Nav(target, owner))

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
        self.run_child(TOOLS[target](), pause=True)

    # --- inline prompts (text entry, confirmations) ------------------------------------------
    PROMPTS = {"prompt:session": "Session name (letters, digits, - _; empty = ephemeral):",
               "prompt:oneshot": "One-shot prompt:",
               "prompt:download-review": "",
               "prompt:rl-reset": "Really delete the shared limiter state file? type yes:"}

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
