#!/usr/bin/env python3
"""GUI prefetch parity (parity plan Task 1): every screen target that build_screen
declares a prefetch kind for must resolve to a loader in the GUI's map — mirroring the
Textual picker's LOADERS (session_picker.py:1371) plus its versions:/rcat: prefixes.

Runs under the GUI venv (FA_GUI_PY) because fa_gui.app imports nicegui; the gate invokes
it with that interpreter (GUI_TESTS in .rtc/pre-flight.sh).
"""
import unittest

import picker_core as pc
import session_picker_model as m
import fa_gui.app as app
from fa_gui.app import PREFETCH_LOADERS, _loader_for


class PrefetchMapTests(unittest.TestCase):
    def test_base_kinds_resolve(self):
        for kind in ("local", "remote", "catalog"):
            self.assertIsNotNone(_loader_for(kind), kind)
            self.assertIs(PREFETCH_LOADERS[kind], _loader_for(kind))

    def test_prefixed_kinds_resolve(self):
        self.assertTrue(callable(_loader_for("versions:rapid-mlx:install")))
        self.assertTrue(callable(_loader_for("rcat:cloudflare")))

    def test_every_screen_prefetch_kind_resolves(self):
        # delivery-shaped: walk the real build_screen for every known nav target and
        # require each declared prefetch kind to map to a callable loader
        settings = m.Settings()
        cache: dict = {}
        targets = ["home", "local", "remote", "lowkey", "download", "about", "api_keys",
                   "rate_limiter", "memguard", "cloud_config", "theme_config",
                   "remote_filters", "runtime_manager", "runtime", "runtime_launchd",
                   "runtime_versions:install"]
        missing = []
        for t in targets:
            build = pc.build_screen(m.Nav(t, m.HOME_OWNED), settings, cache)
            for kind in build.prefetch:
                if _loader_for(kind) is None:
                    missing.append((t, kind))
        self.assertEqual(missing, [], f"screens with unresolvable prefetch kinds: {missing}")

    def test_unknown_kind_returns_none_not_crash(self):
        self.assertIsNone(_loader_for("nope"))

    def test_download_screen_gets_catalog_data_after_prefetch(self):
        # delivery-shaped: build the download screen with an EMPTY cache, run its declared
        # prefetch through the map, rebuild, and the screen must carry real entries
        settings = m.Settings()
        cache: dict = {}
        build1 = pc.build_screen(m.Nav("download", m.HOME_OWNED), settings, cache)
        self.assertEqual(build1.prefetch, ("catalog",))
        fn = _loader_for(build1.prefetch[0])
        catalog_data = fn()          # (entries, free_gb, headroom_gb), like csl's load_catalog
        cache["catalog"] = catalog_data
        build2 = pc.build_screen(m.Nav("download", m.HOME_OWNED), settings, cache)
        entries = catalog_data[0]
        self.assertGreater(len(entries), 0, "catalog loader returned no entries on this machine")
        self.assertEqual(len(build2.screen.entries), len(entries))


class PromptDialogTests(unittest.TestCase):
    """The prompt:* dialog core: review payload + the confirm->CLI-argv delivery path."""

    def _download_screen(self, queued=("kat-coder-optiq",)):
        settings = m.Settings()
        first = queued[0] if queued else "kat-coder-optiq"
        entry = m.CatalogEntry(alias=first, state="ABSENT", size_gb=20.0, groups="g", repo="r")
        scr = m.DownloadScreen(settings, [entry], 100.0, 10.0, m.HOME_OWNED)
        for a in queued:
            scr.queue.append(a)
        return scr

    def test_review_payload_is_the_models_own_review(self):
        scr = self._download_screen()
        payload = app.prompt_payload("prompt:download-review", scr)
        self.assertEqual(payload["kind"], "download-review")
        self.assertEqual(payload["aliases"], ["kat-coder-optiq"])
        self.assertEqual(payload["total_gb"], 20.0)
        self.assertTrue(payload["fits"])

    def test_confirm_produces_the_exact_cli_argv(self):
        # delivery-shaped: confirm(True) -> LaunchRequest -> command_for -> argv
        scr = self._download_screen(("kat-coder-optiq", "qwen-3.8-operator"))
        # second alias needs an entry or review() keys it — add one
        scr.entries["qwen-3.8-operator"] = m.CatalogEntry(
            alias="qwen-3.8-operator", state="ABSENT", size_gb=16.0, groups="g", repo="r")
        argv = app.confirm_prompt_argv(scr, "prompt:download-review")
        self.assertIsNotNone(argv)
        self.assertTrue(argv[0].endswith("install/download-models.sh"), argv[0])
        self.assertEqual(argv[1:].count("--select"), 2)
        self.assertEqual(argv[1:], ["--select", "kat-coder-optiq", "--select", "qwen-3.8-operator"])

    def test_unknown_prompt_yields_none(self):
        about = m.AboutScreen(m.Settings(), m.HOME_OWNED)  # no confirm() method
        self.assertIsNone(app.confirm_prompt_argv(about, "prompt:rt-remove"))
        self.assertIsNone(app.prompt_payload("not-a-prompt", about))
        # and an empty queue declines even on a screen that has confirm()
        self.assertIsNone(app.confirm_prompt_argv(self._download_screen(()), "prompt:download-review"))


class ToolArgvTests(unittest.TestCase):
    """picker_core.tool_argv mirrors csl's goto() switch for tool: targets (Task 3)."""

    def test_plain_tool_uses_backend(self):
        argv = pc.tool_argv("tool:rt-launchd-status", "rapid-mlx")
        self.assertEqual(argv[2:], ["--backend", "rapid-mlx", "launchd", "status"], argv)

    def test_rt_op_uses_screen_argv_for(self):
        scr = m.VersionsScreen(m.Settings(), "remove", owner=m.SUB)
        scr.backend = "vllm-mlx"
        argv = pc.tool_argv("tool:rt-op:remove:1.2.3", "rapid-mlx", scr)
        self.assertEqual(argv[2:], ["--backend", "vllm-mlx", "remove", "1.2.3", "--yes"], argv)

    def test_unknown_tool_is_none(self):
        self.assertIsNone(pc.tool_argv("tool:nope", "rapid-mlx"))


if __name__ == "__main__":
    unittest.main()
