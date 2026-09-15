#!/usr/bin/env python3
"""Regression: the LiteLLM proxy must actually LOAD the repo's Keychain-trust shim.

WHY THIS TEST EXISTS. On corporate wifi a TLS-inspecting proxy makes every Python HTTPS
request fail with "self-signed certificate in certificate chain", because certifi's bundle
has no corporate CA while the macOS Keychain does. The repo ships the fix as a PYTHONPATH
shim (install/hf-ipv4/sitecustomize.py -> truststore.inject_into_ssl()), and
remote-session.sh exports PYTHONPATH intending the LiteLLM proxy to pick it up.

It did not. The pipx console script `~/.local/bin/litellm` has `-E` in its shebang, and
`-E` makes Python IGNORE PYTHONPATH — so the proxy loaded the system sitecustomize instead
and kept using certifi. The failure surfaced as LiteLLM wrapping a local TLS error as an
HTTP 500, which reads like a provider outage and sends you debugging the wrong system.

These tests are HERMETIC: they inspect interpreters and process environments only. No
network request, no generation, no credential is used.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SHIM_DIR = REPO / "install" / "hf-ipv4"
SHIM_FILE = SHIM_DIR / "sitecustomize.py"
WRAPPER = REPO / "bin" / "litellm-with-trust.py"


def _resolve_litellm_python():
    """The interpreter the installed litellm console script actually runs."""
    script = shutil.which("litellm")
    if not script:
        return None, None
    try:
        first = pathlib.Path(script).read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return script, None
    if not first.startswith("#!"):
        return script, None
    return script, first[2:].strip()


class TrustShimContract(unittest.TestCase):
    def setUp(self):
        self.script, self.shebang = _resolve_litellm_python()
        if not self.script:
            self.skipTest("litellm is not installed on this machine")
        self.interp = (self.shebang or "").split()[0] if self.shebang else None
        if not self.interp or not os.path.exists(self.interp):
            self.skipTest(f"could not resolve the litellm interpreter from {self.shebang!r}")

    def _loaded_sitecustomize(self, argv_prefix, env):
        """Ask a Python process which sitecustomize file it actually loaded."""
        code = ("import sitecustomize,sys;"
                "sys.stdout.write(getattr(sitecustomize,'__file__','') or '')")
        proc = subprocess.run(argv_prefix + ["-c", code],
                              capture_output=True, text=True, env=env, timeout=60)
        return proc.stdout.strip()

    def test_shim_exists_and_injects_truststore(self):
        """The fix is worthless if the shim itself stopped calling truststore."""
        self.assertTrue(SHIM_FILE.is_file(), f"missing trust shim: {SHIM_FILE}")
        text = SHIM_FILE.read_text()
        self.assertIn("truststore", text)
        self.assertIn("inject_into_ssl", text)

    def test_dash_E_defeats_pythonpath(self):
        """CAPTURES THE BUG. With -E the shim is NOT loaded, however set PYTHONPATH is.

        This asserts the broken behaviour on purpose: if a future Python or pipx stops
        honouring -E this way, this test fails and tells us the workaround is obsolete,
        rather than leaving a wrapper in place forever for a reason that no longer holds.
        """
        env = dict(os.environ, PYTHONPATH=str(SHIM_DIR))
        loaded = self._loaded_sitecustomize([self.interp, "-E"], env)
        self.assertNotEqual(
            loaded, str(SHIM_FILE),
            "-E unexpectedly honoured PYTHONPATH; the wrapper's rationale needs rechecking")

    def test_without_dash_E_the_repo_shim_loads(self):
        """THE FIX'S PREMISE: the same interpreter, minus -E, does load our shim."""
        env = dict(os.environ, PYTHONPATH=str(SHIM_DIR))
        loaded = self._loaded_sitecustomize([self.interp], env)
        self.assertEqual(loaded, str(SHIM_FILE),
                         "the repo shim did not load even without -E")

    def test_truststore_importable_in_the_litellm_venv(self):
        """A shim that loads but cannot import truststore silently does nothing:
        the shim swallows ImportError by design, so this must be asserted separately."""
        proc = subprocess.run([self.interp, "-c", "import truststore;print('ok')"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0,
                         f"truststore missing from the litellm venv: {proc.stderr[:200]}")

    def test_wrapper_exists_and_supports_help(self):
        """Every script we ship answers --help, and --help must NOT start a server."""
        self.assertTrue(WRAPPER.is_file(), f"missing wrapper: {WRAPPER}")
        proc = subprocess.run([sys.executable, str(WRAPPER), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertIn("trust", proc.stdout.lower())
        # A server would have bound a port and blocked; reaching here with usage text
        # on stdout and a clean exit is the evidence that it parsed instead of ran.
        self.assertNotIn("Uvicorn running", proc.stdout)

    def test_wrapper_loads_the_shim_under_the_litellm_interpreter(self):
        """THE ACTUAL CONTRACT: the process that will import litellm.run_server reports
        the REPO shim as its loaded sitecustomize. This is the assertion the spec asks
        for, and the one the -E console script cannot satisfy."""
        env = dict(os.environ, PYTHONPATH=str(SHIM_DIR),
                   LA_TRUST_SHIM_SELFTEST="1")
        proc = subprocess.run([self.interp, str(WRAPPER), "--print-trust-state"],
                              capture_output=True, text=True, env=env, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertIn(str(SHIM_FILE), proc.stdout,
                      f"wrapper did not load the repo shim; reported: {proc.stdout[:300]}")
        self.assertIn("truststore=injected", proc.stdout,
                      f"truststore was not injected into ssl; reported: {proc.stdout[:300]}")

    def test_launcher_no_longer_relies_on_the_bare_console_script(self):
        """remote-session.sh must start the proxy through the wrapper, not `litellm`,
        or the -E path silently returns."""
        text = (REPO / "bin" / "remote-session.sh").read_text()
        self.assertIn("litellm-with-trust.py", text,
                      "remote-session.sh does not reference the trust wrapper")
        self.assertNotIn("exec litellm --config", text,
                         "remote-session.sh still execs the bare -E console script")


if __name__ == "__main__":
    unittest.main(verbosity=2)
