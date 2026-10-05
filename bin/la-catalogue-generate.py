#!/usr/bin/env python3
"""
la-catalogue-generate.py — Generate derived PSV catalogue from YAML source.

This is the hybrid approach (Plan Phase 5C): YAML is the authoritative source,
this script generates a machine-readable PSV that the bash config system loads.

Commands:
  generate    Generate derived PSV from YAML catalogue
  validate    Validate YAML catalogue structure
  merge       Show merged view of YAML + config.local.sh registrations
"""

import sys
import argparse
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any

REPO_ROOT = Path(__file__).parent.parent
YAML_PATH = REPO_ROOT / "config" / "model-catalogue-context-list.yaml"
DERIVED_PSV_PATH = REPO_ROOT / "config" / "model-catalogue-derived.psv"
CONFIG_LOCAL_PATH = REPO_ROOT / "config" / "config.local.sh"

# Required YAML fields for session models
REQUIRED_SESSION_FIELDS = {"folder", "entry_type", "session_model", "context_window_tokens", "autocompaction_tokens"}
VALID_ENTRY_TYPES = {"standalone_model", "override_alias", "speculative_drafter", "tts_model", "depth_estimation_model", "filesystem_metadata"}
VALID_CATALOGUE_ACTIONS = {"exclude", "exclude_from_claude_sessions", "alias", "attach_to_target"}


def load_yaml_catalogue() -> List[Dict[str, Any]]:
    """Load and parse the YAML catalogue."""
    with open(YAML_PATH, 'r') as f:
        data = yaml.safe_load(f)
    return data.get('entries', [])


def validate_yaml(entries: List[Dict]) -> List[str]:
    """Validate YAML catalogue entries. Returns list of error messages."""
    errors = []
    seen_folders = set()

    for i, entry in enumerate(entries):
        folder = entry.get('folder')
        if not folder:
            errors.append(f"Entry {i}: missing 'folder'")
            continue

        if folder in seen_folders:
            errors.append(f"Duplicate folder: {folder}")
        seen_folders.add(folder)

        entry_type = entry.get('entry_type')
        if entry_type not in VALID_ENTRY_TYPES:
            errors.append(f"{folder}: invalid entry_type '{entry_type}'")
            continue

        # Session models must have required fields
        if entry.get('session_model') is True:
            for field in REQUIRED_SESSION_FIELDS:
                if field not in entry:
                    errors.append(f"{folder}: missing required field '{field}'")

            ctx = entry.get('context_window_tokens')
            if ctx is not None and (not isinstance(ctx, int) or ctx <= 0):
                errors.append(f"{folder}: context_window_tokens must be positive integer")

            ac = entry.get('autocompaction_tokens')
            if ac is not None:
                if not isinstance(ac, int) or ac < 100000 or ac > 1000000 or ac % 100000 != 0:
                    errors.append(f"{folder}: autocompaction_tokens must be 100K increment 100K-1M")
                if ctx is not None and ac > ctx:
                    errors.append(f"{folder}: autocompaction_tokens ({ac}) > context_window_tokens ({ctx})")

            # Extended context validation
            ext_ctx = entry.get('extended_context_tokens')
            ext_ac = entry.get('extended_autocompaction_tokens')
            if ext_ctx is not None:
                if not isinstance(ext_ctx, int) or ext_ctx <= 0:
                    errors.append(f"{folder}: extended_context_tokens must be positive integer")
                if ext_ac is not None:
                    if not isinstance(ext_ac, int) or ext_ac < 100000 or ext_ac > 1000000 or ext_ac % 100000 != 0:
                        errors.append(f"{folder}: extended_autocompaction_tokens must be 100K increment 100K-1M")
                    if ext_ac > ext_ctx:
                        errors.append(f"{folder}: extended_autocompaction_tokens ({ext_ac}) > extended_context_tokens ({ext_ctx})")

        # Aliases must resolve
        if entry_type == 'override_alias':
            target = entry.get('alias_target')
            if not target:
                errors.append(f"{folder}: override_alias missing alias_target")
            elif target not in seen_folders:
                # Target might come later, just warn
                pass

        # Drafters must have target
        if entry_type == 'speculative_drafter':
            target = entry.get('target_model')
            if not target:
                errors.append(f"{folder}: speculative_drafter missing target_model")

        # Excluded entries should have null autocompaction
        if entry.get('catalogue_action') in VALID_CATALOGUE_ACTIONS:
            if entry.get('autocompaction_tokens') is not None:
                errors.append(f"{folder}: excluded entry should have null autocompaction_tokens")

    return errors


def compute_autocompaction(effective_context: Optional[int]) -> Optional[int]:
    """Compute Claude Code autocompaction from effective context (matches plan formula)."""
    if effective_context is None or effective_context < 100000:
        return None
    floor = (effective_context // 100000) * 100000
    return min(1000000, floor)


def generate_psv(entries: List[Dict]) -> List[str]:
    """Generate PSV lines from YAML entries.

    PSV Format (12 fields):
    folder|entry_type|session_model|catalogue_action|context_window_tokens|autocompaction_tokens|
    extended_context_tokens|extended_autocompaction_tokens|alias_target|target_model|
    model_family|notes
    """
    lines = [
        "# model-catalogue-derived.psv — GENERATED from model-catalogue-context-list.yaml",
        "# DO NOT EDIT DIRECTLY — run `bin/la-catalogue-generate.py generate` to regenerate",
        "#",
        "# Format: folder|entry_type|session_model|catalogue_action|context_window_tokens|autocompaction_tokens|extended_context_tokens|extended_autocompaction_tokens|alias_target|target_model|model_family|notes",
        ""
    ]

    for entry in entries:
        folder = entry.get('folder', '')
        entry_type = entry.get('entry_type', '')
        session_model = str(entry.get('session_model', '')).lower()
        catalogue_action = entry.get('catalogue_action', '')
        ctx = entry.get('context_window_tokens')
        ac = entry.get('autocompaction_tokens')
        ext_ctx = entry.get('extended_context_tokens')
        ext_ac = entry.get('extended_autocompaction_tokens')
        alias_target = entry.get('alias_target', '')
        target_model = entry.get('target_model', '')
        model_family = entry.get('model_family', '')
        notes = entry.get('notes', '').replace('|', ';').replace('\n', ' ')

        # Convert None to empty string
        def sv(v):
            return str(v) if v is not None else ''

        line = '|'.join([
            folder,
            entry_type,
            session_model,
            catalogue_action,
            sv(ctx),
            sv(ac),
            sv(ext_ctx),
            sv(ext_ac),
            alias_target,
            target_model,
            model_family,
            notes
        ])
        lines.append(line)

    return lines


def load_config_local_registrations() -> Dict[str, Dict]:
    """Parse config.local.sh for la_register calls. Returns alias -> registration info."""
    registrations = {}
    if not CONFIG_LOCAL_PATH.exists():
        return registrations

    with open(CONFIG_LOCAL_PATH, 'r') as f:
        lines = f.readlines()

    # Parse la_register lines - they can be split across lines with backslash continuation
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('la_register '):
            # Collect full command (handle line continuations)
            full_line = line
            while full_line.endswith('\\'):
                full_line = full_line[:-1].rstrip()
                i += 1
                if i < len(lines):
                    full_line += ' ' + lines[i].strip()
                else:
                    break
            i += 1

            # Parse the la_register arguments
            # Format: la_register <alias> <subdir> <serve> <tool_parser> <reasoning_parser> <thinking> <spoof_id> <effort> [roles] [hf_repo] [size_gb] [rapid_spec]
            parts = full_line.split()
            if len(parts) < 9:
                continue  # Skip malformed

            # Remove 'la_register' from parts
            parts = parts[1:]

            alias = parts[0]
            subdir = parts[1]
            serve = parts[2]
            toolp = parts[3]
            reasonp = parts[4]
            thinking = parts[5]
            spoof = parts[6]
            effort = parts[7]
            roles = parts[8] if len(parts) > 8 else ''
            hf_repo = parts[9] if len(parts) > 9 else ''
            size_gb = parts[10] if len(parts) > 10 else ''
            rapid_spec = parts[11] if len(parts) > 11 else ''

            registrations[alias] = {
                'alias': alias,
                'subdir': subdir,
                'serve': serve,
                'tool_parser': toolp,
                'reasoning_parser': reasonp,
                'thinking': thinking,
                'spoof_id': spoof,
                'effort': effort,
                'roles': roles,
                'hf_repo': hf_repo,
                'size_gb': size_gb,
                'rapid_spec': rapid_spec,
            }
        else:
            i += 1

    return registrations


def merge_catalogue_with_config(entries: List[Dict], registrations: Dict[str, Dict]) -> List[Dict]:
    """Merge YAML catalogue with config.local.sh registrations.

    Returns list of merged entries with all available fields.
    Priority: config.local.sh registration fields override YAML for matching folder/subdir.
    """
    # Build lookup from YAML by folder
    yaml_by_folder = {e['folder']: e for e in entries}

    # Build lookup from config by subdir
    config_by_subdir = {r['subdir']: r for r in registrations.values()}

    merged = []
    seen = set()

    # First, add all YAML entries that have a matching config registration (session models)
    for folder, yaml_entry in yaml_by_folder.items():
        if not yaml_entry.get('session_model'):
            continue
        config_reg = config_by_subdir.get(folder)
        if config_reg:
            merged_entry = {**yaml_entry, **config_reg, '_source': 'both'}
            merged.append(merged_entry)
            seen.add(folder)

    # Then, add config registrations that don't have YAML entry
    for subdir, config_reg in config_by_subdir.items():
        if subdir not in seen:
            yaml_entry = yaml_by_folder.get(subdir, {})
            merged_entry = {**yaml_entry, **config_reg, '_source': 'config_only'}
            merged.append(merged_entry)
            seen.add(subdir)

    # Finally, add YAML-only entries (excluded, drafters, etc.)
    for folder, yaml_entry in yaml_by_folder.items():
        if folder not in seen:
            merged_entry = {**yaml_entry, '_source': 'yaml_only'}
            merged.append(merged_entry)

    return merged


def cmd_generate(args):
    """Generate derived PSV from YAML."""
    entries = load_yaml_catalogue()
    errors = validate_yaml(entries)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    lines = generate_psv(entries)
    output = '\n'.join(lines) + '\n'

    if args.output:
        Path(args.output).write_text(output)
    else:
        DERIVED_PSV_PATH.write_text(output)

    print(f"Generated {len(entries)} entries to {args.output or DERIVED_PSV_PATH}")
    return 0


def cmd_validate(args):
    """Validate YAML catalogue."""
    entries = load_yaml_catalogue()
    errors = validate_yaml(entries)
    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    else:
        print(f"Validation PASSED: {len(entries)} entries")
        return 0


def cmd_merge(args):
    """Show merged view of YAML + config registrations."""
    entries = load_yaml_catalogue()
    registrations = load_config_local_registrations()
    merged = merge_catalogue_with_config(entries, registrations)

    print(f"YAML entries: {len(entries)}")
    print(f"Config registrations: {len(registrations)}")
    print(f"Merged: {len(merged)}")
    print()

    for m in merged:
        src = m.get('_source', 'unknown')
        folder = m.get('folder') or m.get('subdir') or '?'
        entry_type = m.get('entry_type', '?')
        session_model = m.get('session_model', '?')
        ctx = m.get('context_window_tokens', '?')
        ac = m.get('autocompaction_tokens', '?')
        alias = m.get('alias', '?')
        roles = m.get('roles', '')
        print(f"  [{src}] {folder} | {entry_type} | session={session_model} | ctx={ctx} | ac={ac} | alias={alias} | roles={roles}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Generate derived PSV catalogue from YAML")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_gen = subparsers.add_parser("generate", help="Generate derived PSV")
    p_gen.add_argument("--output", "-o", help="Output path (default: config/model-catalogue-derived.psv)")
    p_gen.set_defaults(func=cmd_generate)

    p_val = subparsers.add_parser("validate", help="Validate YAML catalogue")
    p_val.set_defaults(func=cmd_validate)

    p_merge = subparsers.add_parser("merge", help="Show merged YAML + config view")
    p_merge.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())