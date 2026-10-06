#!/usr/bin/env python3
"""tests/test_new_features.py — session picker features added in the 0.22/0.23 line.

Covers the Backend Manager screen, the API Keys accordion with INLINE key entry (paste advances,
typing needs Enter, whitespace is stripped from pastes, the signup page opens through a stub —
never a real browser), Left/Right cycling of settings, last-launched persistence, the loading
line, and emoji constants/spacing. UI flows run through Textual's pilot (picker_harness);
pure model/constant checks need no UI at all.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_new_features.py
"""
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase, PTYCase  # noqa: E402

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

    def test_backend_cycles_and_info_prompt_rejects_bad_version(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            await h.press("c")
            await h.wait_for(lambda: "Backend: rapid-mlx" not in h.text(), "backend to cycle")
            await h.press("f")                        # inline version prompt, no child yet
            await h.wait_text("Version to show")
            await h.press(*"1;rm", "enter")
            await h.wait_text("version rejected")
            self.assertEqual(self.launches(), [])
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
            # header and its items.
            self.assertFalse(rows[0].endswith("\n"), rows[0])
            self.assertTrue(rows[3].lstrip("▸▾ ").startswith("\nGoogle Gemini"), rows[3])
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
            await h.press("m")
            await h.wait_text("MCPs: ENABLED")
            await h.press("right")
            await h.wait_text("MCPs: DISABLED")
            await h.press("left")
            await h.wait_text("MCPs: ENABLED")
        self.run_picker(s, start="local")


class LastLaunchedPersistence(PilotCase):
    def test_local_launch_offers_go_launch_and_persists(self):
        async def s(app, pilot, h):
            await h.press("c")
            await h.wait_text("deepseek-r1-architect")
            await h.goto_row("item", "deepseek-r1-architect")
            await h.press("enter")
            await h.wait_for(lambda: self.launches(), "a recorded launch")
            await h.wait_text("Go launch: deepseek-r1-architect session")
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


class DownloaderQueue(PilotCase):
    def test_space_queues_and_unqueues_like_the_status_line_says(self):
        async def s(app, pilot, h):
            await h.wait_text("qwen-3.8-operator")
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
        self.buf = ""
        self.send("q")
        self.assertEqual(self.exit_code(), 0)
        self.assertNotIn("⏳", self.screen())


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
