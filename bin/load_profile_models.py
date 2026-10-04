#!/usr/bin/env python3
"""Load runtime profiles and emit LocalModel-compatible JSON for the picker."""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

def get_registry_aliases():
    """Get all registry aliases mapped by artifact ID."""
    result = subprocess.run(
        ['bash', '-c', 'source config/config-lib.sh && la_load_config && for a in ${!LA_SUBDIR[@]}; do echo "$a -> ${LA_SUBDIR[$a]}"; done'],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    registry_map = {}
    for line in result.stdout.strip().split('\n'):
        if ' -> ' in line:
            alias, subdir = line.split(' -> ')
            if subdir not in registry_map:
                registry_map[subdir] = []
            registry_map[subdir].append(alias)
    return registry_map

def load_profiles():
    registry_map = get_registry_aliases()
    
    with open(CONFIG_DIR / "model-runtime-profiles.json") as f:
        runtime_profiles = json.load(f)
    with open(CONFIG_DIR / "runtime-resource-profiles.json") as f:
        resource_profiles = json.load(f)
    with open(CONFIG_DIR / "runtime-environments.json") as f:
        env_profiles = json.load(f)
    
    resource_lookup = {p['id']: p for p in resource_profiles.get('profiles', [])}
    env_lookup = {p['id']: p for p in env_profiles.get('profiles', [])}
    
    models = []
    for p in runtime_profiles.get('profiles', []):
        # Only include qualified profiles
        if not all(v == 'passed' for v in p.get('qualification', {}).values()):
            continue
        
        artifact_id = p.get('artifact_id')
        aliases = registry_map.get(artifact_id, [])
        
        # Pick best alias
        alias = None
        for a in aliases:
            if 'operator' in a or 'reasoner' in a:
                alias = a
                break
        if not alias and aliases:
            alias = aliases[0]
        if not alias:
            alias = p['id']
        
        res_profile = resource_lookup.get(p.get('resource_profile', ''), {})
        env_profile = env_lookup.get(p.get('environment_profile', ''), {})
        
        thinking = p.get('thinking', False)
        roles = 'reasoner' if thinking else 'operator'
        
        # Determine family from alias
        family = 'Qwen' if 'qwen' in alias.lower() else \
                 'DeepSeek' if 'deepseek' in alias.lower() else \
                 'Gemma' if 'gemma' in alias.lower() else \
                 'Llama' if 'llama' in alias.lower() else \
                 'Ornith' if 'ornith' in alias.lower() else \
                 'Mistral' if 'mistral' in alias.lower() else \
                 'Other'
        
        models.append({
            'alias': alias,
            'profile_id': p['id'],
            'artifact_id': p.get('artifact_id', ''),
            'backend': p.get('backend', ''),
            'thinking': p.get('thinking', False),
            'effort': 'high',
            'roles': roles,
            'family': family,
            'tool_parser': p.get('tool_parser', ''),
            'reasoning_parser': p.get('reasoning_parser', ''),
            'cache_mb': resource_lookup.get(p.get('resource_profile', ''), {}).get('cache_memory_mb', ''),
            'hybrid_entries': resource_lookup.get(p.get('resource_profile', ''), {}).get('hybrid_cache_entries', ''),
            'max_seqs': resource_lookup.get(p.get('resource_profile', ''), {}).get('max_num_seqs', ''),
            'backend_version': env_lookup.get(p.get('environment_profile', ''), {}).get('version', ''),
        })
    
    return models

if __name__ == '__main__':
    models = load_profiles()
    print(json.dumps(models, indent=2))
