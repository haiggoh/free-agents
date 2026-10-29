"""Regression: manage-backend.py must load the repo's network shim (install/hf-ipv4).

WHY THIS TEST EXISTS. Release discovery (`releases`) fetches PyPI with plain urllib. On home
wifi here, IPv6 to the PyPI CDN black-holes: pypi.org has 8 AAAA records, each connect waits
out the 15 s timeout, and the picker's 60 s subprocess cap kills the run first — so csl's
"Install a release" spun the hourglass for a minute and then said "no releases found"
(user report 2026-10-29; measured: IPv6-only fetch FAILED after 32 s, IPv4 OK in 0.3 s).
The repo already ships the fix (IPv4 forcing + Keychain truststore) as a PYTHONPATH shim, but
nothing put it on manage-backend.py's path. The manager is the choke point every caller
passes (picker, terminal, launchd upgrade job), and its pip installs are child processes.

HERMETIC: inspects the process only — no network request is made.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANAGER = REPO / "install" / "manage-backend.py"
SHIM_DIR = REPO / "install" / "hf-ipv4"

PROBE = f"""
import importlib.util, json, os, socket, sys
spec = importlib.util.spec_from_file_location("manage_backend", {str(MANAGER)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps({{"getaddrinfo": socket.getaddrinfo.__name__,
                   "pythonpath": os.environ.get("PYTHONPATH", "")}}))
"""


def _probe(extra_env=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                          env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    import json
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_manager_forces_ipv4_in_process():
    assert _probe()["getaddrinfo"] == "_ipv4_first"


def test_manager_exports_shim_to_child_processes():
    first = _probe()["pythonpath"].split(os.pathsep)[0]
    assert Path(first) == SHIM_DIR


def test_manager_keeps_an_existing_pythonpath():
    parts = _probe({"PYTHONPATH": "/some/other/dir"})["pythonpath"].split(os.pathsep)
    assert parts[0] == str(SHIM_DIR) and "/some/other/dir" in parts
