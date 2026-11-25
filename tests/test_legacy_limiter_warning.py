#!/usr/bin/env python3
"""Task 3 (0.63.4): the launch-time warning for a proxy that LOADED pre-receipt
limiter/ledger code while the on-disk source has the receipts API (a proxy started
before an update; its wholesale sidecar rewrites drop receipt ids until restart).
Pure unit: no litellm needed, runs under any interpreter."""
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))


def load_hooks():
    spec = importlib.util.spec_from_file_location("la_proxy_hooks", REPO / "bin" / "la_proxy_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LegacyLimiterWarningTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_hooks()
        self.disk = {n: self.mod._disk_receipts_api(REPO / "bin" / f"{n}.py")
                     for n in ("quota_ledger", "rate_limiter")}

    def _run(self, loaded, version="0.63.4"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.mod._warn_pre_receipt_limiter(loaded=loaded, version=version)
        return err.getvalue()

    def test_warns_once_per_stale_module_when_loaded_lacks_the_api(self):
        out = self._run({"quota_ledger": 0, "rate_limiter": 0})
        self.assertIn("free-agents: this proxy runs pre-0.63.4 limiter code", out)
        self.assertIn("restart it to get exact quota receipts", out)
        self.assertIn("quota_ledger", out)
        self.assertIn("rate_limiter", out)
        # the line names both the loaded and on-disk API levels
        self.assertIn(f"on disk {self.disk['quota_ledger']}", out)

    def test_silent_when_loaded_matches_disk(self):
        self.assertEqual(self._run(dict(self.disk)), "",
                         "no warning when the loaded code has the receipts API")

    def test_only_the_stale_module_is_named(self):
        loaded = {"quota_ledger": self.disk["quota_ledger"], "rate_limiter": 0}
        out = self._run(loaded)
        self.assertIn("rate_limiter", out)
        self.assertNotIn("quota_ledger", out)

    def test_disk_parse_missing_marker_reads_as_zero(self):
        # a pre-receipt file has NO RECEIPTS_API line: the parser must read 0, not crash
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "quota_ledger.py"
            p.write_text("# old version, no marker\n")
            self.assertEqual(self.mod._disk_receipts_api(p), 0)
            p.write_text("RECEIPTS_API = 1\n")
            self.assertEqual(self.mod._disk_receipts_api(p), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
