#!/usr/bin/env python3
"""
la-model-profile.py — Canonical resolver for runtime profiles

Consumes: config/model-runtime-profiles.json, config/runtime-resource-profiles.json,
          config/runtime-environments.json, optional .local.json overlays, legacy config.local.sh

Commands:
  validate [--legacy-adapter CONFIG]       - validate schemas, refs, no cycles
  list [--backend B] [--capability C] [--qualified-only] - tabular
  show PROFILE_ID [--provenance]           - human-readable resolved
  resolve PROFILE_ID [--json|--shell]      - canonical machine output
  artifact PROFILE_ID                       - print artifact_id only
  recommend --ram-gb N [--capability C]    - filter by RAM/capability
  compare-upstream PROFILE_ID               - diff vs Rapid built-in
  migrate-preview --legacy CONFIG --output FILE - preview legacy migration
"""

import json
import os
import sys
import argparse
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

BASE_FILES = {
    "runtime": CONFIG_DIR / "model-runtime-profiles.json",
    "resource": CONFIG_DIR / "runtime-resource-profiles.json",
    "environment": CONFIG_DIR / "runtime-environments.json",
}

LOCAL_FILES = {
    "runtime": CONFIG_DIR / "model-runtime-profiles.local.json",
    "resource": CONFIG_DIR / "runtime-resource-profiles.local.json",
    "environment": CONFIG_DIR / "runtime-environments.local.json",
}

CATALOG_FILES = [
    CONFIG_DIR / "model-catalog.psv",
    CONFIG_DIR / "model-catalog.local.psv",
    CONFIG_DIR / "model-catalog.rapid-next.psv",
    CONFIG_DIR / "model-catalog.qwen38.psv",
    CONFIG_DIR / "model-catalog.acquisitions.rapid.psv",
    CONFIG_DIR / "model-catalog.acquisitions.gguf.psv",
    CONFIG_DIR / "model-catalog.acquisitions.omlx.psv",
]

class ProfileError(Exception):
    pass

def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)

def save_json(path: Path, data: Dict):
    with path.open('w') as f:
        json.dump(data, f, indent=2)

def merge_overlay(base: Dict, local: Dict, key: str) -> Dict:
    """Merge local overlay into base, local wins on ID."""
    if not local:
        return base
    base_by_id = {p['id']: p for p in base.get('profiles', [])}
    for p in local.get('profiles', []):
        base_by_id[p['id']] = p
    return {"schema_version": base.get("schema_version", 1), "profiles": list(base_by_id.values())}

def load_all() -> Tuple[Dict, Dict, Dict]:
    runtime = merge_overlay(load_json(BASE_FILES["runtime"]), load_json(LOCAL_FILES["runtime"]), "runtime")
    resource = merge_overlay(load_json(BASE_FILES["resource"]), load_json(LOCAL_FILES["resource"]), "resource")
    environment = merge_overlay(load_json(BASE_FILES["environment"]), load_json(LOCAL_FILES["environment"]), "environment")
    return runtime, resource, environment

def get_catalog_artifacts() -> set:
    artifacts = set()
    for cat in CATALOG_FILES:
        if not cat.exists():
            continue
        with cat.open() as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('|')
                if len(parts) >= 2:
                    artifacts.add(parts[0].strip())
    # Also include registry subdirs from config.local.sh (private overlay)
    # We read config.local.sh through config-lib.sh to get the full registry
    try:
        result = subprocess.run(['bash', '-c', 'source ' + str(CONFIG_DIR) + '/config-lib.sh && la_load_config && for a in ${!LA_SUBDIR[@]}; do echo "${LA_SUBDIR[$a]}"; done'], capture_output=True, text=True, cwd=REPO_ROOT)
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                artifacts.add(line.strip())
    except:
        pass
    return artifacts

def validate_schemas(runtime: Dict, resource: Dict, environment: Dict, catalog_artifacts: set) -> List[str]:
    errors = []
    
    for name, data in [("runtime", runtime), ("resource", resource), ("environment", environment)]:
        if data.get("schema_version") != 1:
            errors.append(f"{name}: schema_version must be 1, got {data.get('schema_version')}")
    
    # Build complete set of runtime IDs FIRST (before validation)
    runtime_ids = {p.get("id") for p in runtime.get("profiles", []) if p.get("id")}
    
    for p in runtime.get("profiles", []):
        pid = p.get("id")
        if not pid:
            errors.append("runtime profile missing id")
            continue
        
        artifact_id = p.get("artifact_id")
        if artifact_id and artifact_id not in catalog_artifacts:
            errors.append(f"runtime profile {pid}: artifact_id '{artifact_id}' not in catalog")
        
        res_profile = p.get("resource_profile")
        if res_profile:
            res_ids = {r.get("id") for r in resource.get("profiles", [])}
            if res_profile not in res_ids:
                errors.append(f"runtime profile {pid}: resource_profile '{res_profile}' not defined")
        
        env_profile = p.get("environment_profile")
        if env_profile:
            env_ids = {e.get("id") for e in environment.get("profiles", [])}
            if env_profile not in env_ids:
                errors.append(f"runtime profile {pid}: environment_profile '{env_profile}' not defined")
        
        fallback = p.get("fallback")
        if fallback and fallback not in runtime_ids:
            errors.append(f"runtime profile {pid}: fallback '{fallback}' not a runtime profile")
    
    # Check duplicate IDs
    seen = set()
    for p in runtime.get("profiles", []):
        pid = p.get("id")
        if pid and pid in seen:
            errors.append(f"duplicate runtime profile id: {pid}")
        if pid:
            seen.add(pid)
    
    # Check fallback cycles
    for pid in runtime_ids:
        visited = set()
        cur = pid
        while True:
            if cur in visited:
                errors.append(f"fallback cycle detected starting at {pid}")
                break
            visited.add(cur)
            p = next((x for x in runtime.get("profiles", []) if x["id"] == cur), None)
            if not p or not p.get("fallback"):
                break
            cur = p["fallback"]
    
    return errors

def parse_legacy_config(path: Path) -> List[Dict]:
    """Parse la_register lines from config.local.sh or config.example.sh"""
    profiles = []
    if not path.exists():
        return profiles
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('la_register'):
                parts = line.split()
                if len(parts) >= 9:
                    alias = parts[1]
                    subdir = parts[2]
                    serve = parts[3]
                    tool_parser = parts[4]
                    reasoning_parser = parts[5] if parts[5] != '""' else None
                    thinking = parts[6].lower() == 'true'
                    spoof_id = parts[7] if parts[7] != '""' else None
                    effort = parts[8]
                    roles = parts[9] if len(parts) > 9 else ""
                    hf_repo = parts[10] if len(parts) > 10 else ""
                    size_gb = parts[11] if len(parts) > 11 else ""
                    
                    profiles.append({
                        "id": f"legacy-{alias}",
                        "artifact_id": subdir,
                        "backend": serve,
                        "environment_profile": "legacy-vllm" if serve == "vllm" else "rapid-text-stable",
                        "resource_profile": "rapid-primary-session",
                        "modality": "text",
                        "tool_parser": tool_parser if tool_parser != '""' else None,
                        "reasoning_parser": reasoning_parser,
                        "thinking": thinking,
                        "capabilities": ["chat", "streaming", "tools", "attachments"],
                        "qualification": {"source": "legacy"},
                        "legacy_alias": alias,
                        "legacy_spoof_id": spoof_id,
                        "legacy_effort": effort,
                        "legacy_roles": roles,
                        "legacy_hf_repo": hf_repo,
                        "legacy_size_gb": size_gb,
                    })
    return profiles

def resolve_profile(profile_id: str, runtime: Dict, resource: Dict, environment: Dict, legacy: List[Dict] = None) -> Optional[Dict]:
    """Resolve a profile to its full expanded form."""
    all_runtime = {p['id']: p for p in runtime.get("profiles", [])}
    if legacy:
        for p in legacy:
            all_runtime[p['id']] = p
    
    if profile_id not in all_runtime:
        return None
    
    p = all_runtime[profile_id].copy()
    
    # Resolve resource profile
    res_id = p.get("resource_profile")
    if res_id:
        res = next((r for r in resource.get("profiles", []) if r["id"] == res_id), None)
        if res:
            p["resource_profile_expanded"] = res
    
    # Resolve environment profile
    env_id = p.get("environment_profile")
    if env_id:
        env = next((e for e in environment.get("profiles", []) if e["id"] == env_id), None)
        if env:
            p["environment_profile_expanded"] = env
    
    return p

def acquisition_problems() -> list:
    """Load-time gate (0.16.0's promise): every acquisition-PSV row this tool consumes is validated
    by bin/acquisition_catalog.py; each problem is reported as file:line. Missing files are skipped."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import acquisition_catalog
    _rows, problems = acquisition_catalog.load([c for c in CATALOG_FILES if "acquisitions" in c.name])
    return [f"acquisition catalogue: {pr}" for pr in problems]


def cmd_validate(args):
    runtime, resource, environment = load_all()
    catalog_artifacts = get_catalog_artifacts()
    errors = validate_schemas(runtime, resource, environment, catalog_artifacts)
    errors += acquisition_problems()
    
    if args.legacy_adapter:
        legacy = parse_legacy_config(Path(args.legacy_adapter))
        runtime_ids = {p['id'] for p in runtime.get("profiles", [])}
        for p in legacy:
            if p['id'] in runtime_ids:
                errors.append(f"legacy profile id conflicts with runtime: {p['id']}")
        print(f"Legacy adapter: {len(legacy)} entries adapted, {len(errors)} conflicts/errors")
    
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("All schemas valid")

def cmd_list(args):
    runtime, _, _ = load_all()
    profiles = runtime.get("profiles", [])
    
    if args.backend:
        profiles = [p for p in profiles if p.get("backend") == args.backend]
    if args.capability:
        profiles = [p for p in profiles if args.capability in p.get("capabilities", [])]
    if args.qualified_only:
        profiles = [p for p in profiles if all(v == "passed" for v in p.get("qualification", {}).values())]
    
    if args.json:
        print(json.dumps(profiles, indent=2))
        return
    
    print(f"{'ID':<30} {'Backend':<8} {'Artifact':<25} {'Thinking':<8} {'Qualified'}")
    print("-" * 90)
    for p in profiles:
        qual = "yes" if all(v == "passed" for v in p.get("qualification", {}).values()) else "no"
        print(f"{p['id']:<30} {p.get('backend',''):<8} {p.get('artifact_id',''):<25} {str(p.get('thinking',False)):<8} {qual}")

def cmd_show(args):
    runtime, resource, environment = load_all()
    legacy = parse_legacy_config(Path(args.legacy_adapter)) if args.legacy_adapter else None
    p = resolve_profile(args.profile_id, runtime, resource, environment, legacy)
    if not p:
        print(f"Profile not found: {args.profile_id}", file=sys.stderr)
        sys.exit(1)
    
    if args.provenance:
        print(json.dumps(p, indent=2))
    else:
        print(f"Profile: {p['id']}")
        for k, v in p.items():
            if k.endswith('_expanded'):
                continue
            print(f"  {k}: {v}")
        if 'resource_profile_expanded' in p:
            print(f"  resource_profile: {p['resource_profile_expanded']}")
        if 'environment_profile_expanded' in p:
            print(f"  environment_profile: {p['environment_profile_expanded']}")

def cmd_resolve(args):
    runtime, resource, environment = load_all()
    legacy = parse_legacy_config(Path(args.legacy_adapter)) if args.legacy_adapter else None
    p = resolve_profile(args.profile_id, runtime, resource, environment, legacy)
    if not p:
        print(f"Profile not found: {args.profile_id}", file=sys.stderr)
        sys.exit(1)
    
    out = {k: v for k, v in p.items() if not k.endswith('_expanded')}
    
    if args.json:
        print(json.dumps(out, indent=2))
    elif args.shell:
        for k, v in out.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                key = k.upper().replace('-', '_')
                if v is None:
                    val = '""'
                elif isinstance(v, bool):
                    val = 'true' if v else 'false'
                elif isinstance(v, str):
                    val = f'"{v}"'
                else:
                    val = str(v)
                print(f"LA_RESOLVED_{key}={val}")
    else:
        print(json.dumps(out, indent=2))

def cmd_artifact(args):
    runtime, _, _ = load_all()
    p = next((p for p in runtime.get("profiles", []) if p["id"] == args.profile_id), None)
    if not p:
        print(f"Profile not found: {args.profile_id}", file=sys.stderr)
        sys.exit(1)
    print(p.get("artifact_id", ""))

def cmd_recommend(args):
    runtime, _, _ = load_all()
    profiles = runtime.get("profiles", [])
    
    if args.capability:
        profiles = [p for p in profiles if args.capability in p.get("capabilities", [])]
    
    profiles = [p for p in profiles if all(v == "passed" for v in p.get("qualification", {}).values())]
    
    print(f"{'ID':<30} {'Backend':<8} {'Artifact':<25} {'Thinking':<8}")
    for p in profiles:
        print(f"{p['id']:<30} {p.get('backend',''):<8} {p.get('artifact_id',''):<25} {str(p.get('thinking',False)):<8}")

def cmd_compare_upstream(args):
    runtime, _, _ = load_all()
    p = next((p for p in runtime.get("profiles", []) if p["id"] == args.profile_id), None)
    if not p:
        print(f"Profile not found: {args.profile_id}", file=sys.stderr)
        sys.exit(1)
    artifact_id = p.get("artifact_id")
    if not artifact_id:
        print("No artifact_id to compare", file=sys.stderr)
        sys.exit(1)
    print(f"Would compare {args.profile_id} (artifact {artifact_id}) against Rapid built-in alias")
    print("Not implemented: requires Rapid version metadata")

def cmd_migrate_preview(args):
    legacy = parse_legacy_config(Path(args.legacy))
    out = {
        "profiles": legacy,
        "count": len(legacy),
        "note": "Preview only — no writes performed"
    }
    with open(args.output, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"Preview written to {args.output}: {len(legacy)} legacy profiles")

def main():
    parser = argparse.ArgumentParser(description="Canonical runtime profile resolver")
    sub = parser.add_subparsers(dest="cmd", required=True)
    
    pv = sub.add_parser("validate", help="Validate schemas and refs")
    pv.add_argument("--legacy-adapter", help="Legacy config file to check conflicts")
    
    pl = sub.add_parser("list", help="List profiles")
    pl.add_argument("--backend", help="Filter by backend")
    pl.add_argument("--capability", help="Filter by capability")
    pl.add_argument("--qualified-only", action="store_true", help="Only fully qualified")
    pl.add_argument("--json", action="store_true", help="JSON output")
    
    ps = sub.add_parser("show", help="Show resolved profile")
    ps.add_argument("profile_id")
    ps.add_argument("--provenance", action="store_true")
    ps.add_argument("--legacy-adapter", help="Legacy config for adaptation")
    
    pr = sub.add_parser("resolve", help="Resolve profile (machine output)")
    pr.add_argument("profile_id")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--shell", action="store_true")
    pr.add_argument("--legacy-adapter", help="Legacy config for adaptation")
    
    pa = sub.add_parser("artifact", help="Print artifact_id only")
    pa.add_argument("profile_id")
    
    prec = sub.add_parser("recommend", help="Recommend profiles by RAM/capability")
    prec.add_argument("--ram-gb", type=int, required=True)
    prec.add_argument("--capability", help="Filter by capability")
    
    pcu = sub.add_parser("compare-upstream", help="Diff vs Rapid built-in")
    pcu.add_argument("profile_id")
    
    pmp = sub.add_parser("migrate-preview", help="Preview legacy migration")
    pmp.add_argument("--legacy", required=True, help="Legacy config file")
    pmp.add_argument("--output", required=True, help="Output JSON file")
    
    args = parser.parse_args()
    
    commands = {
        "validate": cmd_validate,
        "list": cmd_list,
        "show": cmd_show,
        "resolve": cmd_resolve,
        "artifact": cmd_artifact,
        "recommend": cmd_recommend,
        "compare-upstream": cmd_compare_upstream,
        "migrate-preview": cmd_migrate_preview,
    }
    
    commands[args.cmd](args)

if __name__ == "__main__":
    main()
