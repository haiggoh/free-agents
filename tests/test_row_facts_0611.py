"""Row-label facts: param size + context, deduped against the display name (hotfix 2026-11-22).

Delivery-shaped where it matters: the SUFFIX STRING a row label would carry is asserted, for
remote-shaped settings and for the local path, including each dedupe case (name already states
the size, the context, or both). Not load_sensitive (no Textual pilot).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m


def _const(value):
    """A (provider, model_id) -> value hook that ignores both args."""
    def hook(*args):  # noqa: ARG001 — fixed signature of the fact hooks
        del args
        return value
    return hook


class _S:
    """Minimal Settings stand-in with the two fact hooks."""
    def __init__(self, params=None, ctx=None):
        self.model_param_count = _const(params) if params is not None else None
        self.model_context_window = _const(ctx) if ctx is not None else None


class RowSuffixTests(unittest.TestCase):
    def test_both_facts_appended(self):
        s = _S(params=31.0, ctx=1048576)
        self.assertEqual(m.param_size_suffix(s, "nvidia", "z-ai/glm-5.3-flash", "GLM 5.3 Flash"),
                         "  · 31B · 1M ctx")

    def test_size_deduped_when_name_states_it(self):
        s = _S(params=31.0, ctx=1048576)
        self.assertEqual(m.param_size_suffix(s, "nvidia", "glm-31b-flash", "GLM 31B Flash"),
                         "  · 1M ctx")

    def test_context_deduped_when_name_states_it(self):
        s = _S(params=8.0, ctx=131072)
        self.assertEqual(m.param_size_suffix(s, "groq", "x", "Model 128K"), "  · 8B")
        self.assertEqual(m.param_size_suffix(s, "groq", "x", "Model 128k"), "  · 8B")
        s2 = _S(params=8.0, ctx=1048576)
        self.assertEqual(m.param_size_suffix(s2, "groq", "x", "Model 1M"), "  · 8B")

    def test_both_deduped(self):
        s = _S(params=8.0, ctx=131072)
        self.assertEqual(m.param_size_suffix(s, "p", "m", "8B 128K model"), "")

    def test_unknown_facts_empty(self):
        self.assertEqual(m.param_size_suffix(_S(), "p", "m", "Whatever"), "")

    def test_context_only(self):
        s = _S(ctx=262144)
        self.assertEqual(m.param_size_suffix(s, "p", "m", "Some Model"), "  · 256k ctx")

    def test_local_row_uses_inventory_context_first(self):
        # LocalScreen._regroup path: mdl.context present -> inventory column wins, no hook needed.
        mdl = m.LocalModel(alias="gemma-fixture", backend="rapid", folder="gemma-4-31B-it-OptiQ-4bit",
                           context=262144)
        s = _S(params=31.0, ctx=None)   # hook knows nothing; inventory column answers
        # reproduce the branch logic: context from mdl, size from the hook
        ctx_label = m._format_context(mdl.context)
        param_label = m.format_param_count(m.catalog_param_size(s, "local", mdl.folder or mdl.alias))
        name = "Gemma 4 31B IT"
        parts = [p for p, present in ((param_label, not m._name_states(param_label, name)),
                                      (ctx_label, not m._name_states(ctx_label, name)))
                 if present and p]
        # the name "Gemma 4 31B IT" states the size, so only the context is shown
        self.assertEqual(parts, ["256k"])

    def test_format_context_labels(self):
        f = m._format_context
        for n, want in [(1048576, "1M"), (262144, "256k"), (131072, "128k"), (200000, "200k"),
                        (163840, "160k"), (1000000, "1M"), (1050000, "1.05M"), (None, ""),
                        (0, ""), (-5, ""), (True, "")]:
            self.assertEqual(f(n), want, f"format_context({n!r})")


if __name__ == "__main__":
    unittest.main()
