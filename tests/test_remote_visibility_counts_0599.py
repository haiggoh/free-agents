#!/usr/bin/env python3
"""tests/test_remote_visibility_counts_0599.py — 0.59.9 pin (user report 2026-11-19: "only 7
nvidia models in the remote session list"): the remote picker must account for EVERY landed row
exactly once — pinned roster rows plus live catalog rows — so a fetch or filter regression that
silently loses rows turns the shared counts red instead of just showing fewer models.

Verified live 2026-11-19: the fetch is complete (NVIDIA integrate catalog returns 32 rows, anon ==
authed, no pagination; 31 chat candidates after the documented non-chat drop), and the picker's
nvidia count of 7 = 5 visible pinned + 2 visible catalog rows, with every other row hidden by the
intended local-capable/broken filters and all accounted for in (visible, hidden).

Usage: python3 tests/test_remote_visibility_counts_0599.py [-v]   (or pytest)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT
sys.path.insert(0, str(ROOT / 'bin'))

import session_picker_model as m  # noqa: E402


def _settings(lc_shown=False):
    s = m.Settings()
    s.include_trials = False
    s.local_capable_shown = lc_shown
    s.show_broken = False
    s.show_old = False
    s.hidden_providers = []
    # Deterministic stub verdict: ids ending in "-small" are local-capable (hidden by default).
    s.catalog_local_capable = lambda _prov, mid: (mid or '').endswith('-small')
    s.catalog_model_old = lambda _prov, _mid: False
    return s


def _agents():
    return [
        m.RemoteAgent('nv-big', 'nvidia', 'Big', 'unknown', model_id='org/big-500b'),
        m.RemoteAgent('nv-small', 'nvidia', 'Small', 'unknown', local_capable=True, model_id='org/small-1b'),
        m.RemoteAgent('nv-broken', 'nvidia', 'Broken', 'unknown', broken=True, model_id='org/broken-1b'),
        m.RemoteAgent('nv-select', 'nvidia', 'SELECT', 'unknown', model_id='SELECT'),
    ]


def _catalog():
    return {'nvidia': [
        m.CatalogModel('org/big-500b'),        # dup of a visible pinned row: counted once, pinned
        m.CatalogModel('org/small-1b'),        # dup of a hidden pinned row: catalog row, hidden
        m.CatalogModel('org/mid-30b'),         # catalog-only, visible
        m.CatalogModel('org/tiny-2b-small'),   # catalog-only, local-capable -> hidden
    ]}


class RemoteVisibilityCountsTests(unittest.TestCase):
    def test_every_landed_row_is_counted_exactly_once(self):
        """visible + hidden must equal the landed rows exactly; a fetch/filter regression that
        loses a row breaks the invariant. Landed = 3 eligible pinned rows (SELECT dropped) + the
        catalog rows that do not dupe a VISIBLE pinned row: org/big-500b dupes visible nv-big and
        is not recounted, org/small-1b dupes the HIDDEN nv-small and IS a separate catalog row."""
        s = _settings()
        agents, cats = _agents(), _catalog()
        vis, hidden = m.remote_visibility_counts(s, agents, cats)
        total_landed = 3 + 3          # pinned eligible + non-dup catalog rows
        self.assertEqual(vis + hidden, total_landed,
                         f'visible({vis}) + hidden({hidden}) must cover all {total_landed} landed rows')
        # visible = nv-big + org/mid-30b + org/small-1b (catalog dup of the HIDDEN nv-small is a
        # separate landed row and is visible: its id does not end in "-small")
        self.assertEqual(vis, 3, 'visible = nv-big, org/mid-30b, org/small-1b')
        self.assertEqual(hidden, 3, 'hidden = nv-small, nv-broken, org/tiny-2b-small')

    def test_toggle_merges_catalog_dupes_but_loses_no_model(self):
        """Showing local-capable rows merges a catalog row into its now-visible pinned twin
        (counted once). The invariant that must hold in EVERY toggle state: counted rows + the
        catalog rows that dupe a VISIBLE pinned row == the distinct landed models (3 pinned
        eligible + 4 catalog rows = 7). A fetch/filter regression that loses a row turns red."""
        agents, cats = _agents(), _catalog()
        for lc_shown in (False, True):
            s = _settings(lc_shown)
            vis, hidden = m.remote_visibility_counts(s, agents, cats)
            dupes = {c.model_id for c in cats['nvidia']} & {
                a.model_id for a in agents
                if a.provider == 'nvidia' and a.model_id != 'SELECT' and m._agent_visible(s, a)}
            self.assertEqual(vis + hidden + len(dupes), 3 + 4,
                             f'lc_shown={lc_shown}: counted({vis + hidden}) + dupes({len(dupes)}) '
                             'must cover every distinct landed model')
        s0, s1 = _settings(False), _settings(True)
        vis0, hid0 = m.remote_visibility_counts(s0, agents, cats)
        vis1, hid1 = m.remote_visibility_counts(s1, agents, cats)
        self.assertGreater(vis1, vis0)
        self.assertGreater(hid0, hid1)

    def test_counts_match_visible_agents_plus_visible_catalog(self):
        s = _settings()
        agents, cats = _agents(), _catalog()
        vis, _hidden = m.remote_visibility_counts(s, agents, cats)
        pinned_vis = len(m.visible_agents(s, agents, remote_filters=True))
        # visible non-dup catalog rows: org/mid-30b and org/small-1b (dupes only VISIBLE pinned)
        self.assertEqual(vis, pinned_vis + 2,
                         'counts must equal pinned-visible + visible catalog rows')
        self.assertEqual(pinned_vis, 1)


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
    unittest.main()
