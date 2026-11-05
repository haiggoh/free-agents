"""picker_core.py — the picker's non-terminal core (nicegui plan Task 6).

Everything here is UI-free: loaders, verdicts, settings build/persist (Savers), the screen
factory (build_screen) and command_for. session_picker.py (Textual) and bin/fa_gui (NiceGUI)
both import from here, so both frontends drive the SAME core. Moved verbatim from
session_picker.py; the app re-imports these names, so tests patching session_picker.<name>
keep working (same objects).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

import session_menu_state as sms  # noqa: E402
import cloud_session_env as cse  # noqa: E402
import session_picker_model as m  # noqa: E402

REPO = BIN.parent

def _run(cmd, timeout=60, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def load_local_models() -> list[m.LocalModel]:
    # The on-disk registry (csl --inventory) is the model list. 0.23.0 replaced it with
    # load-profile-models.py, which holds only a few hand-declared profiles, maps every one of
    # them to the first "operator" alias, and was run under bash (so it always failed into this
    # path anyway). Profile-aware discovery is tracked as its own work item, not done here.
    csl = os.environ.get("CSL_SELF") or str(BIN / "csl")
    r = _patchable('_run')(["bash", csl, "--inventory"])
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
    # 0.50.1: ONE batched call — the per-model loop spawned a full bash+config+python per
    # model (the same class as the 2026-10-29 roster fix: ~255 spawns, ~9 s).
    registered, failed = 0, []
    if new:
        r = _patchable('_run')([csl, "--register-models", *[x.folder or x.alias for x in new]])
        if r.returncode == 0:
            registered = len(new)
        else:
            failed = [x.alias for x in new]
    moved = 0
    if gone:
        r = _patchable('_run')([csl, "--unregister-models", *gone])
        if r.returncode == 0:
            moved = len(gone)
        else:
            failed += gone
    note = f"auto-synced the registry: registered {registered}, moved {moved} missing"
    return note + (f"; failed: {', '.join(failed)}" if failed else "")


def load_remote_agents() -> list[m.RemoteAgent]:
    rs = os.environ.get("CSL_REMOTE_LAUNCHER") or str(BIN / "remote-session.sh")
    r = _patchable('_run')(["bash", rs, "--inventory"])
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
            # 0.52.1: the count comes from model_param_count FIRST (PARAM_COUNT_OVERRIDES +
            # id parse + HF cache). The estimator's own id-parse misses vendor-documented
            # ids and its HF rung needs the warmed cache, so going through estimate() alone
            # left those models "unknown" — never hidden, in practice.
            total = model_param_count(provider, model_id)
            if total:
                return _footprint().fits_with_total(total)
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
    # 0.50.8 (user: parameter size in every nvidia row name): the named models below. HF
    # resolves most via the warmed cache; the gated/unmapped ones come from NVIDIA docs.
    # Sources verified 2026-11-03 — HF repo safetensors for the first six:
    "glm-5.3-flash": 321.3,            # zai-org/GLM-5.3-Flash: 321,323,031,390
    "deepseek-v4.1-flash": 763.2,      # deepseek-ai/DeepSeek-V4.1-Flash: 763,205,315,794
    "kimi-k2.6": 1026.9,               # moonshotai/Kimi-K2.6: 1,026,879,376,368
    "arctic-embed-l": 0.3,             # Snowflake/snowflake-arctic-embed-l: 334,092,288
    # NVIDIA build card / vendor docs for the gated or unmapped ids:
    "yi-large": 102.6,                 # 01-ai/yi-large: 102.6 B MoE (InferenceBench 2024-05-13)
    "kosmos-2": 1.7,                   # build.nvidia.com card: Parameters 1,664,485,376
    "minimax-m3.1": 428.0,             # MiniMax M3.1 Flash: 428 B total / 23 B active (open weights)
    "mistral-large-2411": 123.0,       # Mistral Large 2 (Nov 2024): 123 B
    "mistral-small-2503": 24.0,        # Mistral Small 3 (Jan 2025): 24 B
    # 0.54.0 Task 3: FULL model ids (contain "/") so each key matches exactly one catalog id —
    # the substring patterns above are pre-existing and left alone. All nine below are HF-gated
    # (HF API answers 401, verified 2026-11-04) and sized from the vendor's own page, fetched
    # 2026-11-04: "# <id>: <count> — <source URL>, fetched 2026-11-04 (HF API 401)".
    "ai21labs/jamba-1.5-large-instruct": 398.0,   # docs.ai21.com/docs/jamba-foundation-models: Jamba Large 398B total / 94B active
    "mistralai/mistral-large": 123.0,             # mistral.ai/news/mistral-large-2407: "123 billion parameters"
    "mistralai/mistral-large-2-instruct": 123.0,  # Mistral Large 2 (2411) 123B — docs.mistral.ai/models
    "nvidia/nemotron-parse-2.0": 0.905,           # build.nvidia.com/nvidia/nemotron-parse-2.0/modelcard: "Number of model parameters: 905M"
    "nvidia/nemotron-parse": 1.0,                 # build.nvidia.com/nvidia/nemotron-parse/modelcard: "Number of Parameters: <1B" (upper bound)
    "nvidia/ai-synthetic-video-detector": 0.172,  # build.nvidia.com/nvidia/synthetic-video-detector/modelcard: "1.72 * 10^8"
}

# 0.54.0 Task 3: HF-gated NVIDIA ids with NO public parameter count — each entry names >= 2
# sources checked and their result. Marking an id unknown is legitimate only after both were
# actually fetched (2026-11-04). model_param_count returns None for these (sorts last).
PARAM_COUNT_UNKNOWN = {
    "nvidia/embed-qa-4": (
        "no public parameter count found — HF API 401 (gated, 2026-11-04); "
        "docs.api.nvidia.com/nim/reference/nvidia-embed-qa-4 fetched 2026-11-04: no count stated; "
        "build.nvidia.com/nvidia/embed-qa-4/modelcard is JS-only"),
    "nvidia/nvclip": (
        "no public parameter count found — HF API 401 (gated, 2026-11-04); "
        "docs.api.nvidia.com/nim/reference/nvidia-nvclip fetched 2026-11-04: no count stated; "
        "build.nvidia.com/nvidia/nvclip/modelcard empty"),
    "nvidia/vila": (
        "no public count for the NIM vila variant — HF API 401 (gated, 2026-11-04); "
        "docs.api.nvidia.com/nim/reference/nvidia-vila fetched 2026-11-04: no count stated; "
        "NVlabs/VILA GitHub lists family sizes 3B/8B/13B/40B but not which the NIM id serves"),
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
                if "/" in pat:
                    # 0.54.0: full-id keys match EXACTLY (a prefix key like
                    # "mistralai/mistral-large" would also match mistral-large-2-instruct)
                    if pat == model_id:
                        return float(b)
                elif pat in (model_id or ""):
                    return float(b)          # pre-existing substring keys, unchanged
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



def _patchable(name):
    """Resolve a collaborator through the TUI module when it is loaded, so tests that
    monkeypatch `session_picker.<name>` (main's single-module layout) also affect the
    extracted core. Falls back to this module's own global when session_picker is absent
    (GUI imports picker_core directly)."""
    sp = sys.modules.get("session_picker")
    if sp is not None:
        f = getattr(sp, name, None)
        if f is not None:
            return f
    return globals()[name]

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
    r = _patchable('_run')(["bash", rs, "--include-trials", "--models", alias])
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else f"catalog exited {r.returncode}")
    rows = [m.CatalogModel.from_line(ln) for ln in r.stdout.splitlines() if "\t" in ln]
    # 0.50.8: the HF size/age warm-up NO LONGER runs here — it was serial (up to
    # HF_WARM_LIMIT ids x 4 s timeout each) and ran BEFORE the rows were returned, so a
    # provider's group stayed on its placeholder for minutes. The app now delivers these
    # rows first and warms in a background thread (_warm_catalog_bg).
    return rows


# A catalog can list hundreds of ids; the first lookups cost ~0.3 s each, so cap the cold
# warm-up per provider. Later opens read the 30-day cache instantly. The cap must exceed a
# provider's full catalog (NVIDIA lists 85 models, 2026-10-31) or the tail rows never get a
# size/age verdict and the picker shows them unsized — user 2026-10-31: every NVIDIA group
# model should display its parameter size.

HF_WARM_LIMIT = int(os.environ.get("LA_PICKER_HF_WARM_LIMIT", "200"))



def load_catalog():
    dl = os.environ.get("LA_DOWNLOADER") or str(REPO / "install/download-models.sh")
    r = _patchable('_run')(["bash", dl, "--inventory"])
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
    inst = _patchable('_run')(_patchable('_mb')(backend, "installed"))
    installed = [m.VersionEntry(p[0], p[1], pinned=(len(p) >= 3 and p[2] == "pinned"))
                 for p in (ln.split() for ln in inst.stdout.splitlines())
                 if len(p) >= 2 and p[0][:1].isdigit()]
    if source == "installed":
        if inst.returncode != 0:
            raise RuntimeError((inst.stderr or inst.stdout).strip() or f"exit {inst.returncode}")
        return installed
    rel = _patchable('_run')(_patchable('_mb')(backend, "releases", "--pre", "--limit", "30"))
    if rel.returncode != 0:
        raise RuntimeError((rel.stderr or rel.stdout).strip() or f"exit {rel.returncode}")
    have = {e.version for e in installed}
    return [m.VersionEntry(v, "installed" if v in have else "")
            for v in (ln.strip() for ln in rel.stdout.splitlines()) if v]


TOOLS = {
    "tool:keys": lambda: ["python3", str(REPO / "install/setup-api-keys.py")],
    "tool:rl-status": lambda: ["python3", str(BIN / "rate_limiter.py"), "status"],
    "tool:report": lambda: ["bash", str(BIN / "local-capable-filter.sh"), "--report",
                            str(REPO / "config/local-capable-remote-models.psv"), "--roster",
                            str(REPO / "config/remote-agents.sh")],
    # Backend manager (install/manage-backend.py): backend -> argv. Version-taking operations
    # (install/validate/info/snapshot/promote/remove) go through VersionsScreen.argv_for. "tool:rl-reset" is
    # the RATE LIMITER state reset; it is handled as a typed-confirm prompt, never run directly.
    "tool:rt-releases": lambda b: _patchable('_mb')(b, "releases", "--pre"),
    "tool:rt-launchd-status": lambda b: _patchable('_mb')(b, "launchd", "status"),
    "tool:rt-launchd-run-once": lambda b: _patchable('_mb')(b, "launchd", "run-once"),
    "tool:rt-launchd-install": lambda b: _patchable('_mb')(b, "launchd", "install"),
    "tool:rt-launchd-uninstall": lambda b: _patchable('_mb')(b, "launchd", "uninstall"),
    "tool:keys:add": lambda slug: ["python3", str(REPO / "install/setup-api-keys.py"), slug],
}




# --- Task 6 new interfaces ---------------------------------------------------------------

class Savers:
    """Field-level persistence for the picker's settings store (one JSON file, merged per
    field so two frontends never clobber each other). warnings/notices are shared lists the
    app aliases as state_warnings/state_notices."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.warnings: list[str] = []
        self.notices: list[str] = []

    def _warn(self, warning):
        if warning and warning not in self.warnings:
            self.warnings.append(warning)

    def effort(self, lane, value):        self._warn(sms.save_effort(self.config_dir, lane, value))
    def hidden_providers(self, providers): self._warn(sms.save_hidden_providers(self.config_dir, providers))
    def temperature(self, lane, value):   self._warn(sms.save_temperature(self.config_dir, lane, value))
    def thinking(self, value):            self._warn(sms.save_thinking_session(self.config_dir, value))

    def cloud(self, values):
        # Written to ~/.claude/settings.json env, which every cloud session applies (any login,
        # any launcher). A failed write is surfaced, never swallowed.
        try:
            if "route" in values:
                path, mode = values["route"]
                cse.save_route(path, mode)
            if "security_review" in values:
                cse.save(values["security_review"])
        except (OSError, ValueError) as exc:
            self._warn(f"cloud setting not saved: {exc}")

    def last_launched(self, lane, alias, tier):
        try:
            warning = sms.save_last_launched(self.config_dir, lane, alias, tier)
        except ValueError as exc:          # an alias the store refuses: never block the launch
            warning = f"{exc}; Go last not remembered"
        self._warn(warning)

    def rate_limiter(self, values):       self._warn(sms.save_rate_limiter(self.config_dir, values))

    def memguard(self, values):
        try:
            warning = sms.save_memguard(self.config_dir, values)
        except ValueError as exc:
            warning = f"{exc}; not saved"
        self._warn(warning)

    def lowkey_filters(self, key, value):          # schema-9 section, ported from main 0.54.0
        try:
            warning = sms.save_lowkey_filters(self.config_dir, key, value)
        except ValueError as exc:
            warning = f"{exc}; not saved"
        self._warn(warning)


def build_settings(flags: "argparse.Namespace | None", savers: Savers,
                   env: "dict | None" = None) -> m.Settings:
    """Build the Settings the way Picker.__init__ does: store first, then env, then flags."""
    env = os.environ if env is None else env
    state = sms.load(savers.config_dir)
    cloud = cse.load()
    flags = flags or argparse.Namespace(include_trials=True, exclude_trials=False,
                                        local_capable_shown=False, show_old=False)
    return m.Settings(
        local_effort=state.effort["local_session"],
        remote_effort=state.effort["remote_api_session"],
        lowkey_effort=state.effort["lowkey"],
        local_temperature=state.temperature.get("local_session", sms.DEFAULT_TEMPERATURE["local_session"]),
        remote_temperature=state.temperature.get("remote_api_session", sms.DEFAULT_TEMPERATURE["remote_api_session"]),
        lowkey_temperature=state.temperature.get("lowkey", sms.DEFAULT_TEMPERATURE["lowkey"]),
        thinking_session=state.thinking_session,
        rate_limiter=dict(state.rate_limiter),
        memguard=dict(state.memguard),
        auto_mode=int(env.get("CSL_AUTO_MODE_STATE", "0") or 0) % 3,
        telemetry=env.get("CSL_TELEMETRY", "0") == "1",
        stop_hook=env.get("CSL_STOP_HOOK", "0") == "1",
        watcher=env.get("CSL_WATCH", "0") == "1",
        enable_mcp=env.get("LA_ENABLE_MCP", "1") == "1",
        include_trials=not flags.exclude_trials,
        local_capable_shown=flags.local_capable_shown or env.get("CSL_LOCAL_CAPABLE") == "1",
        show_old=getattr(flags, "show_old", False),
        on_effort_saved=savers.effort,
        on_temperature_saved=savers.temperature,
        on_thinking_saved=savers.thinking,
        hidden_providers=list(state.hidden_providers),
        on_hidden_providers_saved=savers.hidden_providers,
        lowkey_remote_filters=bool(state.lowkey_filters.get("remote_filters", False)),   # 0.54.0 schema 9
        on_lowkey_filters_saved=savers.lowkey_filters,
        security_review=cloud["security_review"],
        security_review_custom=cloud["custom_model"],
        security_review_offered=cloud["plugin_active"],
        routes={p: cse.route(p) for p in cse.ROUTE_PATHS},
        on_cloud_saved=savers.cloud,
        on_memguard_saved=savers.memguard,
        last_launched_model={"local_session": None, "remote_api_session": None, "lowkey": None,
                             **state.last_launched},
        last_launched_tier=dict(state.last_launched_tier),
        on_last_launched=savers.last_launched,
        catalog_local_capable=catalog_local_capable,
        model_param_count=model_param_count,
        catalog_model_old=catalog_model_old,
    )


from dataclasses import dataclass  # noqa: E402


@dataclass
class ScreenBuild:
    screen: m.Screen
    prefetch: tuple = ()


def build_screen(nav: m.Nav, s: m.Settings, cache: dict,
                 catalog_counts: "dict | None" = None, savers: "Savers | None" = None) -> ScreenBuild:
    """The Picker.goto switch minus rendering/prefetch side effects: pure screen construction.
    Returns the screen plus the kinds the app should prefetch for it."""
    prefetch: tuple = ()
    if nav.target == "home":
        local = cache.get("local")
        remote = cache.get("remote")
        if remote is None:
            catalog_total = None
        else:
            keyed = [a for a in remote if a.has_key]
            catalog_total = (0 if not keyed else
                             (None if not catalog_counts else
                              sum(catalog_counts.get(a.provider, 0) for a in keyed)))
        screen = m.HomeScreen(s, len(local) if local else None, len(remote) if remote else None,
                              catalog_total, nav.owner)
    elif nav.target == "about":
        screen = m.AboutScreen(s, nav.owner)
    elif nav.target == "local":
        local = cache.get("local")
        screen = m.LocalScreen(s, local, nav.owner)
        if local is not None:
            screen.set_models(local, MISSING_MODELS)
    elif nav.target == "remote":
        screen = m.RemoteScreen(s, cache.get("remote"), nav.owner)
    elif nav.target == "lowkey":
        screen = m.LowkeyScreen(s, cache.get("local"), nav.owner,
                                api_agents=cache.get("remote") or [])
    elif nav.target == "download":
        catalog_data = cache.get("catalog")
        if catalog_data:
            entries, free, head = catalog_data
        else:
            entries, free, head = [], 0.0, 100.0
            prefetch = ("catalog",)
        screen = m.DownloadScreen(s, entries, free, head, nav.owner)
    elif nav.target == "rate_limiter":
        screen = m.RateLimiterScreen(s, nav.owner,
                                     on_save=savers.rate_limiter if savers else None)
    elif nav.target == "memguard":
        screen = m.MemoryGuardScreen(s, nav.owner,
                                     on_save=savers.memguard if savers else None)
    elif nav.target == "runtime_manager":
        screen = m.RuntimeManagerScreen(s, nav.owner)
    elif nav.target == "runtime":
        screen = m.RuntimeScreen(s, nav.owner)
    elif nav.target == "runtime_launchd":
        screen = m.LaunchdScreen(s, nav.owner)
    elif nav.target == "api_keys":
        screen = m.APIKeysScreen(s, nav.owner)
    elif nav.target.startswith("runtime_versions:"):
        sm = m.VersionsScreen(s, nav.target.split(":", 1)[1], owner=m.SUB)
        prefetch = (sm.inventory_kind,)
        screen = sm
    elif nav.target == "remote_filters":
        screen = m.RemoteFiltersScreen(s, nav.owner)
    elif nav.target == "cloud_config":
        screen = m.CloudConfigScreen(s, nav.owner)
    elif nav.target == "theme_config":
        screen = m.ThemeConfigScreen(s, nav.owner)
    else:
        raise ValueError(nav.target)
    return ScreenBuild(screen, prefetch)
