"""hooks/session-title-prefix.py: the session title a launcher prefixes onto Claude Code's name.

User 2026-10-22: a home-folder session titled "📡 nvidia-glm53-full-thinking bra0002h-42"
carried two useless parts -- the user name (Claude Code names a session "<folder>-<id>", and in
the home folder the folder IS the user name) and the provider in the alias. The title now reads
"<emoji> <readable model name> · <id>". Every case runs the hook as a subprocess with HOME in a
temp dir, so the user's real ~/.claude/sessions is never read or written.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "session-title-prefix.py"
SID = "11111111-2222-3333-4444-555555555555"


class SessionTitlePrefix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "someuser"
        (self.home / ".claude" / "sessions").mkdir(parents=True)
        self.project = self.home / "ClaudeWorkspace" / "echoprism"
        self.project.mkdir(parents=True)

    def title(self, name, cwd, **env):
        (self.home / ".claude" / "sessions" / "123.json").write_text(
            json.dumps({"sessionId": SID, "name": name}))
        e = {k: v for k, v in os.environ.items()
             if k not in ("LA_SESSION_KIND_EMOJI", "MODEL_ALIAS", "LA_SESSION_TITLE_MODEL")}
        e.update(HOME=str(self.home), **env)
        r = subprocess.run(["python3", str(HOOK)], input=json.dumps({"session_id": SID, "cwd": str(cwd)}),
                           capture_output=True, text=True, env=e, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        return json.loads(r.stdout)["hookSpecificOutput"]["sessionTitle"]

    REMOTE = {"LA_SESSION_KIND_EMOJI": "📡", "MODEL_ALIAS": "nvidia-glm53-full-thinking",
              "LA_SESSION_TITLE_MODEL": "Z.ai GLM 5.3 (thinking)"}

    def test_home_folder_drops_the_user_name_and_the_provider(self):
        self.assertEqual(self.title("someuser-42", self.home, **self.REMOTE),
                         "📡 Z.ai GLM 5.3 (thinking) · 42")

    def test_a_project_folder_keeps_its_name(self):
        self.assertEqual(self.title("echoprism-25", self.project, **self.REMOTE),
                         "📡 Z.ai GLM 5.3 (thinking) · echoprism-25")

    def test_a_project_named_like_the_user_elsewhere_is_not_stripped(self):
        # Only the HOME folder loses its name: a project folder that happens to start with
        # the user name is still a project.
        other = self.home / "work"
        other.mkdir()
        self.assertEqual(self.title("someuser-9", other, **self.REMOTE),
                         "📡 Z.ai GLM 5.3 (thinking) · someuser-9")

    def test_alias_is_the_fallback_without_a_readable_name(self):
        env = dict(self.REMOTE)
        env.pop("LA_SESSION_TITLE_MODEL")
        self.assertEqual(self.title("echoprism-25", self.project, **env),
                         "📡 nvidia-glm53-full-thinking · echoprism-25")

    def test_idempotent(self):
        self.assertIsNone(self.title("📡 Z.ai GLM 5.3 (thinking) · 42", self.home, **self.REMOTE))

    def test_resume_on_another_model_replaces_the_new_format_prefix(self):
        env = dict(self.REMOTE, LA_SESSION_TITLE_MODEL="DeepSeek V4.1 Flash")
        self.assertEqual(self.title("📡 Z.ai GLM 5.3 (thinking) · 42", self.home, **env),
                         "📡 DeepSeek V4.1 Flash · 42")

    def test_resume_converts_an_old_format_title(self):
        # The exact title of the session that prompted this change.
        self.assertEqual(self.title("📡 nvidia-glm53-full-thinking someuser-42", self.home, **self.REMOTE),
                         "📡 Z.ai GLM 5.3 (thinking) · 42")

    def test_no_prefix_configured_changes_nothing(self):
        self.assertIsNone(self.title("someuser-42", self.home))

    def test_help_does_not_run(self):
        r = subprocess.run(["python3", str(HOOK), "--help"], capture_output=True, text=True,
                           input="", timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertIn("LA_SESSION_TITLE_MODEL", r.stdout)


if __name__ == "__main__":
    unittest.main()
