#!/usr/bin/env python3
"""tests/test_picker_dedupe_0474.py — 0.47.4: one row per model in both lane pickers.

The -thinking duplicate rows were a workaround from before the `j` thinking toggle existed
(user 2026-10-29). The toggle owns the mode now; the launcher's capability table
(THINKING_REQUIRED_MODELS / NO_THINKING_MODELS in remote-session.sh) forces it where a
model requires or lacks thinking, with a launch notice. Each test names what breaks it."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402


class RemoteDedupeTests(unittest.TestCase):
    def test_thinking_variant_collapses_into_the_base_alias(self):
        agents = [m.RemoteAgent("nvidia-glm53-thinking", "nvidia", "GLM 5.3 Flash (thinking)", "unknown",
                                model_id="z-ai/glm-5.3-flash"),
                  m.RemoteAgent("nvidia-glm53", "nvidia", "GLM 5.3 Flash", "unknown",
                                model_id="z-ai/glm-5.3-flash"),
                  m.RemoteAgent("nvidia-nemotron-ultra", "nvidia", "Nemotron 3 Ultra", "unknown",
                                model_id="nvidia/nemotron-3-ultra-550b-a55b")]
        lane = m.RemoteScreen(m.Settings(), agents)
        ids = [i.id for g in lane.groups() if g.id == "nvidia" for i in g.items]
        # one row per model_id; the base (non--thinking) alias represents it
        self.assertEqual(ids, ["nvidia-glm53", "nvidia-nemotron-ultra"])

    def test_first_seen_order_preserved_when_thinking_variant_leads(self):
        # the curated nvidia order lists the -thinking variant first; the surviving row
        # keeps that model's position, not the alias's own position
        agents = [m.RemoteAgent("a-x-thinking", "groq", "X (thinking)", "renewing_free", model_id="x"),
                  m.RemoteAgent("b-y", "groq", "Y", "renewing_free", model_id="y"),
                  m.RemoteAgent("a-x", "groq", "X", "renewing_free", model_id="x")]
        lane = m.RemoteScreen(m.Settings(), agents)
        ids = [i.id for g in lane.groups() if g.id == "groq" for i in g.items]
        self.assertEqual(ids, ["a-x", "b-y"])

    def test_distinct_models_are_not_merged(self):
        agents = [m.RemoteAgent("g-a", "groq", "A", "renewing_free", model_id="a"),
                  m.RemoteAgent("g-b", "groq", "B", "renewing_free", model_id="b")]
        lane = m.RemoteScreen(m.Settings(), agents)
        self.assertEqual(len(lane.groups()[0].items), 2)


class LocalDedupeTests(unittest.TestCase):
    def test_profiles_differing_only_in_thinking_collapse(self):
        base = dict(alias="qwen38", effort="medium", roles="", family="Qwen", backend="rapid-mlx",
                    tool_parser="", reasoning_parser="", folder="Qwen3.8-27B-4bit")
        models = [m.LocalModel(**base, profile_id="qwen38", thinking=False),
                  m.LocalModel(**base, profile_id="qwen38-thinking", thinking=True)]
        lane = m.LocalScreen(m.Settings(), models)
        self.assertEqual(list(lane.models), ["qwen38"])   # non-thinking profile represents
        self.assertEqual(len(lane._all_models), 1)

    def test_different_folders_stay_separate(self):
        a = dict(alias="m1", effort="medium", roles="", folder="F1")
        b = dict(alias="m1", effort="medium", roles="", folder="F2")   # same alias, other weights
        lane = m.LocalScreen(m.Settings(), [m.LocalModel(**a), m.LocalModel(**b)])
        self.assertEqual(len(lane._all_models), 2)

    def test_same_folder_different_effort_or_roles_still_one_model(self):
        # qwen "operator" vs "-thinking" profiles share weights; effort/roles are profile
        # choices, not model identity (user 2026-10-30) — one row, non-thinking wins.
        base = dict(alias="qwen38", effort="medium", roles="operator", folder="F", backend="rapid-mlx")
        other = dict(base, effort="max", roles="reasoner", thinking=True, alias="qwen38-thinking")
        models = [m.LocalModel(**base, profile_id="p1", thinking=False),
                  m.LocalModel(**other, profile_id="p2")]
        lane = m.LocalScreen(m.Settings(), models)
        self.assertEqual(len(lane._all_models), 1)
        self.assertEqual(list(lane.models), ["p1"])

    def test_different_backend_config_stays_separate(self):
        base = dict(alias="m1", effort="medium", roles="", folder="F", backend="rapid-mlx")
        other = dict(base, backend="omlx")
        models = [m.LocalModel(**base, profile_id="p1", thinking=False),
                  m.LocalModel(**other, profile_id="p2", thinking=True)]
        lane = m.LocalScreen(m.Settings(), models)
        self.assertEqual(len(lane._all_models), 2)


class LowkeyLabelTests(unittest.TestCase):
    """0.48.0: Lowkey labels must render real emoji (never a literal "${VAR}" from
    emoji.sh indirection) and the nav status line has one source of truth."""

    def test_no_unresolved_emoji_vars_in_lowkey_labels(self):
        lk = m.LowkeyScreen(m.Settings(), [])
        for a in lk.actions():
            self.assertNotIn("${", str(a.label), a.label)

    def test_status_line_single_source_pinned(self):
        self.assertEqual(m.status_line(None),
                         "  ↑↓ move · Enter/click select · ←→ change setting · drag selects text, ⌘C/Ctrl+C copies")
        src = (ROOT / "bin" / "session_picker.py").read_text()
        self.assertIn("m.status_line(sm)", src)   # the picker must not inline its own copy


if __name__ == "__main__":
    unittest.main()
