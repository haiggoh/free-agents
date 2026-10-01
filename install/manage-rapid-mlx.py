#!/usr/bin/env python3
"""Backward compatibility wrapper for manage-rapid-mlx.py

This wrapper translates the old CLI to the new unified CLI:
- Old: manage-rapid-mlx.py install 0.15.3
- New: manage-backend.py --backend rapid-mlx install 0.15.3
"""

import sys
from pathlib import Path

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent))

from install.manage_backend import main

if __name__ == "__main__":
    # Translate old CLI to new unified CLI
    new_argv = ["--backend", "rapid-mlx"] + sys.argv[1:]
    sys.exit(main(new_argv))