#!/usr/bin/env python3
"""
local-model-manifest.py — Portable local model manifest toolkit

Commands:
  build      Build a manifest for a model directory
  validate   Validate a manifest file against schema
  inspect    Inspect a model directory and show discovered context
  reconcile  Reconcile installed artifacts with YAML catalogue
  backfill   Write manifests for installed artifacts

All operations are stdlib-only, atomic, and safe for interrupted runs.
"""

import json
import sys
import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# --- Constants ---
MODELS_DIR = Path.home() / ".models"
SCHEMA_PATH = Path(__file__).parent.parent / "docs/schema/local-model-manifest-v1.schema.json"
YAML_CATALOGUE_PATH = Path(__file__).parent.parent / "config/model-catalogue-context-list.yaml"

MANIFEST_FILENAME = ".local-model-manifest.json"
SCHEMA_VERSION = 1

# Context extraction JSON pointers (in priority order)
CONTEXT_POINTERS = [
    "/text_config/max_position_embeddings",
    "/max_position_embeddings",
    "/text_config/model_max_length",
    "/model_max_length",
    "/max_sequence_length",
]

PLAUSIBLE_MIN_CONTEXT = 1024
PLAUSIBLE_MAX_CONTEXT = 10_485_760  # Llama 4 Scout

AUTOCOMPACTION_MIN = 100_000
AUTOCOMPACTION_INCREMENT = 100_000
AUTOCOMPACTION_MAX = 1_000_000


# --- Utility Functions ---

def run_cmd(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run command and return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def read_json(path: Path) -> Dict:
    """Read JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Dict) -> None:
    """Write JSON atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as tf:
        json.dump(data, tf, indent=2, sort_keys=True)
        tf.write('\n')
        temp_path = Path(tf.name)
    temp_path.replace(path)


def extract_context_from_config(config_path: Path) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Extract context from config.json using priority pointers.
    Returns (context_tokens, source_file, json_pointer)."""
    if not config_path.exists():
        return None, None, None
    try:
        config = read_json(config_path)
    except Exception:
        return None, None, None

    for pointer in CONTEXT_POINTERS:
        try:
            # Simple JSON pointer traversal
            parts = pointer.lstrip('/').split('/')
            val = config
            for part in parts:
                val = val[part]
            if isinstance(val, int) and PLAUSIBLE_MIN_CONTEXT <= val <= PLAUSIBLE_MAX_CONTEXT:
                return val, config_path.name, pointer
        except (KeyError, TypeError):
            continue

    # Tokenizer fallback
    tokenizer_path = config_path.parent / "tokenizer_config.json"
    if tokenizer_path.exists():
        try:
            tok_config = read_json(tokenizer_path)
            val = tok_config.get("model_max_length")
            if isinstance(val, int) and PLAUSIBLE_MIN_CONTEXT <= val <= PLAUSIBLE_MAX_CONTEXT:
                return val, tokenizer_path.name, "/model_max_length (tokenizer fallback)"
        except Exception:
            pass

    return None, None, None


def compute_autocompaction(effective_context: Optional[int]) -> Optional[int]:
    """Compute Claude Code autocompaction from effective context."""
    if effective_context is None or effective_context < AUTOCOMPACTION_MIN:
        return None
    floor = (effective_context // AUTOCOMPACTION_INCREMENT) * AUTOCOMPACTION_INCREMENT
    return min(AUTOCOMPACTION_MAX, floor)


def get_git_revision(model_dir: Path) -> Optional[str]:
    """Get git revision if model dir is a git repo."""
    code, stdout, _ = run_cmd(["git", "rev-parse", "HEAD"], cwd=model_dir)
    if code == 0:
        return stdout[:12]
    return None


def get_directory_size(model_dir: Path) -> int:
    """Get total size of model directory in bytes."""
    total = 0
    for f in model_dir.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def load_yaml_catalogue() -> List[Dict[str, Any]]:
    """Load YAML catalogue (simple parser for our structured format)."""
    # Simple YAML parsing for our specific format
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    in_entry = False

    with open(YAML_CATALOGUE_PATH, 'r') as f:
        for line in f:
            line = line.rstrip('\n')
            stripped = line.strip()

            if stripped.startswith('#'):
                continue

            # Match "- folder:" at any indentation level
            if stripped.startswith('- folder:'):
                if current:
                    entries.append(current)
                current = {'folder': stripped.split(':', 1)[1].strip().strip('"')}
                in_entry = True
                continue

            if in_entry and ':' in stripped and not stripped.startswith('- '):
                key, val = stripped.split(':', 1)
                key = key.strip()
                val = val.strip().strip('"')
                # Handle lists
                if val.startswith('[') and val.endswith(']'):
                    val = [v.strip().strip('"') for v in val[1:-1].split(',')]
                elif val.lower() == 'true':
                    val = True
                elif val.lower() == 'false':
                    val = False
                elif val.lower() == 'null':
                    val = None
                elif val.isdigit():
                    val = int(val)
                current[key] = val  # type: ignore

    if current:
        entries.append(current)
    return entries


def find_yaml_entry_for_folder(folder: str, catalogue: List[Dict]) -> Optional[Dict]:
    """Find YAML catalogue entry matching folder name."""
    for entry in catalogue:
        if entry.get('folder') == folder:
            return entry
    return None


# --- Manifest Building ---

def detect_kind_and_launchable(model_dir: Path, config: Dict) -> Tuple[str, bool, bool]:
    """Detect artifact kind, launchable, session_eligible."""
    folder = model_dir.name.lower()

    # Check for drafter (MTP)
    if 'mtp' in folder or 'drafter' in folder:
        return "draft_model", False, False

    # Check for TTS
    if any(tts in folder for tts in ['tts', 'chatterbox', 'kokoro']):
        return "tts_model", True, False

    # Check for depth
    if 'depth' in folder:
        return "depth_estimation_model", False, False

    # Check for GGUF
    if list(model_dir.glob("*.gguf")):
        return "gguf_bundle", True, True

    # Check for processor/vision
    if 'processor' in folder or 'mmproj' in folder:
        return "processor", False, False

    # Check if mlx-lm only (no /v1/messages)
    # Heuristic: if config has no chat template or specific mlx-lm markers
    architectures = config.get("architectures", [])
    if not architectures:
        return "model", True, True

    # Default: normal model
    return "model", True, True


def build_manifest(model_dir: Path) -> Dict:
    """Build manifest for a model directory."""
    folder_name = model_dir.name
    config_path = model_dir / "config.json"

    # Extract context
    native_context, source_file, json_pointer = extract_context_from_config(config_path)
    configured_context = native_context  # Same unless we have override evidence

    # Load config for kind detection
    config = read_json(config_path) if config_path.exists() else {}

    # Detect kind and launchability
    kind, launchable, session_eligible = detect_kind_and_launchable(model_dir, config)

    # Get git revision
    revision = get_git_revision(model_dir)

    # Determine format
    fmt = "mlx"
    if list(model_dir.glob("*.gguf")):
        fmt = "gguf"
    elif (model_dir / "config.json").exists():
        arch = config.get("architectures", [""])[0].lower()
        if "llama" in arch or "mistral" in arch:
            fmt = "mlx"  # Keep simple

    # Build manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "kind": kind,
            "launchable": launchable,
            "session_eligible": session_eligible,
            "directory_name": folder_name,
            "format": fmt,
            "source": {
                "provider": "huggingface",
                "repository": "",  # Would need to infer from folder or git remote
                "revision": revision or ""
            },
            "payload_bytes": get_directory_size(model_dir)
        },
        # Derive autocompaction for Claude Code
        "capabilities": {
            "native_context_tokens": native_context,
            "configured_context_tokens": configured_context,
            "extended_context_tokens": None,  # Would need explicit opt-in evidence
            "claude_autocompact_tokens": compute_autocompaction(native_context) if native_context else None,
            "context_source": {
                "file": source_file or "config.json",
                "json_pointer": json_pointer or "/text_config/max_position_embeddings"
            } if native_context else {},
            "context_candidates": [],
            "context_floor_100k_tokens": compute_autocompaction(native_context) if native_context else None
        },
        "profiles": [],
        "runtime_qualification": {
            "tested_safe_context_tokens": None,
            "runtime": None,
            "quantization": "mlx-4bit" if "4bit" in folder_name.lower() else "unknown",
            "hardware": None,
            "test_date": None
        },
        "acquisition": {
            "status": "complete",
            "producer": {
                "name": "local-agents",
                "version": "0.19.12"
            }
        }
    }

    # Add profiles based on kind
    if launchable and session_eligible and kind == "model":
        # Check for thinking capability (heuristic)
        thinking_supported = "qwen3" in folder_name.lower() or "nemotron" in folder_name.lower()
        parser = "qwen3_coder_xml" if "qwen" in folder_name.lower() else "hermes"

        manifest["profiles"].append({
            "alias": folder_name.lower().replace("-", "-").replace(".", ""),
            "backend": "rapid",
            "thinking": False,
            "tool_parser": parser,
            "reasoning_parser": None,
            "qualification": "untested"
        })
        if thinking_supported:
            manifest["profiles"].append({
                "alias": folder_name.lower().replace("-", "-").replace(".", "") + "-thinking",
                "backend": "rapid",
                "thinking": True,
                "tool_parser": parser,
                "reasoning_parser": "qwen3",
                "qualification": "untested"
            })

    # Drafter target relationship
    if kind == "draft_model":
        # Try to find target - strip MTP suffixes to find base model
        base_candidates = [
            folder_name.replace("-MTP-", "-").replace("-mtp-", "-"),  # Qwen3.8-27B-MTP-4bit -> Qwen3.8-27B-4bit
            folder_name.replace("-MTP", "").replace("-mtp", ""),       # Qwen3.8-27B-MTP-4bit -> Qwen3.8-27B-4bit
            folder_name.split("-MTP")[0],                              # fallback
            folder_name.split("-mtp")[0],                              # fallback
        ]
        for target_name in base_candidates:
            target_dir = MODELS_DIR / target_name
            if target_dir.exists() and target_dir != model_dir:
                manifest["artifact"]["target_directory_name"] = target_name
                break

    return manifest


def validate_manifest(manifest: Dict) -> List[str]:
    """Validate manifest (basic validation without jsonschema dep)."""
    errors = []

    # Schema version
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    # Artifact
    artifact = manifest.get("artifact", {})
    if not artifact.get("directory_name"):
        errors.append("artifact.directory_name required")
    if artifact.get("directory_name", "").startswith("/"):
        errors.append("artifact.directory_name must not be absolute path")

    # NOTE: a drafter or a TTS asset that is NOT session-eligible is a VALID manifest (docs/
    # model-manifest-v1.md §3). Eligibility is enforced where a session is started (the launcher
    # gate, la_validate_manifest_config), never by `validate`, which checks truthfulness only.
    # Truthfulness rules from the spec (§3, §4, §6), mirrored by tests/test_model_manifest_fixtures.py:
    kind = artifact.get("kind")
    if kind == "draft_model":
        if artifact.get("launchable") is not False:
            errors.append("draft_model must not be launchable")
        if not artifact.get("target_directory_name"):
            errors.append("draft_model needs target_directory_name")
    if kind in ("draft_model", "tts_model", "depth_estimation_model", "adapter", "processor") \
            and artifact.get("session_eligible") is not False:
        errors.append(f"{kind} must not be session_eligible")
    _caps = manifest.get("capabilities") or {}
    _cands = _caps.get("context_candidates") or []
    if len({c.get("value") for c in _cands if isinstance(c.get("value"), int)}) > 1 \
            and not _caps.get("context_conflict_resolution"):
        errors.append("context candidates disagree and no context_conflict_resolution is recorded")
    if (manifest.get("acquisition") or {}).get("status") == "complete" and artifact.get("payload_bytes") == 0:
        errors.append("acquisition.status=complete with payload_bytes=0 (metadata-only directory)")

    # Context validation
    caps = manifest.get("capabilities", {})
    native = caps.get("native_context_tokens")
    configured = caps.get("configured_context_tokens")
    extended = caps.get("extended_context_tokens")
    floor = caps.get("context_floor_100k_tokens")
    claude_autocompact = caps.get("claude_autocompact_tokens")

    # Determine effective context (minimum of all applicable limits)
    limits = []
    for key in ("native_context_tokens", "configured_context_tokens", "extended_context_tokens"):
        val = caps.get(key)
        if isinstance(val, int):
            limits.append(val)

    # Server context from runtime qualification
    rq = manifest.get("runtime_qualification", {})
    server_ctx = rq.get("server_context_tokens")
    if isinstance(server_ctx, int):
        limits.append(server_ctx)

    # Tested safe context
    tested = rq.get("tested_safe_context_tokens")
    if isinstance(tested, int):
        limits.append(tested)

    effective = min(limits) if limits else None

    if native is not None:
        if not (PLAUSIBLE_MIN_CONTEXT <= native <= PLAUSIBLE_MAX_CONTEXT):
            errors.append(f"native_context_tokens {native} out of plausible range")
        if configured is not None and configured != native:
            # Allow conflict but track
            pass

    if floor is not None:
        expected = compute_autocompaction(native) if native else None
        if expected is not None and floor != expected:
            errors.append(f"context_floor_100k_tokens {floor} != derived {expected}")

    # Check: claude_autocompact_tokens must not exceed effective context
    if claude_autocompact is not None and effective is not None:
        if claude_autocompact > effective:
            errors.append(f"autocompact_exceeds_context: claude_autocompact_tokens ({claude_autocompact}) > effective context ({effective})")

    # Check: autocompaction must be valid 100K increment
    if claude_autocompact is not None:
        if claude_autocompact % AUTOCOMPACTION_INCREMENT != 0 or claude_autocompact < AUTOCOMPACTION_MIN or claude_autocompact > AUTOCOMPACTION_MAX:
            errors.append(f"claude_autocompact_tokens ({claude_autocompact}) must be 100K increment between 100K and 1M")

    # Profile validation
    for i, profile in enumerate(manifest.get("profiles", [])):
        if not profile.get("alias"):
            errors.append(f"profile[{i}].alias required")
        if profile.get("thinking") and not profile.get("reasoning_parser"):
            errors.append(f"profile[{i}].thinking=true requires reasoning_parser")

    return errors


# --- Commands ---

def cmd_build(args):
    """Build manifest for a model directory."""
    model_dir = Path(args.dir).resolve()
    if not model_dir.exists() or not model_dir.is_dir():
        print(f"Error: {model_dir} not found", file=sys.stderr)
        return 1

    manifest = build_manifest(model_dir)

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = model_dir / MANIFEST_FILENAME

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
    else:
        write_json_atomic(out_path, manifest)
        print(f"Manifest written to {out_path}")

    # Validate
    errors = validate_manifest(manifest)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    return 0


def cmd_validate(args):
    """Validate a manifest file."""
    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found", file=sys.stderr)
        return 1

    manifest = read_json(manifest_path)
    errors = validate_manifest(manifest)

    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    else:
        print("Validation PASSED")
        return 0


def cmd_inspect(args):
    """Inspect a model directory and show discovered context."""
    model_dir = Path(args.dir).resolve()
    if not model_dir.exists() or not model_dir.is_dir():
        print(f"Error: {model_dir} not found", file=sys.stderr)
        return 1

    config_path = model_dir / "config.json"
    if config_path.exists():
        native_context, source_file, json_pointer = extract_context_from_config(config_path)
        autocompaction = compute_autocompaction(native_context) if native_context else None
        config = read_json(config_path)
        kind, launchable, session_eligible = detect_kind_and_launchable(model_dir, config)
    else:
        # No config.json - detect from folder name
        native_context, source_file, json_pointer = None, None, None
        autocompaction = None
        config = {}
        kind, launchable, session_eligible = detect_kind_and_launchable(model_dir, config)

    revision = get_git_revision(model_dir)
    size = get_directory_size(model_dir)

    print(f"Directory: {model_dir.name}")
    print(f"  Kind: {kind}")
    print(f"  Launchable: {launchable}")
    print(f"  Session-eligible: {session_eligible}")
    print(f"  Format: {'gguf' if list(model_dir.glob('*.gguf')) else 'mlx'}")
    print(f"  Size: {size / (1024**3):.2f} GB")
    print(f"  Git revision: {revision or 'unknown'}")
    print(f"  Native context: {native_context}")
    print(f"  Context source: {source_file} {json_pointer}")
    print(f"  Autocompaction (derived): {autocompaction}")

    # Check existing manifest
    manifest_path = model_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        print(f"  Existing manifest: {manifest_path}")
        existing = read_json(manifest_path)
        print(f"    Schema version: {existing.get('schema_version')}")
        print(f"    Artifact kind: {existing.get('artifact', {}).get('kind')}")
        print(f"    Profiles: {len(existing.get('profiles', []))}")

    return 0


def cmd_reconcile(args):
    """Reconcile installed artifacts with YAML catalogue."""
    catalogue = load_yaml_catalogue()
    print(f"Loaded {len(catalogue)} catalogue entries")

    installed_dirs = [d for d in MODELS_DIR.iterdir() if d.is_dir() and not d.name.startswith('.')]
    print(f"Found {len(installed_dirs)} installed model directories")

    # Build index
    catalogue_by_folder = {e['folder']: e for e in catalogue}

    results = {
        "matched": [],
        "unmatched_installed": [],
        "unmatched_catalogue": [],
        "conflicts": []
    }

    for model_dir in installed_dirs:
        folder = model_dir.name
        yaml_entry = catalogue_by_folder.get(folder)

        manifest_path = model_dir / MANIFEST_FILENAME
        has_manifest = manifest_path.exists()

        if yaml_entry:
            results["matched"].append({
                "folder": folder,
                "has_manifest": has_manifest,
                "yaml_type": yaml_entry.get("entry_type"),
                "yaml_context": yaml_entry.get("context_window_tokens"),
                "yaml_autocompact": yaml_entry.get("autocompaction_tokens")
            })
        else:
            results["unmatched_installed"].append({
                "folder": folder,
                "has_manifest": has_manifest
            })

    # Catalogue entries not on disk
    installed_folders = {d.name for d in installed_dirs}
    for entry in catalogue:
        if entry['folder'] not in installed_folders:
            results["unmatched_catalogue"].append({
                "folder": entry['folder'],
                "type": entry.get("entry_type"),
                "action": entry.get("catalogue_action")
            })

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\nMatched: {len(results['matched'])}")
        for m in results['matched']:
            manifest_flag = "✓" if m['has_manifest'] else "✗"
            print(f"  {manifest_flag} {m['folder']} ({m['yaml_type']}) ctx={m['yaml_context']} ac={m['yaml_autocompact']}")

        print(f"\nInstalled but not in catalogue: {len(results['unmatched_installed'])}")
        for m in results['unmatched_installed']:
            manifest_flag = "✓" if m['has_manifest'] else "✗"
            print(f"  {manifest_flag} {m['folder']}")

        print(f"\nIn catalogue but not installed: {len(results['unmatched_catalogue'])}")
        for m in results['unmatched_catalogue']:
            print(f"  - {m['folder']} ({m['type']}) action={m['action']}")

    return 0


def cmd_backfill(args):
    """Write manifests for installed artifacts."""
    if args.select:
        targets = [args.select]
    elif args.all:
        targets = [d.name for d in MODELS_DIR.iterdir() if d.is_dir() and not d.name.startswith('.')]
    else:
        print("Error: --select <folder> or --all required", file=sys.stderr)
        return 1

    written = 0
    skipped = 0
    errors = 0

    for folder in targets:
        model_dir = MODELS_DIR / folder
        if not model_dir.exists():
            print(f"  {folder}: NOT FOUND")
            errors += 1
            continue

        # Check if already has valid manifest
        manifest_path = model_dir / MANIFEST_FILENAME
        if manifest_path.exists() and not args.force:
            try:
                existing = read_json(manifest_path)
                if validate_manifest(existing) == []:
                    print(f"  {folder}: SKIP (valid manifest exists)")
                    skipped += 1
                    continue
            except Exception:
                pass

        # Build manifest
        if args.dry_run:
            manifest = build_manifest(model_dir)
            print(f"  {folder}: WOULD WRITE manifest")
            print(json.dumps(manifest, indent=2)[:500])
        else:
            manifest = build_manifest(model_dir)
            write_json_atomic(manifest_path, manifest)
            val_errors = validate_manifest(manifest)
            if val_errors:
                print(f"  {folder}: ERROR - {val_errors}")
                errors += 1
            else:
                print(f"  {folder}: WRITTEN")
                written += 1

    if not args.dry_run:
        print(f"\nSummary: {written} written, {skipped} skipped, {errors} errors")

    return 0 if errors == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Portable local model manifest toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Build
    p_build = subparsers.add_parser("build", help="Build manifest for a model directory")
    p_build.add_argument("dir", help="Model directory path")
    p_build.add_argument("--output", "-o", help="Output manifest path (default: dir/.local-model-manifest.json)")
    p_build.add_argument("--dry-run", action="store_true", help="Print to stdout instead of writing")
    p_build.set_defaults(func=cmd_build)

    # Validate
    p_validate = subparsers.add_parser("validate", help="Validate a manifest file")
    p_validate.add_argument("manifest", help="Manifest file path")
    p_validate.set_defaults(func=cmd_validate)

    # Inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect model directory context")
    p_inspect.add_argument("dir", help="Model directory path")
    p_inspect.set_defaults(func=cmd_inspect)

    # Reconcile
    p_reconcile = subparsers.add_parser("reconcile", help="Reconcile installed with YAML catalogue")
    p_reconcile.add_argument("--json", action="store_true", help="Output JSON")
    p_reconcile.set_defaults(func=cmd_reconcile)

    # Backfill
    p_backfill = subparsers.add_parser("backfill", help="Write manifests for installed artifacts")
    p_backfill.add_argument("--select", help="Single folder to backfill")
    p_backfill.add_argument("--all", action="store_true", help="Backfill all installed models")
    p_backfill.add_argument("--force", action="store_true", help="Overwrite existing valid manifests")
    p_backfill.add_argument("--dry-run", action="store_true", help="Show what would be done")
    p_backfill.set_defaults(func=cmd_backfill)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())