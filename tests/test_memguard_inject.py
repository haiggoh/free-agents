import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
import pytest

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
INJECT_PY = BIN_DIR / "la-memguard-inject.py"


def create_fake_tmux(tmp_path):
    faketmux_path = tmp_path / "faketmux"
    code = '''#!/usr/bin/env python3
import sys, os, json, time

state_file = os.environ.get("FAKE_TMUX_STATE")
if not state_file or not os.path.exists(state_file):
    sys.exit(1)

with open(state_file, "r", encoding="utf-8") as f:
    state = json.load(f)

now = time.time()
typing_until = state.get("typing_until")
if typing_until and now < typing_until:
    state["box"] = state.get("box", "") + "x"
    state["activity"] = int(now)

# restore_late: Claude Code puts the draft back only after N more screen reads
if "restore_in" in state and (sys.argv[3:4] == ["capture-pane"] or "capture-pane" in sys.argv):
    state["restore_in"] -= 1
    if state["restore_in"] <= 0:
        state["box"] = state.pop("late_box"); del state["restore_in"]

args = sys.argv[1:]
if args and args[0] == "-S":
    sock = args[1]
    args = args[2:]

cmd = args[0] if args else ""

def save():
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f)

if cmd == "capture-pane":
    if state.get("noprompt"):
        print("filler line 1")
        print("filler line 2")
    else:
        print("filler line 1")
        print("─" * 40)
        box = state.get("box", "")
        if not box:
            print("❯   ")
        else:
            lines = box.split("\\n")
            print("❯ " + lines[0])
            for l in lines[1:]:
                print("  " + l)
        print("─" * 40)
        print("status line")
elif cmd == "display":
    state["displays"] = state.get("displays", 0) + 1
    if state.get("always_fresh"):
        state["activity"] = int(now)
    if state.get("fresh_from_display") and state["displays"] >= state["fresh_from_display"]:
        state["activity"] = int(now)
    act = state.get("activity")
    if act is not None:
        print(str(act))
    else:
        print("")
elif cmd == "send-keys":
    idx = 1
    if idx < len(args) and args[idx] == "-t":
        idx += 2

    if idx < len(args) and args[idx] == "-l":
        text = args[idx + 1]
        state.setdefault("keys", []).append(("-l", text))
        state["box"] = state.get("box", "") + text
    else:
        stash_key = os.environ.get("FAKE_STASH_KEY", "C-s")
        keys_sent = args[idx:]
        for key in keys_sent:
            state.setdefault("keys", []).append(key)
            if key == stash_key:
                cur_box = state.get("box", "")
                if cur_box:
                    state.setdefault("stash", []).append(cur_box)
                    state["box"] = ""
                else:
                    st = state.get("stash", [])
                    if st:
                        state["box"] = st.pop()
                    else:
                        state["box"] = ""
            elif key == "Enter":
                cur_box = state.get("box", "")
                state.setdefault("submitted", []).append(cur_box)
                st = state.get("stash", [])
                if "after_enter_box" in state:
                    state["box"] = state["after_enter_box"]
                elif state.get("restore_late") and st:
                    state["late_box"] = st.pop(); state["restore_in"] = state["restore_late"]
                    state["box"] = ""
                elif state.get("autorestore", True) and st:
                    state["box"] = st.pop()
                else:
                    state["box"] = ""
            elif key == "BSpace":
                cur_box = state.get("box", "")
                if cur_box:
                    state["box"] = cur_box[:-1]

save()
'''
    faketmux_path.write_text(code)
    faketmux_path.chmod(faketmux_path.stat().st_mode | stat.S_IEXEC)
    return faketmux_path


def setup_test_env(tmp_path, session_id="s1", idle=True, initial_state=None):
    state_dir = tmp_path / "state"
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    sess_file = sessions_dir / f"{session_id}.json"
    sess_record = {
        "id": session_id,
        "tmux_socket": str(tmp_path / "s.sock"),
        "tmux_target": "t",
        "idle": idle,
    }
    sess_file.write_text(json.dumps(sess_record))

    fake_state_file = tmp_path / "fake_state.json"
    state_data = {
        "box": "",
        "stash": [],
        "activity": None,
        "autorestore": True,
        "submitted": [],
        "keys": [],
    }
    if initial_state:
        state_data.update(initial_state)
    fake_state_file.write_text(json.dumps(state_data))

    faketmux = create_fake_tmux(tmp_path)

    env = os.environ.copy()
    env["LA_MEMGUARD_STATE_DIR"] = str(state_dir)
    env["LA_MEMGUARD_TMUX_BIN"] = str(faketmux)
    env["LA_MEMGUARD_QUIET_S"] = "0.2"
    env["LA_MEMGUARD_QUIET_MAX_S"] = "1.5"
    env["LA_MEMGUARD_RESTORE_WAIT_S"] = "0.5"
    env["FAKE_TMUX_STATE"] = str(fake_state_file)

    return env, fake_state_file


def run_injector(env, *args):
    cmd = [sys.executable, str(INJECT_PY)] + list(args)
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc


def test_unit_helpers():
    spec = importlib.util.spec_from_file_location("memguard_inject", INJECT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sample_output = "filler\n────────────────────────────────────────\n❯ my draft\n────────────────────────────────────────\n"
    text, ok = mod.parse_box_from_pane(sample_output)
    assert ok is True
    assert text == "my draft"


def test_1_empty_box(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": ""})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0
    assert "sent; box was empty" in res.stdout

    with open(state_file, "r") as f:
        st = json.load(f)
    assert st["keys"] == [["-l", "/compact R"], "Enter"]
    assert st["submitted"] == ["/compact R"]


def test_2_draft_autorestore(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "autorestore": True})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0
    assert "sent; draft preserved" in res.stdout

    with open(state_file, "r") as f:
        st = json.load(f)
    assert st["submitted"] == ["/compact R"]
    assert st["box"] == "my draft"


def test_3_draft_no_autorestore(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "autorestore": False})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0
    assert "sent; draft preserved" in res.stdout

    with open(state_file, "r") as f:
        st = json.load(f)
    assert st["submitted"] == ["/compact R"]
    assert st["box"] == "my draft"
    stash_presses = sum(1 for k in st["keys"] if k == "C-s")
    assert stash_presses == 2


def test_4_user_typing(tmp_path):
    env, state_file = setup_test_env(
        tmp_path, initial_state={"box": "", "typing_until": time.time() + 10.0}
    )
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 3

    with open(state_file, "r") as f:
        st = json.load(f)
    assert st["keys"] == []


def test_5_recent_activity_becomes_quiet(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "", "activity": int(time.time())})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0


def test_6_busy_session_and_missing_record(tmp_path):
    env, state_file = setup_test_env(tmp_path, session_id="s1", idle=False)
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 5

    res_missing = run_injector(env, "nonexistent", "/compact R")
    assert res_missing.returncode == 4


def test_7_unknown_screen_state(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"noprompt": True})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 6

    with open(state_file, "r") as f:
        st = json.load(f)
    assert st["keys"] == []


def test_8_keybindings_remap(tmp_path):
    kb_file = tmp_path / "kb.json"
    kb_file.write_text(json.dumps({"bindings": {"ctrl+g": "chat:stash"}}))

    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft"})
    env["LA_MEMGUARD_KEYBINDINGS"] = str(kb_file)
    env["FAKE_STASH_KEY"] = "C-g"

    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0

    with open(state_file, "r") as f:
        st = json.load(f)
    assert "C-g" in st["keys"]
    assert "C-s" not in st["keys"]


def test_9_help_and_usage():
    env = os.environ.copy()
    res = subprocess.run(
        [sys.executable, str(INJECT_PY), "--help"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode == 0
    assert "Usage:" in res.stdout
    for code in ["0:", "2:", "3:", "4:", "5:", "6:", "7:"]:
        assert code in res.stdout

    res_wrong = subprocess.run(
        [sys.executable, str(INJECT_PY)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res_wrong.returncode == 2


def test_10_discriminant_stash_order_and_count(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "autorestore": True})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 0

    with open(state_file, "r") as f:
        st = json.load(f)

    keys = st["keys"]
    stash_idx = keys.index("C-s")
    literal_idx = keys.index(["-l", "/compact R"])
    enter_idx = keys.index("Enter")

    assert stash_idx < literal_idx < enter_idx

    stash_presses = sum(1 for k in keys if k == "C-s")
    assert stash_presses == 1


def _parse(screen):
    spec = importlib.util.spec_from_file_location("memguard_inject_p", INJECT_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m.parse_box_from_pane(screen)


def test_history_prompt_lines_are_not_the_input_box():
    """Earlier prompts in the transcript also start with the prompt glyph; only the line under a rule is the box."""
    rule = "\u2500" * 40
    screen = f"top\n\u276f an old prompt\n  answer text\n{rule}\n\u276f \n{rule}\n  status\n"
    assert _parse(screen) == ("", True)
    assert _parse("top\n\u276f only history, no box\n  answer\n") == ("", False)


def test_soft_wrapped_draft_compares_equal():
    spec = importlib.util.spec_from_file_location("memguard_inject_w", INJECT_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.same_text("a long draft that\nwraps", "a long draft that wraps")
    assert not m.same_text("draft", "draft!")


def _keys(state_file):
    return json.loads(Path(state_file).read_text())


def test_keystroke_in_the_last_moment_aborts_before_any_key(tmp_path):
    """Quiet during the window, then a key arrives right before the stash: nothing may be sent."""
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "fresh_from_display": 3})
    res = run_injector(env, "s1", "/compact R")
    st = _keys(state_file)
    assert res.returncode == 3, res.stderr
    assert st.get("keys", []) == [] and st["box"] == "my draft"


def test_late_restore_is_waited_for_and_never_restashed(tmp_path):
    """Claude Code restores the draft late: the injector must not press stash on the restored box."""
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "restore_late": 2})
    res = run_injector(env, "s1", "/compact R")
    st = _keys(state_file)
    assert st["box"] == "my draft", st
    assert st["submitted"] == ["/compact R"]
    assert st["keys"].count("C-s") == 1
    assert res.returncode == 0, res.stderr


def test_fresh_keystrokes_alone_block_even_when_the_box_is_unchanged(tmp_path):
    """Keys still arriving (e.g. cursor moves) while the text does not change: still typing."""
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "always_fresh": True})
    res = run_injector(env, "s1", "/compact R")
    assert res.returncode == 3
    assert _keys(state_file).get("keys", []) == []


def test_restore_seen_only_on_the_final_read_counts_as_preserved(tmp_path):
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "restore_late": 1})
    env["LA_MEMGUARD_RESTORE_WAIT_S"] = "0"
    res = run_injector(env, "s1", "/compact R")
    st = _keys(state_file)
    assert res.returncode == 0, res.stderr
    assert st["keys"].count("C-s") == 1 and st["box"] == "my draft"


def test_unexpected_box_after_enter_is_never_stashed_over(tmp_path):
    """After Enter the box holds something that is neither empty nor the draft: hands off, report it."""
    env, state_file = setup_test_env(tmp_path, initial_state={"box": "my draft", "after_enter_box": "other"})
    res = run_injector(env, "s1", "/compact R")
    st = _keys(state_file)
    assert res.returncode == 7
    assert st["keys"].count("C-s") == 1 and st["box"] == "other"
