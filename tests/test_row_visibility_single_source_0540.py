"""0.54.0 Task 4 — ONE row-visibility function for every list (duplicates + hidden providers).

The hidden-provider filter was applied at 3 independent sites and the -thinking dedupe rule was
copy-pasted at 2 sites in session_picker_model.py; any new list that forgot one copy regressed.
0.54.0: `visible_agents()` / `is_provider_hidden()` are the single home, every screen builds its
rows through them, and a source scan fails when a new read of `hidden_providers` appears outside
the frozen allow-list. The property test enumerates EVERY Screen subclass by introspection and
asserts over its rendered accordion: no duplicate item ids, no row of a hidden provider.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402

HIDDEN = "hiddenprov"


def fixture_agents():
    """base + -thinking pair (same model_id), an alias-only agent, a SELECT placeholder, a
    keyless provider's row, and the hidden provider's rows (pinned)."""
    return [
        m.RemoteAgent("nvidia-x", "nvidia", "Model X", "unknown", model_id="vendor/x"),
        m.RemoteAgent("nvidia-x-thinking", "nvidia", "Model X (thinking)", "unknown",
                      model_id="vendor/x"),
        m.RemoteAgent("alias-only", "mistral", "Alias Only", "unknown", model_id=""),
        m.RemoteAgent("nvidia-choose", "nvidia", "Choose…", "unknown", model_id="SELECT"),
        m.RemoteAgent("nokey-a", "nokey", "No Key A", "unknown", model_id="nk/a", has_key=False),
        m.RemoteAgent(f"{HIDDEN}-a", HIDDEN, "Hidden A", "unknown", model_id="hp/a"),
    ]


def fixture_settings():
    s = m.Settings()
    s.hidden_providers = [HIDDEN]
    return s


def _all_item_ids(accordion):
    """Every item id in the accordion, recursing into nested groups."""
    ids = []

    def walk(groups):
        for g in groups:
            ids.extend(i.id for i in g.items)
            walk(getattr(g, "groups", None) or [])

    walk(accordion.groups)
    return ids


# --- unit tests of the one function ---------------------------------------------------------


class VisibleAgentsTests(unittest.TestCase):
    def test_select_and_empty_rows_dropped(self):
        out = m.visible_agents(fixture_settings(), fixture_agents())
        keys = [(a.provider, a.model_id or a.alias) for a in out]
        self.assertNotIn(("nvidia", "SELECT"), keys)
        self.assertTrue(all(a.model_id or a.alias for a in out))

    def test_hidden_provider_dropped(self):
        out = m.visible_agents(fixture_settings(), fixture_agents())
        self.assertNotIn(HIDDEN, {a.provider for a in out})

    def test_keyless_row_dropped_even_with_filters_off(self):
        out = m.visible_agents(fixture_settings(), fixture_agents(), remote_filters=False)
        self.assertNotIn("nokey", {a.provider for a in out})

    def test_dedupe_keeps_first_non_thinking_at_first_seen_position(self):
        out = m.visible_agents(fixture_settings(), fixture_agents(), remote_filters=False)
        xs = [a for a in out if a.provider == "nvidia"]
        self.assertEqual([a.alias for a in xs], ["nvidia-x"],
                         "the -thinking twin must collapse into the base alias")

    def test_remote_filters_opt_in(self):
        agents = fixture_agents() + [
            m.RemoteAgent("trial-a", "nvidia", "Trial A", "trial", model_id="vendor/t"),
            m.RemoteAgent("broken-a", "nvidia", "Broken A", "unknown", model_id="vendor/b",
                          broken=True),
        ]
        s = fixture_settings()
        s.include_trials = False          # trial hidden by the Remote-screen filters
        off = m.visible_agents(s, agents, remote_filters=False)
        on = m.visible_agents(s, agents, remote_filters=True)
        self.assertIn("vendor/t", {a.model_id for a in off}, "filters OFF shows trial rows")
        self.assertNotIn("vendor/t", {a.model_id for a in on}, "filters ON hides trial rows")
        self.assertNotIn("vendor/b", {a.model_id for a in on}, "broken hidden when filters ON")
        self.assertIn("vendor/b", {a.model_id for a in off}, "broken shown when filters OFF")

    def test_is_provider_hidden(self):
        s = fixture_settings()
        self.assertTrue(m.is_provider_hidden(s, HIDDEN))
        self.assertFalse(m.is_provider_hidden(s, "nvidia"))


# --- property test over EVERY screen ---------------------------------------------------------

_SCREEN_EXTRAS = {
    "AboutScreen": {},
    "HomeScreen": {},
    "LocalScreen": {"models": [m.LocalModel(alias="qwen-3.8-operator", family="qwen")]},
    "RemoteScreen": {"agents": fixture_agents()},
    "RemoteFiltersScreen": {},
    "CloudConfigScreen": {},
    "ThemeConfigScreen": {},
    "RateLimiterScreen": {},
    "MemoryGuardScreen": {},
    "VersionsScreen": {"op": "info", "entries": []},
    "RuntimeManagerScreen": {},
    "APIKeysScreen": {},
    "RuntimeScreen": {},
    "LaunchdScreen": {},
    "LowkeyScreen": {"models": [m.LocalModel(alias="qwen-3.8-operator", family="qwen")],
                     "api_agents": fixture_agents()},
    "DownloadScreen": {"entries": [], "free_gb": 100.0, "headroom_gb": 10.0},
}


def _screen_subclasses():
    out = []
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and issubclass(obj, m.Screen) and obj is not m.Screen \
                and obj.__module__ == m.__name__:
            out.append(obj)
    return sorted(out, key=lambda c: c.__name__)


class EveryScreenPropertyTests(unittest.TestCase):
    def test_every_screen_has_unique_item_ids_and_no_hidden_rows(self):
        subclasses = _screen_subclasses()
        self.assertGreater(len(subclasses), 5, "the enumeration must actually find the screens")
        s = fixture_settings()
        built = []
        for cls in subclasses:
            extras = dict(_SCREEN_EXTRAS.get(cls.__name__, {}))
            screen = cls(s, **extras)          # a screen that fails to construct FAILS the test
            built.append((cls.__name__, screen))
        self.assertEqual(len(built), len(subclasses))
        # the fixture itself carries hidden-provider rows (pinned AND catalog) before filtering
        agents = fixture_agents()
        self.assertTrue(any(a.provider == HIDDEN for a in agents), "fixture lost its hidden rows")
        for name, screen in built:
            if isinstance(screen, m.RemoteScreen):
                screen.catalogs = {HIDDEN: [m.CatalogModel(f"{HIDDEN}/cat-1")],
                                   "nvidia": [m.CatalogModel("vendor/x"), m.CatalogModel("vendor/cat-2")]}
                screen._regroup()
            acc = getattr(screen, "accordion", None)
            if acc is None:
                continue                          # screens without an accordion have no rows
            ids = _all_item_ids(acc)
            self.assertEqual(len(ids), len(set(ids)),
                             f"{name}: duplicate item ids {sorted(ids)}")
            self.assertFalse(any(HIDDEN in i for i in ids),
                             f"{name}: a hidden-provider row rendered: {ids}")


# --- the source scan: hidden_providers has ONE home -----------------------------------------

# (file, class, def) triples where a `hidden_providers` occurrence is allowed. None = any.
_ALLOWED = [
    ("bin/session_picker_model.py", "Settings", None),          # the dataclass field
    ("bin/session_picker_model.py", None, "is_provider_hidden"), # the one read home
    ("bin/session_picker_model.py", None, "visible_agents"),     # routes through the above
    ("bin/session_picker_model.py", "APIKeysScreen", "activate_selected"),  # the toggle that WRITES
    ("bin/session_picker.py", None, "_save_hidden_providers"),    # the save path
    ("bin/session_picker.py", "Picker", "__init__"),              # 0.54.0: Settings built from saved state (reported allow-list widening)
]


def _enclosing(src_lines, idx):
    """(class, def) enclosing line index idx — nearest preceding class/def headers."""
    cls = dfn = None
    for j in range(idx + 1):
        c = re.match(r"^class (\w+)", src_lines[j])
        if c:
            cls, dfn = c.group(1), None
            continue
        d = re.match(r"^\s*def (\w+)", src_lines[j])
        if d and cls is not None:
            dfn = d.group(1)
        elif d and cls is None:
            cls, dfn = "<module>", d.group(1)
    return cls, dfn


class OneHomeSourceScanTests(unittest.TestCase):
    def test_visibility_rule_has_one_home(self):
        bad = []
        for rel in ("bin/session_picker_model.py", "bin/session_picker.py",
                    "bin/session_menu_state.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            if rel.endswith("session_menu_state.py"):
                continue                          # whole file allow-listed (state schema + save)
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if "hidden_providers" not in line or line.lstrip().startswith("#"):
                    continue
                cls, dfn = _enclosing(lines, i)
                if not any(f == rel and (c is None or c == cls) and (d is None or d == dfn)
                           for f, c, d in _ALLOWED):
                    bad.append(f"{rel}:{i+1} (class={cls} def={dfn}): {line.strip()[:80]}")
        self.assertEqual(bad, [], "hidden_providers read outside the allow-list:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
