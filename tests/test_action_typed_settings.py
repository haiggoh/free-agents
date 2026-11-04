"""tests/test_action_typed_settings.py — nicegui plan Task 5: every stepping Action row must
carry typed metadata (choices/value/put) so GUI adapters can render real controls (select /
toggle) instead of parse-the-label or cycle-a-button. `name` is the label without the current
value; empty `id` means adapters derive f"{type(screen).__name__}.{key}"."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import session_picker_model as m


def synthetic_settings() -> m.Settings:
    s = m.Settings()
    s.last_launched_model = {"local_session": "qwen-3.8-operator",
                             "remote_api_session": "nvidia-a", "lowkey": None}
    s.last_launched_tier = {"remote_api_session": "unknown"}
    s.routes = {"agent": "free-first"}
    s.hw_hardware = "mlx"
    return s


def all_screens():
    """Every Screen subclass over a synthetic populated Settings (ledger ruling: test-local
    helper over the model classes; build_screen arrives in Task 6)."""
    s = synthetic_settings()
    agents = [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="nvidia-a")]
    local = [m.LocalModel("qwen-3.8-operator", family="qwen")] if hasattr(m, "LocalModel") else []
    screens = [
        m.HomeScreen(s),
        m.LocalScreen(s, local),
        m.RemoteScreen(s, agents),
        m.LowkeyScreen(s, local, api_agents=agents),
        m.APIKeysScreen(s),
        m.RuntimeManagerScreen(s),
        m.VersionsScreen(s, "promote", owner=m.SUB),
        m.DownloadScreen(s, [], 100.0, 20.0),
        m.RateLimiterScreen(s),
        m.RemoteFiltersScreen(s),
        m.CloudConfigScreen(s),
        m.ThemeConfigScreen(s),
        m.LaunchdScreen(s),
        m.AboutScreen(s),
    ]
    if hasattr(m, "MemoryGuardScreen"):
        screens.append(m.MemoryGuardScreen(s))
    return screens


class TypedSettingMetadata(unittest.TestCase):
    def test_every_stepping_row_is_typed(self):
        for scr in all_screens():
            for a in scr.actions():
                if a.step is not None:
                    with self.subTest(screen=type(scr).__name__, key=a.key):
                        self.assertIsNotNone(a.choices, "choices missing")
                        self.assertIsNotNone(a.value, "value getter missing")
                        self.assertIsNotNone(a.put, "put missing")
                        self.assertTrue(callable(a.value) and callable(a.put))

    def test_put_typed_value_equals_step_effect(self):
        s = m.Settings()
        s.telemetry = False
        a = m.setting("t", "Telemetry", (False, True),
                      lambda: s.telemetry, lambda v: setattr(s, "telemetry", v))
        a.put(True)
        self.assertIs(s.telemetry, True, "put(True) must set the field")
        s.telemetry = False
        a.step(1)
        self.assertIs(s.telemetry, True, "one step(+1) from False must equal put(True)")

    def test_setting_carries_name_and_semantic_id_defaults(self):
        s = m.Settings()
        a = m.setting("t", "Telemetry: OFF", (False, True), lambda: s.telemetry,
                      lambda v: setattr(s, "telemetry", v), name="Telemetry")
        self.assertEqual(a.name, "Telemetry")
        self.assertEqual(a.id, "")          # empty => adapters derive it
        self.assertIsNotNone(a.choices)


if __name__ == "__main__":
    unittest.main()
