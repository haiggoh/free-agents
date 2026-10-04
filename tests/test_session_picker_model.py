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


class RateLimiterScreenTests(unittest.TestCase):
    def test_screen_defaults_match_the_limiter(self):
        # Fails if the screen shows a default the proxies do not use (the old 120 s / 10 s drift).
        import rate_limiter as rl
        labels = {a.key: a.label for a in m.RateLimiterScreen(m.Settings()).actions()}
        self.assertIn(f"{int(rl.DEFAULT_MAX_WAIT)}s", labels["w"])
        self.assertIn(f"{int(rl.DEFAULT_429_BASE_COOLDOWN)}s", labels["d"])
        self.assertIn(f"{int(rl.DEFAULT_429_MAX_COOLDOWN)}s", labels["u"])
        self.assertIn(f"×{rl.DEFAULT_429_BACKOFF_MULTIPLIER}", labels["y"])
        self.assertIn(f"{rl.DEFAULT_429_MAX_RETRIES}", labels["z"])

    def test_every_cycle_includes_the_limiter_default(self):
        import rate_limiter as rl
        S = m.RateLimiterScreen
        self.assertIn(rl.DEFAULT_MAX_WAIT, S.WAIT_STEPS)
        self.assertIn(rl.DEFAULT_429_BASE_COOLDOWN, S.COOLDOWN_STEPS)
        self.assertIn(rl.DEFAULT_429_MAX_COOLDOWN, S.MAX_COOLDOWN_STEPS)
        self.assertIn(rl.DEFAULT_429_BACKOFF_MULTIPLIER, S.MULTIPLIER_STEPS)
        self.assertIn(rl.DEFAULT_429_MAX_RETRIES, S.RETRY_STEPS)

    def test_backoff_rows_persist_through_on_save(self):
        saved = {}
        screen = m.RateLimiterScreen(m.Settings(), on_save=saved.update)
        for key in "uyz":
            screen.handle_key(key)
        self.assertEqual(saved, {"max_cooldown": 600, "backoff_multiplier": 3.0, "max_retries": 10})


class BackendManagerContractTests(unittest.TestCase):
    """The runtime screen drives install/manage-backend.py (0.21.0). Fails if a row calls a
    subcommand main's parser rejects OR parses-but-never-dispatches (the merge's dead calls)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("manage_backend", ROOT / "install/manage-backend.py")
        cls.mb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mb)
        cls.dispatched = set(__import__("re").findall(r'args\.command == "([a-z-]+)"',
                             (ROOT / "install/manage-backend.py").read_text()))

    def tool_argvs(self):
        import session_picker as sp
        for name, fn in sp.TOOLS.items():
            if name.startswith("tool:rt-"):
                argv = fn("rapid-mlx", "0.15.3") if name in ("tool:rt-validate", "tool:rt-info") else fn("rapid-mlx")
                yield name, argv[2:]

    def test_backend_list_matches_registry(self):
        self.assertEqual(sorted(m.RUNTIME_BACKENDS), sorted(self.mb.list_managers()))

    def test_every_runtime_row_parses_and_is_dispatched(self):
        import importlib.util
        if importlib.util.find_spec("textual") is None:
            # session_picker exits(3) at import without Textual; the picker venv runs this half.
            self.skipTest("Textual absent: run under the picker venv for the argv half")
        argvs = list(self.tool_argvs())
        self.assertTrue(argvs)
        parser = self.mb.build_parser()
        for name, argv in argvs:
            with self.subTest(tool=name):
                args = parser.parse_args(argv)
                self.assertIn(args.command, self.dispatched)

    def test_runtime_screens_have_back_and_unique_keys(self):
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            rt = m.RuntimeScreen(m.Settings(), owner=owner)
            m.check_action_table(rt.actions())
            ld = m.LaunchdScreen(m.Settings(), owner=owner)
            self.assertEqual(ld.handle_key("escape"), m.Nav("runtime", owner))
            self.assertEqual(ld.handle_key("b"), m.Nav("runtime", owner))
        s = m.Settings()
        m.RuntimeScreen(s).handle_key("c")
        self.assertEqual(s.runtime_backend, m.RUNTIME_BACKENDS[(m.RUNTIME_BACKENDS.index("rapid-mlx") + 1) % 5])


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

    def test_unworking_models_hidden_by_default_and_u_reveals(self):
        # main 0.21.9-0.21.10: rows marked broken in the inventory are hidden until `u`.
        agents = remote_agents() + [m.RemoteAgent("nvidia-dead", "nvidia", "NVIDIA dead", "unknown",
                                                  broken=True)]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        ids = lambda: {i.id for g in lane.groups() for i in g.items}
        self.assertNotIn("nvidia-dead", ids())
        lane.handle_key("u")
        self.assertIn("nvidia-dead", ids())
        lane.handle_key("u")
        self.assertNotIn("nvidia-dead", ids())

    def test_unworking_toggle_has_no_uppercase_b_alias(self):
        # b is Back; the picker does not distinguish case, so 0.21.9's bash `B` must not exist here.
        lane = m.RemoteScreen(m.Settings(), remote_agents(), owner=m.HOME_OWNED)
        keys = {a.key: a for a in lane.actions()}
        self.assertIn("u", keys)
        self.assertEqual(keys["b"].label, f"{m.ec.EMOJI_HOME_STR} Back to Home")
        self.assertIsNone(lane.handle_key("B"))

    def test_temperature_cycles_and_reaches_the_launch_argv(self):
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemini-flash")
        self.assertNotIn("--temperature", lane.activate_selected().argv)   # provider default
        lane.handle_key("o")
        self.assertEqual(s.remote_temperature, m.TEMPERATURES[1])
        argv = lane.activate_selected().argv
        self.assertEqual(argv[argv.index("--temperature") + 1], m.TEMPERATURES[1])
        self.assertEqual(argv[-1], "gemini-flash", "the alias must stay last")
        for _ in range(len(m.TEMPERATURES) - 1):
            lane.handle_key("o")
        self.assertEqual(s.remote_temperature, "", "the cycle must wrap back to provider default")

    def test_lane_emojis_come_from_the_single_source(self):
        # Every emoji in a Local/Remote/Home label must be a value defined in config/emoji.sh,
        # read through emoji_constants. Positive control: a planted foreign emoji is caught.
        import re
        import emoji_constants as ec
        known = {v.strip() for v in ec._load_emojis().values() if v.strip()}
        emo = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u231A-\u23FF]\ufe0f?")
        def foreign(label):
            return [e for e in emo.findall(label) if e not in known and e + "\ufe0f" not in known]
        self.assertEqual(foreign("🦄 planted"), ["🦄"], "the checker itself must be able to fail")
        s = m.Settings(last_launched_model={"local_session": "gemma-4-26b",
                                            "remote_api_session": "gemini-flash", "lowkey": None})
        screens = [m.HomeScreen(s)]
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            screens += [m.LocalScreen(s, local_models(), owner=owner),
                        m.RemoteScreen(s, remote_agents(), owner=owner)]
        for screen in screens:
            for action in screen.actions():
                with self.subTest(screen=type(screen).__name__, key=action.key):
                    self.assertEqual(foreign(action.label), [], action.label)

    def test_no_emoji_literals_in_picker_source(self):
        # Single source of truth: every emoji the picker draws is defined in config/emoji.sh.
        # This scans the source text, so a new hardcoded emoji fails here even on a screen the
        # label test does not instantiate. Comments are exempt.
        import re
        emo = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u231A-\u23FF\u25B2]|\d\ufe0f\u20e3")
        for name in ("session_picker_model.py", "session_picker.py"):
            for n, line in enumerate((ROOT / "bin" / name).read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                with self.subTest(file=name, line=n):
                    self.assertEqual(emo.findall(code), [], line.strip())

    def test_api_keys_screen_builds_for_both_owners(self):
        # Provider rows share the key table with Back/Quit; SambaNova's old `b` crashed Home-owned.
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            with self.subTest(owner=owner):
                keys = [a.key for a in m.APIKeysScreen(m.Settings(), owner=owner).actions()]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertTrue(all(ec_ok for ec_ok in (m.ec.provider_emoji(p[0]) for p in m.APIKeysScreen.PROVIDERS)))

    def test_go_last_is_offered_before_any_model_list_loads(self):
        # The lane must launch the remembered model WITHOUT an inventory call (lazy loading leaves
        # models/agents empty until `c`). Remote needs the tier for --include-trials, so it is
        # remembered alongside the alias (user decision 2026-10-03, option a).
        s = m.Settings(remote_effort="high",
                       last_launched_model={"local_session": "gemma-4-26b",
                                            "remote_api_session": "cerebras-oss", "lowkey": None},
                       last_launched_tier={"remote_api_session": "trial"})
        local = m.LocalScreen(s, models=None, owner=m.DIRECT_ROOT)
        req = local.handle_key("g")
        self.assertEqual(req.argv, ["local", "gemma-4-26b", s.local_effort])
        remote = m.RemoteScreen(s, agents=None, owner=m.DIRECT_ROOT)
        req = remote.handle_key("g")
        self.assertEqual(req.argv[-1], "cerebras-oss")
        self.assertIn("--include-trials", req.argv)
        self.assertEqual(req.argv[req.argv.index("--effort") + 1], "high")

    def test_go_last_absent_without_a_remembered_model(self):
        s = m.Settings()
        for screen in (m.LocalScreen(s, models=None), m.RemoteScreen(s, agents=None)):
            self.assertNotIn("g", {a.key for a in screen.actions()})

    def test_launch_remembers_alias_and_tier(self):
        saved = []
        s = m.Settings(on_last_launched=lambda lane, alias, tier: saved.append((lane, alias, tier)))
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.accordion.select("cerebras-oss")
        lane.activate_selected()
        self.assertEqual(saved, [("remote_api_session", "cerebras-oss", "trial")])
        self.assertEqual(s.last_launched_tier["remote_api_session"], "trial")

    def test_settings_rows_step_both_ways_and_wrap(self):
        # Enter/click/Right = step(+1), Left = step(-1); toggles, auto-mode (3 states), effort.
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.HOME_OWNED)
        acts = {a.key: a for a in lane.actions()}
        for key in ("a", "t", "p", "m", "e", "o", "h", "f", "u"):
            self.assertIsNotNone(acts[key].step, f"{key} must be a settings row")
        self.assertNotIn("v", acts, "the Remote lane does not offer the (local) backend manager")
        for key in ("c", "s", "x", "k", "n", "b"):
            self.assertIsNone(acts[key].step, f"{key} is navigation, not a setting")
        acts["a"].step(+1); self.assertEqual(s.auto_mode, 1)
        acts["a"].step(-1); acts["a"].step(-1); self.assertEqual(s.auto_mode, 2, "Left wraps 0 -> 2")
        start = s.remote_effort
        acts["e"].step(+1); self.assertNotEqual(s.remote_effort, start)
        acts["e"].step(-1); self.assertEqual(s.remote_effort, start)
        acts["t"].step(-1); self.assertTrue(s.telemetry, "a boolean flips either way")

    def test_defaults_and_icons_requested_2026_10_04(self):
        s = m.Settings()
        self.assertFalse(s.stop_hook, "queued-prompt hook is OFF by default")
        labels = {a.key: a.label for a in m.HomeScreen(s).actions()}
        self.assertTrue(labels["t"].startswith(m.ec.EMOJI_TELEMETRY_ON_STR), "satellite even when OFF")
        self.assertTrue(labels["a"].startswith(m.ec.EMOJI_AUTO_MODE_STR))
        self.assertTrue(labels["p"].startswith(m.ec.EMOJI_STOP_HOOK_STR))

    def test_sub_screens_offer_back_not_quit(self):
        for cls in (m.RateLimiterScreen, m.RuntimeManagerScreen, m.APIKeysScreen, m.RuntimeScreen,
                    m.LaunchdScreen):
            sc = cls(m.Settings(), owner=m.SUB)
            nav = [a for a in sc.actions() if a.section == "nav"]
            with self.subTest(screen=cls.__name__):
                self.assertEqual([a.key for a in nav], ["b"])
                self.assertIs(nav[0].run(), m.BACK)
                self.assertIs(sc.handle_key("escape"), m.BACK)

    def test_remote_subheadline_has_no_disclaimer(self):
        lane = m.RemoteScreen(m.Settings(), remote_agents(), owner=m.DIRECT_ROOT)
        self.assertNotIn("leave this machine", lane.policy)
        self.assertRegex(lane.policy, r"^\d+ model\(s\) visible  \(hidden: \d+\)$")
        self.assertTrue(lane.title.startswith(m.ec.SESSION_EMOJI_FREE_API_STR))
        self.assertTrue(m.LocalScreen(m.Settings(), local_models()).title.startswith(m.ec.SESSION_EMOJI_LOCAL_STR))

    def test_loading_label_animates_hourglass_and_dots(self):
        # Needs the Textual picker module; runs under the picker venv (skips otherwise).
        try:
            import session_picker as sp
        except SystemExit:
            self.skipTest("Textual absent")
        frames = [sp.loading_label(t) for t in range(6)]
        self.assertEqual(len({len(f.split(" ", 1)[1]) for f in frames}), 1, "fixed-width text: nothing jumps")
        self.assertEqual([f.split(" ", 1)[1] for f in frames[:3]],
                         ["loading.  ", "loading.. ", "loading..."])
        glyphs = [f.split(" ", 1)[0] for f in frames]
        self.assertEqual(glyphs[0], glyphs[1], "the hourglass holds for two beats")
        self.assertNotEqual(glyphs[1], glyphs[2], "then flips")
        self.assertGreaterEqual(sp.LOADING_INTERVAL, 0.3, "faster than ~3/s reads as static")

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


class LowkeyTests(unittest.TestCase):
    def test_owner_rules_and_effort_default(self):
        s = m.Settings()
        owned = m.LowkeyScreen(s, local_models(), owner=m.HOME_OWNED)
        keys = {a.key for a in owned.actions()}
        self.assertIn("b", keys)
        self.assertNotIn("q", keys)
        self.assertEqual(s.lowkey_effort, "xhigh")
        direct = m.LowkeyScreen(s, local_models(), owner=m.DIRECT_ROOT)
        self.assertIn("q", {a.key for a in direct.actions()})

    def test_effort_cycle_never_offers_max(self):
        s = m.Settings()
        lk = m.LowkeyScreen(s, local_models(), owner=m.DIRECT_ROOT)
        seen = set()
        for _ in range(10):
            lk.handle_key("e")
            seen.add(s.lowkey_effort)
        self.assertNotIn("max", seen)
        self.assertIn("none", seen)

    def test_convo_argv_carries_model_effort_and_session(self):
        s = m.Settings(lowkey_effort="low")
        lk = m.LowkeyScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lk.session_name = "notes"
        lk.accordion.select("gemma-4-26b")
        argv = lk.activate_selected().argv
        self.assertEqual(argv, ["lowkey", "--convo", "--model", "gemma-4-26b",
                                "--effort", "low", "--session", "notes"])

    def test_one_shot_argv(self):
        lk = m.LowkeyScreen(m.Settings(), local_models(), owner=m.DIRECT_ROOT)
        lk.accordion.select("gemma-4-26b")
        argv = lk.one_shot("summarise X").argv
        self.assertEqual(argv, ["lowkey", "--model", "gemma-4-26b", "--effort", "xhigh",
                                "--prompt", "summarise X"])
        self.assertIsNone(lk.one_shot(""))


def catalog():
    return [m.CatalogEntry("ornith-1.5-35b", "COMPLETE", 19.5, "existing", "o/r"),
            m.CatalogEntry("qwen-3.8-operator", "ABSENT", 16.0, "session,operator", "q/q"),
            m.CatalogEntry("deepseek-r1-architect", "ABSENT", 18.0, "validator", "d/d"),
            m.CatalogEntry("mystery", "PRESENT", 5.0, "", "x/y")]


class DownloadTests(unittest.TestCase):
    def screen(self, free=200.0, headroom=100.0):
        return m.DownloadScreen(m.Settings(), catalog(), free_gb=free, headroom_gb=headroom,
                                owner=m.DIRECT_ROOT)

    def test_enter_and_click_never_download(self):
        # Fails if activating a row launches the engine (the "downloader catastrophe").
        d = self.screen()
        d.accordion.select("qwen-3.8-operator")
        self.assertIsNone(d.activate_selected())
        self.assertEqual(d.queue, ["qwen-3.8-operator"], "Enter should only toggle the queue")

    def test_space_queues_review_summarises_and_cancel_makes_zero_calls(self):
        d = self.screen()
        for alias in ("qwen-3.8-operator", "deepseek-r1-architect"):
            d.accordion.select(alias)
            d.toggle_selected()
        review = d.review()
        self.assertEqual(review.aliases, ["qwen-3.8-operator", "deepseek-r1-architect"])
        self.assertAlmostEqual(review.total_gb, 34.0)
        self.assertTrue(review.fits)
        self.assertIsNone(d.confirm(False), "cancel must produce no engine call")

    def test_confirm_passes_exact_aliases_to_existing_engine(self):
        d = self.screen()
        d.accordion.select("deepseek-r1-architect")
        d.toggle_selected()
        req = d.confirm(True)
        self.assertEqual(req.argv, ["download", "--select", "deepseek-r1-architect"])

    def test_complete_entries_cost_nothing_and_empty_queue_confirms_nothing(self):
        d = self.screen()
        d.accordion.select("ornith-1.5-35b")
        d.toggle_selected()
        self.assertEqual(d.review().total_gb, 0.0)
        d.toggle_selected()
        self.assertIsNone(d.confirm(True))

    def test_review_flags_a_breached_reserve_but_leaves_the_refusal_to_the_engine(self):
        d = self.screen(free=120.0, headroom=100.0)
        d.accordion.select("qwen-3.8-operator")
        d.toggle_selected()
        d.accordion.select("deepseek-r1-architect")
        d.toggle_selected()
        self.assertFalse(d.review().fits)
        # The engine's own preflight still runs: the picker never passes --allow-tight.
        self.assertNotIn("--allow-tight", d.confirm(True).argv)

    def test_no_digit_shortcuts_and_back_quit_rules(self):
        d = self.screen()
        for key in "0123456789":
            self.assertIsNone(d.handle_key(key))
        m.check_action_table(d.actions())
        owned = m.DownloadScreen(m.Settings(), catalog(), 200, 100, owner=m.HOME_OWNED)
        self.assertIn("b", {a.key for a in owned.actions()})
        self.assertNotIn("q", {a.key for a in owned.actions()})


if __name__ == "__main__":
    unittest.main()
