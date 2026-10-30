#!/usr/bin/env python3
"""tests/test_picker_ui_0472.py — the 0.47.2 picker UI changes, pure model logic (no terminal).

Covers: version line split from the subheadline, platform-aware subheadline (Linux CUDA +
distro via la-hw-detect values, fail-open fallbacks), the About emoji from the canonical
file, the settings harmonica group in both lane pickers, largest-first size sort with the
hand-curated nvidia exemption, param size in row names (de-duplicated), and the redundant
"· catalog" tag removal. Each test names the user requirement that breaks it."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402
import emoji_constants as ec  # noqa: E402


def remote_agents():
    return [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown"),
            m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free")]


class VersionLineTests(unittest.TestCase):
    """User 2026-10-29: the version must not sit directly below the subheadline in the same
    formatting — it renders as its own #version widget in its own colour."""

    def test_home_policy_has_no_version_and_version_line_carries_it(self):
        home = m.HomeScreen(m.Settings())
        self.assertNotIn("free-agents v", home.policy)
        self.assertEqual(home.version_line, f"free-agents v{m.plugin_version()}")

    def test_other_screens_have_empty_version_line(self):
        self.assertEqual(m.RemoteScreen(m.Settings(), remote_agents()).version_line, "")


class IdentitySubheadlineTests(unittest.TestCase):
    """User 2026-10-29: the subheadline is NOT hardcoded — Linux users get the CUDA line,
    distro-named when the detector (la-hw-detect.sh) or os-release provides it."""

    def test_linux_names_distro_and_cuda(self):
        with tempfile.NamedTemporaryFile("w", suffix=".release", delete=False) as fh:
            fh.write('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n')
            path = fh.name
        try:
            line = m._identity_subheadline(system="Linux", os_release_path=path)
        finally:
            os.unlink(path)
        self.assertIn("Ubuntu 24.04.3 LTS", line)
        self.assertIn("CUDA", line)
        self.assertNotIn("this Mac", line)

    def test_distro_from_detector_wins_over_os_release(self):
        line = m._identity_subheadline(system="Linux", distro="Fedora Linux 40")
        self.assertIn("Fedora Linux 40", line)
        self.assertIn("CUDA", line)

    def test_linux_without_os_release_falls_back_without_raising(self):
        line = m._identity_subheadline(system="Linux", os_release_path="/nonexistent/os-release")
        self.assertIn("CUDA", line)
        self.assertIn("Linux PC", line)

    def test_darwin_keeps_mac_wording(self):
        self.assertIn("this Mac", m._identity_subheadline(system="Darwin"))

    def test_local_backend_word(self):
        self.assertEqual(m._local_backend_word("mlx"), "MLX")
        self.assertEqual(m._local_backend_word("cuda"), "CUDA")
        self.assertEqual(m._local_backend_word(None, "darwin"), "MLX")
        self.assertEqual(m._local_backend_word(None, "linux"), "CUDA")


class AboutEmojiTests(unittest.TestCase):
    """User 2026-10-29: the About section's emoji comes from the canonical file
    (bin/emoji_constants.py), not a literal."""

    def test_about_title_uses_canonical_info_emoji(self):
        self.assertTrue(m.AboutScreen.title.startswith(ec.EMOJI_INFO_STR))


class SettingsGroupTests(unittest.TestCase):
    """Waypoint picker-collapsed-settings-group / user 2026-10-29: settings rows fold into
    ONE collapsed group in both lane pickers; launch-last and choose-model stay top-level."""

    def test_lane_layout_two_groups_nav_last(self):
        # user 2026-10-30: Model group (effort/temp/thinking) open by default, Advanced
        # group folded, nav ("b"/"q") always the LAST row.
        lane = m.RemoteScreen(m.Settings(), remote_agents())
        rendered, hidden = m.rendered_actions(lane)
        self.assertIn(m.MODEL_GROUP, rendered)
        self.assertIn(m.SETTINGS_GROUP, rendered)
        self.assertTrue(lane.model_collapsed)           # model settings folded by default too (user 2026-10-30)
        self.assertTrue(lane.settings_collapsed)        # advanced folded by default
        self.assertIn("e", {a.key for a in hidden})     # effort folded into Model settings
        self.assertTrue(all(a.section in ("settings", "model") for a in hidden))
        # lane switch sits at the end, right before nav (user 2026-10-30)
        self.assertEqual(rendered[-1].section, "nav")
        self.assertEqual(rendered[-2].section, "lanes")
        self.assertIn("c", {a.key for a in rendered})   # choose model stays top-level

    def test_local_lane_same_layout(self):
        lane = m.LocalScreen(m.Settings(), [m.LocalModel(alias="x", effort="medium", roles="")])
        rendered, hidden = m.rendered_actions(lane)
        self.assertIn(m.MODEL_GROUP, rendered)
        self.assertIn(m.SETTINGS_GROUP, rendered)
        self.assertEqual(rendered[-1].section, "nav")
        self.assertIn("c", {a.key for a in rendered})

    def test_toggle_expands_both_groups(self):
        lane = m.RemoteScreen(m.Settings(), remote_agents())
        lane.toggle_model_group()
        lane.toggle_settings()
        rendered, hidden = m.rendered_actions(lane)
        self.assertIn(m.MODEL_GROUP, rendered)          # headers stay
        self.assertIn(m.SETTINGS_GROUP, rendered)
        self.assertIn("e", {a.key for a in rendered})   # now visible
        self.assertEqual(hidden, [])
        self.assertEqual(rendered[-1].section, "nav")
        self.assertEqual(rendered[-2].section, "lanes")

    def test_home_does_not_fold(self):
        rendered, hidden = m.rendered_actions(m.HomeScreen(m.Settings()))
        self.assertEqual(hidden, [])
        self.assertNotIn(m.SETTINGS_GROUP, rendered)
        self.assertNotIn(m.MODEL_GROUP, rendered)


class SizeSortAndLabelTests(unittest.TestCase):
    """User 2026-10-29: largest first (a 1T model must outrank 550B), param size in the row
    name by default, de-duplicated when the name states it, and no redundant '· catalog'."""

    def _lane(self, agents, params):
        s = m.Settings()
        s.model_param_count = lambda prov, mid: params.get(mid)
        return m.RemoteScreen(s, agents)

    def test_non_nvidia_pins_sort_largest_first_nvidia_hand_order_kept(self):
        agents = [m.RemoteAgent("g-small", "groq", "Small", "renewing_free", model_id="small"),
                  m.RemoteAgent("g-big", "groq", "Big", "renewing_free", model_id="big"),
                  m.RemoteAgent("n-1", "nvidia", "N1", "unknown", model_id="small"),
                  m.RemoteAgent("n-2", "nvidia", "N2", "unknown", model_id="big")]
        lane = self._lane(agents, {"small": 8.0, "big": 70.0})
        groups = {g.id: [i.id for i in g.items] for g in lane.groups()}
        self.assertEqual(groups["groq"], ["g-big", "g-small"])
        self.assertEqual(groups["nvidia"], ["n-1", "n-2"])   # hand-curated order untouched

    def test_trillion_parameter_model_sorts_above_550b(self):
        agents = [m.RemoteAgent("a-mid", "groq", "Mid", "renewing_free", model_id="mid"),
                  m.RemoteAgent("a-huge", "groq", "Huge", "renewing_free", model_id="huge")]
        lane = self._lane(agents, {"mid": 550.0, "huge": 1050.0})
        ids = [i.id for g in lane.groups() for i in g.items]
        self.assertEqual(ids, ["a-huge", "a-mid"])

    def test_format_param_count(self):
        self.assertEqual(m.format_param_count(1050.0), "1.05T")
        self.assertEqual(m.format_param_count(1000.0), "1T")
        self.assertEqual(m.format_param_count(52.0), "52B")
        self.assertEqual(m.format_param_count(8.5), "8.5B")
        self.assertEqual(m.format_param_count(None), "")
        self.assertEqual(m.format_param_count("bogus"), "")

    def test_param_size_in_row_label_and_deduped(self):
        agents = [m.RemoteAgent("g-big", "groq", "Big Model", "renewing_free", model_id="big"),
                  m.RemoteAgent("g-named", "groq", "Nemotron 550B-A55B", "renewing_free", model_id="named")]
        lane = self._lane(agents, {"big": 70.0, "named": 550.0})
        labels = {i.id: i.label for g in lane.groups() for i in g.items}
        self.assertIn("70B", labels["g-big"])
        self.assertNotIn("550B  · 550B", labels["g-named"])   # name already states the size
        self.assertNotIn("catalog", labels["g-big"].lower())   # redundant tag removed

    def test_lane_tag_drops_catalog_but_keeps_real_lanes(self):
        orig = m.lane_for
        try:
            m.lane_for = lambda p, mid: "catalog"
            self.assertEqual(m.lane_tag("nvidia", "x"), "")
            m.lane_for = lambda p, mid: "responses"
            self.assertEqual(m.lane_tag("nvidia", "x"), "  · responses")
        finally:
            m.lane_for = orig


class HwDetectCacheTests(unittest.TestCase):
    """User 2026-10-29: detection is cached (hw-detect.env) so launch 2+ needs no subprocess;
    first launch shows the generic placeholder until the background detection lands."""

    def setUp(self):
        try:
            import session_picker as sp
        except (ImportError, SystemExit):
            # session_picker.py exits(3) instead of raising when textual is missing
            self.skipTest("textual not installed in this interpreter (picker-gate venv only)")
        self.sp = sp

    def _with_cache(self, td):
        sp = self.sp
        orig = sp._hw_cache_path
        sp._hw_cache_path = lambda: Path(td) / "hw-detect.env"
        self.addCleanup(setattr, sp, "_hw_cache_path", orig)
        return sp

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._with_cache(td)
            sp._write_hw_cache(("Linux", "Ubuntu 24.04.3 LTS", "cuda", 64.0, "RTX 4090"))
            self.assertEqual(sp._read_hw_cache(), ("Linux", "Ubuntu 24.04.3 LTS", "cuda", 64.0, "RTX 4090"))

    def test_cache_missing_or_broken_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            sp = self._with_cache(td)
            p = Path(td) / "hw-detect.env"
            self.assertIsNone(sp._read_hw_cache())               # missing file
            p.write_text("garbage\n", encoding="utf-8")
            self.assertIsNone(sp._read_hw_cache())               # no LA_OS/LA_HARDWARE
            p.write_text('LA_OS="linux"\nLA_HARDWARE="cuda"\n', encoding="utf-8")
            self.assertEqual(sp._read_hw_cache(), ("linux", "", "cuda", None, ""))

    def test_detect_hw_prefers_env(self):
        sp = self.sp
        old = os.environ.copy()
        try:
            os.environ["LA_OS"] = "linux"
            os.environ["LA_DISTRO_PRETTY_NAME"] = "Fedora Linux 40"
            os.environ["LA_HARDWARE"] = "cuda"
            os.environ["LA_SYSTEM_RAM_GB"] = "64"
            os.environ["LA_GPU_MODEL"] = "RTX 4090"
            self.assertEqual(sp._detect_hw(), ("linux", "Fedora Linux 40", "cuda", 64.0, "RTX 4090"))
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
