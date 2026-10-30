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
                 [--exclude-trials] [--local-capable-shown] [--show-old] [--help]
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
import cloud_session_env as cse  # noqa: E402
import session_picker_model as m  # noqa: E402

try:
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.message import Message
    from textual.screen import ModalScreen
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
def _run(cmd, timeout=60, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def load_local_models() -> list[m.LocalModel]:
    # The on-disk registry (csl --inventory) is the model list. 0.23.0 replaced it with
    # load-profile-models.py, which holds only a few hand-declared profiles, maps every one of
    # them to the first "operator" alias, and was run under bash (so it always failed into this
    # path anyway). Profile-aware discovery is tracked as its own work item, not done here.
    csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
    r = _run(["bash", csl, "--inventory"])
    models = []
    missing = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts and parts[0] == "#missing":
            # Registered, weights gone (csl reports "#missing<TAB>alias<TAB>subdir"): not
            # launchable, collected for the once-per-session cleanup offer.
            if len(parts) >= 2 and parts[1] and parts[1] not in missing:
                missing.append(parts[1])
            continue
        if len(parts) >= 4 and parts[0]:
            auto = parts[3] == "auto-scanned"
            models.append(m.LocalModel(alias=parts[0], backend=parts[1], effort=parts[2],
                                       roles=parts[3], family=parts[4] if len(parts) > 4 else "",
                                       auto_scanned=auto,
                                       folder=(parts[4] if auto and len(parts) > 4 else "")))
    # REPLACE, never append: the inventory is loaded more than once per picker run (startup
    # prefetch, then again after a registration), and appending double-counted every model.
    MISSING_MODELS[:] = missing
    return models


# Filled by load_local_models() from the "#missing" inventory rows; read once by the picker.
MISSING_MODELS: list[str] = []


def auto_sync_registry(models) -> str:
    """Local screen's "auto-sync" ON: register every on-disk unregistered model and move
    every registered-but-missing one to the removed list, without asking (OFF = the panels).

    Uses the same csl subcommands as the panels (each backs the registry up first). Returns the
    status note to show, "" when there was nothing to do. Runs in the loader thread."""
    csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
    new = [x for x in models if x.auto_scanned]
    gone = list(MISSING_MODELS)
    if not new and not gone:
        return ""
    registered, failed = 0, []
    for x in new:
        r = _run([csl, "--register-model", x.folder or x.alias])
        if r.returncode == 0:
            registered += 1
        else:
            failed.append(x.alias)
    moved = 0
    if gone:
        r = _run([csl, "--unregister-model", *gone])
        if r.returncode == 0:
            moved = len(gone)
        else:
            failed += gone
    note = f"auto-synced the registry: registered {registered}, moved {moved} missing"
    return note + (f"; failed: {', '.join(failed)}" if failed else "")


def load_remote_agents() -> list[m.RemoteAgent]:
    rs = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
    r = _run(["bash", rs, "--inventory"])
    agents = []
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) in (6, 7, 8):  # 7th column (broken) arrived with 0.21.9, 8th (model id) with 0.37.0
            agents.append(m.RemoteAgent(p[0], p[1], p[2], p[3], local_capable=p[4] == "1",
                                        has_key=p[5] == "1", broken=len(p) >= 7 and p[6] == "1",
                                        model_id=p[7] if len(p) == 8 else ""))
    return agents


_FOOTPRINT = None


def _footprint():
    global _FOOTPRINT
    if _FOOTPRINT is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("estimate_model_footprint",
                                                      BIN / "estimate-model-footprint.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FOOTPRINT = mod
    return _FOOTPRINT


_VERDICT_MEMO: dict = {}


def _memoized_verdict(name, model_id, compute):
    """One verdict per (kind, model id) per session.

    The estimator reads its cache files from disk on EVERY call, and a regroup asks for
    every row of every loaded provider — the eager warm-up plus each live-filter keystroke
    re-read thousands of files on the UI thread (measured 2026-10-28: 0.3s per regroup for
    one 4000-row provider, multi-second stalls in total). The verdicts depend only on the
    id, the cache the loader warmed BEFORE the row renders, and the device — session-stable
    inputs — so one dict is exact enough and a fresh csl re-derives everything."""
    key = (name, model_id)
    if key not in _VERDICT_MEMO:
        _VERDICT_MEMO[key] = compute()
    return _VERDICT_MEMO[key]


def catalog_local_capable(provider, model_id):
    """True when a live-catalog row would fit THIS device at >=4-bit (the filter hides it).

    User rule 2026-10-20: hidden when downloadable weights exist that fit the device at
    4-bit quantization or finer; 2/3-bit-only fits stay visible (edge cases). The budget
    comes from the device itself (75% of unified RAM / 90% of CUDA VRAM), never a
    hardcoded size. Runs while the accordion regroups, so it must never wait on the
    network: it reads the Hugging Face count only from the cache that load_remote_catalog
    warmed, then falls back to the count in the id. An unknown verdict stays visible
    (fail-open)."""
    def compute():
        try:
            return _footprint().estimate(model_id, use_hf=True, hf_offline=True)["suggested"] == "local-capable"
        except Exception:
            return False
    return _memoized_verdict("cap", model_id, compute)


# Vendor-documented totals for ids whose numbers the estimator cannot parse (user
# 2026-10-30): mistral-large-4-1t-a52b = 1.05T total / 52B active ("Le Chonk", Mistral
# 2026-10-06); Nemotron/Laguna totals from the roster notes.
PARAM_COUNT_OVERRIDES = {
    "mistral-large-4-1t-a52b": 1050.0,
    "nemotron-3-ultra-550b-a55b": 550.0,
    "nemotron-3-super-120b-a12b": 120.0,
    "nemotron-3.5-lightning-30b-a3b": 30.0,
    "laguna-xs-2.1": 33.0,
}


def model_param_count(provider, model_id):
    """Parameter count in billions for the per-provider size sort, or None when unknown.

    Overrides first, then the count in the id (the estimator's parser); when the id states
    none, the Hugging Face count ONLY if already cached — same offline rule as the
    local-capable verdict: a render never waits on the network. None sorts after the sized
    rows."""
    def compute():
        try:
            for pat, b in PARAM_COUNT_OVERRIDES.items():
                if pat in (model_id or ""):
                    return float(b)
            fp = _footprint()
            total = fp.parse_params(model_id)["total_b"]
            if total is None:
                total, _repo = fp.hf_total_params(model_id, offline=True)
                total = round(total / 1e9, 1) if total else None   # HF counts raw params
            return float(total) if total else None
        except Exception:
            return None
    return _memoized_verdict("params", model_id, compute)


def catalog_model_old(provider, model_id):
    """True when a live-catalog row predates the age cutoff (the filter hides it).

    The cutoff is LA_REMOTE_MAX_MODEL_AGE_MONTHS months (default 18) before today; the
    date is the row's Hugging Face createdAt, read from the cache the loader thread
    warmed — never fetched at render time. No cached date means NOT old (fail-open)."""
    def compute():
        try:
            created = _footprint().hf_created(model_id)
        except Exception:
            return False
        return m.created_before_cutoff(created, m.max_model_age_months())
    return _memoized_verdict("old", model_id, compute)


def _warm_hf_cache(model_ids):
    """Fill the HF cache (parameter count + createdAt date) for catalog ids (loader thread).

    Every id is warmed up to the per-provider cap: the age filter needs a date for sized
    ids too, not only for the ones that state no count. Fresh entries answer from the
    cache and cost no network."""
    try:
        fp = _footprint()
    except Exception:
        return
    for mid in model_ids:
        fp.hf_total_params(mid, timeout=4.0)
        # A verdict computed while the cache was still cold (fail-open) must not outlive
        # the warm-up: the first paint often hydrates DISK rows before the fetch lands
        # (review finding 1, 2026-10-28).
        _VERDICT_MEMO.pop(("old", mid), None)
        _VERDICT_MEMO.pop(("cap", mid), None)
        _VERDICT_MEMO.pop(("params", mid), None)


def load_remote_catalog(kind):
    """`rcat:<alias>` -> that provider's live chat/tool catalog as m.CatalogModel rows.

    Runs `remote-session.sh --models <alias>` (one catalog GET, no generation). Raises on
    failure so the picker shows an error row instead of a silently empty group."""
    alias = kind.split(":", 1)[1]
    rs = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
    r = _run(["bash", rs, "--include-trials", "--models", alias])
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else f"catalog exited {r.returncode}")
    rows = [m.CatalogModel.from_line(ln) for ln in r.stdout.splitlines() if "\t" in ln]
    if os.environ.get("LA_PICKER_HF_LOOKUP", "1") != "0":
        _warm_hf_cache([c.model_id for c in rows][:HF_WARM_LIMIT])
    return rows


# A catalog can list hundreds of ids; the first lookups cost ~0.3 s each, so cap the cold
# warm-up per provider. Later opens read the 30-day cache instantly. The cap must exceed a
# provider's full catalog (NVIDIA lists 85 models, 2026-10-31) or the tail rows never get a
# size/age verdict and the picker shows them unsized — user 2026-10-31: every NVIDIA group
# model should display its parameter size.
HF_WARM_LIMIT = int(os.environ.get("LA_PICKER_HF_WARM_LIMIT", "200"))


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
    # Use req.lane to determine the command type, not argv[0]
    # This allows API keys wizard to have argv starting with python3 while lane="keys"
    lane = req.lane
    if lane == "local":
        # Through csl, so the session profile and the watcher (following csl's pid, which the
        # launcher inherits via exec) behave exactly as before.
        return [os.environ.get("CSL_SELF") or str(BIN / "csl"), "--picker-launch", *req.argv[1:]]
    if lane == "remote":
        # When running as a child of csl (HOME_OWNED), pass --csl-owner and --csl-nav-file
        # so remote-session.sh returns via nav file instead of exec'ing claude directly.
        base = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
        if getattr(req, "owner", "direct_root") == "home_owned":
            import tempfile
            navfile = os.path.join(tempfile.gettempdir(), f"_csl_nav.{os.getpid()}")
            return [base, "--csl-owner", "--csl-nav-file", navfile, *req.argv[1:]]
        return [base, *req.argv[1:]]
    if lane == "lowkey":
        return [sys.executable if os.environ.get("LOWKEY_USE_PICKER_PY") else "python3",
                str(BIN / "lowkey-cli.py"), *req.argv[1:]]
    if lane == "download":
        return [os.environ.get("LA_DOWNLOADER") or str(REPO / "install/download-models.sh"), *req.argv[1:]]
    if lane == "keys":
        # API keys setup wizard - runs setup-api-keys.py with provider slug
        return req.argv
    raise ValueError(f"Unknown lane: {lane}")


def _mb(backend: str, *args: str) -> list[str]:
    # LA_MANAGE_BACKEND: an executable standing in for manage-backend.py (demos, like LA_DOWNLOADER).
    exe = os.environ.get("LA_MANAGE_BACKEND")
    head = [exe] if exe else ["python3", str(REPO / "install/manage-backend.py")]
    return [*head, "--backend", backend, *args]


def load_versions(kind: str) -> list[m.VersionEntry]:
    """kind = "versions:<backend>:installed|releases" -> the list a VersionsScreen picks from,
    read from manage-backend.py's own text output (never a second source of truth)."""
    _, backend, source = kind.split(":", 2)
    inst = _run(_mb(backend, "installed"))
    installed = [m.VersionEntry(p[0], p[1]) for p in (ln.split() for ln in inst.stdout.splitlines())
                 if len(p) >= 2 and p[0][:1].isdigit()]
    if source == "installed":
        if inst.returncode != 0:
            raise RuntimeError((inst.stderr or inst.stdout).strip() or f"exit {inst.returncode}")
        return installed
    rel = _run(_mb(backend, "releases", "--pre", "--limit", "30"))
    if rel.returncode != 0:
        raise RuntimeError((rel.stderr or rel.stdout).strip() or f"exit {rel.returncode}")
    have = {e.version for e in installed}
    return [m.VersionEntry(v, "installed" if v in have else "")
            for v in (ln.strip() for ln in rel.stdout.splitlines()) if v]


PROVIDER_SIGNUP_URLS = m.PROVIDER_SIGNUP_URLS   # one table: install/setup-api-keys.py

TOOLS = {
    "tool:keys": lambda: ["python3", str(REPO / "install/setup-api-keys.py")],
    "tool:rl-status": lambda: ["python3", str(BIN / "rate_limiter.py"), "status"],
    "tool:report": lambda: ["bash", str(BIN / "local-capable-filter.sh"), "--report",
                            str(REPO / "config/local-capable-remote-models.psv"), "--roster",
                            str(REPO / "config/remote-agents.sh")],
    # Backend manager (install/manage-backend.py): backend -> argv. Version-taking operations
    # (install/validate/info/snapshot/promote/remove) go through VersionsScreen.argv_for. "tool:rl-reset" is
    # the RATE LIMITER state reset; it is handled as a typed-confirm prompt, never run directly.
    "tool:rt-releases": lambda b: _mb(b, "releases", "--pre"),
    "tool:rt-launchd-status": lambda b: _mb(b, "launchd", "status"),
    "tool:rt-launchd-run-once": lambda b: _mb(b, "launchd", "run-once"),
    "tool:rt-launchd-install": lambda b: _mb(b, "launchd", "install"),
    "tool:rt-launchd-uninstall": lambda b: _mb(b, "launchd", "uninstall"),
    "tool:keys:add": lambda slug: ["python3", str(REPO / "install/setup-api-keys.py"), slug],
}


class PromptInput(Input):
    """The inline prompt. With submit_on_paste set (API keys), a paste replaces the field and
    submits at once, like the old wizard's bracketed-paste reader: a key can never be pasted
    twice into one value, and no extra Enter is needed."""
    submit_on_paste = False

    def _on_paste(self, event):
        if self.submit_on_paste and event.text:
            # prevent_default: Textual also runs Input._on_paste (MRO dispatch), which would
            # insert the raw first line into the field; stop() alone only halts bubbling.
            event.prevent_default()
            event.stop()
            # A key never contains whitespace, so drop ALL of it: trailing newline, stray
            # spaces, or a key wrapped across lines would otherwise be saved as-is (or cut off).
            key = "".join(event.text.split())
            if not key:
                return                      # whitespace-only paste: keep waiting
            removed = len(event.text) - len(key)
            if removed and len(event.text.strip()) != len(key):
                self.app.state_warnings.append(
                    f"removed {removed} whitespace character(s) from the pasted key")
            self.value = key
            self.post_message(self.Submitted(self, self.value))
        # Other prompts: no call to super() — Textual dispatches Input._on_paste itself, and
        # calling it here too inserted every paste twice.


MODEL_LIST_SCREENS = (m.LocalScreen, m.RemoteScreen)   # the screens whose list has the live filter


class SearchInput(Input):
    """The live model filter at the top of the choose-model list (user, 2026-10-21): always
    typing, never a prompt round-trip — every character narrows the list at once, from the
    first one, with no Enter needed. Up/Down move the list's highlight (the caret stays in
    the box), Enter launches the highlighted model; a character typed with focus on the
    LIST lands in the box too (App.on_key routes it), so the filter accepts input no matter
    where the highlight is."""
    BINDINGS = [
        Binding("up", "filter_arrow_up", show=False),
        Binding("down", "filter_arrow_down", show=False),
    ]

    def action_filter_arrow_up(self):
        self.app._filter_arrow(-1)

    def action_filter_arrow_down(self):
        self.app._filter_arrow(1)


class ConfirmPanel(ModalScreen):
    """The picker's one confirmation idiom (user 2026-10-16): the QUESTION is the first line at
    full emphasis, the explanation sits under it, and the answers are selectable rows —
    arrows + Enter or a click, never a typed yes/no. `items` (optional) are pre-ticked
    checkboxes for a per-item choice, drawn as monochrome `[x]` / `[ ]` like the downloader's
    queue (SelectionList's green half-block toggles appeared nowhere else; user 2026-10-16);
    Space, Enter or a click toggles one. Escape always means the safe answer: dismiss(None).
    Dismisses with (choice_id, [ticked item ids])."""

    DEFAULT_CSS = """
    ConfirmPanel { align: center middle; background: ansi_default 0%; }
    #cp-box { width: 90; height: auto; max-height: 90%; border: heavy ansi_yellow;
              padding: 0 1; background: ansi_default; }
    #cp-question { text-style: bold; color: ansi_default; padding: 0 0 1 0; }
    #cp-detail { color: ansi_default; padding: 0 0 1 0; }
    #cp-pick { height: auto; max-height: 12; border: none; background: ansi_default; color: ansi_default; }
    #cp-choices { height: auto; border: none; background: ansi_default; margin-top: 1; }
    """

    def __init__(self, question: str, detail: str, choices: list[tuple[str, str]],
                 items: list[str] | None = None, ticked: list[str] | None = None):
        super().__init__()
        self.question, self.detail, self.choices = question, detail, choices
        self.items = items or []
        self.ticked = set(self.items if ticked is None else ticked)

    def _box(self, item: str) -> str:
        return f"{'[x]' if item in self.ticked else '[ ]'} {item}"

    def compose(self) -> ComposeResult:
        with Vertical(id="cp-box"):
            yield Static(self.question, id="cp-question")
            yield Static(self.detail, id="cp-detail")
            # Text, not str: a str prompt is parsed as markup, and "[x]" is a valid tag (eaten).
            pick = OptionList(*(Option(Text(self._box(i)), id=i) for i in self.items), id="cp-pick")
            pick.display = bool(self.items)
            yield pick
            yield OptionList(*(Option(label, id=cid) for cid, label in self.choices), id="cp-choices")

    def on_mount(self):
        choices = self.query_one("#cp-choices", OptionList)
        choices.highlighted = 0        # Enter answers the first (default) choice at once
        choices.focus()

    def _toggle(self, index: int):
        pick = self.query_one("#cp-pick", OptionList)
        item = pick.get_option_at_index(index).id
        self.ticked ^= {item}
        pick.replace_option_prompt_at_index(index, Text(self._box(item)))

    def on_option_list_option_selected(self, event):
        event.stop()                   # never let it bubble into the picker's own handler
        if event.option_list.id == "cp-pick":
            self._toggle(event.option_index)
            return
        self.dismiss((event.option.id, [i for i in self.items if i in self.ticked]))

    def on_key(self, event):
        pick = self.query_one("#cp-pick", OptionList)
        if event.key == "space" and self.focused is pick and pick.highlighted is not None:
            event.stop()
            self._toggle(pick.highlighted)
            return
        # Escape = the safe answer. Handled HERE and stopped: a screen BINDING fires but the key
        # still bubbles on to App.on_key, which read it as the lane's Escape (= Quit).
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class HoverOptionList(OptionList):
    """OptionList that reports which row the mouse is over, so the info box can follow the
    pointer as well as the keyboard highlight. Textual tracks hover only for styling."""

    class Hovered(Message):
        def __init__(self, index: int | None):
            super().__init__()
            self.index = index

    def _on_mouse_move(self, event):
        before = self._mouse_hovering_over
        super()._on_mouse_move(event)
        if self._mouse_hovering_over != before:
            self.post_message(self.Hovered(self._mouse_hovering_over))

    def _on_leave(self, event):
        super()._on_leave(event)
        self.post_message(self.Hovered(None))


def _detect_hw():
    """One-shot OS/distro/hardware detection through the repo's canonical script
    (bin/la-hw-detect.sh, which exports LA_OS / LA_DISTRO / LA_HARDWARE). Env wins when
    the launcher already exported them; any failure returns Nones and the model falls
    back to platform.system(). Runs once per picker start, never per render."""
    import os
    import subprocess
    from pathlib import Path
    if os.environ.get("LA_OS") and os.environ.get("LA_HARDWARE"):
        ram = None
        try:
            ram = float(os.environ.get("LA_SYSTEM_RAM_GB") or "") or None
        except ValueError:
            pass
        return (os.environ.get("LA_OS"),
                os.environ.get("LA_DISTRO_PRETTY_NAME") or os.environ.get("LA_DISTRO") or "",
                os.environ.get("LA_HARDWARE"), ram, os.environ.get("LA_GPU_MODEL") or "")
    try:
        script = Path(__file__).resolve().parent / "la-hw-detect.sh"
        out = subprocess.run(
            ["bash", "-c", f'. "{script}"; printf "%s\\n%s\\n%s\\n%s\\n%s" "$LA_OS" "$LA_DISTRO_PRETTY_NAME" "$LA_HARDWARE" "$LA_SYSTEM_RAM_GB" "$LA_GPU_MODEL"'],
            capture_output=True, text=True, timeout=8)
        if out.returncode == 0:
            parts = out.stdout.splitlines()
            if len(parts) >= 3:
                ram = None
                try:
                    ram = float(parts[3]) if len(parts) > 3 and parts[3] else None
                except ValueError:
                    pass
                return ((parts[0] or None), parts[1], (parts[2] or None), ram,
                        (parts[4] if len(parts) > 4 else "") or "")
    except Exception:
        pass
    return None, "", None, None, ""


def _hw_cache_path():
    """Where the last detection result lives, so launch 2+ needs no subprocess at all
    (user 2026-10-29: cache it; only the very first launch shows the generic placeholder)."""
    try:
        return m.catalog_cache_dir() / "hw-detect.env"
    except Exception:
        return None


def _read_hw_cache():
    import shlex
    p = _hw_cache_path()
    if p is None or not p.exists():
        return None
    try:
        vals = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k] = shlex.split(v)[0] if shlex.split(v) else ""
        if vals.get("LA_OS") and vals.get("LA_HARDWARE"):
            ram = None
            try:
                ram = float(vals.get("LA_SYSTEM_RAM_GB") or "") or None
            except ValueError:
                pass
            return (vals["LA_OS"], vals.get("LA_DISTRO_PRETTY_NAME", ""), vals["LA_HARDWARE"],
                    ram, vals.get("LA_GPU_MODEL", ""))
    except Exception:
        pass
    return None


def _write_hw_cache(hw):
    p = _hw_cache_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(f'LA_OS="{hw[0] or ""}"\nLA_DISTRO_PRETTY_NAME="{hw[1]}"\nLA_HARDWARE="{hw[2] or ""}"\n'
                       f'LA_SYSTEM_RAM_GB="{hw[3] or ""}"\nLA_GPU_MODEL="{hw[4]}"\n',
                       encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


class Picker(App):
    # R5: no painted background. The `textual-ansi` theme maps background/foreground to the
    # terminal's own defaults (ANSI 39/49) and ansi_color=True keeps them ANSI instead of
    # converting to truecolor, so light, dark and custom terminal themes all show through.
    # R9: the Remote policy line is neutral (muted), not the orange warning colour.
    CSS = """
    Screen { layout: vertical; background: ansi_default; color: ansi_default; }
    #title { text-style: bold; padding: 0 1; }
    #policy { color: ansi_bright_cyan; padding: 0 1 1 1; }  /* subheadline = identity line (cyan since 0.47.7); blank line after it (user 2026-10-30) */
    #version { color: ansi_black; text-style: italic; padding: 1 0; }  /* version footer: flush at the very bottom (blank line only BEFORE it), dimmer+italic than the status line so neither over-emphasizes (user 2026-10-30) */
    #actions { height: auto; padding: 0 1; }
    #rows { height: 1fr; display: none; margin-top: 1; }   /* a blank line before the model list */
    #rows.visible { display: block; }
    #spacer { height: 0; }
    /* Status + version are pinned to the very bottom on EVERY screen: when the model
       list is hidden, the spacer takes the flex space, so the nav line never jumps up
       when switching home <-> lane <-> lowkey (user 2026-10-30). */
    #layout.no-rows #spacer { height: 1fr; }
    /* Secondary text is grey (ANSI bright-black): readable on light AND dark terminals.
       Only background and main text are the terminal's own defaults. */
    /* Remote info box: model notes for the hovered/highlighted row, framed like a tooltip. */
    #info { height: auto; display: none; border: round ansi_bright_black; padding: 0 1; margin: 0 1; color: ansi_default; }
    #status { height: auto; padding: 0 1; color: ansi_bright_black; }
    #prompt { display: none; }
    /* The live model filter: hidden until the remote choose-model list opens (render_model). */
    #search { display: none; height: auto; padding: 0 1; margin-top: 1; color: ansi_default; }
    OptionList { background: ansi_default; color: ansi_default; border: none; }
    OptionList:focus { background: ansi_default; color: ansi_default; border: none; }
    /* Highlight and hover never paint a block: soft-blue text (ANSI bright-blue, which every
       light/dark terminal theme tunes to be readable) on the terminal's own background. */
    OptionList { background-tint: ansi_default 0%; }
    OptionList > .option-list--option-highlighted { background: ansi_default; color: ansi_bright_blue; text-style: none; }
    OptionList:focus > .option-list--option-highlighted { background: ansi_default; color: ansi_bright_blue; text-style: bold; }
    OptionList > .option-list--option-hover { background: ansi_default; color: ansi_bright_blue; }
    /* Every text input (the live filter, the inline prompts): a SYMMETRIC rounded frame.
       Textual's default `tall` border draws barbs on the left that the right side has not
       (user 2026-10-22), so inputs looked lopsided; `round` matches the info box's frame. */
    Input { background: ansi_default; color: ansi_default; border: round ansi_bright_black; }
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
        # The verdict memo is per picker session: a fresh app must not inherit another
        # app's fit/age/param verdicts (measured 2026-10-28: the full suite's age-filter
        # tests share one process, and a cached cutoff from an earlier test leaked).
        _VERDICT_MEMO.clear()
        state = sms.load(Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR))
        self.state_warnings = list(state.warnings)
        # A security-review override left behind while the plugin is not active is removed, and the
        # removal is SHOWN (0.42.0): the row is hidden then, so a kept override would be stale state.
        try:
            removed = cse.clear_stale_override()
        except (OSError, ValueError) as exc:
            removed = []
            self.state_warnings.append(f"could not clear a leftover security-review setting: {exc}")
        if removed:
            self.state_warnings.append(
                f"removed {', '.join(removed)} from ~/.claude/settings.json: the security-guidance "
                "plugin is not active, so the setting did nothing")
        cloud = cse.load()      # read-only; reflects ~/.claude/settings.json + installed plugins
        self.settings = m.Settings(
            local_effort=state.effort["local_session"],
            remote_effort=state.effort["remote_api_session"],
            lowkey_effort=state.effort["lowkey"],
            local_temperature=state.temperature.get("local_session", sms.DEFAULT_TEMPERATURE["local_session"]),
            remote_temperature=state.temperature.get("remote_api_session", sms.DEFAULT_TEMPERATURE["remote_api_session"]),
            lowkey_temperature=state.temperature.get("lowkey", sms.DEFAULT_TEMPERATURE["lowkey"]),
            thinking_session=state.thinking_session,
            rate_limiter=dict(state.rate_limiter),
            memguard=dict(state.memguard),
            auto_mode=int(os.environ.get("CSL_AUTO_MODE_STATE", "0") or 0) % 3,
            telemetry=os.environ.get("CSL_TELEMETRY", "0") == "1",
            stop_hook=os.environ.get("CSL_STOP_HOOK", "0") == "1",   # OFF by default (0.22.3)
            watcher=os.environ.get("CSL_WATCH", "0") == "1",
            enable_mcp=os.environ.get("LA_ENABLE_MCP", "1") == "1",   # default ON (user 2026-10-15); explicit env wins
            include_trials=not flags.exclude_trials,
            local_capable_shown=flags.local_capable_shown or os.environ.get("CSL_LOCAL_CAPABLE") == "1",
            show_old=getattr(flags, "show_old", False),
            on_effort_saved=self._save_effort,
            on_temperature_saved=self._save_temperature,
            on_thinking_saved=self._save_thinking,
            security_review=cloud["security_review"],
            security_review_custom=cloud["custom_model"],
            security_review_offered=cloud["plugin_active"],
            routes={p: cse.route(p) for p in cse.ROUTE_PATHS},
            on_cloud_saved=self._save_cloud,
            on_memguard_saved=self._save_mg,
            # "Go last": restored from the store; written when a launch request is made.
            last_launched_model={"local_session": None, "remote_api_session": None, "lowkey": None,
                                 **state.last_launched},
            last_launched_tier=dict(state.last_launched_tier),
            on_last_launched=self._save_last_launched,
            catalog_local_capable=catalog_local_capable,
            model_param_count=model_param_count,
            catalog_model_old=catalog_model_old)
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

    def _save_temperature(self, lane, value):
        warning = sms.save_temperature(self._config_dir(), lane, value)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    def _save_thinking(self, value):
        warning = sms.save_thinking_session(self._config_dir(), value)
        if warning and warning not in self.state_warnings:
            self.state_warnings.append(warning)

    def _save_cloud(self, values):
        # Written to ~/.claude/settings.json env, which every cloud session applies (any login, any
        # launcher). A failed write is shown, never swallowed: an unsaved toggle would look applied
        # while the next session ignores it.
        try:
            if "route" in values:
                path, mode = values["route"]
                cse.save_route(path, mode)
            if "security_review" in values:
                cse.save(values["security_review"])
        except (OSError, ValueError) as exc:
            warning = f"cloud setting not saved: {exc}"
            if warning not in self.state_warnings:
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

    def _save_mg(self, values):
        try:
            warning = sms.save_memguard(self._config_dir(), values)
        except ValueError as exc:              # the screen never offers one; never crash on it
            warning = f"{exc}; not saved"
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
        with Vertical(id="layout"):
            yield Static("", id="title")
            yield Static("", id="policy")
            yield OptionList(id="actions")
            yield SearchInput(id="search")
            yield HoverOptionList(id="rows")
            yield Static("", id="spacer")
            yield Static("", id="info")
            yield PromptInput(id="prompt")
            yield Static("", id="version")
            yield Static("", id="status")    # status/nav line is ALWAYS the very bottom row (user 2026-10-30)

    def on_mount(self):
        # OS/distro/hardware detection through the repo's canonical script
        # (bin/la-hw-detect.sh); the model's subheadlines read these from Settings.
        # Cache first (instant on every launch after the first), then detect in the
        # BACKGROUND and load last — the first paint never waits on it; a missing cache
        # shows the generic platform line until detection lands (user 2026-10-29).
        cached = _read_hw_cache()
        if cached:
            (self.settings.hw_os, self.settings.hw_distro, self.settings.hw_hardware,
             self.settings.hw_ram_gb, self.settings.hw_gpu) = cached
        self.goto(m.Nav(self.start, m.DIRECT_ROOT))
        # Menu first, then warm the model lists in the background (plan R13/R14): by the time
        # the user presses `c` the slow remote inventory (~1-6 s) is usually already cached.
        self.call_after_refresh(self._prefetch, ("local", "remote"))
        self.set_interval(LOADING_INTERVAL, self._tick_loading)
        import threading
        threading.Thread(target=self._detect_hw_async, daemon=True).start()
        threading.Thread(target=self._gather_stats_async, daemon=True).start()

    def _gather_stats_async(self):
        """About-screen usage stats, gathered in the background (never blocks the first
        paint; re-renders only if About is on screen when they land)."""
        import subprocess
        st = {}
        try:
            # one line per REGISTERED backend family (Task 9b): real health from the managers,
            # the pin from the LIVE config — never a bare ~/.venvs glob (that listed the user's
            # own hf-py312), never backend-pin.txt (a file no code writes, so no pin ever showed).
            st["backends"] = m.about_backend_lines(Path.home(), REPO) or ["none"]
        except Exception:
            pass
        try:
            models = self.cache.get("local") or []
            st["local_models"] = len([x for x in models if not getattr(x, "auto_scanned", False)])
            try:
                out = subprocess.run(["du", "-sm", str(Path.home() / ".models")],
                                     capture_output=True, text=True, timeout=8)
                if out.returncode == 0 and out.stdout.strip():
                    st["local_gb"] = float(out.stdout.split()[0]) / 1024.0
            except Exception:
                pass
        except Exception:
            pass
        try:
            agents = self.cache.get("remote") or []
            provs = sorted({a.provider for a in agents if a.has_key})
            st["providers"] = ", ".join(provs) if provs else "none"
            if agents:
                lane = m.RemoteScreen(self.settings, agents)
                st["remote_visible"] = sum(1 for a in agents if lane._visible(a) and a.model_id != "SELECT")
                st["remote_hidden"] = lane.hidden_count()
        except Exception:
            pass
        rl = getattr(self.settings, "rate_limiter", None) or {}
        if rl:
            st["rate_limiter"] = ", ".join(f"{k}={v}" for k, v in sorted(rl.items()))

        def apply():
            self.settings.stats = st
            if isinstance(self.screen_model, m.AboutScreen):
                self.render_model()
        self.call_from_thread(apply)

    def _detect_hw_async(self):
        """Background hardware detection: runs after the menu is already painted, caches
        the result, and re-renders only if the values actually changed."""
        hw = _detect_hw()
        _write_hw_cache(hw)

        def apply():
            sm = self.screen_model
            before = getattr(sm, "policy", "") if sm is not None else ""
            (self.settings.hw_os, self.settings.hw_distro, self.settings.hw_hardware,
             self.settings.hw_ram_gb, self.settings.hw_gpu) = hw
            after = getattr(sm, "policy", "") if sm is not None else ""
            # Re-render only when the visible subheadline actually changes (e.g. generic
            # "Linux PC" -> "Ubuntu … PC" on first launch); on macOS the platform fallback
            # already produced the same line, so no mid-interaction redraw (flaky arrow
            # keys, 2026-10-29).
            if before != after:
                self.render_model()
        self.call_from_thread(apply)

    # --- background inventory ----------------------------------------------------------------
    LOADERS = {"local": "load_local_models", "remote": "load_remote_agents", "catalog": "load_catalog"}

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
            if kind.startswith("versions:"):
                data, err = load_versions(kind), None
            elif kind.startswith("rcat:"):
                data, err = load_remote_catalog(kind), None
            else:
                data, err = globals()[self.LOADERS[kind]](), None
                if kind == "local" and self.settings.memguard.get("registry_auto_sync"):
                    note = auto_sync_registry(data)
                    if note:
                        data = load_local_models()         # the registry changed: read it again
                        self.call_from_thread(self._warn_here, note)
        except Exception as exc:              # surfaced as a status warning, never swallowed
            # A versions screen shows its own failure in place of "nothing to choose" (_loaded).
            data, err = [], (str(exc) if kind.startswith("versions:")
                             else f"{kind} model list failed to load: {exc}")
        self.call_from_thread(self._loaded, kind, data, err)

    def _loaded(self, kind, data, err):
        self._loading.discard(kind)
        if not self._loading & self.STARTUP_KINDS:
            self._startup_loading = False     # startup prefetch done: drop the label even if no re-render follows
            if self.screen_model is not None:
                self._set_policy(getattr(self.screen_model, "policy", ""))
                self.query_one("#version", Static).update(getattr(self.screen_model, "version_line", ""))
        self.cache[kind] = data
        if err and err not in self.state_warnings and not kind.startswith("versions:"):
            self.state_warnings.append(err)
        sm = self.screen_model
        # Fill an already-open list in place; otherwise just refresh counts/labels.
        if isinstance(sm, m.RemoteScreen) and kind == "remote" and (not sm._models_loaded or not sm.agents):
            sm.agents = data
            sm._regroup()
            self._hydrate_catalogs(sm)         # cached rosters paint at once (T2)
        elif isinstance(sm, (m.LocalScreen, m.LowkeyScreen)) and kind == "local" and not getattr(sm, "_models_loaded", False):
            sm.set_models(data, *([MISSING_MODELS] if isinstance(sm, m.LocalScreen) else []))
            sm._models_loaded = True
        if kind == "remote":
            # Eager warm-up: fetch every keyed provider's catalog once this session, so the
            # counts and the model lists are real from the first paint (user decision
            # 2026-10-27, waypoint csl-remote-provider-catalogs).
            kinds = self._eager_catalog_kinds()
            if kinds:
                self._prefetch(tuple(kinds))
        if kind.startswith("rcat:"):
            prov = next((a.provider for a in (self.cache.get("remote") or [])
                         if a.alias == kind.split(":", 1)[1]), None)
            if prov is not None and not err and data:
                # Keep what landed even when no remote screen is showing (the eager fetch
                # lands on the home screen): counts feed the home row, rows hydrate a
                # remote screen built later. An empty success is INCONCLUSIVE (rate-limited
                # providers answer empty): recording it would pin the provider pinned-only
                # and block the disk cache (review finding 5).
                if not hasattr(self, "catalog_rows"):
                    self.catalog_rows = {}
                self.catalog_rows[prov] = list(data)
                if not hasattr(self, "catalog_counts"):
                    self.catalog_counts = {}
                self.catalog_counts[prov] = len(data)
            if isinstance(sm, m.HomeScreen):
                sm.catalog_total = self._home_catalog_total()   # the sum grows as fetches land
            if isinstance(sm, m.RemoteScreen):
                sm.set_catalog(kind.split(":", 1)[1], data, err)
            if err in self.state_warnings:
                self.state_warnings.remove(err)    # shown as the group's own error row instead
            self.cache.pop(kind, None)             # the screen keeps it; a failed load can be retried
        if isinstance(sm, m.VersionsScreen) and kind == sm.inventory_kind:
            sm.set_entries(data)
            sm.load_error = err
            if not getattr(self, "pending_prompt", None):
                self.render_model()
                self.query_one("#rows", OptionList).focus()   # arrows move through versions at once
            return
        elif isinstance(sm, m.DownloadScreen) and kind == "catalog" and not sm.entries:
            entries, free, head = data if data else ([], 0.0, 100.0)
            self.screen_model = m.DownloadScreen(self.settings, entries, free, head, sm.owner)
        elif isinstance(sm, m.HomeScreen) and kind in ("local", "remote"):
            if kind == "local":
                sm.local_count = len(data)
            else:
                sm.remote_count = len(data)
                sm.catalog_total = self._home_catalog_total()
        if not getattr(self, "pending_prompt", None):
            self._rerender_keep_focus()

    STARTUP_KINDS = {"local", "remote"}

    def _startup_pending(self):
        """True while the startup prefetch is still running (drives the subtitle's loading label)."""
        if not self._startup_loading:
            return False
        loading = getattr(self, "_loading", None)
        return loading is None or bool(loading & self.STARTUP_KINDS)   # None: prefetch not started yet

    def _set_policy(self, text):
        pol = self.query_one("#policy", Static)
        pol.update(text)
        # About uses a styled multi-line readout in the default color, not the cyan
        # subheadline (user 2026-10-30); every other screen keeps the cyan identity line.
        pol.styles.color = "ansi_default" if isinstance(self.screen_model, m.AboutScreen) else "ansi_bright_cyan"

    def _startup_policy(self):
        # The label rides the subtitle's FIRST line; the version footer is a separate widget.
        p = getattr(self.screen_model, "policy", "")
        if not isinstance(p, str):
            p = p.plain
        head, sep, rest = p.partition("\n")
        return head + "  " + loading_label(self._loading_frame) + sep + rest

    def _startup_tick(self):
        """Animate the subtitle's startup label: hourglass flips and dots step, like the status line."""
        if not self._startup_pending():
            return False
        self._set_policy(self._startup_policy())
        self.query_one("#version", Static).update(getattr(self.screen_model, "version_line", ""))
        return True

    def _waiting_for(self):
        """The inventory the visible screen is waiting on, if any (drives the loading line)."""
        sm = self.screen_model
        loading = getattr(self, "_loading", set())
        if isinstance(sm, m.RemoteScreen) and sm._choose_model_visible and "remote" in loading:
            return "remote"
        if isinstance(sm, (m.LocalScreen, m.LowkeyScreen)) and getattr(sm, "_choose_model_visible", True) and "local" in loading:
            return "local"
        if isinstance(sm, m.DownloadScreen) and "catalog" in loading:
            return "catalog"
        if isinstance(sm, m.VersionsScreen) and sm.inventory_kind in loading:
            return "versions"
        return None

    def _tick_loading(self):
        waiting = self._waiting_for()
        if waiting or self._startup_pending():
            self._loading_frame += 1          # one shared beat, so the two labels stay in step
        if waiting:
            what = "loading versions" if waiting == "versions" else "loading models"
            self.query_one("#status", Static).update("  " + loading_label(self._loading_frame, what))
            # The empty-list placeholder plays the same animation while it is the only row
            # (user 2026-10-28: a static "nothing to show yet" read as stuck).
            rows = self.query_one("#rows", OptionList)
            if rows.option_count == 1 and getattr(rows.get_option_at_index(0), "id", None) == "empty":
                rows.replace_option_prompt_at_index(
                    0, Text("  " + loading_label(self._loading_frame, what), style="dim"))
        self._startup_tick()                  # independent of the status line: both can be animating

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
        if nav.target == "about":
            return m.AboutScreen(s, nav.owner)
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
        if nav.target == "memguard":
            return m.MemoryGuardScreen(s, nav.owner, on_save=self._save_mg)
        if nav.target == "runtime_manager":
            return m.RuntimeManagerScreen(s, nav.owner)
        if nav.target == "api_keys":
            return m.APIKeysScreen(s, nav.owner)
        if nav.target == "cloud_config":
            return m.CloudConfigScreen(s, nav.owner)
        raise ValueError(nav.target)

    def _warn_here(self, msg: str):
        """A warning about THIS screen's action: shown here, dropped on the next screen change
        (user 2026-10-16: the cleanup result followed them to Home and Remote)."""
        self.state_warnings.append(msg)
        self._screen_warnings = getattr(self, "_screen_warnings", set()) | {msg}

    def goto(self, nav: m.Nav):
        scoped = getattr(self, "_screen_warnings", set())
        if scoped:
            self.state_warnings = [w for w in self.state_warnings if w not in scoped]
            self._screen_warnings = set()
        # Build screen WITHOUT loading inventory (lazy loading)
        # Inventory will be loaded on-demand when user toggles "Choose model"
        s = self.settings
        if nav.target == "home":
            local = self.cache.get("local")
            remote = self.cache.get("remote")
            self.screen_model = m.HomeScreen(s, len(local) if local else None, len(remote) if remote else None,
                                             self._home_catalog_total(), nav.owner)
        elif nav.target == "about":
            self.screen_model = m.AboutScreen(s, nav.owner)
        elif nav.target == "local":
            # Pass models only if already cached; otherwise pass None for lazy loading
            local = self.cache.get("local")
            self.screen_model = m.LocalScreen(s, local, nav.owner)
            if local is not None:
                self.screen_model.set_models(local, MISSING_MODELS)
        elif nav.target == "remote":
            remote = self.cache.get("remote")
            self.screen_model = m.RemoteScreen(s, remote, nav.owner)
            if remote is not None:
                self._hydrate_catalogs(self.screen_model)   # cached lists paint at once
        elif nav.target == "lowkey":
            local = self.cache.get("local")
            self.screen_model = m.LowkeyScreen(s, local, nav.owner)
        elif nav.target == "download":
            # 0.22.3's background loading dropped the catalog load, so this screen always
            # opened empty. It now loads like local/remote and fills in via _loaded().
            catalog_data = self.cache.get("catalog")
            if catalog_data:
                entries, free, head = catalog_data
            else:
                entries, free, head = [], 0.0, 100.0
                self._prefetch(("catalog",))
            self.screen_model = m.DownloadScreen(s, entries, free, head, nav.owner)
        elif nav.target == "rate_limiter":
            self.screen_model = m.RateLimiterScreen(s, nav.owner, on_save=self._save_rl)
        elif nav.target == "memguard":
            self.screen_model = m.MemoryGuardScreen(s, nav.owner, on_save=self._save_mg)
        elif nav.target == "runtime_manager":
            self.screen_model = m.RuntimeManagerScreen(s, nav.owner)
        elif nav.target == "runtime":
            self.screen_model = m.RuntimeScreen(s, nav.owner)
        elif nav.target == "runtime_launchd":
            self.screen_model = m.LaunchdScreen(s, nav.owner)
        elif nav.target == "api_keys":
            self.screen_model = m.APIKeysScreen(s, nav.owner)
        elif nav.target.startswith("runtime_versions:"):
            # Always a fresh list: an install/remove a moment ago changes what is there.
            sm = m.VersionsScreen(s, nav.target.split(":", 1)[1], owner=m.SUB)
            self.cache.pop(sm.inventory_kind, None)
            self._prefetch((sm.inventory_kind,))
            self.screen_model = sm
        elif nav.target == "remote_filters":
            self.screen_model = m.RemoteFiltersScreen(s, nav.owner)
        elif nav.target == "cloud_config":
            self.screen_model = m.CloudConfigScreen(s, nav.owner)
        elif nav.target == "theme_config":
            self.screen_model = m.ThemeConfigScreen(s, nav.owner)
        else:
            raise ValueError(nav.target)

        self.nav = nav
        self.render_model()

    def render_model(self, keep: str | None = None):
        sm = self.screen_model
        self.query_one("#title", Static).update(Text(sm.title, style="bold"))
        # Startup: the label shows at once and animates (_startup_tick) until the prefetch lands.
        # It used to be drawn on the first render only and never updated, so it looked frozen.
        policy = self._startup_policy() if self._startup_pending() else getattr(sm, "policy", "")
        self._set_policy(policy)
        self.query_one("#version", Static).update(getattr(sm, "version_line", ""))
        rows = self.query_one("#rows", OptionList)
        rows.clear_options()
        self.row_ids = []

        # Handle lazy loading when "Choose model" is toggled
        self._handle_lazy_load()
        # An open remote provider group fetches its live catalog once, in the background. Every
        # way a group opens (Enter, Right, the automatic first group) passes through here.
        if isinstance(sm, m.RemoteScreen) and sm._choose_model_visible:
            kind = sm.catalog_kind_needed()
            if kind:
                self._prefetch((kind,))

        # Conditionally show model list based on _choose_model_visible
        show_models = True
        if isinstance(sm, (m.LocalScreen, m.RemoteScreen, m.LowkeyScreen)):
            show_models = getattr(sm, '_choose_model_visible', False)

        # Show/hide the rows container
        rows.display = show_models
        # flex spacer keeps status+version at the bottom when no model list is shown
        self.query_one("#layout", Vertical).set_class(not show_models, "no-rows")
        # No input box unless a prompt is actually pending (user 2026-10-30: About and
        # other plain menus must not show an empty textbox at the bottom).
        if not getattr(self, "pending_prompt", None):
            self.query_one("#prompt", Input).display = False

        # The live model filter (user, 2026-10-21; the Local list too since 2026-10-22): the
        # box sits at the top of the open model list, seeded with any filter still active,
        # and takes focus the moment the list opens so typing filters from the very first
        # character — no prompt, no Enter. Tracked per SCREEN, so switching Local <-> Remote
        # with the list open seeds and focuses the box for the new screen.
        search = self.query_one("#search", Input)
        was_open = getattr(self, "_search_owner", None) is sm
        search.display = isinstance(sm, MODEL_LIST_SCREENS) and show_models
        if search.display and not was_open:
            search.value = sm.search
            search.placeholder = "type to filter models — results narrow from the first character"
            search.focus()
        self._search_owner = sm if search.display else None

        # Check if screen has accordion with groups (RuntimeManagerScreen has accordion but no groups)
        has_groups = hasattr(sm, 'accordion') and sm.accordion is not None and len(sm.accordion.groups) > 0

        if show_models and has_groups:
            for kind, obj in sm.accordion.rows():
                if kind == "group":
                    # Separator groups (id == "__sep__"): render as blank disabled row, but
                    # keep row_ids aligned by appending None so UI index matches row_ids index.
                    if obj.id == "__sep__":
                        rows.add_option(Option("", id="sep", disabled=True))
                        self.row_ids.append(None)
                        continue
                    if getattr(obj, "style", ""):         # an action header, not a fold
                        rows.add_option(Option(Text(obj.label, style=obj.style), id=f"g:{obj.id}"))
                        self.row_ids.append(("group", obj.id))
                        continue
                    mark = "▾" if sm.accordion.is_open(obj.id) else "▸"
                    rows.add_option(Option(f"{mark} {obj.label}", id=f"g:{obj.id}"))
                    self.row_ids.append(("group", obj.id))
                else:
                    queued = isinstance(sm, m.DownloadScreen) and obj.id in sm.queue
                    box = "[x] " if queued else ("[ ] " if isinstance(sm, m.DownloadScreen) else "")
                    # Text whenever a box is drawn: a str "[x]" is markup and renders as nothing.
                    label = f"    {box}{obj.label}"
                    style = getattr(obj, "style", "")
                    rows.add_option(Option(Text(label, style=style) if (style or box) else label,
                                           id=f"i:{obj.id}"))
                    self.row_ids.append(("item", obj.id))

        # The "(nothing to show here yet)" placeholder only makes sense on model-list
        # screens (empty while loading). Plain menus (About, settings sub-screens, …)
        # get no placeholder row at all (user 2026-10-30).
        if not self.row_ids and isinstance(sm, (m.LocalScreen, m.RemoteScreen, m.LowkeyScreen)):
            waiting = self._waiting_for()
            if waiting:
                # The hourglass plays here too, not only in the status line (2026-10-28).
                what = "loading versions" if waiting == "versions" else "loading models"
                rows.add_option(Option(f"  {loading_label(self._loading_frame, what)}", id="empty", disabled=True))
            else:
                rows.add_option(Option("  (nothing to show here yet)", id="empty", disabled=True))
        target = keep or (f"i:{sm.accordion.selected_id}" if sm.accordion and sm.accordion.selected_id else None)
        if target:
            for idx in range(rows.option_count):
                if rows.get_option_at_index(idx).id == target:
                    rows.highlighted = idx
                    break

        # Build actions list in the actions OptionList. Lane pickers fold their settings
        # rows under one collapsed group header (user 2026-10-29, waypoint
        # picker-collapsed-settings-group): launch/choose-model and all non-settings rows
        # stay top-level; the folded rows remain reachable by their hotkeys.
        actions_list = self.query_one("#actions", OptionList)
        actions_list.clear_options()
        self.action_ids = []  # Track action IDs for keyboard navigation
        rendered, self._collapsed_actions = m.rendered_actions(sm)
        collapsed = bool(self._collapsed_actions)
        # A nav-ONLY actions pane on a screen WITH rows (API keys setup) renders BELOW the rows,
        # so Back is the last row on screen like every other submenu (user 2026-10-31: it used
        # to sit at the very top because the pane is mounted above #rows). Nav-only screens
        # WITHOUT rows (About) must NOT move — an empty 1fr #rows would push Back under a blank
        # gap (out-of-family review finding 2). Screens with real actions keep the pane on top.
        layout = self.query_one("#layout")
        if show_models and has_groups and rendered and all(a.section == "nav" for a in rendered):
            layout.move_child(actions_list, after=self.query_one("#rows"))
        else:
            layout.move_child(actions_list, before=self.query_one("#search"))
        prev_section = None
        for a in rendered:
            # Store action object reference
            self.action_ids.append(a)
            # Blank separator row only before MAJOR top-level groups (tools/nav/appearance) —
            # universal rule (user 2026-10-30): never between launch and choose-model, never
            # between a harmonica group header and its contents, never between the two
            # groups. Sentinels carry their group's section so no separator fires around them.
            if (prev_section is not None and a.section != prev_section
                    and a.section in ("tools", "nav", "appearance")
                    and a not in (m.SETTINGS_GROUP, m.MODEL_GROUP)):
                actions_list.add_option(Option(Text(""), id=f"sep:{len(self.action_ids)}", disabled=True))
            prev_section = a.section
            if a in (m.SETTINGS_GROUP, m.MODEL_GROUP):
                is_model = a is m.MODEL_GROUP
                is_collapsed = sm.model_collapsed if is_model else sm.settings_collapsed
                arrow = "▸" if is_collapsed else "▾"
                emoji = ec.EMOJI_BOLT_STR if is_model else ec.EMOJI_TOOLBOX_STR   # 🔩 model settings, 🛠️ advanced — not 🔧 (backend manager) nor lane emojis (user 2026-10-30)
                name = "Model settings" if is_model else "Advanced"
                # convention: the open/close triangle comes BEFORE the menu text (user 2026-10-30)
                actions_list.add_option(Option(Text(f"  {ec.gap(emoji)} {arrow} {name}"),
                                               id=f"a:{len(self.action_ids)-1}"))
                continue
            enabled_str = "" if a.enabled else "  —"
            # Display shortcut as uppercase per R8 requirement. Rich labels (the theme
            # rows) must reach the Option as Text: an f-string here calls str() on the
            # label and flattens every style — the 0.35.0 swatch/hex-colour bug
            # (user 2026-10-15; the f-string predates the theme work entirely).
            prompt = Text(f"  {a.key.upper()}) ")
            if isinstance(a.label, Text):
                prompt += a.label            # rich Text + Text keeps the spans
            else:
                prompt.append(str(a.label))
            if enabled_str:
                prompt.append(enabled_str)
            actions_list.add_option(Option(prompt, id=f"a:{len(self.action_ids)-1}"))

        # Focus/highlight contract (user 2026-10-15, measured): the actions list must show a
        # highlighted row as soon as it renders — rebuilding options resets Textual's
        # highlight to None, and with none set, left/right/enter do nothing until the user
        # presses down first. That is why the theme screen's arrows were dead on arrival.
        if actions_list.option_count and actions_list.highlighted is None:
            actions_list.highlighted = 0

        status = m.status_line(sm)   # single source of truth for the nav line on every screen
        waiting = self._waiting_for() if hasattr(self, "_loading") else None
        if waiting:
            status = "  " + loading_label(self._loading_frame, "loading versions" if waiting == "versions" else "loading models")
        elif isinstance(sm, m.VersionsScreen) and sm.load_error:
            status = ("  could not load " + ("releases" if sm.OPS[sm.op][1] == "releases"
                                            else f"installed {sm.backend} versions") + ": " + sm.load_error)
        elif isinstance(sm, m.VersionsScreen) and sm.entries == []:
            status = "  nothing to choose: " + ("no releases found" if sm.OPS[sm.op][1] == "releases"
                                               else f"no installed {sm.backend} versions")
        if isinstance(sm, m.DownloadScreen):
            status = "  ↑↓ move · Space/Enter queue · c review"
        if isinstance(sm, m.RemoteScreen) and sm._choose_model_visible and sm._models_loaded and not waiting:
            status = "  type to filter · ↑↓ move · Enter launch · ←→ fold · Esc close list"
        if self.state_warnings:
            status += f"\n  {ec.EMOJI_WARNING_STR}  " + self.state_warnings[-1]   # 2 spaces: VS16 emoji
        self.query_one("#status", Static).update(status)
        self._update_info()

    def _update_info(self, item_id: str | None = None, from_hover: bool = False):
        """Show the info box while a model list (Remote, or Local since 0.41.0) is open; it
        describes the hovered row (mouse) or else the highlighted one (keyboard)."""
        info = self.query_one("#info", Static)
        sm = self.screen_model
        model_list = (isinstance(sm, MODEL_LIST_SCREENS) and sm._choose_model_visible
                      and sm._models_loaded)
        # Any other screen opts in by defining info_text (API Keys: the provider's requirements).
        if not (model_list or (not isinstance(sm, MODEL_LIST_SCREENS) and hasattr(sm, "info_text"))):
            info.display = False
            return
        if not from_hover:
            h = self._highlighted()
            item_id = h[1] if h and h[0] == "item" else None
        info.update(sm.info_text(item_id))
        info.display = True

    def on_hover_option_list_hovered(self, event):
        if event.index is None or event.index >= len(self.row_ids):
            self._update_info()                  # pointer left: fall back to the highlight
            return
        row = self.row_ids[event.index]
        self._update_info(row[1] if row and row[0] == "item" else None, from_hover=True)

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
            self._update_info()
        elif event.option_list.id == "actions":
            # Update status to show action description
            pass

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "rows":
            self.enter()
        elif event.option_list.id == "actions":
            self._activate_action(event.option_index)

    def _activate_action(self, option_index: int):
        """Activate an action from the actions list. option_index is a row in the
        OptionList; the option's id carries the index into action_ids (separators
        use a non-"a:" id and are never selectable)."""
        actions_list = self.query_one("#actions", OptionList)
        if not hasattr(self, 'action_ids') or option_index >= actions_list.option_count:
            return
        oid = actions_list.get_option_at_index(option_index).id
        if not oid.startswith("a:"):
            return
        action_index = int(oid[2:])
        action = self.action_ids[action_index]
        if not action.enabled:
            return
        if action is m.SETTINGS_GROUP:
            self.screen_model.toggle_settings()
            self._rerender_keep_action(action_index)
            return
        if action is m.MODEL_GROUP:
            self.screen_model.toggle_model_group()
            self._rerender_keep_action(action_index)
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
        self.render_model()
        want = f"a:{action_index}"
        for i in range(actions_list.option_count):
            if actions_list.get_option_at_index(i).id == want:
                actions_list.highlighted = i
                return

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
                    sm.set_models(self.cache["local"], MISSING_MODELS)
                    sm._models_loaded = True
                return True
        # Check RemoteScreen
        if isinstance(sm, m.RemoteScreen) and getattr(sm, '_choose_model_visible', False):
            # The agent list, not just the flag: an early _regroup() during the roster load
            # used to set the flag on an EMPTY roster, and this then never filled (2026-10-27).
            if not getattr(sm, '_models_loaded', False) or not getattr(sm, 'agents', None):
                self._maybe_load_inventory("remote")
                if "remote" in self.cache:
                    sm.agents = self.cache["remote"]
                    sm._regroup()  # Re-group with loaded agents (sets _models_loaded)
                    self._hydrate_catalogs(sm)   # cached rosters paint at once (T2)
                return True
        return False

    def _hydrate_catalogs(self, sm):
        """Paint a remote screen's provider catalogs from this session's fetches and the
        disk cache — never the network. The goto path built the screen from a warm roster
        but never read the cache back, so every group opened pinned-only ("nvidia always
        starts with only 9 models") while the cached full list sat unused (2026-10-27).
        Disk-cached providers still owe one live revision (_catalog_stale)."""
        if not hasattr(self, "catalog_rows"):
            self.catalog_rows = {}     # this session's fetch results, keyed by provider
        changed = False
        for prov in dict.fromkeys(a.provider for a in sm.agents):
            if prov in sm.catalogs or prov in sm._catalog_pending:
                continue
            rows = self.catalog_rows.get(prov) or None   # an empty LIVE reply is inconclusive: fall through to disk
            from_disk = rows is None
            if from_disk:
                rows = m.read_catalog_cache(prov)
            if rows:
                sm.catalogs[prov] = rows
                if from_disk:
                    sm._catalog_stale.add(prov)          # disk-cached: still owes one live revision
                changed = True
        if changed:
            # The groups on the screen were built BEFORE hydration: without a regroup the
            # accordion still carries the pinned-only labels and rows (review finding 13's
            # coverage test pinned this hole — the store updated but the render never saw it).
            sm._refresh_keep_open()

    def _home_catalog_total(self):
        """The home row's honest number: the catalog-model sum over the providers the user
        has keys for; None while no roster has landed ("?"), 0 when none has a key."""
        roster = self.cache.get("remote")
        if roster is None:
            return None
        keyed = [a for a in roster if a.has_key]
        if not keyed:
            return 0                       # a keyless user: an honest zero, not a stale count
        if not getattr(self, "catalog_counts", None):
            return None                    # nothing known yet ("?") — never claim zero
        return sum(self.catalog_counts.get(a.provider, 0) for a in keyed)

    def _eager_catalog_kinds(self):
        """`rcat:` kinds for every keyed provider not yet fetched this session, with counts
        seeded from the disk cache so the home row is honest before the fetches land.
        LA_EAGER_CATALOGS=0 opts out (the test harness sets it: a dozen contract tests
        assert what a picker start is allowed to call, and the warm-up IS calls)."""
        if os.environ.get("LA_EAGER_CATALOGS", "1") == "0":
            return []
        if not hasattr(self, "catalog_counts"):
            self.catalog_counts = {}
        if not hasattr(self, "catalog_rows"):
            self.catalog_rows = {}
        kinds = []
        for a in self.cache.get("remote") or []:
            if (not a.has_key or a.provider in self.catalog_rows
                    or f"rcat:{a.alias}" in getattr(self, "_loading", set())):
                continue
            self.catalog_counts.setdefault(a.provider, len(m.read_catalog_cache(a.provider) or []))
            kinds.append(f"rcat:{a.alias}")
        return kinds

    def enter(self):
        h = self._highlighted()
        if not h:
            return
        acc = self.screen_model.accordion
        if h == ("group", m.NEW_MODELS_GROUP):
            self._open_register_panel()
            return
        if h[0] == "group":
            if acc.is_open(h[1]):
                acc.collapse(h[1])
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
        if isinstance(req, m.Nav):      # API Keys "Add key" -> inline prompt via run_tool
            self.dispatch(req)
        elif req:
            self.launch(req)
        elif isinstance(self.screen_model, m.LowkeyScreen):
            # 0.48.0: in Lowkey, Enter/click on a model row only SELECTS it (re-render so
            # the selection is visible); launching is the explicit `p`/`g` action.
            self.render_model(keep=f"i:{h[1]}")

    def on_key(self, event):
        if isinstance(self.screen, ConfirmPanel):   # the panel owns every key while it is open
            return
        prompt = self.query_one("#prompt", Input)
        if prompt.display:
            if event.key == "escape":
                self._close_prompt()
                event.stop()
            return
        key = event.key
        sm = self.screen_model
        # The downloader's status line promises "Space/Enter queue"; Space was never wired.
        if key == "space" and isinstance(sm, m.DownloadScreen):
            h = self._highlighted()
            if h and h[0] == "item":
                sm.accordion.select(h[1])
                sm.toggle_selected()
                self.render_model(keep=f"i:{h[1]}")
            event.stop()
            return
        # Seamless navigation between actions and rows
        if key == "down" or key == "up":
            actions_list = self.query_one("#actions", OptionList)
            rows_list = self.query_one("#rows", OptionList)
            actions_count = actions_list.option_count
            rows_count = rows_list.option_count

            # The actions pane can render BELOW the rows (nav-only sub-screens, user 2026-10-31),
            # so arrow transitions follow the actual DOM order, not an assumed actions-on-top
            # (out-of-family review finding 1: hardcoding it inverted Up/Down there).
            layout = self.query_one("#layout", Vertical)
            actions_below = layout.children.index(actions_list) > layout.children.index(rows_list)
            # Check if actions list is focused
            if self.focused is actions_list:
                if key == "down" and not actions_below and actions_list.highlighted == actions_count - 1:
                    # Move from last action to first row (if rows visible)
                    if rows_count > 0 and rows_list.display:
                        rows_list.focus()
                        rows_list.highlighted = 0
                        event.stop()
                        return
                elif key == "up" and actions_below and actions_list.highlighted == 0:
                    # Pane below rows: Up from the top action wraps to the last row
                    if rows_count > 0 and rows_list.display:
                        rows_list.focus()
                        rows_list.highlighted = rows_count - 1
                        event.stop()
                        return
                elif key == "up" and actions_list.highlighted == 0:
                    # At top of actions, could wrap or stay - let default handle
                    pass
            # Check if rows list is focused
            elif self.focused is rows_list:
                if key == "up" and not actions_below and rows_list.highlighted == 0:
                    # Move from first row to last action
                    if actions_count > 0:
                        actions_list.focus()
                        actions_list.highlighted = actions_count - 1
                        event.stop()
                        return
                elif key == "down" and actions_below and rows_list.highlighted == rows_count - 1:
                    # Pane below rows: Down from the last row wraps to the first action
                    if actions_count > 0:
                        actions_list.focus()
                        actions_list.highlighted = 0
                        event.stop()
                        return
                elif key == "down" and rows_list.highlighted == rows_count - 1:
                    # At bottom of rows, could wrap or stay - let default handle
                    pass

        if key in ("left", "right"):
            actions_list = self.query_one("#actions", OptionList)
            if self.focused is actions_list and actions_list.highlighted is not None:
                oid = actions_list.get_option_at_index(actions_list.highlighted).id
                if oid.startswith("a:"):
                    ai = int(oid[2:])
                    if ai < len(self.action_ids) and self.action_ids[ai].step is not None:
                        self._step_action(ai, +1 if key == "right" else -1)
                        event.stop()
                        return
            # Handle group expand/collapse when on a group row (only when rows list is focused)
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
                        sm.accordion.collapse(h[1])
                        self.render_model(keep=f"g:{h[1]}")
                        event.stop()
                        return
                elif h and h[0] == "item" and sm.accordion is not None:
                    # Left arrow on an item collapses ITS group (under a search several are open)
                    g = sm.accordion._group_of(h[1])
                    if key == "left" and g and sm.accordion.is_open(g):
                        sm.accordion.collapse(g)
                        self.render_model(keep=f"g:{g}")
                        event.stop()
                        return

        # The live filter owns typing while a choose-model list is open (user, 2026-10-21;
        # Local too since 2026-10-22). Escape closes the list first — a second press leaves the screen as
        # before. A printable character typed with focus on the LIST lands in the filter
        # box, which re-filters from the first character; letters no longer fire action
        # hotkeys in this state. Focus on the ACTIONS list keeps letter hotkeys, so the
        # settings rows stay reachable by arrowing up from the list.
        model_list_open = isinstance(sm, MODEL_LIST_SCREENS) and sm._choose_model_visible
        if model_list_open and key == "escape":
            sm._choose_model_visible = False
            self.dispatch(None)
            event.stop()
            return
        if model_list_open and self.focused is self.query_one("#rows", OptionList):
            ch = " " if key == "space" else (key if len(key) == 1 and key.isprintable() else None)
            if ch:
                search = self.query_one("#search", Input)
                search.value += ch
                search.focus()
                event.stop()
                return

        if key == "escape" or (len(key) == 1 and key.isalpha() and key.islower()):
            keep = self._highlighted()
            idx = next((i for i, a in enumerate(getattr(self, "action_ids", [])) if a.key == key), None)
            if idx is None:
                # A hotkey of a collapsed settings row still works: folded, not gone.
                hidden = next((a for a in getattr(self, "_collapsed_actions", []) if a.key == key), None)
                if hidden is not None:
                    event.stop()
                    if hidden.step is not None:
                        hidden.step(+1)
                    else:
                        r = hidden.run()
                        if r:
                            self.dispatch(r, keep)
                            return
                    self.render_model()
                    return
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
            self.launch(result)
            return
        target = result.target
        if target.startswith("tool:"):
            self.run_tool(target)
            return
        if target.startswith("prompt:"):
            self._open_prompt(target)
            return
        if target.startswith("confirm:missing:"):     # a red (weights-missing) row was selected
            self._open_cleanup_panel(clicked=target.split(":", 2)[2])
            return
        if target.startswith("warn:"):
            self.state_warnings.append(target[5:])
            self.render_model(keep=keep_id)
            return
        if result is m.BACK or target == "back":
            self._go_back()
            return
        owner = result.owner
        # Tool/settings screens are sub-screens of whatever opened them: Back returns there
        # (Home or the exact lane), never Quit — even when the lane itself was a direct root.
        if target in self.SUB_TARGETS or target.startswith("runtime_versions:"):
            self.nav_stack.append(self.nav)
            caller = self.screen_model
            self.goto(m.Nav(target, m.SUB))
            self.screen_model.back_label = (
                f"{ec.EMOJI_HOME_STR} Back to Home" if isinstance(caller, m.HomeScreen)
                else f"{ec.EMOJI_BACK_STR}  Back to " + (caller.title.split(" ", 1)[-1] if getattr(caller, "title", "") else "previous"))
            self.render_model()
            return
        if target == "home":
            self.goto(m.Nav("home", m.DIRECT_ROOT))
            return
        self.goto(m.Nav(target, owner))

    SUB_TARGETS = ("rate_limiter", "memguard", "runtime_manager", "runtime", "api_keys", "runtime_launchd",
                   "remote_filters")   # Filter settings… returns to Remote, never Quit

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
        if target.startswith("tool:rt-op:"):
            _, _, op, version = target.split(":", 3)
            sm = self.screen_model
            self.run_child(_mb(sm.backend, *sm.argv_for(version)), pause=True)
            self.cache.pop(sm.inventory_kind, None)          # install/remove changed the list
            if op in ("install", "remove", "snapshot"):
                self._prefetch((sm.inventory_kind,))
            return
        if target.startswith("tool:rt-"):
            self.run_child(TOOLS[target](self.settings.runtime_backend), pause=True)
            return
        if target.startswith("tool:keys:open:"):
            # Open specific provider's signup page
            slug = target.split(":", 3)[3]
            url = PROVIDER_SIGNUP_URLS.get(slug)
            if url:
                m.open_url(url)
            return
        if target.startswith("tool:keys:wizard:"):
            # Open inline prompt for API key entry
            slug = target.split(":", 3)[3]
            self._pending_provider = slug
            # Find provider name for the prompt
            self._open_prompt("prompt:keys:key")
            return
        if target.startswith("tool:keys:"):
            # API keys with specific provider (tool:keys:add:<slug>)
            slug = target.split(":", 3)[3] if target.count(":") >= 3 else target.split(":", 2)[2]
            self.run_child(TOOLS["tool:keys:add"](slug), pause=True)
            return
        self.run_child(TOOLS[target](), pause=True)

    # --- inline prompts (text entry, confirmations) ------------------------------------------
    PROMPTS = {"prompt:session": "Set session name (letters, digits, - _; empty = ephemeral):",
               "prompt:oneshot": "One-shot prompt:",
               "prompt:keys:add": "Provider slug to add key for (gemini, groq, nvidia, etc.):",
               "prompt:keys:key": "API key/token for {provider} (hidden; paste advances automatically, or type it and press Enter; empty Enter skips):"}

    # Yes/no questions are ConfirmPanels, never typed (user 2026-10-16: ONE confirmation idiom).
    # The FIRST choice is what Enter answers: the action for harmless ones, the safe answer for
    # anything that deletes. Escape is always the safe answer.
    CONFIRMS = ("prompt:download-review", "prompt:rl-reset", "prompt:rt-remove",
                "prompt:rt-remove-sure", "prompt:rt-launchd-install",
                "prompt:rt-launchd-uninstall", "prompt:register")

    def _confirm(self, kind):
        sm = self.screen_model
        if kind == "prompt:download-review":
            rv = sm.review()
            fit = ("fits the disk reserve" if rv.fits
                   else "BREACHES the disk reserve — the downloader will refuse")
            q, d = (f"Download {len(rv.aliases)} model(s), ~{rv.total_gb:g} GB?",
                    f"{', '.join(rv.aliases)} · {fit}.")
            choices = [("yes", "Download now"), ("no", "Cancel (Esc)")]
        elif kind == "prompt:rl-reset":
            q, d = ("Delete the shared rate-limiter state file?",
                    "Every free-API lane forgets its recorded request history and cooldowns.")
            choices = [("no", "Cancel (Esc)"), ("yes", "Delete the state file")]
        elif kind == "prompt:rt-remove":
            v = sm.accordion.selected_id
            q, d = (f"Remove Rapid-MLX {v}?", "You are asked once more before anything is deleted.")
            choices = [("no", "Cancel (Esc)"), ("yes", f"Remove {v}")]
        elif kind == "prompt:rt-remove-sure":
            v = sm.accordion.selected_id
            q, d = (f"Are you sure? {v} is deleted permanently.", "This cannot be undone from the picker.")
            choices = [("no", "Cancel (Esc)"), ("yes", f"Yes, delete {v}")]
        elif kind in ("prompt:rt-launchd-install", "prompt:rt-launchd-uninstall"):
            verb = "Install" if kind.endswith("-install") else "Remove"
            q, d = (f"{verb} the weekly LaunchAgent update check?",
                    "A per-user launchd job that checks for backend updates once a week.")
            choices = ([("yes", "Install it"), ("no", "Cancel (Esc)")] if verb == "Install"
                       else [("no", "Cancel (Esc)"), ("yes", "Remove it")])
        else:  # prompt:register
            req = self._pending_launch
            mdl = getattr(sm, "models", {}).get(req.selected_id)
            backend = (mdl.backend if mdl else "").replace(" (auto)", "") or "rapid"
            q, d = (f"{req.selected_id} is on disk but not registered.",
                    f"Register it (backend: {backend}) and launch it now?")
            choices = [("yes", "Register and launch"), ("no", "Cancel (Esc)")]
        self.push_screen(ConfirmPanel(q, d, choices), lambda ans: self._confirmed(kind, ans))

    def _confirmed(self, kind, answer):
        self.query_one("#rows", OptionList).focus()
        yes = bool(answer) and answer[0] == "yes"
        sm = self.screen_model
        if kind == "prompt:download-review":
            req = sm.confirm(yes)
            if req:
                self.run_child(command_for(req), pause=True)
                self.goto(m.Nav("download", self.nav.owner))
            else:
                self.state_warnings.append("download cancelled — nothing was started")
                self.render_model()
        elif kind == "prompt:rl-reset":
            if yes:
                self.run_child(["python3", "-c",
                                "import rate_limiter as r,os;p=r.DEFAULT_STATE if not os.environ.get('LA_NVIDIA_THROTTLE_STATE') else r.Path(os.environ['LA_NVIDIA_THROTTLE_STATE']);p.unlink(missing_ok=True);print('state file reset')"],
                               pause=True, cwd=str(BIN))
            else:
                self.state_warnings.append("limiter state unchanged — nothing was deleted")
                self.render_model()
        elif kind in ("prompt:rt-remove", "prompt:rt-remove-sure"):
            # Select, yes, then an "are you sure" yes: the highlighted row names the version.
            v = sm.accordion.selected_id if isinstance(sm, m.VersionsScreen) else None
            if not (v and yes):
                self.state_warnings.append("removal cancelled — nothing was deleted")
                self.render_model()
            elif kind == "prompt:rt-remove":
                self._confirm("prompt:rt-remove-sure")
            else:
                self.run_tool(f"tool:rt-op:remove:{v}")
        elif kind in ("prompt:rt-launchd-install", "prompt:rt-launchd-uninstall"):
            if yes:
                self.run_child(TOOLS["tool:" + kind.split(":", 1)[1]](self.settings.runtime_backend), pause=True)
            else:
                self.state_warnings.append("launchd unchanged — nothing was run")
                self.render_model()
        elif kind == "prompt:register":
            req, self._pending_launch = self._pending_launch, None
            if not yes:
                self.state_warnings.append(f"{req.selected_id} not registered — nothing launched")
                self.render_model()
                return
            csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
            r = subprocess.run([csl, "--register-model", req.register_folder],
                               capture_output=True, text=True)
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).strip().splitlines()
                self.state_warnings.append(f"could not register {req.selected_id}: "
                                           + (msg[-1] if msg else f"exit {r.returncode}"))
                self.render_model()
                return
            self.cache.pop("local", None)          # the registry changed: reload on next view
            self._prefetch(("local",))
            req.needs_register = False
            self.launch(req)

    def _open_prompt(self, kind):
        if kind in self.CONFIRMS:
            self._confirm(kind)
            return
        self.pending_prompt = kind
        prompt = self.query_one("#prompt", Input)
        label = self.PROMPTS[kind]
        if kind == "prompt:keys:key":
            # Replace {provider} placeholder with actual provider name
            slug = getattr(self, '_pending_provider', 'unknown')
            label = label.format(provider=next((name for s_, name, *_ in m.APIKeysScreen.PROVIDERS if s_ == slug), slug))
            # The provider's setup steps sit right above the key field: account requirements such as
            # Vercel's credit card are only useful BEFORE the key is saved.
            steps = next((p.steps for p in m.KEY_PROVIDERS if p.slug == slug), "")
            if steps:
                label = steps + "\n  " + label
            prompt.password = True  # Hidden input for API key
            prompt.submit_on_paste = True
        prompt.placeholder = ""   # the label is shown once, in #status below the input
        prompt.value = ""
        prompt.display = True
        self.query_one("#status", Static).update("  " + label)
        prompt.focus()

    def _close_prompt(self):
        prompt = self.query_one("#prompt", Input)
        prompt.display = False
        prompt.password = False
        prompt.submit_on_paste = False
        self.pending_prompt = None
        self.query_one("#rows", OptionList).focus()
        self.render_model()

    def _filter_arrow(self, delta: int):
        """The filter box's Up/Down move the model list's highlight (SearchInput bindings);
        the caret stays in the box, so typing can resume mid-navigation. At the first row,
        Up continues into the actions list — the same seamless hand-off the list itself has."""
        rows = self.query_one("#rows", OptionList)
        if not rows.display:
            return
        h = rows.highlighted
        if h is None:
            rows.highlighted = 0 if delta > 0 else rows.option_count - 1
            return
        if delta < 0 and h == 0:
            actions = self.query_one("#actions", OptionList)
            if actions.option_count:
                actions.highlighted = actions.option_count - 1
                actions.focus()
            return
        if delta > 0 and h >= rows.option_count - 1:
            rows.highlighted = 0                # the list's own Down wraps at the bottom; so does the box's
            return
        rows.highlighted = max(0, min(rows.option_count - 1, h + delta))

    def on_input_changed(self, event):
        """The live model filter: every character re-filters the list at once, no Enter
        (user, 2026-10-21). The #prompt modal's changes are not ours to act on."""
        if getattr(event.input, "id", None) != "search":
            return
        sm = self.screen_model
        if isinstance(sm, MODEL_LIST_SCREENS):
            sm.set_search(event.value)
            self.render_model()

    def on_input_submitted(self, event):
        if getattr(event.input, "id", None) == "search":
            # Enter from the live filter: exactly the list's own Enter — launch the
            # highlighted model, or fold/unfold the highlighted group header.
            self.enter()
            return
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
        elif kind == "prompt:keys:add":
            if value:
                self.run_child(TOOLS["tool:keys:add"](value), pause=True)
            else:
                self.state_warnings.append("provider slug required")
                self.render_model()
        elif kind == "prompt:keys:key":
            if value:
                provider = getattr(self, '_pending_provider', None)
                if provider:
                    self._save_api_key(provider, value)
                    if isinstance(sm, m.APIKeysScreen):   # refresh the Saved/Missing labels
                        fresh = m.APIKeysScreen(self.settings, sm.owner)
                        fresh.back_label, fresh.accordion.open_group = sm.back_label, provider
                        self.screen_model = fresh
                else:
                    self.state_warnings.append("no provider selected")
                self.render_model()
            else:
                self.state_warnings.append("skipped — no key saved")
                self.render_model()

    # --- launches ------------------------------------------------------------------------------
    def launch(self, req):
        """Start a session. An unregistered (auto-scanned) model is offered for registration first;
        a launch that FAILS keeps its output on screen until Enter instead of being painted over
        (user 2026-10-15: deepseek-v4 bounced back to the menu with no message at all)."""
        if getattr(req, "needs_register", False):
            self._pending_launch = req
            self._open_prompt("prompt:register")
            return
        # Every launch carries the picker's own version, so a running session can always
        # answer which free-agents launched it (user request 2026-10-28).
        env = dict(req.env or {})
        env["LA_FREE_AGENTS_VERSION"] = m.plugin_version()
        # Lowkey launches INLINE and the menu stays put until Enter (user 2026-10-30).
        self.run_child(command_for(req), env=env,
                       pause=(req.lane == "lowkey"), pause_on_error=True)

    def _open_cleanup_panel(self, clicked: str | None = None):
        """The missing-models question as a ConfirmPanel. `clicked` = the red row the user
        selected: the panel then leads with WHY it will not launch, and ticks only that model."""
        names = list(MISSING_MODELS)
        if not names:
            return
        if clicked:
            question = f"{clicked} cannot launch: its weights are missing from disk."
            ticked = [clicked]
        else:
            question = f"{len(names)} registered model(s) are missing from disk."
            ticked = names
        detail = ("Their registry entries still point at deleted weights, so they can never "
                  "launch. Remove the ticked ones from the list? They move to the removed-models "
                  "list (csl --unregister-model, with a backup) and can be re-registered later.")
        choices = [("remove", "Remove selected from the list"),
                   ("keep", "Keep them for now (Esc)")]
        self.push_screen(ConfirmPanel(question, detail, choices, items=names, ticked=ticked),
                         self._cleanup_answered)

    def _open_register_panel(self):
        """Every on-disk, unregistered model in one ConfirmPanel, all ticked: register the ticked
        ones (csl --register-model per folder) without launching any of them."""
        new = list(getattr(self.screen_model, "unregistered", []) or [])
        if not new:
            return
        self._register_folders = {x.alias: (x.folder or x.alias) for x in new}
        names = [x.alias for x in new]
        self.push_screen(ConfirmPanel(
            f"{len(names)} new model(s) found on disk, not registered yet.",
            "Registering adds each ticked model to your local registry (config.local.sh, with a "
            "backup) so it can be launched from this list. Nothing is launched now.",
            [("register", "Register selected"), ("keep", "Not now (Esc)")],
            items=names), self._register_answered)

    def _register_answered(self, answer):
        self.query_one("#rows", OptionList).focus()
        choice, picked = answer if answer else ("keep", [])
        if choice != "register" or not picked:
            self._warn_here("no models registered")
            self.render_model()
            return
        csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
        failed = []
        for alias in picked:
            r = subprocess.run([csl, "--register-model", self._register_folders[alias]],
                               capture_output=True, text=True)
            if r.returncode != 0:
                msg = (r.stderr or r.stdout).strip().splitlines()
                failed.append(f"{alias} ({msg[-1] if msg else f'exit {r.returncode}'})")
        done = len(picked) - len(failed)
        self._warn_here(f"registered {done} model(s)" + (f"; failed: {', '.join(failed)}" if failed else ""))
        self.cache.pop("local", None)                  # the registry changed: reload the list
        sm = self.screen_model
        if isinstance(sm, m.LocalScreen):
            sm._models_loaded = False
        self._prefetch(("local",))
        self.render_model()

    def _cleanup_answered(self, answer):
        self.query_one("#rows", OptionList).focus()   # as _close_prompt: back to the list
        choice, picked = answer if answer else ("keep", [])
        names = list(MISSING_MODELS)
        if choice != "remove" or not picked:
            self._warn_here(f"kept {len(names)} missing model(s) in the registry")
            self.render_model()
            return
        csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
        r = subprocess.run([csl, "--unregister-model", *picked], capture_output=True, text=True)
        if r.returncode == 0:
            MISSING_MODELS[:] = [n for n in MISSING_MODELS if n not in picked]
            self._warn_here(f"moved {len(picked)} missing model(s) to the removed-models list")
            sm = self.screen_model
            if isinstance(sm, m.LocalScreen) and sm._models_loaded:
                sm.set_models(list(sm.models.values()), MISSING_MODELS)   # red rows go at once
        else:
            msg = (r.stderr or r.stdout).strip().splitlines()
            self.state_warnings.append("cleanup failed: " + (msg[-1] if msg else f"exit {r.returncode}"))
        self.render_model()

    # --- children ------------------------------------------------------------------------------
    def run_child(self, cmd, env=None, pause=False, cwd=None, pause_on_error=False):
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
            if pause or (pause_on_error and rc not in (0, None)):
                try:
                    input(f"\n[picker] finished (exit {rc}). Press Enter to return…")
                except (EOFError, KeyboardInterrupt):
                    pass
        self.state_warnings = [w for w in self.state_warnings if not w.startswith("last child")]
        if rc not in (0, None):
            self.state_warnings.append(f"last child exited {rc}")
        if isinstance(self.screen_model, m.DownloadScreen):   # a download changes what is on disk
            self.cache.pop("local", None)
            self.cache.pop("catalog", None)
        self.render_model(keep=(f"{'g' if keep[0] == 'group' else 'i'}:{keep[1]}" if keep else None))

    def _save_api_key(self, provider: str, key: str) -> None:
        """Save API key for a provider using the Store class."""
        import sys
        REPO = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(REPO / "install"))
        try:
            # The module file is install/setup-api-keys.py (hyphenated), so a plain import fails
            import importlib.util
            spec = importlib.util.spec_from_file_location("setup_api_keys", REPO / "install/setup-api-keys.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            Store, validate_value, StoreError, PROVIDERS = mod.Store, mod.validate_value, mod.StoreError, mod.PROVIDERS
        except (ImportError, OSError, AttributeError):
            self.state_warnings.append("Could not load install/setup-api-keys.py")
            return

        api_keys_dir = Path(os.environ.get("LA_API_KEYS_DIR", "~/.api_keys")).expanduser()

        try:
            with Store(api_keys_dir) as store:
                # Find provider to get required files
                provider_obj = None
                for p in PROVIDERS:
                    if p.slug == provider:
                        provider_obj = p
                        break

                if not provider_obj:
                    self.state_warnings.append(f"Unknown provider: {provider}")
                    return

                missing = [n for n in provider_obj.files if store.state(n) == "missing"]
                if not missing:
                    self.state_warnings.append(f"{provider_obj.name}: key already saved")
                    return

                # One value per entry: a multi-file provider (Cloudflare: token + account id)
                # fills its next missing file each time "Add key" is used
                name = missing[0]
                try:
                    validate_value(name, key)
                    store.save_new(name, key)
                    rest = f"; add again for {', '.join(missing[1:])}" if missing[1:] else ""
                    self.state_warnings.append(f"Saved {name} for {provider_obj.name}{rest}")
                except StoreError as exc:
                    self.state_warnings.append(f"{name}: {exc}")
                except OSError:
                    self.state_warnings.append(f"Could not save {name}; check file status")

        except StoreError as exc:
            self.state_warnings.append(f"Store error: {exc}")
        except Exception as exc:
            self.state_warnings.append(f"Error saving key: {exc}")

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
                    choices=["home", "local", "remote", "lowkey", "download", "rate-limiter", "remote_filters", "cloud_config"])
    ap.add_argument("--include-trials", action="store_true", help="show trial rows (the default)")
    ap.add_argument("--exclude-trials", action="store_true", help="start with trial rows hidden")
    ap.add_argument("--local-capable-shown", action="store_true",
                    help="start with locally-runnable remote models shown")
    ap.add_argument("--show-old", action="store_true",
                    help="start with remote catalog models over the age cutoff shown "
                         "(LA_REMOTE_MAX_MODEL_AGE_MONTHS, default 18 months)")
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
