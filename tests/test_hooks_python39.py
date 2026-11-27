"""Every hook entry point must run on Python 3.9 — macOS's stock /usr/bin/python3.

Hooks are launched as bare `python3 …`, so on a machine without Homebrew/uv Python the
interpreter is the system 3.9.6. Colleagues' fresh installs (2026-11-25) hit
`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` at SessionStart
(offload-nudge.py's `dict | None` signature) and a Stop-hook crash
(`dataclass(slots=True)`). Non-hook CLIs may keep requiring 3.10+; HOOK PATHS may not.

Two layers:
  * static (never skipped): parse each hook entry and every local module it can import,
    transitively, and reject constructs that are EVALUATED at runtime and need >= 3.10;
  * runtime: execute each hook under a real 3.9 interpreter in a throwaway HOME and
    fail on any traceback. Skips (visibly) only when no 3.9 interpreter exists.
"""
import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
LOCAL_DIRS = (ROOT / "bin", ROOT / "hooks")
SCRIPT_RE = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/(\S+?\.(?:py|sh))")
SH_PY_RE = re.compile(r"([A-Za-z0-9_-]+\.py)\b")


def hook_commands():
    """(event, command) for every hook in hooks.json."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    out = []
    for event, matchers in data["hooks"].items():
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                out.append((event, hook.get("command", "")))
    return out


def hook_entry_files():
    """Python files a hook runs directly, or that a shell hook hands to python."""
    files = set()
    for _event, cmd in hook_commands():
        for rel in SCRIPT_RE.findall(cmd):
            path = ROOT / rel
            if path.suffix == ".py":
                files.add(path)
            elif path.suffix == ".sh" and path.is_file():
                for name in SH_PY_RE.findall(path.read_text(encoding="utf-8")):
                    for d in LOCAL_DIRS:
                        if (d / name).is_file():
                            files.add(d / name)
    return files


def _local_module(name):
    for d in LOCAL_DIRS:
        cand = d / (name.split(".")[0] + ".py")
        if cand.is_file():
            return cand
    return None


def hook_closure():
    """Entry files plus every LOCAL module importable from them (imports inside
    functions count: they run on the hook path too)."""
    seen, todo = set(), list(hook_entry_files())
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for n in names:
                mod = _local_module(n)
                if mod and mod not in seen:
                    todo.append(mod)
    return seen


def _is_pep604(node):
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)


def py310_runtime_constructs(path):
    """Constructs that a 3.9 interpreter rejects or crashes on AT RUNTIME."""
    src = path.read_text(encoding="utf-8")
    problems = []
    try:
        ast.parse(src, feature_version=(3, 9))
    except SyntaxError as exc:
        problems.append("%s:%s syntax needs >= 3.10 (%s)" % (path.name, exc.lineno, exc.msg))
    tree = ast.parse(src)
    future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                 and any(a.name == "annotations" for a in n.names) for n in tree.body)
    for node in ast.walk(tree):
        if not future:
            annots = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                annots = [a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs
                          + [args.vararg, args.kwarg] if a is not None and a.annotation]
                if node.returns is not None:
                    annots.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                annots = [node.annotation]
            for ann in annots:
                if any(_is_pep604(sub) for sub in ast.walk(ann)):
                    problems.append("%s:%s 'X | Y' annotation is evaluated at runtime "
                                    "(needs 3.10; quote it or add `from __future__ import "
                                    "annotations`)" % (path.name, ann.lineno))
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname == "dataclass":
                for kw in node.keywords:
                    if kw.arg in ("slots", "kw_only", "match_args"):
                        problems.append("%s:%s dataclass(%s=) needs 3.10"
                                        % (path.name, node.lineno, kw.arg))
            if fname == "zip" and any(kw.arg == "strict" for kw in node.keywords):
                problems.append("%s:%s zip(strict=) needs 3.10" % (path.name, node.lineno))
            if isinstance(fn, ast.Name) and fn.id == "isinstance" and len(node.args) == 2 \
                    and _is_pep604(node.args[1]):
                problems.append("%s:%s isinstance(x, A | B) needs 3.10"
                                % (path.name, node.lineno))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) \
                else [node.module or ""]
            if "tomllib" in mods:
                problems.append("%s:%s tomllib needs 3.11" % (path.name, node.lineno))
    return problems


def test_hook_closure_is_not_empty():
    """An empty closure would let the static check pass vacuously."""
    closure = hook_closure()
    names = {p.name for p in closure}
    assert "offload-nudge.py" in names and "local-queue-stop-hook.py" in names, names
    assert len(closure) >= 8, sorted(names)


@pytest.mark.parametrize("path", sorted(hook_closure()), ids=lambda p: p.name)
def test_hook_path_has_no_py310_runtime_constructs(path):
    assert py310_runtime_constructs(path) == []


def test_static_check_catches_a_planted_positive(tmp_path):
    """Negative control: the checker must flag each construct it claims to."""
    bad = tmp_path / "bad.py"
    bad.write_text("from dataclasses import dataclass\n"
                   "def f(x: dict | None = None) -> int | None: ...\n"
                   "@dataclass(slots=True)\nclass C: pass\n"
                   "y: str | None = None\n", encoding="utf-8")
    found = py310_runtime_constructs(bad)
    assert len(found) == 4, found
    ok = tmp_path / "ok.py"
    ok.write_text("from __future__ import annotations\n"
                  "def f(x: dict | None = None) -> 'int | None': ...\n", encoding="utf-8")
    assert py310_runtime_constructs(ok) == []


def _python39():
    for cand in ("/usr/bin/python3", shutil.which("python3.9")):
        if not cand or not os.path.exists(cand):
            continue
        out = subprocess.run([cand, "-c", "import sys; print(sys.version_info[:2])"],
                             capture_output=True, text=True)
        if out.stdout.strip() == "(3, 9)":
            return cand
    return None


PY39 = _python39()


@pytest.mark.skipif(PY39 is None, reason="no Python 3.9 interpreter on this machine "
                    "(the static check above still guards the hook paths)")
@pytest.mark.parametrize("event,command", hook_commands(),
                         ids=lambda v: v if isinstance(v, str) and len(v) < 30 else None)
def test_hook_runs_under_python39_without_traceback(event, command, tmp_path):
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").symlink_to(PY39)
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps({"session_id": "py39-test", "hook_event_name": event,
                          "source": "startup", "cwd": str(home),
                          "transcript_path": str(home / "none.jsonl"),
                          "tool_name": "Read", "tool_input": {"file_path": str(home / "x")},
                          "prompt": "hi", "stop_hook_active": False})
    env = {"HOME": str(home), "PATH": "%s:/usr/bin:/bin" % shim,
           "CLAUDE_PLUGIN_ROOT": str(ROOT), "TMPDIR": str(tmp_path)}
    proc = subprocess.run(["/bin/bash", "-c", command], input=payload, env=env,
                          capture_output=True, text=True, timeout=60)
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined and "TypeError" not in combined, combined[-2000:]
