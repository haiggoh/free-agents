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
  drafter      a speculative-decoding helper (model_type *_mtp / *_assistant) — loaded by a target
               model's spec config, never served alone
  unsupported  neither engine knows the architecture, OR the architecture is fine but the WEIGHTS
               are in a serialization no MLX engine reads (a custom runtime's bundle)
  unknown      no readable config.json / model_type

How support is decided: each engine's OWN interpreter (taken from the shebang of its binary) lists
the module names under its mlx_lm/models (and, for oMLX, mlx_vlm/models) plus mlx_lm's
MODEL_REMAPPING keys. Nothing is imported from mlx, and no list is hard-coded here, so an engine
upgrade that adds an architecture changes the verdict with no edit to this file. When an engine
cannot be inspected, its support is UNKNOWN: an uninspectable rapid never causes an omlx pin.

★ 2026-10-25 — architecture support is necessary, NOT sufficient. A second, independent question
is whether any installed engine can read the weights' SERIALIZATION, and answering only the first
produced a confident false positive measured on this machine:
~/.models/deepseek-v4-flash-0731-coder declares model_type=deepseek_v4 (oMLX's mlx_vlm has it) and
weight_format="mxtq" (2-bit MoEspresso TurboQuant bundles) — so the verdict was `omlx`, the launch
loaded 53 GB, and the load then died in mlx's strict load_weights: "Received 46 parameters not in
model: embed.biases, embed.scales, head.scales, model.layers.N.ffn.experts.tq_bundle". The session
saw only a 409 "unavailable after a previous load failure", which names no cause and no fix. So the
weights' own format is now read too, from two places that cannot both be missing: config.json's
`weight_format`, and the safetensors header keys (a `*.tq_bundle` bundle is a custom runtime's, not
an MLX quantization — plain MLX quantized layers legitimately write *.scales / *.biases, so only
the bundle suffix is a signal). A foreign format yields `unsupported` with the format named, plus
the runtime the model catalog says that artifact needs (read from the catalog, never duplicated
here). Extension point: an engine that learns to read a foreign format adds it to
LA_MLX_WEIGHT_FORMATS.

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
import struct
import subprocess
import sys
from pathlib import Path

# Weight serializations an MLX engine can read: absent/empty means plain safetensors (the normal
# case, optionally with MLX's own `quantization` block, which needs no marker of its own). Any
# OTHER value names a foreign runtime's bundle. Override for an engine that adds one.
MLX_WEIGHT_FORMATS = frozenset(
    f.strip() for f in os.environ.get("LA_MLX_WEIGHT_FORMATS", "").split(",") if f.strip())

# A header-key suffix only a custom runtime writes. Deliberately NOT "*.scales" or "*.biases":
# those are ordinary MLX quantization keys and matching them would gate out every 4-bit model.
BUNDLE_SUFFIX = ".tq_bundle"
# A header larger than this is not a model we want to parse for a verdict (or a sane one).
_MAX_HEADER_BYTES = 8 << 20

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


def _first_shard_header(folder: Path) -> dict | None:
    """The safetensors header of the folder's first shard, or None.

    Only 8 bytes of framing plus the JSON header are read, so this is cheap even for a 53 GB
    model, and it never touches the tensor data. Any surprise — a huge, absent or malformed
    header — returns None: an unreadable weight file is not evidence about the format.
    """
    try:
        shards = sorted(p for p in folder.iterdir() if p.suffix == ".safetensors")
    except OSError:
        return None
    for shard in shards[:1]:
        try:
            with shard.open("rb") as fh:
                (n,) = struct.unpack("<Q", fh.read(8))
                if n <= 0 or n > _MAX_HEADER_BYTES:
                    return None
                header = json.loads(fh.read(n).decode("utf-8", "replace"))
        except (OSError, ValueError, struct.error):
            continue
        if isinstance(header, dict):
            return header
    return None


def _declared_weight_format(folder: Path) -> str | None:
    """config.json's `weight_format` when it names something no MLX engine reads, else None."""
    try:
        config = json.loads((folder / "config.json").read_text())
    except (OSError, ValueError):
        return None
    fmt = config.get("weight_format") if isinstance(config, dict) else None
    if isinstance(fmt, str) and fmt and fmt not in MLX_WEIGHT_FORMATS:
        return fmt
    return None


def _foreign_weight_format(folder: Path) -> str | None:
    """A weight serialization no installed MLX backend reads, from the weights' own evidence.

    Two independent sources, because either alone can lie: a config whose `weight_format` was
    stripped, or a shard carrying bundles the config never mentions. Returns a description for
    the error message, or None when the artifact is ordinary MLX weights.
    """
    fmt = _declared_weight_format(folder)
    if fmt:
        return f"config.json declares weight_format={fmt}"
    header = _first_shard_header(folder)
    if header:
        bundled = sorted(k for k in header if isinstance(k, str) and k.endswith(BUNDLE_SUFFIX))
        if bundled:
            return f"{len(bundled)} weight key(s) are {BUNDLE_SUFFIX.lstrip('.')} bundles"
    return None


def _catalog_runtime(folder: Path) -> str | None:
    """The runtime the model catalog says this artifact needs, read from the catalog itself.

    Derived, never duplicated: the catalog's `runtime` column already carries it (this machine's
    row for the deepseek coder build has said `MoEspresso>=2.1` since 2026-09-01). Silent when the
    catalog is absent — the verdict must not depend on a file this tool does not own.
    """
    catalog = Path(__file__).resolve().parents[1] / "config" / "model-catalog.local.psv"
    try:
        for line in catalog.read_text().splitlines():
            fields = line.split("|")
            if len(fields) >= 10 and fields[4] == folder.name and fields[9].strip():
                return fields[9].strip()
    except OSError:
        return None
    return None


def _capable_moespresso() -> tuple[str, str] | None:
    """(version, path) of a MoEspresso that can load a MoEspresso package, or None.

    Capable means the **3.x line**. Measured 2026-10-25: 4.0.0 refuses a MoEspresso-packaged
    DeepSeek V4 model with `Error: unsupported required package feature` (the manifest requires
    `calibration` + `deepseek_v4_per_layer_experts`, and 4.0.0 is a Qwen engine). So the version
    is asked of the binary, never assumed from which path it was found at.

    Resolution order mirrors config-lib.sh's la_discover_moespresso_bin: an explicit pin, then the
    @3 kegs newest-first, then Homebrew's versioned symlink, then the unversioned PATH entry. The
    pin chooses WHICH binary to ask, never whether it is capable — a pin to a 4.x binary is
    itself the mistake this check exists to catch.

    An explicit LA_MOESPRESSO_BIN set to the EMPTY STRING means "this machine has no MoEspresso"
    and skips discovery entirely — an override that cannot be expressed cannot be tested, and
    without it the "nothing can read this format" case is only observable on a machine that
    happens to lack the runtime. Only DISCOVERY is memoised (`csl` calls this per folder); a pin
    is re-read every time so one caller's pin cannot become another caller's answer.
    """
    pinned = os.environ.get("LA_MOESPRESSO_BIN")
    if pinned is not None:
        if not pinned.strip():
            return None
        return _scan_moespresso([pinned])

    if "moe" in _MOESPRESSO_CACHE:
        return _MOESPRESSO_CACHE["moe"]
    kegs: list[str] = []
    for root in (Path("/opt/homebrew/Cellar/moespresso@3"),
                 Path("/usr/local/Cellar/moespresso@3")):
        if root.is_dir():
            kegs += [str(p) for p in root.glob("*/bin/moespresso")]
    # Newest keg first, by version rather than mtime (reinstalling an older version would
    # otherwise look newest).
    kegs.sort(key=lambda p: [int(x) if x.isdigit() else x
                             for x in Path(p).parent.parent.name.replace("-", ".").split(".")],
              reverse=True)
    found = _scan_moespresso(kegs + ["/opt/homebrew/bin/moespresso@3",
                                     "/usr/local/bin/moespresso@3",
                                     shutil.which("moespresso") or "",
                                     "/opt/homebrew/bin/moespresso", "/usr/local/bin/moespresso"])
    _MOESPRESSO_CACHE["moe"] = found
    return found


def _scan_moespresso(paths: list[str]) -> tuple[str, str] | None:
    """The first path that reports a 3.x MoEspresso, as (version, path)."""
    for path in paths:
        if not path or not os.access(path, os.X_OK):
            continue
        try:
            r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (r.stdout or "") + (r.stderr or "")
        for token in text.replace(",", " ").split():
            head = token.lstrip("vV")
            if head[:1].isdigit() and "." in head:
                if head.split(".", 1)[0] == "3":
                    return head, path
                break
    return None


_MOESPRESSO_CACHE: dict = {}


def detect(folder: Path, rapid: dict | None, omlx: dict | None) -> tuple[str, str]:
    if folder.is_dir() and any(p.suffix.lower() == ".gguf" for p in folder.iterdir()):
        return "llama_cpp", "GGUF artifact: MLX backends cannot load GGUF"
    try:
        model_type = json.loads((folder / "config.json").read_text()).get("model_type")
    except (OSError, ValueError):
        model_type = None
    if not isinstance(model_type, str) or not model_type:
        return "unknown", "no readable config.json model_type"
    if model_type.lower().endswith(("_mtp", "_assistant")):
        # oMLX's helper rule (omlx/model_discovery.py): a speculative-decoding drafter, loaded BY
        # a target model's spec config — never served on its own.
        return "drafter", f"model_type={model_type} is a speculative-decoding drafter, not a model"
    # Weight format, checked before any engine verdict: an engine that could build the
    # architecture still cannot read another runtime's bundle, and "omlx" would be a confident
    # lie that spends a 45-second load to disprove itself.
    foreign = _foreign_weight_format(folder)
    if foreign:
        moe = _capable_moespresso()
        if moe is not None:
            version, _path = moe
            return ("moespresso",
                    f"model_type={model_type} is not MLX-readable ({foreign}); MoEspresso "
                    f"{version} serves this format")
        runtime = f" (catalog says: {_catalog_runtime(folder)})" if _catalog_runtime(folder) else ""
        return ("unsupported",
                f"model_type={model_type} but the weights are not MLX-readable: {foreign}"
                f"{runtime} — no installed backend can load it")
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
    ap.add_argument("--gate", action="store_true",
                    help="print ONLY artifacts no MLX backend can load (one reason per folder, "
                         "exit 3); silent and exit 0 when every folder is launchable. Skips the "
                         "engine probes entirely — launchers call this before starting a server")
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
    if args.gate:
        # The launch guard: name what cannot be loaded and exit non-zero, so a caller refuses
        # BEFORE it starts a server that would burn a full weight load to fail late and cryptic.
        # Engine probes are deliberately skipped — the format verdict does not need them. An
        # artifact a CAPABLE MoEspresso can serve is not blocked: it is launched by that backend.
        blocked = 0
        moespresso = _capable_moespresso() if any(_foreign_weight_format(Path(f)) for f in args.folders) else None
        for f in args.folders:
            foreign = _foreign_weight_format(Path(f))
            if not foreign:
                continue
            if moespresso is not None:
                continue
            blocked += 1
            runtime = _catalog_runtime(Path(f))
            suffix = f" (catalog says: {runtime})" if runtime else ""
            print(f"{Path(f).name}: weights are not MLX-readable — {foreign}{suffix}")
        return 3 if blocked else 0
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['folder']}\t{r['backend']}\t{r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
