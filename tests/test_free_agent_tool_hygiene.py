"""free-agent-tool.py hygiene fixes (waypoints fa-local-fallback-inert, free-agent-tool-local-only,
free-agent-tool-an-invalid, fa-lane-id-groq-qwen32-stale-name).

1. (local fallback: deferred — its registry reads config.example.sh in an installed plugin;
   waypoint fa-local-fallback-inert)
2. The 'only local agents' opt-in must be read from the session-menu config dir, not CLAUDE_CONFIG_DIR.
3. An unknown role name is a caller typo: refuse it, never silently reorder the dispatch chain.
4. A dispatch id must not name a model family its display label does not serve.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "bin" / "free-agent-tool.py"
sys.path.insert(0, str(REPO / "bin"))
spec = importlib.util.spec_from_file_location("free_agent_tool_hygiene", TOOL)
fat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fat)
import session_menu_state as sms  # noqa: E402  (same module object free-agent-tool imports)

KNOWN_ROLES = set(fat.ROLE_THINKING_DEFAULT)   # derived, never a literal copy


def _prefs(*entries):
    return {"order": list(entries)}


REMOTE = {"id": "r1", "kind": "remote", "provider": "gemini", "family": "google",
          "roles": ["operator"], "display": "R1"}


class LocalOnlyReadsSessionMenuDir(unittest.TestCase):
    def test_reads_the_session_menu_config_dir_not_claude_config_dir(self):
        with tempfile.TemporaryDirectory() as menu, tempfile.TemporaryDirectory() as claude:
            seen = []

            class _State:
                def __init__(self, on):
                    self.local_registry = {"agents_local_only": on}

            def fake_load(path=None):
                seen.append(Path(path).resolve())
                return _State(Path(path).resolve() == Path(menu).resolve())

            env = {"CLAUDE_CONFIG_DIR": claude}
            with mock.patch.dict(os.environ, {sms.CONFIG_DIR_ENV: menu}), \
                    mock.patch.object(sms, "load", fake_load):
                os.environ.pop(sms.CONFIG_DIR_ENV_ALIAS, None)
                self.assertTrue(fat._local_agents_only(env), f"load() was called with {seen}")


class UnknownRoleRefused(unittest.TestCase):
    def setUp(self):
        self.calls = []
        p = mock.patch.object(fat, "dispatch_entry",
                              lambda entry, *a, **k: self.calls.append(entry["id"]) or "x")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fn(*args)
        return code, err.getvalue()

    def test_run_refuses_an_unknown_role_and_names_the_valid_set(self):
        code, err = self._run(fat.run, {"prompt": "x", "role": "reviewer"}, {})
        self.assertEqual(code, 1, err)
        self.assertEqual(self.calls, [], "nothing may be dispatched for a typo'd role")
        msg = json.loads(err.strip().splitlines()[-1])["error"]
        self.assertIn("reviewer", msg)
        for r in KNOWN_ROLES:
            self.assertIn(r, msg)

    def test_run_background_refuses_it_too(self):
        with tempfile.TemporaryDirectory() as rd:
            code, err = self._run(fat.run_background, {"prompt": "x", "role": "reviewer"}, {},
                                  str(Path(rd) / "run"))
        self.assertEqual(code, 2, err)
        self.assertIn("reviewer", err)
        self.assertEqual(self.calls, [])

    def test_every_known_role_is_still_accepted(self):
        for r in KNOWN_ROLES:
            self.assertEqual(fat.request_role({"role": r}), r)
        self.assertIsNone(fat.request_role({}))   # general-purpose: no filter


class DispatchIdsMatchTheirModels(unittest.TestCase):
    # a model-family word in the id must also appear in the display label (stale-name class:
    # groq-qwen32 kept naming Qwen while serving GPT-OSS 120B)
    FAMILY_WORDS = ("qwen", "glm", "mistral", "gemini", "deepseek", "nemotron", "gpt-oss",
                    "step", "kimi", "llama", "laguna", "kat", "codestral")

    def test_no_id_names_a_family_its_display_does_not(self):
        prefs = json.loads((REPO / "config" / "dispatch-preferences.json").read_text())
        self.assertTrue(prefs["order"], "empty roster: the check found nothing to check")
        bad = []
        for e in prefs["order"]:
            ident, label = e["id"].lower(), (e.get("display") or "").lower().replace(" ", "-")
            bad += [f"{e['id']} names {w!r} but serves {e.get('display')!r}"
                    for w in self.FAMILY_WORDS if w in ident and w not in label]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
