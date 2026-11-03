#!/usr/bin/env python3
"""tests/test_picker_lowkey_api_dedupe_0505.py — 0.50.8: the Lowkey API chooser must not
crash with DuplicateID when the roster lists two aliases for one model.

Root cause (crash 2026-11-02, csl on Lowkey -> "Choose API model…"): the registry still
carries -thinking aliases next to the base alias with the SAME model_id (e.g.
nvidia-glm53-thinking + nvidia-glm53 -> z-ai/glm-5.3-flash). RemoteScreen._regroup already
collapses those to one row per model (0.47.4, user 2026-10-29: one entry per model), but
LowkeyScreen.set_models built the api group straight from api_agents, so two Item ids
"nvidia:z-ai/glm-5.3-flash" hit OptionList.add_option and the whole picker died with
DuplicateID. Fix: set_models applies the same one-row-per-model rule at the choke point
(it is called both from __init__ and from the picker's _loaded handler).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402


def dup_agents():
    """The registry's nvidia-glm53 pair: same provider, same model_id, two aliases."""
    return [m.RemoteAgent("nvidia-glm53-thinking", "nvidia", "Z.ai GLM 5.3 Flash (thinking)",
                          "unknown", model_id="z-ai/glm-5.3-flash"),
            m.RemoteAgent("nvidia-glm53", "nvidia", "Z.ai GLM 5.3 Flash", "unknown",
                          model_id="z-ai/glm-5.3-flash"),
            m.RemoteAgent("nvidia-nemotron-ultra", "nvidia", "Nemotron 3 Ultra", "unknown",
                          model_id="nvidia/nemotron-3-ultra-550b-a55b")]


class LowkeyApiDedupeTests(unittest.TestCase):
    def test_api_group_has_one_item_per_model_id(self):
        lk = m.LowkeyScreen(m.Settings(), [], api_agents=dup_agents())
        api = next(g for g in lk.groups() if g.id == "api")
        ids = [i.id for i in api.items]
        # two registry rows for z-ai/glm-5.3-flash collapse to ONE row — the crash was
        # OptionList DuplicateID on the second "i:nvidia:z-ai/glm-5.3-flash"
        # (item id = f"{provider}:{model_id}"; nvidia model ids already carry the prefix)
        self.assertEqual(ids, ["nvidia:z-ai/glm-5.3-flash", "nvidia:nvidia/nemotron-3-ultra-550b-a55b"])

    def test_base_alias_represents_the_model(self):
        # same rule as RemoteScreen._regroup (0.47.4): the non--thinking variant wins
        lk = m.LowkeyScreen(m.Settings(), [], api_agents=dup_agents())
        api = next(g for g in lk.groups() if g.id == "api")
        self.assertEqual(api.items[0].label, "Z.ai GLM 5.3 Flash (nvidia)")

    def test_all_accordion_item_ids_unique(self):
        # the render-path invariant render_model relies on: OptionList forbids duplicate
        # ids, so every item id across ALL groups must be unique
        lk = m.LowkeyScreen(m.Settings(), [], api_agents=dup_agents())
        lk._toggle_choose_api()
        ids = [i.id for kind, obj in lk.accordion.rows() if kind == "item" for i in [obj]]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate item ids: {ids}")

    def test_set_models_dedupes_when_called_again(self):
        # the picker's _loaded handler calls set_models a second time when the remote
        # inventory lands — the dedupe must live in set_models, not only in __init__
        lk = m.LowkeyScreen(m.Settings(), [])
        lk.api_agents = dup_agents()
        lk.set_models([])
        api = next(g for g in lk.groups() if g.id == "api")
        self.assertEqual([i.id for i in api.items],
                         ["nvidia:z-ai/glm-5.3-flash", "nvidia:nvidia/nemotron-3-ultra-550b-a55b"])


class LowkeyApiRenderTests(unittest.TestCase):
    """Drive the real Picker.render_model with a duplicated roster row — this is the exact
    crash path (on_option_list_option_selected -> _activate_action -> _rerender_keep_action
    -> render_model -> OptionList.add_option DuplicateID). Needs the picker venv."""

    def test_choose_api_renders_without_duplicate_id(self):
        try:
            from picker_harness import PilotCase
        except ImportError:
            self.skipTest("picker harness unavailable")

        class Case(PilotCase):
            def runTest(self):
                pass

        # remote stub whose roster carries the -thinking duplicate
        dup_inv = ("nvidia-glm53-thinking\tnvidia\tZ.ai GLM 5.3 Flash (thinking)\tunknown\t0\t1\t0\tz-ai/glm-5.3-flash\n"
                   "nvidia-glm53\tnvidia\tZ.ai GLM 5.3 Flash\tunknown\t0\t1\t0\tz-ai/glm-5.3-flash\n"
                   "nvidia-nemotron-ultra\tnvidia\tNemotron 3 Ultra\tunknown\t0\t1\t0\tnvidia/nemotron-3-ultra-550b-a55b\n")

        case = Case()
        case.setUp()
        self.addCleanup(case._restore_env)
        try:
            from picker_harness import inventory_stub
            stub = Path(case.env["CSL_REMOTE_LAUNCHER"])
            stub.write_text(inventory_stub(dup_inv))

            async def scenario(app, pilot, h):
                await h.press("c")          # 0.52.0: "Choose model 📡 🦾…" opens the combined list
                await h.goto_row("group", "api")
                await h.press("right")      # expand the API group
                await pilot.pause()
                app.render_model()          # explicit re-render: raised DuplicateID pre-fix
                rows = app.query_one("#rows")
                ids = [rows.get_option_at_index(i).id for i in range(rows.option_count)]
                self.assertEqual(len(ids), len(set(ids)), f"duplicate option ids: {ids}")
                api_items = [i for i in ids if i.startswith("i:nvidia:")]
                self.assertEqual(api_items, ["i:nvidia:z-ai/glm-5.3-flash",
                                             "i:nvidia:nvidia/nemotron-3-ultra-550b-a55b"])

            case.run_picker(scenario, start="lowkey")
        finally:
            pass


if __name__ == "__main__":
    unittest.main()
