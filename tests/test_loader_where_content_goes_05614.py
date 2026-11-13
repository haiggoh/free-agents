#!/usr/bin/env python3
"""A loader sits WHERE the content it is loading will appear — never pinned to the bottom.

User 2026-11-13, backend manager → Install a release: the "loading versions" animation was
drawn only in #status, the row pinned to the very bottom of the window, so it was easy to
overlook while the list area above stayed blank. General rule from the same message: "loader
spinner positioned where the content it's loading will go". So on EVERY screen waiting on a
list (versions, download catalog, model lists) the animated loader is the first row of #rows,
and #status keeps its ordinary nav line instead of a second copy.
"""

import unittest

from picker_harness import PilotCase


class LoaderWhereContentGoesTest(PilotCase):
    def _stall_prefetch(self, app):
        """Keep a load in flight: mark the kind loading, never deliver it."""
        def stalled(kinds):
            if not hasattr(app, "_loading"):
                app._loading = set()
            app._loading.update(kinds)
        app._prefetch = stalled

    def _assert_loader_in_list(self, app, h):
        rows = h.labels("rows")
        self.assertTrue(rows, "the list area is empty while loading: the loader must be there")
        self.assertIn("loading", rows[0], f"first list row is not the loader: {rows!r}")
        self.assertNotIn("loading", h.status(), "the loader is pinned to the bottom status row")

    def test_install_release_loader_is_in_the_list_not_the_status_row(self):
        async def scenario(app, pilot, h):
            self._stall_prefetch(app)
            app.goto(self.m.Nav("runtime_versions:install", self.m.SUB))
            await pilot.pause(0.1)
            self._assert_loader_in_list(app, h)
            app._tick_loading()                # the animation beat must not move it to #status
            await pilot.pause(0.1)
            self._assert_loader_in_list(app, h)
        self.run_picker(scenario)

    def test_download_catalog_loader_is_in_the_list_not_the_status_row(self):
        async def scenario(app, pilot, h):
            self._stall_prefetch(app)
            app.cache.pop("catalog", None)
            app.goto(self.m.Nav("download"))
            await pilot.pause(0.1)
            app._tick_loading()
            await pilot.pause(0.1)
            self._assert_loader_in_list(app, h)
        self.run_picker(scenario)


if __name__ == "__main__":
    unittest.main()
