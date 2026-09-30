#!/usr/bin/env python3
"""tests/test_session_picker_model.py — the picker's pure logic, no terminal needed.

Covers the navigation contract (home-owned vs direct-root), the one-action-table-per-screen
key rule, family/provider grouping, the one-open-group accordion, stable selection by id,
and the launch argv each screen produces. Each test names the change that breaks it.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402


def local_models(n=12):
    names = ["qwen-3.8-operator", "qwen-3.6-thinking", "deepseek-r1-architect", "devstral-2-123b",
             "codestral-25", "gemma-4-26b", "ornith-1.5-35b", "mystery-model", "llama-4-scout",
             "qwen-3.8-thinking", "mistral-small-4", "glm-5-air", "phi-5", "kimi-dev-72b"]
    return [m.LocalModel(alias=a, effort="medium", roles="") for a in names[:n]]


def remote_agents():
    return [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown"),
            m.RemoteAgent("nvidia-b", "nvidia", "NVIDIA B", "unknown"),
            m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free"),
            m.RemoteAgent("cerebras-oss", "cerebras", "Cerebras OSS", "trial"),
            m.RemoteAgent("groq-local", "groq", "Groq local-capable", "renewing_free",
                          local_capable=True)]


class NavigationTests(unittest.TestCase):
    def labels(self, screen):
        return {a.key: a.label for a in screen.actions()}

    def test_home_has_quit_and_no_back(self):
        home = m.HomeScreen(m.Settings())
        keys = self.labels(home)
        self.assertIn("q", keys)
        self.assertNotIn("b", keys)
        self.assertIn("l", keys)
        self.assertIn("r", keys)

    def test_home_owned_lane_has_back_and_no_quit(self):
        # Fails if ownership is inferred from the screen type instead of the origin.
        lane = m.LocalScreen(m.Settings(), local_models(), owner=m.HOME_OWNED)
        keys = self.labels(lane)
        self.assertIn("b", keys)
        self.assertNotIn("q", keys)

    def test_direct_root_lane_has_quit_and_no_back(self):
        lane = m.RemoteScreen(m.Settings(), remote_agents(), owner=m.DIRECT_ROOT)
        keys = self.labels(lane)
        self.assertIn("q", keys)
        self.assertNotIn("b", keys)

    def test_switch_preserves_owner(self):
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            lane = m.LocalScreen(m.Settings(), local_models(), owner=owner)
            result = lane.handle_key("s")
            self.assertEqual(result, m.Nav("remote", owner))

    def test_same_lane_key_is_a_harmless_unlisted_noop(self):
        lane = m.LocalScreen(m.Settings(), local_models(), owner=m.HOME_OWNED)
        self.assertNotIn("l", self.labels(lane))
        self.assertIsNone(lane.handle_key("l"))
        self.assertEqual(lane.handle_key("r"), m.Nav("remote", m.HOME_OWNED))

    def test_escape_matches_back_only_where_back_exists(self):
        owned = m.LocalScreen(m.Settings(), local_models(), owner=m.HOME_OWNED)
        direct = m.LocalScreen(m.Settings(), local_models(), owner=m.DIRECT_ROOT)
        self.assertEqual(owned.handle_key("escape"), m.Nav("home", m.HOME_OWNED))
        self.assertEqual(direct.handle_key("escape"), m.QUIT)

    def test_rate_limiter_child_has_back_when_home_owned(self):
        rl = m.RateLimiterScreen(m.Settings(), owner=m.HOME_OWNED)
        keys = self.labels(rl)
        self.assertIn("b", keys)
        self.assertNotIn("q", keys)
        rl2 = m.RateLimiterScreen(m.Settings(), owner=m.DIRECT_ROOT)
        self.assertIn("q", self.labels(rl2))
        self.assertNotIn("b", self.labels(rl2))


class ActionTableTests(unittest.TestCase):
    def all_screens(self):
        s = m.Settings()
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            yield m.LocalScreen(s, local_models(), owner=owner)
            yield m.RemoteScreen(s, remote_agents(), owner=owner)
            yield m.RateLimiterScreen(s, owner=owner)
        yield m.HomeScreen(s)

    def test_no_duplicate_keys_on_any_screen(self):
        for screen in self.all_screens():
            with self.subTest(screen=type(screen).__name__):
                m.check_action_table(screen.actions())

    def test_collision_check_actually_detects_a_collision(self):
        # Planted negative control: if check_action_table were a no-op this passes silently.
        broken = [m.Action("e", "Effort", lambda: None), m.Action("e", "Exit", lambda: None)]
        with self.assertRaises(ValueError):
            m.check_action_table(broken)

    def test_uppercase_and_digits_are_never_shortcuts(self):
        for screen in self.all_screens():
            for action in screen.actions():
                with self.subTest(screen=type(screen).__name__, key=action.key):
                    self.assertTrue(action.key.islower() and action.key.isalpha(), action.key)

    def test_digits_never_launch_a_model(self):
        # "11" must never launch model 1: digits are not bound at all.
        lane = m.LocalScreen(m.Settings(), local_models(12), owner=m.DIRECT_ROOT)
        for key in "0123456789":
            self.assertIsNone(lane.handle_key(key))

    def test_uppercase_is_not_an_alias_for_lowercase(self):
        lane = m.LocalScreen(m.Settings(), local_models(), owner=m.DIRECT_ROOT)
        self.assertIsNone(lane.handle_key("Q"))

    def test_disabled_actions_are_visible_and_marked(self):
        s = m.Settings(auto_mode=2)   # MCP in local lane only matters in blind-trust
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        mcp = [a for a in lane.actions() if a.key == "m"][0]
        self.assertFalse(mcp.enabled)
        self.assertIn("unavailable", mcp.label)


class GroupingTests(unittest.TestCase):
    def test_family_classifier(self):
        cases = {"codestral-25": "Mistral", "devstral-2-123b": "Mistral",
                 "mistral-small-4": "Mistral", "qwen-3.8-operator": "Qwen",
                 "deepseek-r1-architect": "DeepSeek", "gemma-4-26b": "Gemma",
                 "mystery-model": "Other", "ornith-1.5-35b": "Ornith", "llama-4-scout": "Llama"}
        for alias, family in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(m.family_for(alias), family)

    def test_deepseek_distill_on_qwen_is_deepseek(self):
        # Architecture must not win over identity.
        self.assertEqual(m.family_for("deepseek-r1-distill-qwen-32b"), "DeepSeek")

    def test_explicit_family_overrides_classifier(self):
        self.assertEqual(m.family_for("mystery-model", explicit="Acme"), "Acme")

    def test_remote_groups_by_provider_after_filters_with_counts(self):
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        groups = {g.id: len(g.items) for g in lane.groups()}
        # trials VISIBLE by default, local-capable hidden by default
        self.assertEqual(groups, {"nvidia": 2, "gemini": 1, "cerebras": 1})
        lane.handle_key("h")                     # hide trials
        self.assertNotIn("cerebras", {g.id for g in lane.groups()})
        lane.handle_key("f")                     # show local-capable
        self.assertIn("groq", {g.id for g in lane.groups()})

    def test_trial_rows_are_marked(self):
        lane = m.RemoteScreen(m.Settings(), remote_agents(), owner=m.DIRECT_ROOT)
        row = [i for g in lane.groups() for i in g.items if i.id == "cerebras-oss"][0]
        self.assertIn("trial", row.label.lower())

    def test_one_group_open_and_selection_stable_by_id(self):
        lane = m.LocalScreen(m.Settings(), local_models(14), owner=m.DIRECT_ROOT)
        acc = lane.accordion
        acc.expand("Mistral")
        self.assertEqual(acc.open_group, "Mistral")
        acc.expand("Qwen")
        self.assertEqual(acc.open_group, "Qwen", "opening a group must close the previous one")
        acc.select("qwen-3.8-thinking")
        # Reorder the underlying models; the selection follows the id, not the row number.
        lane.set_models(list(reversed(local_models(14))))
        self.assertEqual(lane.accordion.selected_id, "qwen-3.8-thinking")

    def test_zero_models_is_a_valid_screen(self):
        lane = m.LocalScreen(m.Settings(), [], owner=m.HOME_OWNED)
        self.assertEqual(lane.groups(), [])
        self.assertIn("b", {a.key for a in lane.actions()})
        self.assertIsNone(lane.activate_selected())


class LaunchTests(unittest.TestCase):
    def test_local_launch_passes_selected_effort(self):
        s = m.Settings()
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemma-4-26b")
        req = lane.activate_selected()
        self.assertEqual(req.lane, "local")
        self.assertEqual(req.argv[-2:], ["gemma-4-26b", "high"])

    def test_remote_provider_default_omits_effort(self):
        s = m.Settings(remote_effort="provider_default")
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemini-flash")
        req = lane.activate_selected()
        self.assertNotIn("--effort", req.argv)
        self.assertNotIn("provider_default", " ".join(req.argv))
        self.assertEqual(req.argv[-1], "gemini-flash")

    def test_remote_effort_and_toggles_become_flags(self):
        s = m.Settings(remote_effort="max", auto_mode=2, telemetry=True, include_trials=True)
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.accordion.select("cerebras-oss")
        argv = lane.activate_selected().argv
        self.assertEqual(argv[argv.index("--effort") + 1], "max")
        self.assertEqual(argv.count("-a"), 2)
        self.assertIn("-t", argv)
        self.assertIn("--include-trials", argv)

    def test_remote_hidden_trials_launch_is_blocked_by_filter_not_guard(self):
        # With trials hidden the row is simply not offered; the direct-CLI guard is untouched.
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.handle_key("h")
        self.assertFalse(lane.accordion.select("cerebras-oss"))

    def test_local_env_carries_toggles(self):
        s = m.Settings(auto_mode=1, telemetry=True, stop_hook=False, enable_mcp=True)
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemma-4-26b")
        env = lane.activate_selected().env
        self.assertEqual((env["LA_AUTO_MODE"], env["LA_BLIND_AUTO"]), ("1", "0"))
        self.assertEqual(env["LA_TELEMETRY"], "1")
        self.assertEqual(env["LA_QUEUE_STOP_HOOK"], "0")
        self.assertEqual(env["LA_ENABLE_MCP"], "1")

    def test_effort_cycle_lists_provider_default_only_for_remote(self):
        self.assertIn("provider_default", m.effort_choices("remote_api_session"))
        self.assertNotIn("provider_default", m.effort_choices("local_session"))


if __name__ == "__main__":
    unittest.main()
