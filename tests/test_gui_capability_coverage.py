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

    def test_nav_actions_carry_nav_target_matching_their_nav(self):
        # Phase 1 (continuation plan): the GUI must learn a nav row's target from METADATA,
        # never by calling run() at render time. Every nav-section action must carry
        # nav_target, and it must agree with the Nav its run() returns (tests may call run;
        # render may not).
        for target, scr in full_matrix():
            for a in scr.actions():
                if a.section not in adapter.NAV_SECTIONS:
                    continue
                sid = semantic_id(scr, a)
                with self.subTest(target=target, action=sid):
                    self.assertTrue(getattr(a, "nav_target", ""),
                                    f"nav action {sid} has no nav_target metadata")
                    res = a.run()
                    self.assertIsInstance(res, m.Nav, f"nav action {sid} run() must return a Nav")
                    self.assertEqual(a.nav_target, res.target,
                                     f"nav_target {a.nav_target!r} != run() target {res.target!r}")

    def test_nav_controls_expose_target(self):
        for target, scr in full_matrix():
            plan = {c.action_id: c for c in adapter.render_plan(scr)}
            for a in scr.actions():
                if a.section == "hidden" or a.section not in adapter.NAV_SECTIONS:
                    continue
                sid = semantic_id(scr, a)
                with self.subTest(target=target, action=sid):
                    c = plan[sid]
                    self.assertEqual(c.kind, "nav")
                    self.assertEqual(getattr(c, "target", ""), a.nav_target,
                                     "nav RenderedControl must expose the metadata target")

    def test_render_plan_refuses_nav_action_without_target(self):
        # drift barrier: a nav-section action missing nav_target must fail loudly here,
        # not silently fall back to calling run() at render time in the GUI
        class FakeScreen(m.Screen):
            def __init__(self):
                super().__init__(m.Settings())
            def actions(self):
                return [m.Action("x", "X", lambda: m.Nav("x"), section="nav")]  # no nav_target
        with self.assertRaises(ValueError):
            adapter.render_plan(FakeScreen())

    def test_render_plan_never_calls_run(self):
        # pin the contract directly: wrap every action's run() in a raiser and build the
        # whole matrix — render_plan must not touch it. actions() returns FRESH objects per
        # call (verified), so pin the instance's method to the wrapped list, otherwise the
        # raiser would wrap stale objects and the test would pass vacuously.
        for _t, scr in full_matrix():
            acts = scr.actions()
            for a in acts:
                def boom(_a=a):
                    raise AssertionError(f"render_plan called run() of {_a.key!r}")
                a.run = boom
            scr.actions = lambda: acts          # render_plan re-calls screen.actions()
            try:
                with self.subTest(screen=type(scr).__name__):
                    adapter.render_plan(scr)
            finally:
                del scr.actions                 # restore the class method on the instance

    def test_gui_render_rows_never_calls_run(self):
        # structural guard for the GUI binding: no .run() call may live inside render_rows
        # (run() at render time is the 2026-11-05 auto-sync mutation class). Click handlers
        # live in separate functions (_run_action), so an AST walk of render_rows is exact.
        import ast
        src = (Path(__file__).resolve().parents[1] / "bin" / "fa_gui" / "app.py").read_text()
        tree = ast.parse(src)
        renders = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and "render" in n.name]
        self.assertTrue(renders, "no render* functions found in app.py")
        for fn in renders:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "run":
                    self.fail(f"render_rows calls .run() at line {node.lineno} — "
                              f"render must use metadata, never run()")


if __name__ == "__main__":
    unittest.main()
