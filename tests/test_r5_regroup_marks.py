#!/usr/bin/env python3
"""tests/test_r5_regroup_marks.py — regression pin for the R5 grey-out bug (2026-11-28):
RemoteScreen._regroup rebuilt the row list with `for a in items` after the rows were
already Item objects, so ANY RemoteScreen construction raised
AttributeError: 'Item' object has no attribute 'alias' (44 picker tests red on main
since cbe1872 — introduced by R5, shipped in v0.65.0). The fix iterates the source
agents (pinned) and appends catalog rows afterwards, unchanged.

Mutation: rebuilding from `items` again makes test_constructor_survives_pinned_rows fail
with that AttributeError; dropping the suffix branch makes test_marked_alias_is_greyed
fail; rebuilding over catalog rows makes test_catalog_rows_survive_the_marks_rebuild
fail. Verified both sides: RED on pristine origin/main, GREEN on fix/r5-picker-item-alias.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402


class R5RegroupMarksTests(unittest.TestCase):
    def _agent(self, alias="nvidia-a", model_id="vendor/a"):
        return m.RemoteAgent(alias, "nvidia", f"Agent {alias}", "unknown", model_id=model_id)

    def test_constructor_survives_pinned_rows(self):
        # The crash itself: the RemoteScreen constructor calls _regroup, which read
        # .alias off the just-built Item objects.
        screen = m.RemoteScreen(m.Settings(), [self._agent()], owner=m.HOME_OWNED)
        rows = [item for group in screen.accordion.groups for item in group.items]
        self.assertIn("nvidia-a", [i.id for i in rows],
                      f"the pinned row must survive the marks rebuild: {[i.id for i in rows]}")

    def test_marked_alias_is_greyed(self):
        # The R5 FEATURE, not just the crash: a probe mark on a pinned alias must add
        # the "not reachable" suffix (the infobox sentence explains why).
        real = m._unreachable_marks
        m._unreachable_marks = lambda: {"nvidia-a": {"since": 0}}
        try:
            screen = m.RemoteScreen(m.Settings(), [self._agent()], owner=m.HOME_OWNED)
        finally:
            m._unreachable_marks = real
        labels = [i.label for g in screen.accordion.groups for i in g.items]
        self.assertTrue(any("· not reachable" in lbl for lbl in labels),
                        f"a marked pinned row must be greyed: {labels}")

    def test_catalog_rows_survive_the_marks_rebuild(self):
        # Catalog rows are Item objects, never agents: the marks rebuild must leave them
        # untouched (this is exactly what the bug violated), and they must still render.
        screen = m.RemoteScreen(m.Settings(), [self._agent()], owner=m.HOME_OWNED)
        screen.catalogs = {"nvidia": [m.CatalogModel("vendor/cat-only")]}
        screen._regroup()
        rows = [i for g in screen.accordion.groups for i in g.items]
        ids = [i.id for i in rows]
        self.assertIn("nvidia-a", ids, f"pinned row missing after catalog rebuild: {ids}")
        self.assertTrue(any(i.startswith(m.CATALOG_PREFIX) for i in ids),
                        f"catalog row missing after catalog rebuild: {ids}")
        self.assertFalse(any("not reachable" in i.label for i in rows
                             if i.id.startswith(m.CATALOG_PREFIX)),
                         f"catalog rows must never be marked: {[i.label for i in rows]}")