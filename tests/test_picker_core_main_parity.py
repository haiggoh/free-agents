"""tests/test_picker_core_main_parity.py — the extracted core must not drift from csl's own copy.

session_picker.py (csl, the TUI) still defines the loaders/verdicts/TOOLS it also imports from
picker_core, so csl runs ITS copy while the NiceGUI frontend runs picker_core's. Measured
2026-11-25 after the rebase onto 0.63.2: six shared names had silently gone stale in picker_core
(no context/subdir columns, no limits facts, no rl-lane tool, pre-0.55.8 HF warm-up). These
tests pin the two copies together so a rebase or a main-side edit cannot drop a change again.
"""
import ast
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import picker_core as pc  # noqa: E402


def _top_level(path: Path) -> dict:
    src = path.read_text()
    out = {}
    for node in ast.parse(src).body:
        name = getattr(node, "name", None) or next(
            (t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)), None)
        if name:
            out[name] = ast.get_source_segment(src, node)
    return out


def _unwire(code: str) -> str:
    # picker_core routes collaborator calls through _patchable('<name>') (patch visibility for
    # tests that monkeypatch session_picker.<name>); that wiring is the ONLY allowed difference
    return re.sub(r"_patchable\('(\w+)'\)", r"\1", code)


class SharedBodiesMatchCsl(unittest.TestCase):
    def test_every_shared_name_has_csls_body(self):
        core = _top_level(BIN / "picker_core.py")
        csl = _top_level(BIN / "session_picker.py")
        shared = sorted(set(core) & set(csl))
        self.assertGreaterEqual(len(shared), 20, f"comparison found too few shared names: {shared}")
        drifted = [n for n in shared if _unwire(core[n]) != csl[n]]
        self.assertEqual(drifted, [], f"picker_core bodies drifted from session_picker.py: {drifted}")


class EveryDispatchedToolRowHasACommand(unittest.TestCase):
    # tool: targets that open a SCREEN or a typed PROMPT instead of running a command
    NOT_DISPATCHED = {"tool:download", "tool:lowkey", "tool:rl-reset"}

    def test_each_tool_nav_target_resolves_to_argv(self):
        # csl imports TOOLS from picker_core (session_picker.py has no copy), so a row whose
        # target is missing here is dead in BOTH frontends (measured: tool:rl-lane, 0.63.0)
        src = (BIN / "session_picker_model.py").read_text()
        targets = sorted(set(re.findall(r'nav_target="(tool:[a-z0-9_-]+(?::[a-z0-9_-]+)*)"', src))
                         - self.NOT_DISPATCHED)
        self.assertGreaterEqual(len(targets), 5, f"too few tool targets found: {targets}")
        dead = [t for t in targets if not pc.tool_argv(t, "rapid-mlx")]
        self.assertEqual(dead, [], f"tool rows with no command: {dead}")


class LoaderDeliversMainColumns(unittest.TestCase):
    def test_inventory_context_and_subdir_columns_reach_the_gui_loader(self):
        row = "qwen-fast\trapid\tmedium\tauto-scanned\tQwen3-30B\t262144\tqwen3-30b-a3b\n"
        fake = mock.Mock(return_value=mock.Mock(stdout=row, returncode=0))
        # session_picker absent => _patchable resolves picker_core's own global (the GUI's path)
        with mock.patch.dict(sys.modules, {"session_picker": None}), \
                mock.patch.object(pc, "_run", fake):
            models = pc.load_local_models()
        self.assertEqual(len(models), 1)
        self.assertEqual((models[0].context, models[0].subdir), (262144, "qwen3-30b-a3b"))


if __name__ == "__main__":
    unittest.main()
