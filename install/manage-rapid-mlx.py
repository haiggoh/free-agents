#!/usr/bin/env python3
"""Backward compatibility wrapper for manage-rapid-mlx.py

This wrapper translates the old CLI to the new unified CLI:
- Old: manage-rapid-mlx.py install 0.16.1
- New: manage-backend.py --backend rapid-mlx install 0.16.1

With no arguments and a TTY, opens the interactive menu.
"""

import sys
import importlib.util
from pathlib import Path

# Import manage-backend.py directly (hyphen in filename prevents normal import)
manage_backend_path = Path(__file__).parent / "manage-backend.py"
spec = importlib.util.spec_from_file_location("manage_backend", manage_backend_path)
manage_backend = importlib.util.module_from_spec(spec)
sys.modules["manage_backend"] = manage_backend
spec.loader.exec_module(manage_backend)

# Also import the RapidMLXManager for the interactive menu
sys.path.insert(0, str(Path(__file__).parent))
from managers.rapid_mlx import RapidMLXManager

if __name__ == "__main__":
    # If no arguments and TTY, open interactive menu
    if len(sys.argv) == 1 and sys.stdin.isatty():
        mgr = RapidMLXManager()
        sys.exit(mgr.run_interactive_menu())
    # Otherwise translate old CLI to new unified CLI
    new_argv = ["--backend", "rapid-mlx"] + sys.argv[1:]
    sys.exit(manage_backend.main(new_argv))