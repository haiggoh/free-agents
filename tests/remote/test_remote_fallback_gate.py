"""tests/remote/test_remote_fallback_gate.py — gate wrapper for the script-style
tests/remote/test_remote_fallback.py (it defines no test_ functions, so pytest never
collected it and no gate ran it). Runs the script and requires exit 0."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_remote_fallback_script_passes():
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "remote" / "test_remote_fallback.py")],
                       capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert r.returncode == 0, f"fallback script failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
