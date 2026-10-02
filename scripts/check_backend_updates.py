#!/usr/bin/env python3
"""Unified backend update checker for local-agents."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import managers to trigger registration via @register_manager decorator
import managers.rapid_mlx  # noqa: F401
import managers.vllm_mlx  # noqa: F401
import managers.omlx  # noqa: F401
import managers.llama_cpp  # noqa: F401
import managers.litellm  # noqa: F401

from managers import get_manager, get_manager_instance, list_managers, ManagerError

LOG_DIR = Path.home() / ".claude" / "logs"
LOG_FILE = LOG_DIR / "backend-update-check.log"

def log_message(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with LOG_FILE.open("a") as f:
        f.write(f"{timestamp} {msg}\n")

def check_backend(backend_name: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Check a single backend for updates. Returns (backend_name, outdated_list)."""
    try:
        mgr = get_manager(backend_name)
        outdated = mgr.check_updates()
        checked = mgr.get_packages_to_check()
        log_message(f"CHECKED {backend_name}: {checked}")
        return backend_name, outdated
    except ManagerError as e:
        log_message(f"ERROR {backend_name}: {e}")
        return backend_name, []
    except Exception as e:
        log_message(f"UNEXPECTED {backend_name}: {e}")
        return backend_name, []

def check_all_backends(backends: list[str] | None = None) -> dict[str, list[tuple[str, str, str]]]:
    """Check all backends for updates."""
    if backends is None:
        backends = list_managers()

    # At this point backends is guaranteed to be a list
    assert backends is not None

    results = {}
    for backend in backends:
        name, outdated = check_backend(backend)
        results[name] = outdated

    # Log summary
    total_outdated = sum(len(v) for v in results.values())
    if total_outdated > 0:
        details = "; ".join(f"{b}: {', '.join(f'{p} {i}->{latest_v}' for p,i,latest_v in u)}" for b, u in results.items() if u)
        log_message(f"OUTDATED: {details}")
        # macOS notification
        try:
            import subprocess
            subprocess.run([
                "osascript", "-e",
                f'display notification "{details} — upgrade manually via manage-backend.py" with title "Backend updates available"'
            ], check=False)
        except Exception:
            pass
    else:
        checked = "; ".join(f"{b}: {get_manager_instance(b).get_packages_to_check()}" for b in backends)
        log_message(f"UP-TO-DATE: checked {checked}")

    return results

def format_update_report(results: dict[str, list[tuple[str, str, str]]]) -> str:
    """Format update results for human-readable output."""
    lines = ["Backend Update Check Report", "=" * 40, ""]
    any_outdated = False

    for backend in sorted(results.keys()):
        outdated = results[backend]
        lines.append(f"{backend}:")
        if outdated:
            any_outdated = True
            for pkg, installed, latest_version in outdated:
                lines.append(f"  ⬆ {pkg} {installed} -> {latest_version}")
        else:
            lines.append("  ✓ up to date")
        lines.append("")

    if not any_outdated:
        lines.append("All backends up to date.")

    return "\n".join(lines)

def save_json_report(results: dict[str, list[tuple[str, str, str]]]) -> None:
    """Save machine-readable JSON report."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_file = LOG_DIR / "backend-update-check.json"
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backends": {
            b: [{"package": p, "installed": i, "latest": l} for p, i, l in u]
            for b, u in results.items()
        }
    }
    report_file.write_text(json.dumps(data, indent=2))

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Check all backends for updates")
    parser.add_argument("--backend", action="append", help="Specific backend(s) to check")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--save-json", action="store_true", help="Save JSON report to log dir")
    args = parser.parse_args()

    backends = args.backend if args.backend else None
    results = check_all_backends(backends)

    if args.json:
        print(json.dumps({
            b: [{"package": p, "installed": i, "latest": l} for p, i, l in u]
            for b, u in results.items()
        }, indent=2))
    else:
        print(format_update_report(results))

    if args.save_json:
        save_json_report(results)

    # Exit code: 0 = up to date, 1 = updates available, 2 = error
    any_outdated = any(len(u) > 0 for u in results.values())
    return 1 if any_outdated else 0

if __name__ == "__main__":
    sys.exit(main())