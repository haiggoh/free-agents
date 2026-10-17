#!/usr/bin/env python3
"""la-memguard - Memory guard watchdog for local LLM sessions.

Subcommands:
  run [--interval S] [--once]  Run the watchdog loop.
  ensure                      Ensure watchdog is running detached.
  status                      Print watchdog status JSON.

Options:
  -h, --help                  Show this help message and exit.
  --interval S                Polling interval in seconds (default: 2.0).
  --once                      Run a single tick and exit.

Environment Variables:
  LA_MEMGUARD_SEGMENT         Path to RAM statusline segment script.
  LA_MEMGUARD_STATE_DIR       State directory (default: ~/.local/state/free-agents/memguard).
  LA_MEMGUARD_FOOTPRINT_CMD   Command to measure per-session memory footprint.
  LA_MEMGUARD_SOFT_PCT        Soft RAM threshold percentage (default: 85).
  LA_MEMGUARD_HARD_PCT        Hard RAM threshold percentage (default: 93).
  LA_MEMGUARD_COOLDOWN_S      Cooldown between session compacts in seconds (default: 300).
  LA_MEMGUARD_INJECT_CMD      Command to inject compact instruction into session.
  LA_MEMGUARD_NOTIFY          Set to 0 to disable macOS notifications (default: 1).
  LA_MEMGUARD_INTERVAL_S      Default loop interval for run subcommand (default: 2.0).
"""

import fcntl
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def get_state_dir() -> Path:
    env_dir = os.environ.get("LA_MEMGUARD_STATE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "state" / "free-agents" / "memguard"


def load_cfg() -> dict[str, Any]:
    return {
        "soft_pct": float(os.environ.get("LA_MEMGUARD_SOFT_PCT", 85)),
        "hard_pct": float(os.environ.get("LA_MEMGUARD_HARD_PCT", 93)),
        "cooldown_s": float(os.environ.get("LA_MEMGUARD_COOLDOWN_S", 300)),
        "interval_s": float(os.environ.get("LA_MEMGUARD_INTERVAL_S", 2.0)),
    }


def read_ram() -> tuple[float, float, int] | None:
    segment_script = os.environ.get("LA_MEMGUARD_SEGMENT")
    if not segment_script:
        segment_script = str(Path(__file__).resolve().parent / "la-statusline-segment.sh")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1"

    try:
        res = subprocess.run(
            [segment_script],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return None
        line = res.stdout.strip().splitlines()[0]
        data = json.loads(line)
        text = data.get("text", "")
        m = re.match(r"^([0-9.]+)/([0-9.]+)G ([0-9]+)%$", text)
        if not m:
            return None
        wired_gb = float(m.group(1))
        cap_gb = float(m.group(2))
        pct = int(m.group(3))
        return (wired_gb, cap_gb, pct)
    except Exception:
        return None


def get_footprint(port: str | int, footprint_cmd: str | None = None) -> float:
    cmd_env = footprint_cmd or os.environ.get("LA_MEMGUARD_FOOTPRINT_CMD")
    if cmd_env:
        try:
            cmd = shlex.split(cmd_env) + [str(port)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return float(res.stdout.strip())
        except Exception:
            return 0.0
        return 0.0

    try:
        lsof_res = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if lsof_res.returncode != 0 or not lsof_res.stdout.strip():
            return 0.0
        pid = lsof_res.stdout.strip().splitlines()[0]
        # NOT ps RSS: on MLX the weights and KV cache live in Metal (IOAccelerator) memory that
        # RSS does not count (measured 2026-10-17: rapid server RSS 9.3 GB, phys_footprint 53 GB).
        fp_res = subprocess.run(["footprint", "-p", pid], capture_output=True, text=True, timeout=10)
        return parse_phys_footprint(fp_res.stdout)
    except Exception:
        return 0.0


_UNITS = {"B": 1 / 1024**3, "KB": 1 / 1024**2, "MB": 1 / 1024, "GB": 1.0, "TB": 1024.0}


def parse_phys_footprint(text: str) -> float:
    """GB from macOS `footprint -p` output ('phys_footprint: 53 GB'); 0.0 when absent."""
    m = re.search(r"phys_footprint:\s*([0-9.]+)\s*(B|KB|MB|GB|TB)\b", text)
    return float(m.group(1)) * _UNITS[m.group(2)] if m else 0.0


def load_state(state_dir: Path) -> dict[str, Any]:
    state_file = state_dir / "watchdog.json"
    if state_file.exists():
        try:
            with open(state_file, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {"level": "ok", "last_compact_at": {}, "pending": {}, "inflight": None}


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "watchdog.json"
    tmp_file = state_file.with_suffix(".tmp")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp_file, flags, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, state_file)
    except Exception:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass


def decide(
    reading: tuple[float, float, int] | None,
    sessions: list[dict[str, Any]],
    footprints: dict[str, float],
    state: dict[str, Any],
    now: float,
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    new_state = {
        "level": state.get("level", "ok"),
        "last_compact_at": dict(state.get("last_compact_at", {})),
        "pending": dict(state.get("pending", {})),
        "inflight": state.get("inflight"),
    }

    if reading is None:
        return [], new_state

    wired, cap, pct = reading
    soft_pct = cfg.get("soft_pct", 85)
    hard_pct = cfg.get("hard_pct", 93)
    cooldown_s = cfg.get("cooldown_s", 300)

    if pct >= hard_pct:
        level = "hard"
    elif pct >= soft_pct:
        level = "soft"
    else:
        level = "ok"

    actions: list[dict[str, Any]] = []
    old_level = state.get("level", "ok")
    if level != old_level:
        actions.append({"kind": "level", "from": old_level, "to": level, "pct": pct})
    new_state["level"] = level

    inflight = new_state.get("inflight")
    if inflight and isinstance(inflight, dict):
        if (now - inflight.get("at", 0)) > 120:
            inflight = None
            new_state["inflight"] = None

    if level == "ok":
        new_state["pending"] = {}
        return actions, new_state

    reason_str = (
        f"Memory guard: RAM at {pct}% of the {cap:g} GB ceiling — compacting to keep this session alive"
    )

    old_pending = state.get("pending", {})
    new_pending = dict(new_state.get("pending", {}))

    for s in sessions:
        sid = s["id"]
        if not s.get("idle", True):
            if sid not in new_pending:
                new_pending[sid] = {"reason": reason_str, "since": now}
            if sid not in old_pending:
                actions.append(
                    {
                        "kind": "compact-after-turn",
                        "session": sid,
                        "reason": reason_str,
                        "level": level,
                    }
                )

    new_state["pending"] = new_pending

    inflight_id = inflight.get("id") if (inflight and isinstance(inflight, dict)) else None

    candidates = []
    for s in sessions:
        sid = s["id"]
        if not s.get("idle", True):
            continue
        last_compact = new_state["last_compact_at"].get(sid, 0.0)
        if (now - last_compact) < cooldown_s:
            continue
        if inflight_id and sid == inflight_id:
            continue
        candidates.append(s)

    if candidates:

        def sort_key(s: dict[str, Any]) -> tuple[float, float]:
            fp = footprints.get(s["id"], 0.0)
            idle_since = s.get("idle_since", now)
            if idle_since is None:
                idle_since = now
            return (-fp, idle_since)

        candidates.sort(key=sort_key)
        chosen = candidates[0]
        cid = chosen["id"]

        actions.append(
            {
                "kind": "compact",
                "session": cid,
                "reason": reason_str,
                "level": level,
            }
        )
        new_state["last_compact_at"][cid] = now
        new_state["inflight"] = {"id": cid, "at": now}
        if cid in new_state["pending"]:
            del new_state["pending"][cid]
    else:
        if level == "hard" and old_level != "hard":
            actions.append({"kind": "notify", "level": "hard", "text": reason_str})

    return actions, new_state


def act_on_actions(actions: list[dict[str, Any]], state_dir: Path, now: float) -> None:
    if not actions:
        return
    log_file = state_dir / "watchdog.log"
    state_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_file, "a") as f:
            for action in actions:
                action_log = dict(action)
                action_log["t"] = now
                f.write(json.dumps(action_log) + "\n")

                kind = action.get("kind")
                if kind == "compact":
                    inject_cmd = os.environ.get("LA_MEMGUARD_INJECT_CMD")
                    if inject_cmd:
                        try:
                            cmd = shlex.split(inject_cmd) + [action["session"], action["reason"]]
                            subprocess.run(cmd, check=False)
                        except Exception:
                            pass
                elif kind == "notify":
                    if os.environ.get("LA_MEMGUARD_NOTIFY") != "0":
                        try:
                            text = action.get("text", "")
                            safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
                            script = f'display notification "{safe_text}" with title "free-agents memory guard"'
                            subprocess.run(["osascript", "-e", script], check=False)
                        except Exception:
                            pass
    except Exception:
        pass


def load_registry_module() -> Any:
    registry_path = Path(__file__).resolve().parent / "la-memguard-registry.py"
    if not registry_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("memguard_registry", registry_path)
        if spec and spec.loader:
            registry_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(registry_mod)
            return registry_mod
    except Exception:
        pass
    return None


def do_run(interval: float, once: bool, state_dir: Path) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = state_dir / "watchdog.lock"

    try:
        lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        sys.stderr.write("already running\n")
        return 0

    registry_mod = load_registry_module()
    if not registry_mod:
        return 0

    cfg = load_cfg()

    while True:
        sessions = registry_mod.live_sessions()
        if not sessions:
            break

        reading = read_ram()
        footprints = {}
        for s in sessions:
            port = s.get("port")
            if port:
                footprints[s["id"]] = get_footprint(port)

        now = time.time()
        state = load_state(state_dir)
        actions, new_state = decide(reading, sessions, footprints, state, now, cfg)
        act_on_actions(actions, state_dir, now)
        save_state(state_dir, new_state)

        if once:
            break

        time.sleep(interval)

    return 0


def do_ensure(state_dir: Path) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = state_dir / "watchdog.lock"

    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        lock_fd = None
    except (BlockingIOError, OSError):
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
        return 0

    stderr_log = state_dir / "watchdog-stderr.log"
    try:
        out_f = open(stderr_log, "a")
    except Exception:
        out_f = subprocess.DEVNULL  # type: ignore

    python_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    cmd = [python_exe, script_path, "run"]
    subprocess.Popen(
        cmd,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=out_f,
        stderr=out_f,
    )
    return 0


def do_status(state_dir: Path) -> int:
    registry_mod = load_registry_module()
    sessions = registry_mod.live_sessions() if registry_mod else []

    reading = read_ram()
    state = load_state(state_dir)

    sess_list = []
    for s in sessions:
        port = s.get("port")
        fp = get_footprint(port) if port else 0.0
        sess_list.append(
            {
                "id": s["id"],
                "idle": s.get("idle", True),
                "footprint_gb": fp,
            }
        )

    out = {
        "reading": reading,
        "level": state.get("level", "ok"),
        "sessions": sess_list,
        "state": state,
    }
    print(json.dumps(out))
    return 0


def main(argv: list[str]) -> int:
    if not argv or "-h" in argv or "--help" in argv:
        print((__doc__ or "").strip())
        return 0

    subcommand = argv[0]
    rest = argv[1:]

    if subcommand == "run":
        interval = float(os.environ.get("LA_MEMGUARD_INTERVAL_S", 2.0))
        once = False
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--once":
                once = True
                i += 1
            elif arg == "--interval":
                if i + 1 >= len(rest):
                    sys.stderr.write("Usage: la-memguard.py run [--interval S] [--once]\n")
                    return 2
                try:
                    interval = float(rest[i + 1])
                except ValueError:
                    sys.stderr.write("Invalid interval\n")
                    return 2
                i += 2
            else:
                sys.stderr.write(f"Unknown flag: {arg}\n")
                return 2
        state_dir = get_state_dir()
        return do_run(interval, once, state_dir)

    elif subcommand == "ensure":
        if rest:
            sys.stderr.write("Usage: la-memguard.py ensure\n")
            return 2
        state_dir = get_state_dir()
        return do_ensure(state_dir)

    elif subcommand == "status":
        if rest:
            sys.stderr.write("Usage: la-memguard.py status\n")
            return 2
        state_dir = get_state_dir()
        return do_status(state_dir)

    else:
        sys.stderr.write(f"Unknown subcommand: {subcommand}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
