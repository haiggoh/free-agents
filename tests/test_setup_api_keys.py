"""Offline credential onboarding checks; all credentials and HOME directories are fake."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install/setup-api-keys.py"
spec = importlib.util.spec_from_file_location("setup_api_keys", SCRIPT)
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.keys = self.home / ".api_keys"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], text=True,
                              input="", capture_output=True,
                              env={**os.environ, "HOME": str(self.home),
                                   "LA_API_KEYS_DIR": str(self.keys)}, timeout=5)

    def test_help_unknown_flags_and_inventory_never_create_store(self):
        for args, code in [(('--help',), 0), (('--not-a-flag',), 2), (('--list',), 0), ((), 1)]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertFalse(self.keys.exists())

    def test_csl_entrypoint_works_without_local_configuration(self):
        result = subprocess.run([str(ROOT / "bin/csl"), "setup-remote", "--help"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LA_API_KEYS_DIR", result.stdout)

    def test_atomic_new_files_have_private_permissions_and_no_leftovers(self):
        with setup.Store(self.keys) as store:
            store.save_new("mistral", "fixture-value")
            self.assertEqual(store.state("mistral"), "saved")
        self.assertEqual(stat.S_IMODE(self.keys.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.keys / "mistral").stat().st_mode), 0o600)
        self.assertEqual((self.keys / "mistral").read_text(), "fixture-value\n")
        self.assertEqual([p.name for p in self.keys.iterdir()], ["mistral"])

    def test_existing_key_and_mode_preserved_even_on_concurrent_add(self):
        with setup.Store(self.keys) as store:
            store.save_new("groq", "original-fixture")
            before = (self.keys / "groq").stat()
            with self.assertRaises(setup.StoreError):
                store.save_new("groq", "new-fixture")
        self.assertEqual((self.keys / "groq").read_text(), "original-fixture\n")
        self.assertEqual((self.keys / "groq").stat().st_ino, before.st_ino)
        self.assertEqual((self.keys / "groq").stat().st_mode, before.st_mode)

    def test_failed_publish_cleans_temp_and_does_not_create_key(self):
        with setup.Store(self.keys) as store:
            with patch.object(setup.os, "link", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    store.save_new("groq", "fixture-value")
        self.assertEqual(list(self.keys.iterdir()), [])

    def test_refuse_symlink_directory_and_parent(self):
        target = self.home / "target"
        target.mkdir(mode=0o700)
        self.keys.symlink_to(target, target_is_directory=True)
        for path in (self.keys, self.keys / "child"):
            with self.assertRaises(setup.StoreError):
                with setup.Store(path):
                    pass

    def test_refuse_nonprivate_directory_without_changing_mode(self):
        self.keys.mkdir(mode=0o755)
        with self.assertRaises(setup.StoreError):
            with setup.Store(self.keys):
                pass
        self.assertEqual(stat.S_IMODE(self.keys.stat().st_mode), 0o755)

    def test_foreign_owner_directory_refused(self):
        self.keys.mkdir(mode=0o700)
        with patch.object(setup.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(setup.StoreError):
                with setup.Store(self.keys):
                    pass

    def test_symlink_and_empty_existing_files_kept(self):
        self.keys.mkdir(mode=0o700)
        outside = self.home / "outside"
        outside.write_text("original")
        (self.keys / "groq").symlink_to(outside)
        (self.keys / "mistral").touch(mode=0o600)
        with setup.Store(self.keys) as store:
            self.assertEqual(store.state("groq"), "protected")
            self.assertEqual(store.state("mistral"), "empty (kept)")
            for name in ("groq", "mistral"):
                with self.assertRaises(setup.StoreError):
                    store.save_new(name, "fixture-value")
        self.assertEqual(outside.read_text(), "original")

    def test_reject_multiline_commands_quotes_and_bad_account_id(self):
        for value in ("", "abc\nxyz", "export KEY=abc", "Bearer abc", '"abc"', "abc\x1b[1m", "üabc"):
            with self.subTest(value=value), self.assertRaises(setup.StoreError):
                setup.validate_value("groq", value)
        with self.assertRaises(setup.StoreError):
            setup.validate_value("cloudflare-account-id", "not-an-account-id")
        setup.validate_value("cloudflare-account-id", "a" * 32)

    def test_selection_deduplicates_and_missing_excludes_optional_accounts(self):
        with setup.Store(self.keys) as store:
            store.save_new("groq", "fixture-value")
            self.assertEqual([p.slug for p in setup.select("6,mistral 8", store)], ["mistral", "siliconflow"])
            missing = [p.slug for p in setup.select("missing", store)]
            self.assertNotIn("groq", missing)
            self.assertNotIn("cerebras", missing)
            self.assertNotIn("modelscope", missing)
            with self.assertRaises(setup.StoreError):
                setup.select("1,invalid", store)

    def test_hidden_input_refuses_echo_fallback(self):
        with patch.object(setup.sys.stdin, "isatty", return_value=True), \
             patch.object(setup.sys.stderr, "isatty", return_value=True), \
             patch.object(setup.getpass, "getpass", side_effect=setup.getpass.GetPassWarning("fixture")):
            with self.assertRaises(setup.StoreError):
                setup.hidden_input("Key: ")

    def test_cancel_second_cloudflare_field_creates_nothing(self):
        provider = next(p for p in setup.PROVIDERS if p.slug == "cloudflare")
        with setup.Store(self.keys) as store, patch("builtins.input", return_value=""), \
             patch.object(setup, "hidden_input", side_effect=["fixture-value", KeyboardInterrupt]), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                setup.wizard(store, [provider])
        self.assertFalse(self.keys.exists())

    def test_existing_cloudflare_token_only_prompts_for_account(self):
        provider = next(p for p in setup.PROVIDERS if p.slug == "cloudflare")
        with setup.Store(self.keys) as store:
            store.save_new("cloudflare", "original-fixture")
            with patch("builtins.input", return_value=""), \
                 patch.object(setup, "hidden_input", return_value="a" * 32) as prompt, \
                 contextlib.redirect_stdout(io.StringIO()):
                setup.wizard(store, [provider])
            prompt.assert_called_once()
            self.assertIn("Account ID", prompt.call_args.args[0])
        self.assertEqual((self.keys / "cloudflare").read_text(), "original-fixture\n")

    def test_real_terminal_paste_is_hidden_and_saved(self):
        import pty
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, str(SCRIPT), "mistral"],
                      {**os.environ, "HOME": str(self.home), "LA_API_KEYS_DIR": str(self.keys)})
        output = b""
        stage = 0
        deadline = time.monotonic() + 10
        secret = b"terminal-fixture-only"
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(fd, 8192)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                if stage == 0 and b"Finish:" in output:
                    os.write(fd, b"\n")
                    stage = 1
                if stage == 1 and b"Enter skips provider):" in output:
                    os.write(fd, secret + b"\n")
                    stage = 2
                if b"Session integration is a separate step." in output:
                    break
            self.assertEqual(stage, 2, output.decode())
            self.assertNotIn(secret, output)
            self.assertIn(b"Saved mistral (not tested)", output)
            self.assertEqual((self.keys / "mistral").read_bytes(), secret + b"\n")
        finally:
            os.close(fd)
            # Close the PTY and reap the bounded fixture process even on failure.
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
