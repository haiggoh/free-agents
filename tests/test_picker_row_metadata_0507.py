#!/usr/bin/env python3
"""tests/test_picker_row_metadata_0507.py — 0.50.8 Task 3: param size in every nvidia row
name, infobox name-dedupe, large→small sort of non-pinned rows.

User (verbatim): nvidia rows must carry the parameter size in the ENTRY NAME (GLM 5.3 Flash,
DeepSeek V4.1 Flash, Yi Large, Microsoft Kosmos, MiniMax M3.1 Flash, Snowflake Arctic Embed,
Kimi K2.6, older Mistral ids…), sourced from the NVIDIA endpoint/docs or HuggingFace when no
curated infobox text exists; the default infobox must not repeat the model name; non-pinned
items sorted large→small descending.

Sources verified 2026-11-03: HF (online, via the warm-up cache) resolves glm-5.3-flash,
step-5-preview, deepseek-v4.1-flash, arctic-embed-l, kimi-k2.6, glm-5.3, mistral-7b. The
gated/unmapped ids resolve through PARAM_COUNT_OVERRIDES: yi-large 102.6 B MoE (InferenceBench
2024-05-13), kosmos-2 1.66 B (build.nvidia.com card: Parameters 1,664,485,376), minimax-m3.1
428 B total / 23 B active (MiniMax M3.1 Flash, open weights), mistral-large-2411 123 B,
mistral-small-2503 24 B (Mistral Small 3).

Mutation anchors: dropping an override entry → test_named_nvidia_models_resolve_a_size red;
unsorted catalog → sort test red; name-echoing infobox → dedupe test red.

Usage: python3 tests/test_picker_row_metadata_0507.py -v   (or pytest)
"""
import os
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive  # some tests drive model_param_count (HF cache reads)

# The user-named nvidia models that must carry a size in the row name.
NAMED_NVIDIA_IDS = [
    "z-ai/glm-5.3-flash",          # GLM 5.3 Flash
    "deepseek-ai/deepseek-v4.1-flash",  # DeepSeek V4.1 Flash
    "01-ai/yi-large",              # Yi Large
    "microsoft/kosmos-2",          # Microsoft Kosmos(-2)
    "minimaxai/minimax-m3.1",      # MiniMax M3.1 Flash
    "snowflake/arctic-embed-l",    # Snowflake Arctic Embed
    "moonshotai/kimi-k2.6",        # Kimi K2.6
    "mistralai/mistral-large-2411",    # older Mistral id
    "mistralai/mistral-small-2503",    # older Mistral id
]


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class NamedNvidiaSizes(unittest.TestCase):
    def setUp(self):
        # model_param_count reads the HF cache offline-only; point it at a throwaway cache so
        # the test never sees nor poisons the real one, and never waits on the network.
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="fa-hf-cache-")
        self._saved = os.environ.get("LA_HF_PARAMS_CACHE")
        os.environ["LA_HF_PARAMS_CACHE"] = os.path.join(self._tmp, "hf.json")
        self.addCleanup(self._restore)

    def _restore(self):
        import shutil
        if self._saved is None:
            os.environ.pop("LA_HF_PARAMS_CACHE", None)
        else:
            os.environ["LA_HF_PARAMS_CACHE"] = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_every_named_nvidia_model_resolves_a_size(self):
        assert sp is not None
        missing = [mid for mid in NAMED_NVIDIA_IDS
                   if sp.model_param_count("nvidia", mid) is None]
        self.assertEqual(missing, [],
                         f"no parameter size for: {missing} — the row name cannot carry it")


class RowLabelsAndSort(unittest.TestCase):
    """Model-level: row names carry the size, sort is large→small, infobox dedupes the name."""

    def _screen(self, sizes):
        s = m.Settings()
        s.model_param_count = lambda prov, mid: sizes.get(mid)
        agents = [m.RemoteAgent("nvidia-big", "nvidia", "NVIDIA Big", "unknown", model_id="x/big"),
                  m.RemoteAgent("nvidia-small", "nvidia", "NVIDIA Small", "unknown", model_id="x/small")]
        sm = m.RemoteScreen(s, agents)
        sm._regroup()
        return sm

    def test_pin_row_label_carries_size_and_never_duplicates_it(self):
        sm = self._screen({"x/big": 123.0})
        label = sm._pin_row_label("nvidia", next(a for a in sm.agents if a.alias == "nvidia-big"))
        self.assertIn("123B", label, f"size missing from row name: {label!r}")
        # word-boundary dedupe: a display already stating the size must not get it twice
        sm2 = self._screen({"x/big": 123.0})
        a = next(a for a in sm2.agents if a.alias == "nvidia-big")
        a.display = "NVIDIA Big 123B"
        label2 = sm2._pin_row_label("nvidia", a)
        self.assertEqual(label2.count("123B"), 1, f"size duplicated in row name: {label2!r}")

    def test_catalog_rows_sorted_large_to_small_unknowns_last(self):
        sizes = {"x/big": 300.0, "x/mid": 30.0, "x/small": 3.0, "x/huge": 1050.0}
        sm = self._screen(sizes)
        sm.catalogs["nvidia"] = [m.CatalogModel(mid) for mid in
                                 ("x/small", "x/big", "x/unsized", "x/huge", "x/mid")]
        items = sm._catalog_items("nvidia", [])
        ids = [i.id for i in items]
        self.assertEqual(ids, ["cat:nvidia:x/huge", "cat:nvidia:x/big",
                               "cat:nvidia:x/mid", "cat:nvidia:x/small",
                               "cat:nvidia:x/unsized"],
                         f"catalog rows must sort large→small, unknowns last: {ids}")
        # and each sized row's label carries the size
        labels = {i.id: i.label for i in items}
        self.assertIn("1.05T", labels["cat:nvidia:x/huge"])
        self.assertIn("300B", labels["cat:nvidia:x/big"])

    def test_infobox_does_not_repeat_the_model_name(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        notes = tmp / "notes.json"
        notes.write_text('{"nvidia-big": "NVIDIA Big is huge, 123B params, great for big jobs."}', encoding="utf-8")
        # model_note accepts a path argument
        s = m.Settings()
        agents = [m.RemoteAgent("nvidia-big", "nvidia", "NVIDIA Big", "unknown", model_id="x/big")]
        sm = m.RemoteScreen(s, agents)
        sm._regroup()
        # monkeypatch model_note resolution by pointing the module-level loader at our file
        real = m.model_note
        m.model_note = lambda alias, path=None: real(alias, path=notes)
        try:
            text = sm.info_text("nvidia-big")
        finally:
            m.model_note = real
        lines = text.splitlines()
        self.assertEqual(lines[0], "NVIDIA Big")
        self.assertFalse(lines[1].startswith("NVIDIA Big"),
                         f"infobox repeats the model name on the note line: {text!r}")


if __name__ == "__main__":
    unittest.main()
