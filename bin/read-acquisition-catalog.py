#!/usr/bin/env python3
"""read-acquisition-catalog.py — the 0.16.0 CLI name for the acquisition-catalogue reader.

Thin alias for bin/acquisition_catalog.py (also reachable as `la-catalogue-generate.py acquisitions`).
  read-acquisition-catalog.py --report [FILE ...]   human-readable rows + every problem (default)
  read-acquisition-catalog.py --parse  [FILE ...]   JSON {"rows": [...], "problems": [...]}
Exit 0 when every row is valid, 1 on any problem, 2 on a usage error.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import acquisition_catalog  # noqa: E402

if __name__ == "__main__":
    sys.exit(acquisition_catalog.main())
