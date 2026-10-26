"""tests/test_venv_claims_bottle_backends.py — a Homebrew bottle must not promise a venv path.

WHY THIS EXISTS: `install-backend.sh --backend <bottle> --dry-run` reported
`"target": "~/.venvs/<name>-<ver>"` for backends that install a Homebrew bottle — a path that is
never created, because the real install runs brew. Found by dogfooding the installed free-agents
0.45.0, which reported `~/.venvs/moespresso-3.0.0`.

The base class's `target_for` comes from VenvMixin; a bottle manager must override it. Only
rapid-mlx did (it really does install venvs), so every bottle backend inherited the wrong answer.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

# Importing each module self-registers it via @register_manager (the package __init__ registers
# nothing on its own — manage-backend.py does the explicit imports).
import managers.moespresso  # noqa: F401,E402
import managers.omlx  # noqa: F401,E402
import managers.llama_cpp  # noqa: F401,E402
import managers.rapid_mlx  # noqa: F401,E402
import managers.vllm_mlx  # noqa: F401,E402
from managers import list_managers  # noqa: E402


def _brew(argv, stdout="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def test_every_bottle_backend_refuses_to_claim_a_venv():
    """The bug, stated once: no bottle backend's target may live under .venvs/."""
    # llama.cpp is deliberately NOT here: the manager DETECTS a Homebrew install (it is in
    # get_installed_versions, and this machine has brew llama.cpp 0.7.0) but only INSTALLS from a
    # GitHub release into ~/.local/llama-cpp/<version>. That mismatch is a known gap the user
    # asked about on 2026-10-26 and is tracked as its own change — so its target path is neither
    # the venv claim nor a bottle path yet, and asserting either here would be wrong.
    bottle_backends = {"omlx", "moespresso"}
    assert bottle_backends <= set(list_managers()), (
        f"these bottle backends must be registered: {bottle_backends - set(list_managers())}")
    for name in sorted(bottle_backends):
        mgr = {"omlx": __import__("managers.omlx", fromlist=["x"]).OMLXManager,
               "moespresso": __import__("managers.moespresso", fromlist=["x"]).MoespressoManager,
               }[name]()
        # omlx has no resolve_binary(); patch only what this manager actually has.
        ctx = [patch.object(mgr, "run_command", return_value=_brew([], "🍺")),
               patch("pathlib.Path.exists", return_value=False)]
        if hasattr(mgr, "resolve_binary"):
            ctx.append(patch.object(mgr, "resolve_binary", return_value=None))
        import contextlib
        with contextlib.ExitStack() as stack:
            for c in ctx:
                stack.enter_context(c)
            target = str(mgr.target_for("9.9.9"))
        assert ".venvs/" not in target, (
            f"{name} installs a Homebrew bottle, but its dry run claims {target}")


def test_moespresso_names_the_real_bottle_path():
    """The one backend we can name concretely, pinned so the shape is visible."""
    from managers.moespresso import MoespressoManager
    mgr = MoespressoManager()
    with patch.object(mgr, "resolve_binary", return_value=Path("/opt/homebrew/Cellar/moespresso@3/3.0.0/bin/moespresso")):
        assert str(mgr.target_for("3.0.0")) == "/opt/homebrew/Cellar/moespresso@3/3.0.0/bin/moespresso"
    with patch.object(mgr, "resolve_binary", return_value=None), \
         patch("pathlib.Path.exists", return_value=False):
        target = str(mgr.target_for("3.0.0"))
    assert "moespresso" in target and "/.venvs/" not in target, target


def test_venv_backends_keep_their_venv_targets():
    """The complement: rapid-mlx and vllm-mlx really do install venvs; they must not change."""
    for name in ("rapid-mlx", "vllm-mlx"):
        assert name in list_managers(), name
    from managers.rapid_mlx import RapidMLXManager
    mgr = RapidMLXManager()
    with patch("pathlib.Path.home", return_value=Path("/tmp/fakehome")):
        t = str(mgr.target_for("0.16.1"))
    assert "/.venvs/rapid-mlx-0.16.1" in t, (
        f"rapid-mlx installs a venv — its target must still say so: {t}")
