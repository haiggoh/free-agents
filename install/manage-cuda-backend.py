#!/usr/bin/env python3
"""manage-cuda-backend.py — thin compatibility wrapper over the registered CUDA managers.

0.49.0 Task 14 moved CUDA backends INTO the manager registry (install/managers/_cuda.py):
versioned side-by-side venvs under <LA_USER_STATE_DIR>/cuda-backends/<name>-<version>, health
that RUNS the binary, repair, remove, snapshot, promote gated by a GPU smoke, and the live
config as the only pin store. This file keeps the old CLI verbs and maps the old backend names:

  vllm      -> cuda-vllm        install/uninstall/switch/info/smoke/detect-cuda/list/health/repair
  llama-cpp -> cuda-llama-cpp
  + the new cuda-exllamav2 (TabbyAPI) and cuda-sglang

Everything is delegated to install/manage-backend.py, so the picker, the launchers and this
wrapper can never disagree about a backend. `launchd`/other commands pass through unchanged.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANAGE_BACKEND = HERE / "manage-backend.py"

NAME_MAP = {"vllm": "cuda-vllm", "llama-cpp": "cuda-llama-cpp"}
CUDA_BACKENDS = ("cuda-vllm", "cuda-llama-cpp", "cuda-exllamav2", "cuda-sglang")

VERB_MAP = {"uninstall": "remove", "switch": "promote", "info": "installed", "list": "installed",
            "detect-cuda": "detect-cuda"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CUDA backend lifecycle manager (wrapper over manage-backend.py; "
                    "backends: " + ", ".join(CUDA_BACKENDS) + ")",
        epilog="Environment: LA_USER_STATE_DIR overrides the cuda-backends root (default "
               "$HOME/.local/share/free-agents). All state lives in the LIVE config.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for verb, help_text in (("install", "install a versioned CUDA backend (GPU smoke gates it)"),
                            ("uninstall", "remove an installed version (alias of remove)"),
                            ("switch", "promote a version (alias of promote; GPU smoke gates it)"),
                            ("info", "list installed versions (alias of installed)"),
                            ("list", "list installed versions"),
                            ("status", "alias of info"),
                            ("smoke", "run the GPU smoke test in a backend's venv"),
                            ("health", "health of every installed version (add --all for all backends)"),
                            ("repair", "rebuild a broken/fragile version in place"),
                            ("detect-cuda", "print the detected CUDA version and PyTorch index")):
        p = sub.add_parser(verb, help=help_text)
        p.add_argument("backend", nargs="?", choices=list(NAME_MAP) + list(CUDA_BACKENDS),
                       help="backend name (old names vllm/llama-cpp map to the cuda-* managers)")
        p.add_argument("version", nargs="?", help="version for install/uninstall/switch/repair")
        p.add_argument("--force", action="store_true")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--all", action="store_true")
        p.add_argument("--json", action="store_true")
        p.add_argument("--cuda-version", default=None, help="override CUDA detection (e.g. 12.8)")
    args = parser.parse_args()

    backend = NAME_MAP.get(args.backend, args.backend) if args.backend else None
    cmd = VERB_MAP.get(args.cmd, args.cmd)

    if cmd == "detect-cuda":
        sys.path.insert(0, str(HERE))
        import managers  # noqa: F401  (registry)
        import managers._cuda  # noqa: F401
        inst = managers.get_manager_instance("cuda-vllm")
        try:
            cuda = inst.detect_cuda(args.cuda_version)
        except Exception as exc:
            print(f"CUDA not detected: {exc}", file=sys.stderr)
            return 1
        print(f"CUDA version: {cuda}")
        print(f"PyTorch index URL: {inst.pytorch_index(cuda)}")
        return 0

    argv = [sys.executable, str(MANAGE_BACKEND)]
    if backend:
        argv += ["--backend", backend]
    argv.append(cmd)
    if args.version:
        argv.append(args.version)
    if args.yes:
        argv.append("--yes")
    if args.all:
        argv.append("--all")
    if args.json:
        argv.append("--json")
    if args.cuda_version:
        argv += ["--cuda-version", args.cuda_version]
    return subprocess.run(argv).returncode


if __name__ == "__main__":
    sys.exit(main())
