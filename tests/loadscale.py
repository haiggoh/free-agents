"""Load-scaled test timeouts (waypoint pty-litellm-venv-tests-flake).

PTY and real-proxy tests measured their waits on an idle machine; on a shared box
(load avg > 5) the same waits flake (rendering lags, proxy startup slows). The scale
reads the 1-minute load average and maps it to a timeout multiplier:
load 2 -> x1 (idle-machine numbers stay), load 8 -> x4, clamped to x8 max.
"""
import subprocess


def load_avg1() -> float:
    """1-minute load average (Linux /proc, macOS sysctl); 1.0 when unreadable."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True,
                             text=True, timeout=5).stdout
        return float(out.strip("{} \n").split()[0])
    except Exception:
        return 1.0


def scale_for(load: float) -> float:
    """Pure mapping: load -> timeout multiplier, clamped to [1, 8]."""
    return max(1.0, min(8.0, load / 2.0))


def current_scale() -> float:
    """Load-scaled multiplier, sampled AT CALL TIME — load moves fast on shared boxes, so
    an import-time snapshot goes stale within a test run."""
    return scale_for(load_avg1())
