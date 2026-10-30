#!/usr/bin/env python3
"""tests/test_new_features.py — session picker features added in the 0.22/0.23 line.

Covers the Backend Manager screen, the API Keys accordion with INLINE key entry (paste advances,
typing needs Enter, whitespace is stripped from pastes, the signup page opens through a stub —
never a real browser), Left/Right cycling of settings, last-launched persistence, the loading
line, and emoji constants/spacing. UI flows run through Textual's pilot (picker_harness);
pure model/constant checks need no UI at all.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_new_features.py
"""
import json
import os
import re
import stat
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import RECORDER, ROOT, PilotCase, PTYCase  # noqa: E402

sys.path.insert(0, str(ROOT / "bin"))


class BackendManagerScreen(PilotCase):
    def test_reachable_from_home_with_its_actions_and_back(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            self.assertIn("Backend Manager", h.title())
            text = h.text()
            for label in ("Backend: rapid-mlx", "List installable releases", "Install a release",
                          "Validate an installed version", "Show info for an installed version",
                          "Launchd update checks", "Back to Home"):
                self.assertIn(label, text)
            await h.press("b")
            await h.wait_screen("HomeScreen")
        self.run_picker(s)

    def test_backend_cycles_and_hides_ops_its_manager_lacks(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            self.assertIn("Remove an installed version", h.text())      # rapid-mlx has remove
            await h.press("c")
            await h.wait_for(lambda: "Backend: rapid-mlx" not in h.text(), "backend to cycle")
            # 0.49.0: snapshot/promote/remove/repair live in the shared venv base, so vllm-mlx
            # keeps the rows this test used to see hidden (old contract: remove was Rapid-only)
            self.assertIn("Backend: vllm-mlx", h.text())
            self.assertIn("Remove an installed version", h.text())
            self.assertIn("Repair an installed version", h.text())
            # a backend with NO version ops (omlx) must hide every one of them
            await h.press("c")
            await h.wait_for(lambda: "Backend: omlx" in h.text(), "backend to cycle to omlx")
            for label in ("Snapshot an installed version", "Promote pins to an installed version",
                          "Remove an installed version", "Repair an installed version"):
                self.assertNotIn(label, h.text())
            # litellm is the partial case: remove only (pipx owns the live install), the rest hidden
            await h.press("c")
            await h.wait_for(lambda: "Backend: llama-cpp" in h.text(), "backend to cycle")
            await h.press("c")
            await h.wait_for(lambda: "Backend: litellm" in h.text(), "backend to cycle to litellm")
            self.assertIn("Remove an installed version", h.text())
            self.assertNotIn("Snapshot an installed version", h.text())
            self.assertNotIn("Promote pins to an installed version", h.text())
            self.assertNotIn("Repair an installed version", h.text())
        self.run_picker(s)

    def _stub_manager(self, installed="0.16.1         complete   /x/rapid-mlx-0.16.1\n"
                                       "0.15.3         complete   /x/rapid-mlx-0.15.3\n"):
        """Point the picker's manage-backend.py calls at a recorder that answers `installed`
        and `releases` with fixed lists and records every other call."""
        import session_picker as sp
        stub = Path(self.tmp.name) / "stubs" / "manage-backend"
        stub.write_text("#!/bin/sh\n"
                        "case \"$3\" in installed) printf '" + installed.replace("\n", "\\n") + "'; exit 0;;\n"
                        "releases) printf '0.17.0\\n0.16.1\\n'; exit 0;; esac\n"
                        + RECORDER.split("\n", 1)[1])
        stub.chmod(0o755)
        real = sp._mb
        sp._mb = lambda backend, *a: [str(stub), "--backend", backend, *a]
        self.addCleanup(setattr, sp, "_mb", real)

    def test_remove_picks_from_list_and_asks_twice(self):
        self._stub_manager()
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("x")
            await h.wait_screen("VersionsScreen")
            await h.wait_for(lambda: "0.15.3" in h.text(), "installed versions listed")
            # Opens ON the first version with the list focused: plain arrows + Enter, no header.
            self.assertIs(app.focused, app.query_one("#rows"))
            self.assertEqual(app._highlighted(), ("item", "0.16.1"))
            await h.press("down")
            self.assertEqual(app._highlighted(), ("item", "0.15.3"))
            await h.press("enter")
            await h.wait_panel("Remove Rapid-MLX 0.15.3?")
            await h.press("enter")                                   # default = Cancel
            await h.wait_text("removal cancelled")
            await h.goto_row("item", "0.15.3")
            await h.press("enter")
            await h.wait_panel("Remove Rapid-MLX 0.15.3?")
            await h.press("down", "enter")                           # Remove 0.15.3
            await h.wait_panel("Are you sure? 0.15.3")
            await h.press("escape")                                  # second question: Esc
            await h.wait_text("removal cancelled")
            self.assertEqual(self.launches(), [])
            await h.goto_row("item", "0.15.3")
            await h.press("enter")
            await h.wait_panel("Remove Rapid-MLX 0.15.3?")
            await h.press("down", "enter")
            await h.wait_panel("Are you sure? 0.15.3")
            await h.press("down", "enter")                           # Yes, delete 0.15.3
            await h.wait_for(lambda: self.launches(), "the remove child")
            self.assertEqual(self.launches()[-1]["argv"], ["--backend", "rapid-mlx", "remove", "0.15.3", "--yes"])
        self.run_picker(s)

    def test_launchd_install_and_uninstall_are_panel_questions(self):
        self._stub_manager()
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("l")
            await h.wait_screen("LaunchdScreen")
            await h.press("u")
            await h.wait_panel("Remove the weekly LaunchAgent update check?")
            await h.press("enter")                                   # default = Cancel
            await h.wait_text("launchd unchanged")
            self.assertEqual(self.launches(), [])
            await h.press("i")
            await h.wait_panel("Install the weekly LaunchAgent update check?")
            await h.press("enter")                                   # default = Install it
            await h.wait_for(lambda: self.launches(), "the install child")
            self.assertEqual(self.launches()[-1]["argv"][-2:], ["launchd", "install"])
        self.run_picker(s)

    def test_last_rapid_mlx_version_is_never_offered_for_removal(self):
        self._stub_manager(installed="0.16.1         complete   /x/rapid-mlx-0.16.1\n")
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("x")
            await h.wait_screen("VersionsScreen")
            await h.wait_for(lambda: "0.16.1" in h.text(), "the one installed version")
            await h.press("enter")
            await h.wait_text("core dependency of free-agents")
            self.assertFalse(app.query_one("#prompt").display)      # no prompt was even opened
            self.assertEqual(self.launches(), [])
        self.run_picker(s)

    def test_install_picks_a_release_from_the_list(self):
        self._stub_manager()
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("i")
            await h.wait_screen("VersionsScreen")
            await h.wait_for(lambda: "0.17.0" in h.text(), "releases listed")
            self.assertIn("0.16.1  (installed)", h.text())
            app.query_one("#rows").focus()
            await h.goto_row("item", "0.17.0")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "the install child")
            self.assertEqual(self.launches()[-1]["argv"], ["--backend", "rapid-mlx", "install", "0.17.0"])
        self.run_picker(s)

    def test_a_failed_release_fetch_says_so_instead_of_no_releases(self):
        # 2026-10-29: the PyPI fetch timed out (IPv6 black hole) and the screen said "no releases
        # found" — a failure dressed up as an empty answer, which read as a regression.
        import session_picker as sp
        stub = Path(self.tmp.name) / "stubs" / "manage-backend"
        stub.write_text("#!/bin/sh\ncase \"$3\" in installed) exit 0;;\n"
                        "releases) echo 'ERROR: could not retrieve Rapid-MLX releases: timed out' >&2; exit 2;; esac\n")
        stub.chmod(0o755)
        real = sp._mb
        sp._mb = lambda backend, *a: [str(stub), "--backend", backend, *a]
        self.addCleanup(setattr, sp, "_mb", real)
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("i")
            await h.wait_screen("VersionsScreen")
            await h.wait_for(lambda: "could not load releases" in h.status(), "the load failure")
            self.assertIn("could not retrieve Rapid-MLX releases: timed out", h.status())
            self.assertNotIn("no releases found", h.status())
            self.assertNotIn("model list failed", h.status())
        self.run_picker(s)


class APIKeysScreen(PilotCase):
    async def _open_add(self, h, slug):
        await h.open_group(slug)
        await h.goto_row("item", f"add:{slug}")
        await h.press("enter")
        await h.wait_for(lambda: h.app.query_one("#prompt").display, "the inline key prompt")
        return h.app.query_one("#prompt")

    def _key(self, name):
        return (self.keys / name).read_text()

    def test_reachable_from_home_nvidia_first_with_missing_status(self):
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            rows = h.labels("rows")
            self.assertIn("NVIDIA: ❌ Missing", rows[0])   # NVIDIA first, expanded
            self.assertIn("Open NVIDIA signup page", rows[1])
            self.assertIn("Add NVIDIA key", rows[2])
            for name in ("Google Gemini", "Groq", "OpenRouter"):
                self.assertIn(name, h.text())
            # The separator blank line sits AFTER "Add NVIDIA key", never between NVIDIA's
            # header and its items. It's a disabled empty row (index 3 = blank separator).
            self.assertFalse(rows[0].endswith("\n"), rows[0])
            self.assertEqual(rows[3], "")  # separator row (disabled, no arrow)
            self.assertTrue(rows[4].lstrip("▸▾ ").startswith("Google Gemini"), rows[4])
        self.run_picker(s)

    def test_info_box_shows_the_highlighted_providers_requirements(self):
        """0.38.1: the same bordered info box the remote model list has, under the provider list:
        it must SHOW (display on) and say what the account needs -- Vercel's credit card."""
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            info = app.query_one("#info")
            await h.goto_row("item", "add:nvidia")
            await h.pilot.pause(0.3)
            self.assertTrue(info.display, "info box hidden on the API Keys screen")
            self.assertIn("NVIDIA", str(info.render()))
            await h.open_group("vercel")
            await h.goto_row("item", "add:vercel")
            await h.pilot.pause(0.3)
            self.assertIn("CREDIT CARD", str(info.render()))
        self.run_picker(s)

    def test_open_signup_page_uses_the_opener_not_a_real_browser(self):
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            await h.goto_row("item", "open:nvidia")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "the opener stub to run")
            self.assertEqual([(r["prog"], r["argv"]) for r in self.launches()],
                             [("url-opener", ["https://build.nvidia.com/settings/api-keys"])])
            self.assertEqual(h.screen(), "APIKeysScreen")
        self.run_picker(s)

    def test_add_key_is_inline_hidden_and_label_shown_once(self):
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            prompt = await self._open_add(h, "nvidia")
            self.assertTrue(prompt.password, "key input must be hidden")
            self.assertEqual(prompt.placeholder, "", "label must not repeat inside the field")
            self.assertIn("API key/token for NVIDIA", h.status())
            self.assertIn("paste advances automatically", h.status())
            self.assertEqual(self.launches(), [], "must not hand off to the old wizard")
        self.run_picker(s)

    def test_typed_key_waits_for_enter_then_saves_600(self):
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            prompt = await self._open_add(h, "nvidia")
            await h.press(*"nvapi-TYPED1", gap=0.02)
            self.assertTrue(prompt.display, "typing alone must not submit")
            self.assertFalse((self.keys / "nvidia").exists())
            await h.press("enter")
            await h.wait_text("NVIDIA: ✅ Saved")
            self.assertFalse(prompt.display)
            self.assertFalse(prompt.password, "hidden mode must not leak into later prompts")
        self.run_picker(s)
        self.assertEqual(self._key("nvidia"), "nvapi-TYPED1\n")
        self.assertEqual(stat.S_IMODE((self.keys / "nvidia").stat().st_mode), 0o600)

    def test_paste_advances_and_strips_all_whitespace(self):
        from textual import events

        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            prompt = await self._open_add(h, "groq")
            prompt.post_message(events.Paste("  gsk-AB CD\nEF\t12\r\n"))
            await h.wait_text("Groq: ✅ Saved")
            self.assertFalse(prompt.display, "a paste submits without Enter")
            self.assertTrue(any("removed" in w and "whitespace" in w for w in app.state_warnings),
                            app.state_warnings)
        self.run_picker(s)
        self.assertEqual(self._key("groq"), "gsk-ABCDEF12\n")

    def test_trailing_newline_is_dropped_silently_and_blank_paste_waits(self):
        from textual import events

        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            prompt = await self._open_add(h, "gemini")
            prompt.post_message(events.Paste(" \n "))
            await h.settle(0.4)
            self.assertTrue(prompt.display, "whitespace-only paste must keep waiting")
            self.assertEqual(prompt.value, "")
            prompt.post_message(events.Paste("AIza-KEY\n"))
            await h.wait_text("Google Gemini: ✅ Saved")
            self.assertFalse(any("whitespace" in w for w in app.state_warnings), app.state_warnings)
        self.run_picker(s)
        self.assertEqual(self._key("gemini"), "AIza-KEY\n")

    def test_empty_enter_skips_without_saving(self):
        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            await self._open_add(h, "nvidia")
            await h.press("enter")
            await h.wait_for(lambda: "skipped — no key saved" in app.state_warnings, "skip notice")
        self.run_picker(s)
        self.assertEqual(list(self.keys.iterdir()), [])

    def test_existing_key_is_never_overwritten(self):
        (self.keys / "nvidia").write_text("nvapi-ORIGINAL\n")
        (self.keys / "nvidia").chmod(0o600)

        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            await self._open_add(h, "nvidia")
            await h.press(*"nvapi-NEW", "enter", gap=0.02)
            await h.wait_for(lambda: any("already saved" in w for w in app.state_warnings), "kept notice")
        self.run_picker(s)
        self.assertEqual(self._key("nvidia"), "nvapi-ORIGINAL\n")

    def test_cloudflare_fills_token_then_account_id(self):
        acct = "0123456789abcdef0123456789abcdef"

        async def s(app, pilot, h):
            await h.press("k")
            await h.wait_screen("APIKeysScreen")
            await self._open_add(h, "cloudflare")
            await h.press(*"cf-TOKEN", "enter", gap=0.02)
            await h.wait_for(lambda: any("add again for cloudflare-account-id" in w
                                         for w in app.state_warnings), "next-file notice")
            await self._open_add(h, "cloudflare")
            await h.press(*acct, "enter", gap=0.02)
            await h.wait_text("Cloudflare Workers AI: ✅ Saved")
        self.run_picker(s)
        self.assertEqual(self._key("cloudflare"), "cf-TOKEN\n")
        self.assertEqual(self._key("cloudflare-account-id"), acct + "\n")


class ArrowKeysCycleSettings(PilotCase):
    def _effort(self, h):
        return next(a for a in h.labels() if "Effort:" in a).split("Effort:", 1)[1].strip()

    def test_right_and_left_cycle_effort(self):
        async def s(app, pilot, h):
            await h.expand_settings()              # 0.47.2: settings rows live under the folded group
            await h.press("e")                        # select the Effort row (it also cycles once)
            first = self._effort(h)
            await h.press("right")
            second = self._effort(h)
            self.assertNotEqual(first, second)
            await h.press("left")
            self.assertEqual(self._effort(h), first)
        self.run_picker(s, start="local")

    def test_right_left_toggle_boolean_mcps(self):
        async def s(app, pilot, h):
            await h.expand_settings()              # 0.47.2: settings rows live under the folded group
            await h.wait_text("Load MCPs in session: ENABLED")     # default ON (user 2026-10-15)
            await h.press("m")
            await h.wait_text("Load MCPs in session: DISABLED")
            await h.press("m")
            await h.wait_text("Load MCPs in session: ENABLED")
        self.run_picker(s, start="local")

    def test_settings_hotkey_works_while_collapsed(self):
        # 0.47.2: a folded settings row keeps its hotkey — cycling must not require expanding.
        async def s(app, pilot, h):
            await h.wait_text("▸ Advanced")        # still folded
            await h.press("m")
            await h.expand_settings()
            await h.wait_text("Load MCPs in session: DISABLED")
        self.run_picker(s, start="local")


class LastLaunchedPersistence(PilotCase):
    def test_local_launch_offers_go_launch_and_persists(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("DeepSeek R1 Architect")
            await h.goto_row("item", "deepseek-r1-architect")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            await h.wait_text("Go launch: DeepSeek R1 Architect session")
            self.assertEqual(self.saved_state()["last_launched"]["local_session"], "deepseek-r1-architect")
        self.run_picker(s, start="local")

    def test_remote_launch_persists(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("NVIDIA A")
            await h.goto_row("item", "nvidia-a")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            self.assertEqual(self.saved_state()["last_launched"]["remote_api_session"], "nvidia-a")
        self.run_picker(s, start="remote")


class RemoteLiveCatalog(PilotCase):
    """Opening a provider group fetches its catalog in the background and lists it under the pins."""

    def setUp(self):
        super().setUp()
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        # The inventory stub answers --inventory; --models answers after a delay that would
        # freeze the UI if the fetch ran on the event loop.
        stub.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do\n"
            "  [ \"$a\" = --inventory ] && { printf 'nvidia-a\\tnvidia\\tNVIDIA A\\tunknown\\t0\\t1\\t0\\tvendor/pinned\\n'; exit 0; }\n"
            "  [ \"$a\" = --models ] && { sleep 1.5; printf 'vendor/pinned\\t{\"tools\": true}\\nvendor/extra\\t{\"tools\": true}\\n'; exit 0; }\n"
            "done\n" + RECORDER.split("\n", 1)[1])

    def test_open_group_lists_catalog_and_launches_with_remote_model(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("NVIDIA A")
            await h.wait_text("loading the provider")       # visible while the stub sleeps
            # The stub sleeps 1.5 s: if the fetch ran on the event loop, this short pause could
            # not return before it finished.
            t0 = time.time()
            await pilot.pause(0.1)
            self.assertLess(time.time() - t0, 0.8)
            self.assertIn("rcat:nvidia-a", app._loading)
            await h.wait_text("Extra", secs=10)
            self.assertNotIn("cat:nvidia:vendor/pinned", [r for r in app.row_ids if r])
            await h.goto_row("item", "cat:nvidia:vendor/extra")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            argv = self.launches()[-1]["argv"]
            self.assertEqual(argv[-3:], ["--remote-model", "vendor/extra", "nvidia-a"])
            self.assertEqual(self.saved_state()["last_launched"]["remote_api_session"],
                             "nvidia-a@vendor/extra")
        self.run_picker(s, start="remote")

    def test_cached_catalog_renders_at_first_paint_and_revises(self):
        # T2: rows from the provider's disk cache are on screen before the live fetch
        # answers; the background revision then adds new ids and removes vanished ones.
        cache_dir = Path(self.tmp.name) / "cat-cache"
        cache_dir.mkdir(mode=0o700)
        os.environ["LA_REMOTE_CATALOG_CACHE_DIR"] = str(cache_dir)
        self.addCleanup(os.environ.pop, "LA_REMOTE_CATALOG_CACHE_DIR", None)
        (cache_dir / "nvidia.json").write_text(json.dumps({
            "provider": "nvidia", "fetched": "2026-10-19T00:00:00Z",
            "rows": [["vendor/cached-old", {"tools": True}]]}))

        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("Cached Old")          # from the cache, instantly
            await h.wait_text("Extra", secs=10)      # revision adds the new id
            self.assertNotIn("Cached Old", h.text())  # and removes the vanished one
        self.run_picker(s, start="remote")


class RemoteAgeFilterAndShowOld(PilotCase):
    """T6: catalog rows over the age cutoff hide unless --show-old seeds the picker, and
    LA_REMOTE_MAX_MODEL_AGE_MONTHS moves the cutoff. The date comes from the HF cache the
    harness pre-writes; the network is never touched (LA_PICKER_HF_LOOKUP=0)."""

    def setUp(self):
        super().setUp()
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do\n"
            "  [ \"$a\" = --inventory ] && { printf 'nvidia-a\\tnvidia\\tNVIDIA A\\tunknown\\t0\\t1\\t0\\tvendor/pinned\\n'; exit 0; }\n"
            "  [ \"$a\" = --models ] && { printf 'vendor/ancient\\t{\"tools\": true}\\nvendor/fresh\\t{\"tools\": true}\\n'; exit 0; }\n"
            "done\n" + RECORDER.split("\n", 1)[1])
        # The HF cache knows one id is old; the other was never warmed (fail-open visible).
        Path(self.env["LA_HF_PARAMS_CACHE"]).write_text(json.dumps({
            "vendor/ancient": {"total": None, "repo": None,
                               "created": "2023-01-01T00:00:00.000Z", "at": time.time()}}))

    def test_old_row_hidden_by_default(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("Fresh", secs=10)
            self.assertNotIn("Ancient", h.text())
        self.run_picker(s, start="remote")

    def test_show_old_flag_shows_the_old_row_from_the_start(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("Ancient", secs=10)
        self.run_picker(s, start="remote", show_old=True)

    def test_env_cutoff_moves_the_hidden_set(self):
        # 240 months = 20 years: the 2023 date is young again, so the row shows without
        # the flag — the cutoff, not the row, decided.
        os.environ["LA_REMOTE_MAX_MODEL_AGE_MONTHS"] = "240"
        self.addCleanup(os.environ.pop, "LA_REMOTE_MAX_MODEL_AGE_MONTHS", None)

        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("Ancient", secs=10)
        self.run_picker(s, start="remote")


class RemoteModelSearch(PilotCase):
    """T7: W asks for a model name; every provider whose rows match opens at once, showing
    only the matching rows, and an empty search restores the one-open accordion."""

    def setUp(self):
        super().setUp()
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do\n"
            "  [ \"$a\" = --inventory ] && { printf 'nv-glm\\tnvidia\\tNV GLM Flash\\tunknown\\t0\\t1\\t0\\tz-ai/glm-flash\\n"
            "nv-kimi\\tnvidia\\tNV Kimi\\tunknown\\t0\\t1\\t0\\tmoonshot/kimi\\n"
            "zai-glm\\tzai\\tZai GLM\\tunknown\\t0\\t1\\t0\\tglm-zai\\n'; exit 0; }\n"
            "  [ \"$a\" = --models ] && { printf ''; exit 0; }\n"
            "done\n" + RECORDER.split("\n", 1)[1])

    def test_search_opens_every_matching_provider(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("NV Kimi", secs=10)
            self.assertNotIn("Zai GLM", h.text())             # one group open before the search
            # 0.40.0: the live filter box owns typing — characters narrow the list at once,
            # no W action and no Enter to apply.
            await h.press(*"glm")
            await h.wait_text("Zai GLM", secs=10)
            self.assertIn("NV GLM Flash", h.text())
            self.assertNotIn("NV Kimi", h.text())
            from textual.widgets import Input as _Input
            self.assertEqual(app.query_one("#search", _Input).value, "glm")
            from textual.widgets import Static
            self.assertIn('matching "glm"', str(app.query_one("#policy", Static).render()))
            for _ in range(3):
                await h.press("backspace")                    # an empty box restores the list
            await h.wait_text("NV Kimi", secs=10)
            self.assertNotIn("Zai GLM", h.text())
        self.run_picker(s, start="remote")


class ModelParamCountUnits(unittest.TestCase):
    """The size sort's real data source: an id-stated count and a cached Hugging Face count
    must come back in the SAME unit (billions), or every HF-only row outranks every
    id-stated row regardless of true size."""

    def test_hf_cached_count_is_billions_like_the_id_count(self):
        import tempfile
        try:
            import textual  # noqa: F401  (session_picker exits without it)
        except ImportError:
            self.skipTest("textual not importable: run under the picker venv python")
        import session_picker as sp
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Path(tmp.name) / "hf-params.json"
        cache.write_text(json.dumps({"vendor/nameless": {
            "total": 4_000_000_000, "repo": "vendor/nameless", "created": None, "at": time.time()}}))
        os.environ["LA_HF_PARAMS_CACHE"] = str(cache)
        self.addCleanup(os.environ.pop, "LA_HF_PARAMS_CACHE", None)
        self.assertEqual(sp.model_param_count("mistral", "vendor/big-70b"), 70.0)
        self.assertEqual(sp.model_param_count("mistral", "vendor/nameless"), 4.0)


class StaleSecurityReviewOverride(PilotCase):
    def test_startup_removes_a_leftover_override_and_says_so(self):
        # Plugin not installed, our override still in Claude Code's settings.json: csl removes ONLY
        # our key and shows the removal (0.42.0). Runs against the sandboxed CLAUDE_CONFIG_DIR.
        real = Path.home() / ".claude" / "settings.json"
        real_before = real.read_bytes() if real.exists() else None
        cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "settings.json").write_text(json.dumps(
            {"env": {"SECURITY_REVIEW_MODEL": "claude-sonnet-4-6", "KEEP": "1"}}))

        async def s(app, pilot, h):
            self.assertTrue(any("SECURITY_REVIEW_MODEL" in w for w in app.state_warnings),
                            app.state_warnings)
            self.assertNotIn("s", {a.key for a in self.m.CloudConfigScreen(app.settings).actions()})
        self.run_picker(s, start="home")
        self.assertEqual(json.loads((cfg / "settings.json").read_text()), {"env": {"KEEP": "1"}})
        self.assertEqual(real.read_bytes() if real.exists() else None, real_before,
                         "the user's real ~/.claude/settings.json must never be touched by a test")


class MemoryGuardSettings(PilotCase):
    def test_local_y_toggles_auto_sync_and_it_is_saved_and_reloaded(self):
        # 0.43.0: the row lives ON the Local screen (user 2026-10-23); no guard detour.
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.press("y")                                  # auto-sync OFF -> ON (hotkey works folded, 0.47.2)
            await h.expand_settings()
            await h.wait_text("Auto-sync local registry with the disk (no prompts): ON")
            self.assertEqual(self.saved_state()["memguard"], {"registry_auto_sync": True})
        self.run_picker(s, start="home")
        # A fresh picker reads it back.
        async def again(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            await h.wait_text("Auto-sync local registry with the disk (no prompts): ON")
        self.run_picker(again, start="home")


class DownloaderQueue(PilotCase):
    def test_space_queues_and_unqueues_like_the_status_line_says(self):
        async def s(app, pilot, h):
            await h.wait_text("Alibaba Qwen 3.8 Operator")
            self.assertIn("Space/Enter queue", h.status())
            await h.goto_row("item", "qwen-3.8-operator")
            await h.press("space")
            await h.wait_text("(1 queued)")
            await h.press("space")
            await h.wait_text("(0 queued)")
        self.run_picker(s, start="download")


class LoadingLine(PTYCase):
    def test_no_loading_flash_on_fast_quit(self):
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        # The startup label legitimately animates until the background prefetch lands. Wait for
        # it to go quiet (half a second with no hourglass written), THEN check quitting adds none.
        # Waiting for the title alone flaked ~1/7: the rest of the first frame still carried it.
        self.wait_for("Quit")
        for _ in range(20):
            self.buf = ""
            self.pump(0.5)
            if "⏳" not in self.screen() and "⌛" not in self.screen():
                break
        self.buf = ""
        self.send("q")
        self.assertEqual(self.exit_code(), 0)
        self.assertNotIn("⏳", self.screen())


class StartupLoadingAnimates(PTYCase):
    """The subtitle's startup label used to be drawn once and never updated, so it looked frozen."""

    def test_startup_label_animates_until_prefetch_lands(self):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        slow = stub.with_name("slow-remote.sh")
        slow.write_text(f'#!/bin/sh\nsleep 3\nexec "{stub}" "$@"\n')
        slow.chmod(0o755)
        self.env["CSL_REMOTE_LAUNCHER"] = str(slow)
        self.spawn()
        self.wait_for("Claude Code Free-Agents: Session Launcher")
        self.pump(2.0)
        frames = set(re.findall(r"free cloud APIs\s+(⏳|⌛️?) loading(\.+)", self.screen()))
        self.assertEqual({g[:1] for g, _ in frames}, {"⏳", "⌛"}, f"hourglass must flip: {frames}")
        self.assertGreaterEqual(len({d for _, d in frames}), 2, f"dots must step: {frames}")
        deadline = time.time() + 10           # then it goes away once the slow load lands
        while time.time() < deadline:
            self.buf = ""
            self.pump(0.6)
            if "loading" not in self.screen():
                break
        self.assertNotIn("loading", self.screen(), "label must clear after the prefetch")
        self.send("q")
        self.assertEqual(self.exit_code(), 0)


class EmojiConstants(unittest.TestCase):
    def test_emoji_constants_from_config(self):
        import emoji_constants as ec
        self.assertEqual(ec.SESSION_EMOJI_LOCAL_STR, "🦾")
        self.assertEqual(ec.SESSION_EMOJI_FREE_API_STR, "📡")
        self.assertEqual(ec.EMOJI_EFFORT_STR, "⚙️ ")
        self.assertEqual(ec.EMOJI_NVIDIA_RATE_LIMITER_STR, "🚦")
        self.assertEqual(ec.EMOJI_TOOLS_STR, "🔧")
        self.assertEqual(ec.EMOJI_MCP_STR, "🔌")
        self.assertEqual(ec.EMOJI_KEY_STR, "🔑")

    def test_emoji_constants_used_in_screens(self):
        import session_picker_model as m
        acts = m.HomeScreen(m.Settings(), local_count=12, remote_count=36).actions()
        self.assertIn("🦾", next(a for a in acts if a.key == "l").label)
        self.assertIn("📡", next(a for a in acts if a.key == "r").label)

    def test_emoji_spacing_in_effort_labels(self):
        import session_picker_model as m
        for screen in (m.LocalScreen(m.Settings(), models=[]), m.RemoteScreen(m.Settings(), agents=[])):
            effort = next(a for a in screen.actions() if a.key == "e")
            self.assertIn("⚙️  Effort", effort.label)

    def test_emoji_spacing_in_other_labels(self):
        import session_picker_model as m
        for action in m.LocalScreen(m.Settings(), models=[]).actions():
            label = action.label
            for emoji in ["🦾", "📡", "🔧", "💬", "🔑", "🔌", "🚀", "📥", "📌", "🔬", "📄", "🧹", "📦", "🔖", "🏷️"]:
                if emoji in label:
                    idx = label.index(emoji) + len(emoji)
                    if idx < len(label):
                        self.assertEqual(label[idx], " ", f"Emoji {emoji} in {label!r} needs a space after it")


if __name__ == "__main__":
    unittest.main()


class ThinkingToggleWiring(PilotCase):
    """The thinking toggle's PICKER wiring (review finding, 2026-10-25): the model layer had
    the fields, but nothing hydrated the stored value into Settings or wired the persist
    callback — the row would reset on every picker restart. End to end through the real
    Picker: hydrate from the store, cycle the row, find it persisted."""

    def test_hydrates_the_stored_choice_and_persists_through_the_row(self):
        import session_menu_state as sms
        sms.save_thinking_session(self.cfg, False)      # the store already says OFF

        async def s(app, pilot, h):
            self.assertIs(app.settings.thinking_session, False, "hydrated from the store")
            self.assertIsNotNone(app.settings.on_thinking_saved, "persist callback wired")
            await h.press("l")                          # Home -> Local lane
            await h.wait_screen("LocalScreen")
            await h.press("j")                          # OFF -> back to the default (None)
            self.assertIsNone(app.settings.thinking_session)
            self.assertNotIn("thinking", self.saved_state(),
                             "None is the default: the key is dropped, not stored")
            await h.press("j")                          # and OFF again
            self.assertIs(app.settings.thinking_session, False)
        self.run_picker(s)
        state = self.saved_state()
        self.assertIs(state.get("thinking", {}).get("session"), False,
                      "the choice reaches the store the picker will load next time")
