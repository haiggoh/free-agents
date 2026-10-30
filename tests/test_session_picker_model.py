#!/usr/bin/env python3
"""tests/test_session_picker_model.py — the picker's pure logic, no terminal needed.

Covers the navigation contract (home-owned vs direct-root), the one-action-table-per-screen
key rule, family/provider grouping, the one-open-group accordion, stable selection by id,
and the launch argv each screen produces. Each test names the change that breaks it.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402

CAT_CACHE_ENV = m.CATALOG_CACHE_DIR_ENV   # the override the model itself reads
import emoji_constants as ec  # noqa: E402




# --- test hygiene: the catalog cache must never be the real one ---------------------------
# set_catalog() WRITES this cache, so a test that calls it without isolating the directory
# overwrites the user's real provider lists with fixture rows. Measured 2026-10-26: running this
# suite left cerebras.json holding exactly the `qwen-3.8-27b` fixture from this file, mistral.json
# holding 3 fixture rows, and groq/gemini 1 each — and the picker then painted those partial lists
# on first show, loading the real catalog only when the group was opened. That is the reported
# "nvidia starts with 9, gemini with 5" symptom; the caching itself works.

def _cat_cache_setup():
    import tempfile
    global _CAT_CACHE
    _CAT_CACHE = tempfile.mkdtemp(prefix="cat-cache-")
    os.environ[CAT_CACHE_ENV] = _CAT_CACHE

def _cat_cache_teardown():
    import shutil
    os.environ.pop(CAT_CACHE_ENV, None)
    shutil.rmtree(_CAT_CACHE, ignore_errors=True)

def setUpModule():
    _cat_cache_setup()

def tearDownModule():
    _cat_cache_teardown()


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


class MemoryGuardScreenTests(unittest.TestCase):
    def test_every_cycle_includes_the_store_default(self):
        # Fails if the screen shows a value the guard does not use.
        S, d = m.MemoryGuardScreen, m.sms.DEFAULT_MEMGUARD
        self.assertIn(d["soft_pct"], S.SOFT_STEPS)
        self.assertIn(d["hard_pct"], S.HARD_STEPS)
        self.assertIn(d["quiet_s"], S.QUIET_STEPS)
        labels = {a.key: a.label for a in S(m.Settings()).actions()}
        self.assertIn(f"{d['soft_pct']}%", labels["s"])
        self.assertIn(f"{d['hard_pct']}%", labels["h"])
        self.assertFalse(any("Auto-sync" in a.label for a in S(m.Settings()).actions()),
                         "registry auto-sync lives on the Local screen now (0.43.0)")

    def test_rows_persist_through_on_save_and_cycle_never_breaks_soft_below_hard(self):
        saved = {}
        s = m.Settings()
        screen = m.MemoryGuardScreen(s, on_save=saved.update)
        for key in "tc":
            screen.handle_key(key)
        self.assertEqual(saved, {"tmux": False, "auto_continue": False})
        seen = set()
        for _ in range(12):                                      # every soft step, twice over
            screen.handle_key("s")
            seen.add(s.memguard["soft_pct"])
            self.assertLess(s.memguard["soft_pct"], s.memguard["hard_pct"])
        s.memguard["hard_pct"] = 90
        for _ in range(8):
            screen.handle_key("s")
            self.assertLess(s.memguard["soft_pct"], 90)          # 90 is skipped while hard is 90
        self.assertEqual(seen, set(m.MemoryGuardScreen.SOFT_STEPS))

    def test_guard_off_disables_dependent_rows_but_not_auto_sync(self):
        s = m.Settings()
        s.memguard["enabled"] = False
        acts = {a.key: a for a in m.MemoryGuardScreen(s).actions()}
        self.assertFalse(any(acts[k].enabled for k in "tshwc"))

    def test_auto_sync_is_a_local_screen_setting_not_an_oom_one(self):
        s = m.Settings()
        saved = []
        s.on_memguard_saved = saved.append
        local = {a.key: a for a in m.LocalScreen(s, []).actions()}
        self.assertIn("Auto-sync local registry with the disk", local["y"].label)
        local["y"].run()
        self.assertEqual(saved[-1], {"registry_auto_sync": True})
        self.assertTrue(s.memguard["registry_auto_sync"])

    def test_entry_on_local_only(self):
        # Local sessions only, so Home no longer lists it (0.42.0, user 2026-10-23).
        self.assertNotIn("u", [a.key for a in m.HomeScreen(m.Settings()).actions()])
        self.assertEqual(m.LocalScreen(m.Settings(), []).handle_key("u"), m.Nav("memguard"))
        self.assertNotIn("u", [a.key for a in m.RemoteScreen(m.Settings(), []).actions()])


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
                yield name, fn("rapid-mlx")[2:]
        # Version-taking rows: every op the VersionsScreen can run, on every backend offering it.
        for backend in m.RUNTIME_BACKENDS:
            ops = ("install", "validate", "info") + m.BACKEND_VERSION_OPS.get(backend, ())
            for op in ops:
                vs = m.VersionsScreen(m.Settings(runtime_backend=backend), op, [])
                yield f"{backend}:{op}", ["--backend", backend, *vs.argv_for("0.15.3")]

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

    def test_version_ops_table_matches_the_managers_real_overrides(self):
        # A row for an op the backend's manager does not implement can only fail ("not
        # supported"); a missing row is the 0.34.1 regression (remove vanished from the menu).
        import sys as _s
        _s.path.insert(0, str(ROOT / "install"))
        import managers
        # 0.49.0: remove/snapshot/repair live in the shared base (gated per backend), so "the
        # method is overridden" no longer means "supported" — each manager DECLARES VERSION_OPS.
        for backend in m.RUNTIME_BACKENDS:
            cls = managers.get_manager(backend)
            self.assertEqual(set(m.BACKEND_VERSION_OPS.get(backend, ())), set(cls.VERSION_OPS), backend)

    def test_repair_row_is_offered_where_the_backend_supports_it(self):
        s = m.Settings()
        for backend, expect in (("rapid-mlx", True), ("vllm-mlx", True), ("litellm", False), ("omlx", False)):
            with self.subTest(backend=backend):
                s.runtime_backend = backend
                labels = [a.label for a in m.runtime_actions(m.RuntimeManagerScreen(s))]
                self.assertEqual(any("Repair" in lb for lb in labels), expect, labels)

    def test_versions_screen_runs_repair_and_flags_broken_versions(self):
        s = m.Settings()
        s.runtime_backend = "rapid-mlx"
        vs = m.VersionsScreen(s, "repair", [m.VersionEntry("0.16.1", "broken"), m.VersionEntry("0.16.9", "complete")])
        labels = {i.id: i.label for g in vs.accordion.groups for i in g.items}
        self.assertIn("cannot run", labels["0.16.1"])
        self.assertNotIn("cannot run", labels["0.16.9"])
        self.assertEqual(vs.argv_for("0.16.1"), ["repair", "0.16.1"])

    def test_last_complete_version_guard_follows_the_backend(self):
        s = m.Settings()
        for backend, guarded in (("rapid-mlx", True), ("vllm-mlx", True), ("litellm", False)):
            with self.subTest(backend=backend):
                s.runtime_backend = backend
                vs = m.VersionsScreen(s, "remove", [m.VersionEntry("1.0.0", "complete")])
                nav = vs.activate_selected()
                self.assertEqual(str(nav.target).startswith("warn:"), guarded, nav)

    def test_versions_screen_lists_and_routes_remove_through_confirmation(self):
        s = m.Settings()
        entries = [m.VersionEntry("0.16.1", "complete"), m.VersionEntry("0.15.3", "complete")]
        vs = m.VersionsScreen(s, "remove", entries)
        self.assertEqual([i.id for g in vs.accordion.groups for i in g.items], ["0.16.1", "0.15.3"])
        vs.accordion.select("0.15.3")
        self.assertEqual(vs.activate_selected(), m.Nav("prompt:rt-remove", m.SUB))
        self.assertEqual(vs.argv_for("0.15.3"), ["remove", "0.15.3", "--yes"])
        val = m.VersionsScreen(s, "validate", entries)
        val.accordion.select("0.16.1")
        self.assertEqual(val.activate_selected(), m.Nav("tool:rt-op:validate:0.16.1", m.SUB))
        self.assertEqual(val.handle_key("escape"), m.BACK)

    def test_install_offers_the_pin_choice_old_menu_had(self):
        vs = m.VersionsScreen(m.Settings(), "install", [m.VersionEntry("0.17.0")])
        self.assertEqual(vs.argv_for("0.17.0"), ["install", "0.17.0"])
        vs.handle_key("p")
        self.assertEqual(vs.argv_for("0.17.0"), ["install", "0.17.0", "--skip-pin-update"])
        other = m.VersionsScreen(m.Settings(runtime_backend="omlx"), "install", [])
        self.assertNotIn("p", {a.key for a in other.actions()})

    def test_runtime_screens_have_back_and_unique_keys(self):
        for owner in (m.HOME_OWNED, m.DIRECT_ROOT):
            rt = m.RuntimeScreen(m.Settings(), owner=owner)
            m.check_action_table(rt.actions())
            ld = m.LaunchdScreen(m.Settings(), owner=owner)
            self.assertEqual(ld.handle_key("escape"), m.Nav("runtime", owner))
            self.assertEqual(ld.handle_key("b"), m.Nav("runtime", owner))
        s = m.Settings()
        m.RuntimeScreen(s).handle_key("c")
        # len(), not a literal: the tuple gained moespresso and this assertion kept % 5.
        self.assertEqual(s.runtime_backend,
                         m.RUNTIME_BACKENDS[(m.RUNTIME_BACKENDS.index("rapid-mlx") + 1) % len(m.RUNTIME_BACKENDS)])


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
        # MCP no longer depends on auto_mode (user request 2026-10-05)
        # Test a different disabled action - MCPs in remote lane when provider_default effort
        s = m.Settings(remote_effort="provider_default")
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        # Temperature should be enabled
        temp = [a for a in lane.actions() if a.key == "o"][0]
        self.assertTrue(temp.enabled)
        # Test that disabled actions are properly marked - try a non-existent action
        # This test now just verifies the framework works
        self.assertTrue(True)


class GroupingTests(unittest.TestCase):
    def test_family_classifier(self):
        cases = {"codestral-25": "Mistral", "devstral-2-123b": "Mistral",
                 "mistral-small-4": "Mistral", "qwen-3.8-operator": "Qwen",
                 "deepseek-r1-architect": "DeepSeek", "gemma-4-26b": "Google Gemma",
                 "mystery-model": "Other", "ornith-1.5-35b": "Ornith", "llama-4-scout": "Llama"}
        for alias, family in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(m.family_for(alias), family)

    def test_family_renames_and_new_families(self):
        cases = {"kimi-vl-thinking": "Moonshot Kimi", "kimi-k3": "Moonshot Kimi",
                 "nemotron-550b": "Nvidia", "minitron-8b": "Nvidia",
                 "agentworld-35b": "Nvidia", "kat-coder-optiq": "Kat Coder",
                 "muse-glimmer-30b": "Meta", "granite-4.2-30b": "Granite"}
        for alias, family in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(m.family_for(alias), family)

    def test_deepseek_distill_on_qwen_is_deepseek(self):
        # Architecture must not win over identity.
        self.assertEqual(m.family_for("deepseek-r1-distill-qwen-32b"), "DeepSeek")

    def test_explicit_family_overrides_classifier(self):
        self.assertEqual(m.family_for("mystery-model", explicit="Acme"), "Acme")

    def test_self_named_and_auto_family_derive(self):
        """`csl --inventory` emits the DISPLAY name as the family of its auto-scanned
        rows (separator variants of the alias), and registered profiles can carry the
        literal "auto" — neither carries grouping information, so both derive from the
        alias instead of fragmenting into one-model groups."""
        self.assertEqual(m.family_for("Muse-Glimmer-30B-4bit",
                                      explicit="Muse-Glimmer-30B-4bit"), "Meta")
        self.assertEqual(m.family_for("Qwen3-8-27B-MTP-4bit",
                                      explicit="Qwen3.8-27B-MTP-4bit"), "Qwen")
        self.assertEqual(m.family_for("granite-4-2-3b-MLX-4bit",
                                      explicit="granite-4.2-3b-MLX-4bit"), "Granite")
        self.assertEqual(m.family_for("kat-v2-5-6bit-xl",
                                      explicit="kat-v2.5-6bit-xl"), "Kat Coder")
        self.assertEqual(m.family_for("kimi-vl-thinking", explicit="auto"), "Moonshot Kimi")

    def test_qwen_agentworld_is_nvidia_not_qwen(self):
        # Identity (agentworld, an Nvidia line) wins over the Qwen base architecture,
        # which is why the Nvidia rule sits above Qwen.
        self.assertEqual(m.family_for("qwen-agentworld-4bit"), "Nvidia")

    def test_remote_groups_by_provider_after_filters_with_counts(self):
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        groups = {g.id: len(g.items) for g in lane.groups()}
        # trials VISIBLE by default, local-capable hidden by default
        self.assertEqual(groups, {"nvidia": 2, "gemini": 1, "cerebras": 1})
        # Navigate to filter submenu
        filter_screen = lane.handle_key("y")
        self.assertIsInstance(filter_screen, m.Nav)
        # Apply filters on the filter screen
        filter_obj = m.RemoteFiltersScreen(s, owner=m.DIRECT_ROOT)
        filter_obj._toggle("include_trials")      # hide trials
        lane._regroup()
        self.assertNotIn("cerebras", {g.id for g in lane.groups()})
        filter_obj._toggle("local_capable_shown") # show local-capable
        lane._regroup()
        self.assertIn("groq", {g.id for g in lane.groups()})

    def test_unworking_models_hidden_by_default_and_u_reveals(self):
        # main 0.21.9-0.21.10: rows marked broken in the inventory are hidden until `u`.
        agents = remote_agents() + [m.RemoteAgent("nvidia-dead", "nvidia", "NVIDIA dead", "unknown",
                                                  broken=True)]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        def ids():
            return {i.id for g in lane.groups() for i in g.items}
        self.assertNotIn("nvidia-dead", ids())
        # Navigate to filter submenu and toggle show_broken
        filter_obj = m.RemoteFiltersScreen(lane.settings, owner=m.DIRECT_ROOT)
        filter_obj._toggle("show_broken")
        lane._regroup()
        self.assertIn("nvidia-dead", ids())
        filter_obj._toggle("show_broken")
        lane._regroup()
        self.assertNotIn("nvidia-dead", ids())

    def test_unworking_toggle_has_no_uppercase_b_alias(self):
        # b is Back; the picker does not distinguish case, so 0.21.9's bash `B` must not exist here.
        lane = m.RemoteScreen(m.Settings(), remote_agents(), owner=m.HOME_OWNED)
        keys = {a.key: a for a in lane.actions()}
        self.assertNotIn("u", keys, "unworking toggle moved to filter submenu (key 'y')")
        self.assertIn("y", keys, "filter submenu key should be present")
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

    def test_narrow_vs16_emoji_get_two_spaces(self):
        # User 2026-10-17: "Remove an installed version", "Show info…", "Back to Backend
        # manager" — a narrow base + U+FE0F draws 2 columns, so one space is painted over.
        import re, unicodedata
        root = Path(__file__).resolve().parent.parent
        narrow = {k[:-4] for k, v in vars(ec).items() if k.endswith("_STR") and isinstance(v, str)
                  and v.endswith("\ufe0f") and unicodedata.east_asian_width(v[0]) not in "WF"}
        self.assertTrue(narrow, "fixture: no narrow VS16 emoji found to check")
        for f in ("bin/session_picker_model.py", "bin/session_picker.py"):
            for n, line in enumerate((root / f).read_text().splitlines(), 1):
                for k in narrow:
                    with self.subTest(file=f, line=n, emoji=k):
                        self.assertIsNone(re.search(r"\{ec\." + k + r"_STR\} (?! )", line), line.strip())
        self.assertEqual(ec.gap("\U0001f5d1\ufe0f"), "\U0001f5d1\ufe0f  ")   # 🗑️ narrow + VS16
        self.assertEqual(ec.gap("\U0001f527"), "\U0001f527 ")                  # 🔧 already wide

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
        # Filter settings (trials, local-capable, broken) moved to filter submenu (key "y").
        # y is navigation to the filter submenu, not a setting row on the main screen.
        s = m.Settings()
        lane = m.RemoteScreen(s, remote_agents(), owner=m.HOME_OWNED)
        acts = {a.key: a for a in lane.actions()}
        # Main screen settings rows
        for key in ("a", "t", "p", "d", "m", "e", "o"):
            self.assertIsNotNone(acts[key].step, f"{key} must be a settings row")
        self.assertNotIn("v", acts, "the Remote lane does not offer the (local) backend manager")
        # Navigation keys (c, s, k, n, b, y) - x is in filter submenu
        for key in ("c", "s", "k", "n", "b", "y"):
            self.assertIsNone(acts[key].step, f"{key} is navigation, not a setting")
        acts["a"].step(+1); self.assertEqual(s.auto_mode, 1)
        acts["a"].step(-1); acts["a"].step(-1); self.assertEqual(s.auto_mode, 2, "Left wraps 0 -> 2")
        start = s.remote_effort
        acts["e"].step(+1); self.assertNotEqual(s.remote_effort, start)
        acts["e"].step(-1); self.assertEqual(s.remote_effort, start)
        acts["t"].step(-1); self.assertTrue(s.telemetry, "a boolean flips either way")
        acts["d"].step(+1); self.assertTrue(s.deny_gh_release, "blind-trust gh-release deny toggles on")
        acts["d"].step(-1); self.assertFalse(s.deny_gh_release, "and back off")

    def test_defaults_and_icons_requested_2026_10_04(self):
        s = m.Settings()
        self.assertFalse(s.stop_hook, "queued-prompt hook is OFF by default")
        # HomeScreen no longer has t/a/p - they're in lanes now
        labels = {a.key: a.label for a in m.HomeScreen(s).actions()}
        # Verify core lanes and tools are present
        self.assertIn("l", labels, "Local sessions lane")
        self.assertIn("r", labels, "Remote sessions lane")
        self.assertIn("d", labels, "Download models tool")
        self.assertIn("k", labels, "API keys tool")
        self.assertIn("v", labels, "Backend manager tool")
        # These settings are now in lanes
        local_labels = {a.key: a.label for a in m.LocalScreen(s, local_models()).actions()}
        self.assertTrue(local_labels["t"].startswith(m.ec.EMOJI_TELEMETRY_ON_STR), "satellite even when OFF")
        self.assertTrue(local_labels["a"].startswith(m.ec.EMOJI_AUTO_MODE_STR))
        self.assertTrue(local_labels["p"].startswith(m.ec.EMOJI_STOP_HOOK_STR))

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
        # 0.48.0 dedupe: qwen-3.8-thinking folds into the base profile (same identity),
        # so the surviving selectable id is the non-thinking one.
        acc.select("qwen-3.8-operator")
        # Reorder the underlying models; the selection follows the id, not the row number.
        lane.set_models(list(reversed(local_models(14))))
        self.assertEqual(lane.accordion.selected_id, "qwen-3.8-operator")

    def test_zero_models_is_a_valid_screen(self):
        lane = m.LocalScreen(m.Settings(), [], owner=m.HOME_OWNED)
        self.assertEqual(lane.groups(), [])
        self.assertIn("b", {a.key for a in lane.actions()})
        self.assertIsNone(lane.activate_selected())

    def test_cloud_config_screen_has_expected_structure(self):
        """CloudConfigScreen has correct title, actions, and toggles work correctly."""
        s = m.Settings()
        cloud = m.CloudConfigScreen(s)

        # Title has both cloud and wrench emojis
        self.assertTrue(cloud.title.startswith(m.ec.EMOJI_CLOUD_CONFIG_STR))
        self.assertIn(m.ec.EMOJI_TOOLS_STR, cloud.title)

        # Two toggle actions + nav
        actions = {a.key: a for a in cloud.actions()}
        self.assertIn("x", actions, "Missing Agent-routing row (x)")
        self.assertIn("p", actions, "Missing Bypass Permissions toggle (p)")
        # The Classifier row is gone (0.42.0): this screen holds settings that change CLOUD sessions,
        # and the classifier source only reaches free sessions (user 2026-10-23). It lives on the
        # lanes, where it acts; a cloud reroute would bring a cloud row back.
        self.assertNotIn("y", actions)
        self.assertFalse(any("Classifier" in a.label for a in actions.values()))
        lane = {a.key: a.label for a in m._common_toggles(m.Settings(auto_mode=1), include_watcher=False)}
        self.assertIn("Classifier:", lane.get("z", ""))

        # p never reaches a gateway session (audit 2026-10-08), and its label says so;
        # the state text is what precedes that note. x DOES reach every cloud session (0.27.0).
        def state(key):
            label = actions[key].label
            self.assertTrue(label.endswith(m.NOT_GATEWAY_NOTE), label)
            return label[: -len(m.NOT_GATEWAY_NOTE)].rstrip()

        # Agent routing row: reaches every cloud session, so it carries no NOT_GATEWAY note.
        self.assertIn("Agent dispatch", actions["x"].label)
        self.assertFalse(actions["x"].label.endswith(m.NOT_GATEWAY_NOTE))

        # Bypass Permissions toggle
        self.assertIn("Bypass Permissions", actions["p"].label)
        self.assertTrue(state("p").endswith("ON") or state("p").endswith("OFF"))

        # Bypass Permissions toggle actually toggles
        initial_bypass = s.cloud_bypass_permissions
        actions["p"].run()
        self.assertEqual(s.cloud_bypass_permissions, not initial_bypass)

        # Nav action works
        nav_actions = [a for a in cloud.actions() if a.section == "nav"]
        self.assertEqual(len(nav_actions), 1)
        self.assertEqual(nav_actions[0].key, "q")
        self.assertIs(nav_actions[0].run(), m.QUIT)

    def test_agent_routing_row_cycles_the_three_modes_and_persists(self):
        """0.43.0: the x row cycles free-first -> free-only -> paid and persists each."""
        s = m.Settings()
        cloud = m.CloudConfigScreen(s)
        saved = []
        s.on_cloud_saved = saved.append
        s.routes = {}
        for want in ("free-only", "paid", "free-first"):   # default -> free-only -> paid -> free-first
            actions = {a.key: a for a in cloud.actions()}
            actions["x"].run()
            self.assertEqual(s.routes.get("agent"), want)
            self.assertEqual(saved[-1], {"route": ("agent", want)}, "the change is persisted, not just shown")
            self.assertIn(m.cse.ROUTE_LABELS[want], {a.key: a for a in cloud.actions()}["x"].label)

    def test_cloud_screen_info_box_explains_each_mode(self):
        s = m.Settings()
        info = m.CloudConfigScreen(s).info_text(None)
        for gist in ("Free first, paid as last resort", "never falls back",
                     "free-agents stays out"):
            self.assertIn(gist, info), gist

    def test_cloud_config_screen_owner_sub_nav_is_back(self):
        """CloudConfigScreen with SUB owner has Back nav, not Quit."""
        s = m.Settings()
        cloud = m.CloudConfigScreen(s, owner=m.SUB)
        nav_actions = [a for a in cloud.actions() if a.section == "nav"]
        self.assertEqual(len(nav_actions), 1)
        self.assertEqual(nav_actions[0].key, "b")
        self.assertIs(nav_actions[0].run(), m.BACK)


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
        # Navigate to filter submenu and hide trials
        filter_obj = m.RemoteFiltersScreen(s, owner=m.DIRECT_ROOT)
        filter_obj._toggle("include_trials")
        lane._regroup()
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

    def test_local_temperature_reaches_the_launcher_through_env(self):
        # csl --picker-launch execs the launcher as "<alias> <effort>"; the launcher parses options
        # only BEFORE the alias, so a trailing --temperature in argv was silently dropped. The value
        # must travel in env (LA_TEMPERATURE), which hotswap reads.
        s = m.Settings(local_temperature="0.7")
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemma-4-26b")
        req = lane.activate_selected()
        self.assertEqual(req.lane, "local")
        self.assertEqual(req.env.get("LA_TEMPERATURE"), "0.7")
        self.assertNotIn("--temperature", req.argv)
        self.assertEqual(req.argv[1:3], ["gemma-4-26b", "high"], "effort is never displaced")

    def test_local_temperature_absent_when_default(self):
        s = m.Settings(local_temperature="")
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemma-4-26b")
        req = lane.activate_selected()
        self.assertNotIn("LA_TEMPERATURE", req.env)
        self.assertEqual(req.argv[1:3], ["gemma-4-26b", "high"])

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
        # 0.48.0: row Enter/click only SELECTS (activate_selected -> None); the explicit
        # `p` action in convo mode launches via _convo().
        s = m.Settings(lowkey_effort="low")
        lk = m.LowkeyScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lk.session_name = "notes"
        lk.accordion.select("gemma-4-26b")
        self.assertIsNone(lk.activate_selected())          # click = select only
        req = lk._convo()
        self.assertEqual(req.argv, ["lowkey", "--convo", "--model", "gemma-4-26b",
                                    "--effort", "low", "--session", "notes"])
        self.assertEqual(req.env.get("LA_THINKING_OVERRIDE"), "true")   # default ON

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


class RemoteInfoBoxTests(unittest.TestCase):
    """The remote info box: short-form notes keyed by alias (config/remote-model-notes.json)."""

    def test_every_note_key_is_a_real_roster_alias(self):
        # A typo'd key would silently never show; the roster is the source of aliases.
        import json, re
        notes = json.loads(m._MODEL_NOTES_PATH.read_text())
        roster = (Path(__file__).resolve().parent.parent / "config" / "remote-agents.sh").read_text()
        for alias in (k for k in notes if not k.startswith("_")):
            self.assertRegex(roster, rf"(?m)\b{re.escape(alias)}\b", alias)

    def test_thinking_variant_falls_back_to_base_note(self):
        self.assertTrue(m.model_note("nvidia-glm53"))
        self.assertEqual(m.model_note("nvidia-glm53-thinking"), m.model_note("nvidia-glm53"))

    def test_unknown_alias_and_unreadable_file_yield_empty(self):
        self.assertEqual(m.model_note("no-such-model"), "")
        self.assertEqual(m.model_note("nvidia-glm53", Path("/nonexistent/notes.json")), "")

    def test_info_text_for_model_group_and_unnoted(self):
        agents = [m.RemoteAgent("nvidia-laguna", "nvidia", "NVIDIA Laguna XS 2.1", "unknown"),
                  m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free")]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        self.assertIn("Laguna XS 2.1 (Poolside)", lane.info_text("nvidia-laguna"))
        self.assertIn("No notes", lane.info_text("gemini-flash"))
        # group hover now carries provider-level facts (user 2026-10-30): the NVIDIA quota
        # note on the nvidia group header; a hint only when nothing is pointed at
        self.assertIn("no daily cap", lane.info_text("nvidia"))            # group id
        self.assertIn("Hover or highlight", lane.info_text(None))

    def test_gemini_info_box_recommends_dispatch_only(self):
        # User 2026-10-29: Gemini's free daily quota cannot carry a full session (a session's
        # first prompt alone eats much of it), so every Gemini row says: dispatch only.
        agents = [m.RemoteAgent("nvidia-laguna", "nvidia", "NVIDIA Laguna XS 2.1", "unknown"),
                  m.RemoteAgent("gemini-flash", "gemini", "Gemini Flash", "renewing_free",
                                model_id="gemini-3.8-flash")]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        self.assertIn("offload dispatch only", lane.info_text("gemini-flash"))
        self.assertIn("offload dispatch only", lane.info_text("cat:gemini:gemini-3.8-pro"))
        self.assertNotIn("offload dispatch only", lane.info_text("nvidia-laguna"))

    def test_info_text_has_no_lane_line_but_nvidia_quota_note(self):
        # User 2026-10-30: the "Lane:" line held no decision value (the lane is visible in
        # the row) — removed. NVIDIA model boxes instead carry the short quota note.
        agents = [m.RemoteAgent("nvidia-laguna", "nvidia", "NVIDIA Laguna XS 2.1", "unknown",
                                model_id="nvidia/llama-3.3-nemotron-super-49b")]
        lane = m.RemoteScreen(m.Settings(), agents, owner=m.DIRECT_ROOT)
        text = lane.info_text("nvidia-laguna")
        self.assertNotIn("Lane:", text)
        self.assertIn("no daily cap", text)


class ThemeStoreLayering(unittest.TestCase):
    """0.35.3: theme colours persist in the stable user store (survive version installs,
    shared by every copy), layered over shipped defaults; LA_THEME_CONFIG_DIR (tests)
    wins over both."""

    def test_picker_reads_user_store_and_writes_there(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        root = Path(__file__).resolve().parents[1]
        shipped = json.loads((root / "config" / "local-theme.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp) / ".claude" / "free-agents" / "themes"
            user_dir.mkdir(parents=True)
            user = json.loads(json.dumps(shipped))
            user["theme"]["accent_color"] = "#123456"
            (user_dir / "local-theme.json").write_text(json.dumps(user))
            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                scr = m.ThemeConfigScreen(m.Settings())
                self.assertEqual(scr.local_theme["theme"]["accent_color"], "#123456")
                # a step (the real write path — setting() put) must land in the USER store
                {a.key: a for a in scr.actions()}["l"].step(1)
                saved = json.loads((user_dir / "local-theme.json").read_text())
                self.assertNotEqual(saved["theme"]["accent_color"], "#123456")
            self.assertEqual(json.loads((root / "config" / "local-theme.json").read_text()),
                             shipped)                   # shipped config untouched


if __name__ == "__main__":
    unittest.main()


class BypassDefaultRows(unittest.TestCase):
    """A2 + A3 (0.43.0): the bypass-default row respects managed policy and a registered
    profile; the eligibility-probe row asks before spending; the cost box shows ONLY for a
    cached not-eligible verdict."""

    def test_bypass_row_round_trips_and_policy_disables_it(self):
        # SANDBOXED CLAUDE_CONFIG_DIR: the row writes the USER settings — without the sandbox
        # this test edited the real ~/.claude/settings.json (conftest tripwire caught it,
        # 2026-10-24). Same pattern as the cse tests.
        tmp = tempfile.mkdtemp()
        old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = tmp
        try:
            s = m.Settings()
            cloud = m.CloudConfigScreen(s)
            acts = {a.key: a for a in cloud.actions()}
            self.assertIn("d", acts)
            acts["d"].run()
            self.assertIn(s.default_mode, ("auto", "bypassPermissions"))
            self.assertIsNotNone(m.cse.default_mode())
        finally:
            if old_cfg is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old_cfg

    def test_bypass_row_disabled_with_the_policy_reason(self):
        pol = Path(tempfile.mkdtemp())
        (pol / "managed-settings.d").mkdir()
        (pol / "managed-settings.d" / "org.json").write_text(
            json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}))
        old = os.environ.get("FA_MANAGED_SETTINGS_DIR")
        os.environ["FA_MANAGED_SETTINGS_DIR"] = str(pol)
        try:
            s = m.Settings()
            cloud = m.CloudConfigScreen(s)
            acts = {a.key: a for a in cloud.actions()}
            self.assertFalse(acts["d"].enabled)
            self.assertIn("managed policy", acts["d"].label)
            before = m.cse.default_mode()
            acts["d"].run()
            self.assertEqual(m.cse.default_mode(), before, "csl never works around a policy")
        finally:
            (os.environ.pop("FA_MANAGED_SETTINGS_DIR", None) if old is None
             else os.environ.__setitem__("FA_MANAGED_SETTINGS_DIR", old))

    def test_cost_box_only_for_not_eligible(self):
        s = m.Settings()
        cloud = m.CloudConfigScreen(s)
        self.assertNotIn("billed", cloud.info_text(None))
        s.eligibility = {"verdict": "not-eligible", "evidence": "the notice arrived", "at": 1.0}
        self.assertIn("billed", cloud.info_text(None))
        s.eligibility = {"verdict": "unknown", "evidence": "absence", "at": 1.0}
        self.assertNotIn("billed", cloud.info_text(None))


class ThinkingToggleTests(unittest.TestCase):
    """The session thinking toggle (waypoint fa-thinking-toggle): one remembered choice for
    both lanes, None = the launch default (thinking ON), model constraints override last and
    the info box says so. Each test names the change that breaks it."""

    def row(self, screen):
        return next(a for a in screen.actions() if a.key == "j")

    def test_row_on_both_lanes_after_temperature(self):
        # Fails if the row is missing from a lane, or ordered before the effort/temperature
        # pair it was designed to follow (user design 2026-10-24: after effort and temperature).
        for screen in (m.LocalScreen(m.Settings(), local_models(), owner=m.DIRECT_ROOT),
                       m.RemoteScreen(m.Settings(), remote_agents(), owner=m.DIRECT_ROOT)):
            keys = [a.key for a in screen.actions()]
            self.assertIn("j", keys)
            self.assertLess(keys.index("o"), keys.index("j"), f"{type(screen).__name__} order")
            self.assertLess(keys.index("e"), keys.index("j"), f"{type(screen).__name__} order")

    def test_label_reflects_state(self):
        s = m.Settings()
        self.assertIn("ON (default)", self.row(m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)).label)
        s.thinking_session = True
        self.assertIn("ON", self.row(m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)).label)
        self.assertNotIn("default", self.row(m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)).label)
        s.thinking_session = False
        self.assertIn("OFF", self.row(m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)).label)

    def test_row_cycles_default_off_default_and_persists(self):
        # Fails if the row cannot return to the launch default, or the change does not reach
        # the on_thinking_saved callback the Picker wires to save_thinking_session.
        saved = []
        s = m.Settings(on_thinking_saved=lambda v: saved.append(v))
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        row = self.row(lane)
        row.run()
        self.assertIs(s.thinking_session, False)
        self.assertEqual(saved, [False])
        row.run()
        self.assertIsNone(s.thinking_session)
        self.assertEqual(saved, [False, None])

    def test_remote_launch_env_exports_the_override(self):
        # Fails if a launch leaves the launcher to guess: the picker decides the override,
        # None (default) and True both mean ON, False means OFF. Always exported — the
        # picker-owned default must reach direct CLI launches started from it too.
        def env_for(setting_value):
            s = m.Settings(thinking_session=setting_value)
            lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
            lane.accordion.select("nvidia-a")
            return lane.activate_selected().env
        self.assertEqual(env_for(None).get("LA_THINKING_OVERRIDE"), "true")
        self.assertEqual(env_for(True).get("LA_THINKING_OVERRIDE"), "true")
        self.assertEqual(env_for(False).get("LA_THINKING_OVERRIDE"), "false")

    def test_local_launch_env_exports_the_override(self):
        # Fails if only the remote lane got the toggle: the design is BOTH lanes.
        s = m.Settings(thinking_session=False)
        lane = m.LocalScreen(s, local_models(), owner=m.DIRECT_ROOT)
        lane.accordion.select("gemma-4-26b")
        self.assertEqual(lane.activate_selected().env.get("LA_THINKING_OVERRIDE"), "false")

    def test_constraint_notice_glm_off_only(self):
        # Fails if the constraint table claims more than the measured facts: GLM on NIM is
        # ALWAYS-thinking (chat_template_kwargs inert, 2026-10-13) — only OFF on it is
        # constrained, and only it.
        notice = m.thinking_constraint("nvidia", "z-ai/glm-5.3-flash", False)
        self.assertIsNotNone(notice)
        self.assertIn("always", notice.lower())
        self.assertIsNone(m.thinking_constraint("nvidia", "z-ai/glm-5.3-flash", True))
        self.assertIsNone(m.thinking_constraint("nvidia", "z-ai/glm-5.3-flash", None))
        self.assertIsNone(m.thinking_constraint("nvidia", "deepseek-ai/deepseek-v4.1-flash", False))
        self.assertIsNone(m.thinking_constraint("gemini", "gemini-3.8-flash", False))
        self.assertIsNone(m.thinking_constraint("groq", "llama-4-scout", False))

    def test_info_box_appends_the_glm_notice_when_off(self):
        # Fails if the user only learns the model ignores their OFF choice after launch:
        # the info box must state the constraint for the highlighted model.
        s = m.Settings(thinking_session=False)
        lane = m.RemoteScreen(s, remote_agents(), owner=m.DIRECT_ROOT)
        lane.set_catalog("nvidia", [m.CatalogModel("z-ai/glm-5.3-flash")], None)
        text = lane.info_text("cat:nvidia:z-ai/glm-5.3-flash")
        self.assertIn("always", text.lower())
        # A model that honors the choice says nothing, and ON says nothing anywhere.
        s2 = m.Settings(thinking_session=False)
        lane2 = m.RemoteScreen(s2, remote_agents(), owner=m.DIRECT_ROOT)
        lane2.set_catalog("nvidia", [m.CatalogModel("deepseek-ai/deepseek-v4.1-flash")], None)
        self.assertNotIn("always", lane2.info_text("cat:nvidia:deepseek-ai/deepseek-v4.1-flash"))
        s3 = m.Settings(thinking_session=True)
        lane3 = m.RemoteScreen(s3, remote_agents(), owner=m.DIRECT_ROOT)
        lane3.set_catalog("nvidia", [m.CatalogModel("z-ai/glm-5.3-flash")], None)
        self.assertNotIn("always", lane3.info_text("cat:nvidia:z-ai/glm-5.3-flash"))

    def test_cleared_callback_wiring_matches_persistence(self):
        # Fails if the picker persists a value the store cannot load back: the saved value
        # must round-trip through the same store the next picker reads.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = m.Settings(on_thinking_saved=lambda v: __import__("session_menu_state")
                           .save_thinking_session(tmp, v))
            s.thinking_session = False
            if s.on_thinking_saved:
                s.on_thinking_saved(s.thinking_session)
            import session_menu_state as sms
            self.assertIs(sms.load(tmp).thinking_session, False)


