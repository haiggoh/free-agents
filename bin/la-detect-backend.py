#!/usr/bin/env python3
"""la-detect-backend.py — pick the backend a local model folder needs, from evidence on disk.

Usage:
  la-detect-backend.py [--json] <model-folder> [<model-folder> ...]

Prints one line per folder: <folder><TAB><backend><TAB><reason>   (--json: a list of objects).

Backends (the `serve` value a registration should declare):
  llama_cpp    the folder holds a GGUF artifact — no MLX backend can load it
  mlx          the PINNED Rapid-MLX can build config.json's model_type (generic value: resolves to
               the machine default, LA_DEFAULT_MLX_BACKEND, which is rapid)
  omlx         Rapid-MLX cannot build it but oMLX's bundled mlx_lm or mlx_vlm can — a per-model
               hard dependency, not a preference (measured 2026-10-15: rapid 0.16.1 rejects
               `laguna`, oMLX serves it)
  unsupported  neither engine knows the architecture
  unknown      no readable config.json / model_type

How support is decided: each engine's OWN interpreter (taken from the shebang of its binary) lists
the module names under its mlx_lm/models (and, for oMLX, mlx_vlm/models) plus mlx_lm's
MODEL_REMAPPING keys. Nothing is imported from mlx, and no list is hard-coded here, so an engine
upgrade that adds an architecture changes the verdict with no edit to this file. When an engine
cannot be inspected, its support is UNKNOWN: an uninspectable rapid never causes an omlx pin.

Environment:
  LA_RAPID_BIN         Rapid-MLX executable (default: discovered like config-lib's la_discover_rapid_bin)
  LA_OMLX_BIN          oMLX executable (default: /opt/homebrew/bin/omlx, then PATH)
  LA_DETECT_RAPID_PY   interpreter to inspect instead of the rapid binary's shebang (tests)
  LA_DETECT_OMLX_PY    interpreter to inspect instead of the oMLX binary's shebang (tests)

Exit: 0 when every folder got a verdict (including unsupported/unknown), 2 on usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Runs INSIDE an engine's interpreter. Stdlib only: locates packages with find_spec (no import of
# mlx, which would load Metal) and reads MODEL_REMAPPING with ast instead of importing mlx_lm.
_PROBE = r"""
import ast, importlib.util, json, os, sys
out = {}
for pkg in ("mlx_lm", "mlx_vlm"):
    spec = importlib.util.find_spec(pkg)
    if spec is None or not spec.submodule_search_locations:
        continue
    root = list(spec.submodule_search_locations)[0]
    mdir = os.path.join(root, "models")
    if not os.path.isdir(mdir):
        continue
    names = set()
    for e in os.listdir(mdir):
        p = os.path.join(mdir, e)
        if e.endswith(".py") and e != "__init__.py":
            names.add(e[:-3])
        elif os.path.isdir(p) and os.path.exists(os.path.join(p, "__init__.py")):
            names.add(e)
    utils = os.path.join(root, "utils.py")
    if os.path.exists(utils):
        tree = ast.parse(open(utils, encoding="utf-8").read())
        for node in tree.body:
            if (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "MODEL_REMAPPING"
                                                     for t in node.targets)):
                try:
                    remap = ast.literal_eval(node.value)
                except ValueError:
                    remap = {}
                names |= {k for k, v in remap.items() if v in names}
    out[pkg] = sorted(names)
print(json.dumps(out))
"""


def _shebang_interpreter(binary: str | None) -> str | None:
    """The interpreter a console-script binary runs under (its shebang), or None."""
    if not binary:
        return None
    path = Path(binary).expanduser()
    try:
        path = path.resolve(strict=True)
        with path.open("rb") as fh:
            first = fh.readline(512).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    if not parts:
        return None
    # "#!/usr/bin/env python3" style: take the interpreter after env.
    if Path(parts[0]).name == "env" and len(parts) > 1:
        return shutil.which(parts[1])
    return parts[0]


def _discover_rapid_bin() -> str | None:
    """Same order as config-lib's la_discover_rapid_bin: brew, PATH, newest ~/.venvs/rapid-mlx-*."""
    for c in ("/opt/homebrew/bin/rapid-mlx", "/usr/local/bin/rapid-mlx"):
        if os.access(c, os.X_OK):
            return c
    c = shutil.which("rapid-mlx")
    if c:
        return c
    venvs = sorted(Path.home().glob(".venvs/rapid-mlx-*"),
                   key=lambda p: [int(x) if x.isdigit() else x
                                  for x in p.name.removeprefix("rapid-mlx-").replace("-", ".").split(".")],
                   reverse=True)
    for v in venvs:
        b = v / "bin" / "rapid-mlx"
        if os.access(b, os.X_OK):
            return str(b)
    return None


def _engine_registry(interpreter: str | None) -> dict[str, set[str]] | None:
    """{"mlx_lm": {...}, "mlx_vlm": {...}} from inside that interpreter, or None if uninspectable."""
    if not interpreter or not os.access(interpreter, os.X_OK):
        return None
    try:
        r = subprocess.run([interpreter, "-I", "-c", _PROBE], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return {k: set(v) for k, v in data.items()}


def _rapid_interpreter() -> str | None:
    return os.environ.get("LA_DETECT_RAPID_PY") or _shebang_interpreter(
        os.environ.get("LA_RAPID_BIN") or _discover_rapid_bin())


def _omlx_interpreter() -> str | None:
    return os.environ.get("LA_DETECT_OMLX_PY") or _shebang_interpreter(
        os.environ.get("LA_OMLX_BIN") or
        ("/opt/homebrew/bin/omlx" if os.access("/opt/homebrew/bin/omlx", os.X_OK) else shutil.which("omlx")))


def detect(folder: Path, rapid: dict | None, omlx: dict | None) -> tuple[str, str]:
    if folder.is_dir() and any(p.suffix.lower() == ".gguf" for p in folder.iterdir()):
        return "llama_cpp", "GGUF artifact: MLX backends cannot load GGUF"
    try:
        model_type = json.loads((folder / "config.json").read_text()).get("model_type")
    except (OSError, ValueError):
        model_type = None
    if not isinstance(model_type, str) or not model_type:
        return "unknown", "no readable config.json model_type"
    if rapid is None:
        # Absence of evidence about rapid is not evidence against it: keep the default.
        return "mlx", f"model_type={model_type}; rapid-mlx could not be inspected, keeping the default"
    if model_type in rapid.get("mlx_lm", set()):
        return "mlx", f"model_type={model_type} is supported by rapid-mlx"
    if omlx is not None and (model_type in omlx.get("mlx_lm", set())
                             or model_type in omlx.get("mlx_vlm", set())):
        where = "mlx_lm" if model_type in omlx.get("mlx_lm", set()) else "mlx_vlm"
        return "omlx", f"model_type={model_type} is not in rapid-mlx's registry; oMLX's {where} has it"
    if omlx is None:
        return "unsupported", f"model_type={model_type} is not in rapid-mlx's registry; oMLX not inspectable"
    return "unsupported", f"model_type={model_type} is in neither rapid-mlx's nor oMLX's registry"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="la-detect-backend.py",
        description="Pick the backend a local model folder needs (llama_cpp / mlx / omlx).",
        epilog=(__doc__ or "").split("\n\n", 1)[-1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print a JSON list instead of TSV")
    ap.add_argument("folders", nargs="*", help="model folders (absolute, or relative to cwd)")
    args = ap.parse_args(argv)
    if not args.folders:
        ap.print_usage(sys.stderr)
        print("la-detect-backend.py: at least one model folder is required", file=sys.stderr)
        return 2
    rapid = _engine_registry(_rapid_interpreter())
    omlx = _engine_registry(_omlx_interpreter())
    results = []
    for f in args.folders:
        backend, reason = detect(Path(f), rapid, omlx)
        results.append({"folder": f, "backend": backend, "reason": reason})
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['folder']}\t{r['backend']}\t{r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
