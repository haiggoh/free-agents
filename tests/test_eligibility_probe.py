#!/usr/bin/env python3
"""fa-eligibility-probe: the eligibility verdict comes from EVIDENCE, never from absence.

Refuses on a free-lane shell (Review Focus 5); reports unknown without running when a settings
layer allows Bash outright; reads a `system` notice as not-eligible; absence alone is unknown.
The credential command's secret travels only in the child ENV — never in argv, output or the
cache (reviewed in the adversarial table). Every run is a stub: no request is ever sent."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "fa_eligibility_probe", ROOT / "bin" / "fa-eligibility-probe.py")
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

CLOUD_ENV = {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}

NOTICE = json.dumps({"type": "system", "subtype": "warning",
                     "content": "auto mode could not determine safety — the check ran server-side"})


class EligibilityProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = t / "cfg"
        self.cfg.mkdir()
        self.state = t / "state"
        self.old = {k: os.environ.get(k) for k in ("FA_STATE_DIR", "CLAUDE_CONFIG_DIR",
                                                   "FA_CREDENTIAL_COMMAND", "ANTHROPIC_BASE_URL")}
        os.environ["FA_STATE_DIR"] = str(self.state)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def probe(self, child, env=None):
        # A stub credential command by default: the real `joyia ... --setup-only` must never
        # run from a test (it reached it once here — the default resolves when joyia is on PATH).
        stub = Path(self.tmp.name, "cred-default.sh")
        if not stub.exists():
            stub.write_text('#!/bin/sh\necho \'{"ANTHROPIC_AUTH_TOKEN": "sk-fixture-default"}\'\n')
            stub.chmod(0o755)
        merged = {**CLOUD_ENV, "FA_CREDENTIAL_COMMAND": str(stub), **(env or {})}
        seen = {}
        out = fp.probe(lambda argv, e, stdin: seen.update(argv=argv, env=e) or child(argv, e, stdin),
                       env=merged, cfg_dir=self.cfg)
        return out, seen

    def test_probe_refuses_on_a_free_lane_shell(self):
        ran = []

        def child(argv, env, stdin):
            ran.append(argv)
            return 0, NOTICE

        out = fp.probe(child, env={"ANTHROPIC_BASE_URL": "http://localhost:4142"}, cfg_dir=self.cfg)
        self.assertEqual(out["verdict"], "refused")
        self.assertEqual(ran, [], "a free-lane shell never sends a request")

    def test_probe_reads_the_notice_as_not_eligible(self):
        out, _ = self.probe(lambda argv, env, stdin: (0, NOTICE + "\n"))
        self.assertEqual(out["verdict"], "not-eligible")
        self.assertIn("safety", out["evidence"])
        self.assertTrue(fp.cache_path().exists(), "the verdict is cached")

    def test_absent_notice_alone_is_never_eligible(self):
        out, _ = self.probe(lambda argv, env, stdin: (0, '{"type":"assistant","message":{}}\n'))
        self.assertEqual(out["verdict"], "unknown")

    def test_probe_reports_unknown_when_bash_is_blanket_allowed(self):
        ran = []

        def child(argv, env, stdin):
            ran.append(argv)
            return 0, NOTICE

        (self.cfg / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(*)"]}}))
        out = fp.probe(child, env=dict(CLOUD_ENV), cfg_dir=self.cfg)
        self.assertEqual(out["verdict"], "unknown")
        self.assertEqual(ran, [], "an allowlisted machine is measured without a run")

    def test_credential_env_reaches_claude_but_never_argv_or_output(self):
        token = "sk-fixture-0123456789abcdefSECRET"

        def stub_claude(argv, env, stdin):
            # The stub claude writes what it SAW (its own env + argv) for the test to inspect.
            Path(self.tmp.name, "claude_env.json").write_text(
                json.dumps({"token": env.get("ANTHROPIC_AUTH_TOKEN", ""),
                            "argv": argv}))
            return 0, NOTICE + "\n"

        cred = Path(self.tmp.name, "cred.sh")
        cred.write_text(f"#!/bin/sh\necho '{{\"ANTHROPIC_AUTH_TOKEN\": \"{token}\"}}'\n")
        cred.chmod(0o755)
        env = {**CLOUD_ENV, "FA_CREDENTIAL_COMMAND": str(cred)}
        out, seen = self.probe(stub_claude, env=env)
        self.assertEqual(out["verdict"], "not-eligible")
        seen_env = json.loads(Path(self.tmp.name, "claude_env.json").read_text())
        self.assertEqual(seen_env["token"], token, "the child env carries the credential")
        self.assertNotIn(token, json.dumps(seen["argv"]), "never in the child argv")
        cache = fp.cache_path().read_text()
        self.assertNotIn(token, cache, "never in the cache")

    def test_verdict_cache_is_used_until_forced(self):
        runs = []

        def child(argv, env, stdin):
            runs.append(argv)
            return 0, NOTICE + "\n"

        first, _ = self.probe(child)
        second, _ = self.probe(child)
        self.assertEqual(first["verdict"], "not-eligible")
        self.assertEqual((second["verdict"], second["evidence"]), (first["verdict"], first["evidence"]),
                         "the cached verdict is returned verbatim")
        self.assertEqual(len(runs), 1, "the second probe reads the cache instead of running")


if __name__ == "__main__":
    unittest.main()
