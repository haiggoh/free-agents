#!/usr/bin/env python3
"""Inject text into a running Claude Code session inside a private tmux session.

Usage:
    la-memguard-inject.py [--force-busy] <session-id> <text>
    la-memguard-inject.py -h | --help

Exit Codes:
    0: Text injected successfully.
    2: Usage error / unknown flag / invalid argument count.
    3: User is typing (quiet period not established before timeout or activity detected; nothing sent).
    4: No tmux target (session record missing or tmux socket/target empty).
    5: Session busy (idle is False and --force-busy not specified).
    6: Unsafe screen state (prompt missing, stash failed, or text verification failed; any stash restored).
    7: Text injected, but draft remained in stash (warning printed to stderr).

Environment Variables:
    LA_MEMGUARD_TMUX_BIN: Path to tmux binary (default: tmux).
    LA_MEMGUARD_QUIET_S: Required quiet duration in seconds (default: 3.0).
    LA_MEMGUARD_QUIET_MAX_S: Maximum wait time for quiet condition in seconds (default: 60).
    LA_MEMGUARD_KEYBINDINGS: Path to keybindings JSON file (default: ~/.claude/keybindings.json).
    LA_MEMGUARD_RESTORE_WAIT_S: Seconds to wait for draft auto-restore (default: 2.0).
    LA_MEMGUARD_STATE_DIR: Directory containing session records.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_now = time.time
_sleep = time.sleep


def find_stash_binding(obj) -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v == "chat:stash" and isinstance(k, str):
                return k
            if k == "chat:stash" and isinstance(v, str):
                return v
            res = find_stash_binding(v)
            if res is not None:
                return res
            res = find_stash_binding(k)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_stash_binding(item)
            if res is not None:
                return res
    return None


def get_stash_key() -> str | None:
    kb_env = os.environ.get("LA_MEMGUARD_KEYBINDINGS")
    kb_path = Path(kb_env) if kb_env else Path.home() / ".claude" / "keybindings.json"

    if not kb_path.exists():
        return "C-s"

    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_key = find_stash_binding(data)
        if raw_key is None:
            return "C-s"
        m = re.match(r"^ctrl\+([a-zA-Z0-9])$", raw_key.strip(), re.IGNORECASE)
        if m:
            return f"C-{m.group(1).lower()}"
        else:
            return None
    except Exception:
        return "C-s"


def read_session_record(session_id: str) -> dict | None:
    reg_file = Path(__file__).resolve().parent / "la-memguard-registry.py"
    try:
        spec = importlib.util.spec_from_file_location("memguard_registry", reg_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.read(session_id)
    except Exception:
        return None


def run_tmux(socket: str, *args: str, timeout: float = 5.0) -> str:
    tmux_bin = os.environ.get("LA_MEMGUARD_TMUX_BIN", "tmux")
    cmd = [tmux_bin, "-S", socket] + list(args)
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout
        )
        return proc.stdout
    except Exception:
        return ""


def parse_box_from_pane(output: str) -> tuple[str, bool]:
    lines = output.splitlines()
    # The input box is the `❯` line directly under a `─` rule. Earlier prompts in the transcript
    # also start with `❯`, so the rule above is what tells the live box from history.
    prompt_idx = -1
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].startswith("❯") and _is_rule(lines[i - 1]):
            prompt_idx = i
            break
    if prompt_idx == -1:
        return "", False

    first_line = lines[prompt_idx]
    rest = first_line[1:]
    if rest.startswith(" "):
        rest = rest[1:]
    collected = [rest]

    for i in range(prompt_idx + 1, len(lines)):
        line = lines[i]
        if line.startswith("─") or (len(line) > 0 and all(c == "─" for c in line.strip())):
            break
        if line.startswith("  "):
            line = line[2:]
        collected.append(line)

    processed = [l.rstrip(" ") for l in collected]
    box_text = "\n".join(processed).strip()
    return box_text, True


def _is_rule(line: str) -> bool:
    s = line.strip()
    return len(s) >= 3 and all(c == "─" for c in s)


def same_text(a: str, b: str) -> bool:
    """Box text compared without whitespace: the box soft-wraps long text onto new lines."""
    return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)


def read_box_text(socket: str, target: str) -> tuple[str, bool]:
    out = run_tmux(socket, "capture-pane", "-p", "-t", target)
    return parse_box_from_pane(out)


# client_activity is whole seconds; callers add 1.0 so a key is dated to the end of its second.
def get_client_activity(socket: str, target: str) -> str:
    return run_tmux(socket, "display", "-p", "-t", target, "#{client_activity}").strip()


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print((__doc__ or "").strip())
        sys.exit(0)

    force_busy = False
    positionals = []
    for arg in args:
        if arg == "--force-busy":
            force_busy = True
        elif arg.startswith("-"):
            sys.stderr.write(f"Unknown flag: {arg}\n")
            sys.exit(2)
        else:
            positionals.append(arg)

    if len(positionals) != 2:
        sys.stderr.write("Usage: la-memguard-inject.py [--force-busy] <session-id> <text>\n")
        sys.exit(2)

    session_id, text_to_send = positionals

    rec = read_session_record(session_id)
    if not rec:
        sys.exit(4)

    socket = rec.get("tmux_socket")
    target = rec.get("tmux_target")
    if not socket or not target:
        sys.exit(4)

    if rec.get("idle") is False and not force_busy:
        sys.exit(5)

    quiet_s = float(os.environ.get("LA_MEMGUARD_QUIET_S", "3.0"))
    quiet_max_s = float(os.environ.get("LA_MEMGUARD_QUIET_MAX_S", "60.0"))

    start_t = _now()
    quiet_ok = False

    while True:
        now_t = _now()
        if now_t - start_t > quiet_max_s:
            break

        act_str = get_client_activity(socket, target)
        keys_quiet = False
        if not act_str:
            keys_quiet = True
        else:
            try:
                act_time = float(act_str) + 1.0
                if now_t - act_time >= quiet_s:
                    keys_quiet = True
            except ValueError:
                keys_quiet = True

        if not keys_quiet:
            sleep_dur = min(0.5, quiet_s) if quiet_s > 0 else 0.05
            _sleep(sleep_dur)
            continue

        b_start, ok_start = read_box_text(socket, target)
        if not ok_start:
            sys.exit(6)  # no input box on screen (a dialog, a pager): nothing is safe to type

        if quiet_s > 0:
            _sleep(quiet_s)

        act_str2 = get_client_activity(socket, target)
        keys_quiet2 = False
        if not act_str2:
            keys_quiet2 = True
        else:
            try:
                act_time2 = float(act_str2) + 1.0
                if _now() - act_time2 >= quiet_s:
                    keys_quiet2 = True
            except ValueError:
                keys_quiet2 = True

        if not keys_quiet2:
            continue

        b_end, ok_end = read_box_text(socket, target)
        if not ok_end:
            continue

        if same_text(b_start, b_end):
            quiet_ok = True
            break

    if not quiet_ok:
        sys.exit(3)

    b0, ok = read_box_text(socket, target)
    if not ok:
        sys.exit(6)

    stash_key = get_stash_key()
    if b0 != "" and stash_key is None:
        sys.exit(6)

    stashed = False
    if b0 != "":
        act_str_last = get_client_activity(socket, target)
        if act_str_last:
            try:
                act_time_last = float(act_str_last) + 1.0
                if _now() - act_time_last < quiet_s:
                    sys.exit(3)
            except ValueError:
                pass

        run_tmux(socket, "send-keys", "-t", target, stash_key)

        poll_start = _now()
        emptied = False
        while _now() - poll_start < 1.5:
            b_cur, ok_cur = read_box_text(socket, target)
            if ok_cur and b_cur == "":
                emptied = True
                break
            _sleep(0.1)

        if not emptied:
            b_cur, ok_cur = read_box_text(socket, target)
            if ok_cur and not same_text(b_cur, b0):
                run_tmux(socket, "send-keys", "-t", target, stash_key)
            sys.exit(6)

        stashed = True

    run_tmux(socket, "send-keys", "-t", target, "-l", text_to_send)

    poll_start = _now()
    typed_ok = False
    while _now() - poll_start < 1.5:
        b_cur, ok_cur = read_box_text(socket, target)
        if ok_cur and same_text(b_cur, text_to_send):
            typed_ok = True
            break
        _sleep(0.1)

    if not typed_ok:
        bspaces = ["BSpace"] * len(text_to_send)
        if bspaces:
            run_tmux(socket, "send-keys", "-t", target, *bspaces)
        if stashed and stash_key:
            b_cur, ok_cur = read_box_text(socket, target)
            if ok_cur and b_cur == "":
                run_tmux(socket, "send-keys", "-t", target, stash_key)
        sys.exit(6)

    run_tmux(socket, "send-keys", "-t", target, "Enter")

    restore_wait_s = float(os.environ.get("LA_MEMGUARD_RESTORE_WAIT_S", "2.0"))

    if stashed:
        poll_start = _now()
        restored = False
        while _now() - poll_start < restore_wait_s:
            b_cur, ok_cur = read_box_text(socket, target)
            if ok_cur and same_text(b_cur, b0):
                restored = True
                break
            _sleep(0.25)

        if not restored:
            b_cur, ok_cur = read_box_text(socket, target)
            if ok_cur and same_text(b_cur, b0):
                restored = True
            elif ok_cur and b_cur == "":
                run_tmux(socket, "send-keys", "-t", target, stash_key)
                _sleep(0.3)
                b_cur2, ok_cur2 = read_box_text(socket, target)
                if ok_cur2 and same_text(b_cur2, b0):
                    restored = True

        if restored:
            print("sent; draft preserved")
            sys.exit(0)
        else:
            sys.stderr.write("draft is in Claude Code's stash (Ctrl+S restores it)\n")
            sys.exit(7)
    else:
        print("sent; box was empty")
        sys.exit(0)


if __name__ == "__main__":
    main()
