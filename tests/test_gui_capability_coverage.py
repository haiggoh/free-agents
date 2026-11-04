"""tests/test_gui_capability_coverage.py — nicegui plan Task 7: a lightweight capability
coverage check — every action of every screen is either rendered by fa_gui.adapter.render_plan
or explicitly pending in GUI_PENDING. A common import alone does not prove linkage."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m  # noqa: E402
import picker_core as pc  # noqa: E402

# adapter must be importable WITHOUT nicegui installed (pure render plan)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "fa_gui"))
import adapter  # noqa: E402


def full_matrix():
    s = m.Settings()
    s.last_launched_model = {"local_session": "qwen-3.8-operator",
                             "remote_api_session": "nvidia-a", "lowkey": None}
    s.hw_hardware = "mlx"
    cache = {"local": [m.LocalModel("qwen-3.8-operator", family="qwen")],
             "remote": [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown",
                                      model_id="nvidia-a")]}
    targets = ["home", "about", "local", "remote", "lowkey", "download", "rate_limiter",
               "memguard", "runtime_manager", "runtime", "runtime_launchd", "api_keys",
               "runtime_versions:promote", "remote_filters", "cloud_config", "theme_config"]
    return [(t, pc.build_screen(m.Nav(t, m.HOME_OWNED), s, dict(cache)).screen) for t in targets]


def semantic_id(screen, a) -> str:
    return a.id or f"{type(screen).__name__}.{a.key}"


class CapabilityCoverage(unittest.TestCase):
    def test_every_action_rendered_or_pending(self):
        # hidden-section rows never render and are never pending (they duplicate a rendered
        # action, e.g. the lane shortcut) — exactly the rendered_actions rule in the model
        for target, scr in full_matrix():
            rendered = {c.action_id for c in adapter.render_plan(scr)}
            for a in scr.actions():
                sid = semantic_id(scr, a)
                if a.section == "hidden":
                    with self.subTest(target=target, action=sid):
                        self.assertNotIn(sid, rendered, "hidden rows must not render")
                    continue
                with self.subTest(target=target, action=sid):
                    self.assertTrue(sid in rendered or sid in adapter.GUI_PENDING,
                                    f"{sid} neither rendered nor pending")

    def test_pending_entries_still_exist(self):
        # planted negative: a GUI_PENDING id no screen emits must fail this test
        emitted = set()
        for _t, scr in full_matrix():
            emitted |= {c.action_id for c in adapter.render_plan(scr)}
        for pid in adapter.GUI_PENDING:
            with self.subTest(pending=pid):
                self.assertIn(pid, emitted, f"stale pending id {pid}")

    def test_typed_setting_rendered_as_select_not_button(self):
        for _t, scr in full_matrix():
            for a in scr.actions():
                if a.section == "hidden":
                    continue
                if a.choices is not None:
                    sid = semantic_id(scr, a)
                    kinds = [c.kind for c in adapter.render_plan(scr) if c.action_id == sid]
                    with self.subTest(action=sid):
                        self.assertTrue(kinds, f"{sid} not rendered")
                        self.assertIn(kinds[0], ("select", "toggle"),
                                      f"typed row {sid} must be select/toggle, got {kinds[0]}")


if __name__ == "__main__":
    unittest.main()
