#!/usr/bin/env python3
"""Maintainer tool to generate runtime registry artifacts from config/remote-registry.yaml.

This script reads config/remote-registry.yaml (the single hand-edited source
of truth for remote LLM providers and the model roster) and generates:
  1. config/remote-registry.json
     Read at runtime by Python with stdlib json (no pyyaml dependency for end users).
  2. bin/remote-provider-registry.sh
     A bash 3.2-compatible library sourced by bin/remote-session.sh.

This tool is maintainer-only and requires pyyaml:
    pipx inject <venv> pyyaml   OR   uv pip install pyyaml

Options:
  --write     Render and write both artifacts. Only modifies files whose content
              actually changed (preserving mtimes). Ensures bin/*.sh is 0755
              and config/*.json is 0644.
  --check     Render in memory and verify disk contents match. Exits 0 if up to
              date, or exits 1 printing stale paths to stderr.
  --yaml PATH Path to remote-registry.yaml (default: <repo>/config/remote-registry.yaml)
  --root DIR  Repository root directory (default: parent of this script's directory)
  -h, --help  Show this help message and exit.

Exit codes:
  0 - Success (artifacts written or up to date, or help displayed)
  1 - Drift detected under --check
  2 - Argument / usage error (e.g. neither or both of --write/--check specified)
  3 - Missing pyyaml dependency
  4 - Schema validation error in input YAML
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import stat
import sys
from typing import Any

# Defer pyyaml import until after CLI argument parsing so -h / --help works
# cleanly in environments without pyyaml installed.
yaml: Any = None


# = remote_provider_registry.TIER_CHOICES. Roster ENTRIES use the picker vocabulary instead
# (renewing_free | trial | unknown | paid_only), which is why entries are not checked here.
PROVIDER_TIERS = ("local", "renewing_free", "trial", "paid", "consumer_web", "unknown")


def _sh_quote(val: str) -> str:
    """Return a single-quoted bash string literal, safely escaping single quotes."""
    return "'" + val.replace("'", "'\\''") + "'"


def _coerce_dates(obj: Any) -> Any:
    """Coerce any datetime.date or datetime.datetime objects to ISO-format strings."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _coerce_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_dates(v) for v in obj]
    return obj


def load(yaml_path: str | Path) -> dict[str, Any]:
    """Load and date-coerce registry data from a YAML file."""
    global yaml
    if yaml is None:
        try:
            import yaml as _yaml
            yaml = _yaml
        except ImportError:
            sys.stderr.write(
                "remote-registry-generate: pyyaml is required (maintainers only): "
                "pipx inject <venv> pyyaml, or uv pip install pyyaml\n"
            )
            sys.exit(3)

    path = Path(yaml_path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return _coerce_dates(data)


REQUIRED_PROVIDER_FIELDS = {
    "id",
    "display",
    "tier",
    "base_url",
    "chat_path",
    "models_path",
    "catalog_url",
    "key_env",
    "extra_env",
    "static_headers",
    "default_models",
    "supports_responses",
    "notes",
    "litellm_prefix",
    "api_base_mode",
    "generic_openai_base",
}

REQUIRED_ENTRY_FIELDS = {
    "alias",
    "provider",
    "model_id",
    "display",
    "tier",
    "notes",
    "autocompact_default",
}


def validate(data: dict[str, Any]) -> None:
    """Validate structure, types, and constraints of parsed registry data."""
    if not isinstance(data, dict):
        raise ValueError("Registry data must be a dictionary")

    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("'providers' must be a mapping")

    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("'entries' must be a list")

    clear_env_extra = data.get("clear_env_extra", [])
    if not isinstance(clear_env_extra, list):
        raise ValueError("'clear_env_extra' must be a list")

    for p_key, prov in providers.items():
        if not isinstance(prov, dict):
            raise ValueError(f"Provider '{p_key}' must be a mapping")
        prov_id = prov.get("id")
        if prov_id != p_key:
            raise ValueError(
                f"Provider key '{p_key}' does not match provider id '{prov_id}'"
            )

        missing = REQUIRED_PROVIDER_FIELDS - set(prov.keys())
        if missing:
            raise ValueError(
                f"Provider '{p_key}' missing required fields: {sorted(missing)}"
            )

        api_base_mode = prov.get("api_base_mode")
        # The router (agent-fallback.py) gates on this enum; any other value would never be routed.
        if prov.get("tier") not in PROVIDER_TIERS:
            raise ValueError(f"Provider '{p_key}' has tier {prov.get('tier')!r}; "
                             f"one of {', '.join(PROVIDER_TIERS)}")
        if api_base_mode not in {"none", "static", "cloudflare"}:
            raise ValueError(
                f"Provider '{p_key}' has invalid api_base_mode '{api_base_mode}'"
            )

        if api_base_mode == "static" and not prov.get("base_url"):
            raise ValueError(
                f"Provider '{p_key}' has api_base_mode 'static' but empty base_url"
            )

        catalog_mode = prov.get("catalog_mode")
        if catalog_mode is not None and catalog_mode != "cloudflare_search":
            raise ValueError(
                f"Provider '{p_key}' has invalid catalog_mode '{catalog_mode}'"
            )

        # Check for newlines in values rendered into shell script
        shell_checked = [
            prov.get("base_url", ""),
            prov.get("models_path", ""),
            prov.get("catalog_url", ""),
            prov.get("litellm_prefix", ""),
            prov.get("catalog_disabled_message", ""),
            prov.get("retired_message", ""),
        ]
        for val in shell_checked:
            if isinstance(val, str) and "\n" in val:
                raise ValueError(
                    f"Provider '{p_key}' field contains disallowed newline: {val!r}"
                )

    seen_aliases: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("All entries must be mappings")
        missing_entry = REQUIRED_ENTRY_FIELDS - set(entry.keys())
        if missing_entry:
            raise ValueError(
                f"Entry missing required fields: {sorted(missing_entry)}"
            )
        alias = entry["alias"]
        if alias in seen_aliases:
            raise ValueError(f"Duplicate entry alias: '{alias}'")
        seen_aliases.add(alias)

        prov = entry["provider"]
        if prov not in providers:
            raise ValueError(
                f"Entry '{alias}' references unknown provider '{prov}'"
            )

        for field in REQUIRED_ENTRY_FIELDS:
            val = str(entry[field])
            if "|" in val:
                raise ValueError(
                    f"Entry '{alias}' field '{field}' contains disallowed pipe character '|'"
                )
            if "\n" in val:
                raise ValueError(
                    f"Entry '{alias}' field '{field}' contains disallowed newline"
                )


def _render_json(data: dict[str, Any]) -> str:
    """Render canonical JSON artifact."""
    out = {
        "schema_version": 1,
        "generated_by": "bin/remote-registry-generate.py",
        "source": "config/remote-registry.yaml",
        "clear_env_extra": data.get("clear_env_extra", []),
        "providers": data.get("providers", {}),
        "entries": data.get("entries", []),
    }
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _render_sh(data: dict[str, Any]) -> str:
    """Render bash 3.2-compatible library artifact."""
    providers: dict[str, dict[str, Any]] = data.get("providers", {})
    clear_env_extra: list[str] = data.get("clear_env_extra", [])

    provider_ids = list(providers.keys())
    provider_ids_str = " ".join(provider_ids)

    # Compute set of env vars to clear
    env_vars: set[str] = set(clear_env_extra)
    for prov in providers.values():
        if "session_env" in prov and prov["session_env"] is not None:
            for v in prov["session_env"]:
                env_vars.add(v)
        else:
            if prov.get("key_env"):
                env_vars.add(prov["key_env"])
            for ev in prov.get("extra_env", []):
                env_vars.add(ev)

    sorted_env_vars = " ".join(sorted(env_vars))

    lines: list[str] = [
        "#!/usr/bin/env bash",
        "# GENERATED by bin/remote-registry-generate.py from config/remote-registry.yaml — DO NOT EDIT; edit the YAML and run --write.",
        "#",
        "# bash 3.2 library for remote session provider resolution and routing.",
        "# Exposed functions and variables:",
        "#   _PROVIDER_IDS",
        "#   _CLEAR_PROVIDER_ENV_VARS",
        "#   _available <provider>",
        "#   _openai_base <provider>",
        "#   _clear_provider_env",
        "#   _catalog_request <provider>",
        "#   _litellm_route <provider> <model>",
        "# NOTE: The sourcing script must provide $KEYS and _cloudflare_base.",
        "",
        f"_PROVIDER_IDS={_sh_quote(provider_ids_str)}",
        f"_CLEAR_PROVIDER_ENV_VARS={_sh_quote(sorted_env_vars)}",
        "",
        "_available() {",
        '    case "$1" in',
    ]

    has_retired = False
    for pid, prov in providers.items():
        msg = prov.get("retired_message")
        if msg:
            has_retired = True
            lines.append(f"        {pid})")
            lines.append(f"            echo {_sh_quote(msg)} >&2")
            lines.append("            return 2 ;;")

    if not has_retired:
        lines.append("        *) return 0 ;;")
    else:
        lines.append("        *) return 0 ;;")
    lines.append("    esac")
    lines.append("}")
    lines.append("")

    # _openai_base
    lines.append("_openai_base() {")
    lines.append('    case "$1" in')
    for pid, prov in providers.items():
        if prov.get("generic_openai_base"):
            base_url = prov.get("base_url", "")
            lines.append(f"        {pid}) echo {_sh_quote(base_url)} ;;")
    lines.append("        *) return 2 ;;")
    lines.append("    esac")
    lines.append("}")
    lines.append("")

    # _clear_provider_env
    lines.append("_clear_provider_env() {")
    lines.append("    local name")
    lines.append('    for name in $_CLEAR_PROVIDER_ENV_VARS; do')
    lines.append('        unset "$name"')
    lines.append("    done")
    lines.append("}")
    lines.append("")

    # _catalog_request
    lines.append("_catalog_request() {")
    lines.append('    local prov="$1" envline key base')
    lines.append('    _available "$prov" || return 2')
    lines.append('    case "$prov" in')

    for pid, prov in providers.items():
        if prov.get("retired_message"):
            continue
        cat_dis = prov.get("catalog_disabled_message")
        if cat_dis:
            lines.append(
                f"        {pid}) echo {_sh_quote(cat_dis)} >&2; return 2 ;;"
            )
        elif prov.get("catalog_mode") == "cloudflare_search":
            lines.append(
                '        cloudflare) base="$(_cloudflare_base)" || return 1'
            )
            lines.append(
                '                    base="${base%/v1}/models/search?per_page=100" ;;'
            )
        elif prov.get("catalog_url"):
            cat_url = prov["catalog_url"]
            lines.append(f"        {pid}) base={_sh_quote(cat_url)} ;;")
        elif prov.get("models_path"):
            full_models = prov["base_url"] + prov["models_path"]
            lines.append(f"        {pid}) base={_sh_quote(full_models)} ;;")
        else:
            msg = (
                f"remote-session: no documented catalog route for {pid}; "
                "choose an API model from its provider docs with --remote-model ID. "
                "Catalog verification unavailable."
            )
            lines.append(
                f"        {pid}) echo {_sh_quote(msg)} >&2; return 2 ;;"
            )

    lines.append(
        '        *) echo "remote-session: no documented catalog route for $prov; choose an API model from its provider docs with --remote-model ID. Catalog verification unavailable." >&2; return 2 ;;'
    )
    lines.append("    esac")
    lines.append('    envline="$("$KEYS" --env "$prov")" || return 1')
    lines.append('    envline="${envline%%$\'\\n\'*}"')
    lines.append('    key="${envline#*=}"')
    lines.append(
        "    # curl, not python: it uses the system trust store, which survives the"
    )
    lines.append(
        "    # corporate TLS-inspecting proxy that breaks python's default bundle."
    )
    lines.append(
        "    # Keep the credential out of argv; escape curl-config quotes/backslashes."
    )
    lines.append('    key="${key//\\\\/\\\\\\\\}"; key="${key//\\"/\\\\\\"}"')
    lines.append('    printf \'header = "Authorization: Bearer %s"\\n\' "$key" |')
    lines.append(
        '        curl --config - --fail --silent --max-time 25 "$base" || {'
    )
    lines.append(
        "        printf '  model check : ? catalog request failed (network, authorization, or service error)\\n' >&2; return 1; }"
    )
    lines.append("}")
    lines.append("")

    # _litellm_route
    lines.append(
        '_litellm_route() { # _litellm_route <provider> <model> -> "litellm_model|api_base"'
    )
    lines.append('    local prov="$1" model="$2" base=""')
    lines.append('    case "$prov" in')

    for pid, prov in providers.items():
        if prov.get("retired_message"):
            continue
        prefix = prov.get("litellm_prefix", "")
        mode = prov.get("api_base_mode")
        if mode == "none":
            lines.append(f'        {pid}) echo "{prefix}/$model|" ;;')
        elif mode == "cloudflare":
            lines.append(
                f'        {pid}) base="$(_cloudflare_base)" || return 1; echo "{prefix}/$model|$base" ;;'
            )
        elif mode == "static":
            base_url = prov.get("base_url", "")
            lines.append(
                f'        {pid}) echo "{prefix}/$model|"{_sh_quote(base_url)} ;;'
            )

    lines.append("        *) return 1 ;;")
    lines.append("    esac")
    lines.append("}")
    lines.append("")

    # CLI self-execution dispatch
    lines.extend(
        [
            'if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then',
            '    case "${1:-}" in',
            "        -h|--help)",
            "            cat <<'EOF'",
            "remote-provider-registry.sh - bash 3.2 remote provider library",
            "",
            "Source this script in bash to use its routing and catalog functions:",
            "  _available <provider>",
            "  _openai_base <provider>",
            "  _clear_provider_env",
            "  _catalog_request <provider>",
            "  _litellm_route <provider> <model>",
            "",
            f"Configured provider IDs: {provider_ids_str}",
            "EOF",
            "            exit 0",
            "            ;;",
            "        *)",
            '            echo "remote-provider-registry.sh: source this file; see --help" >&2',
            "            exit 2",
            "            ;;",
            "    esac",
            "fi",
            "",
        ]
    )

    return "\n".join(lines)


def render(data: dict[str, Any]) -> dict[str, str]:
    """Render in-memory artifact paths and their generated string contents."""
    validate(data)
    return {
        "config/remote-registry.json": _render_json(data),
        "bin/remote-provider-registry.sh": _render_sh(data),
    }


def _update_file_if_changed(path: Path, content: str, mode: int) -> bool:
    """Write file if changed or missing, and set mode. Return True if modified."""
    data_bytes = content.encode("utf-8")
    changed = True
    if path.is_file():
        try:
            if path.read_bytes() == data_bytes:
                changed = False
        except OSError:
            changed = True

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data_bytes)

    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        if current_mode != mode:
            path.chmod(mode)
    except OSError:
        pass

    return changed


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    repo_root = Path(__file__).resolve().parent.parent
    default_yaml = repo_root / "config" / "remote-registry.yaml"

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--write",
        action="store_true",
        help="Generate and write artifacts to disk if changed",
    )
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Verify committed artifacts match YAML source",
    )
    parser.add_argument(
        "--yaml",
        dest="yaml_path",
        default=str(default_yaml),
        help=f"Path to input YAML registry (default: {default_yaml})",
    )
    parser.add_argument(
        "--root",
        dest="root_dir",
        default=str(repo_root),
        help=f"Target root directory for artifacts (default: {repo_root})",
    )

    args = parser.parse_args(argv)

    try:
        data = load(args.yaml_path)
    except SystemExit:
        raise
    except Exception as err:
        sys.stderr.write(f"remote-registry-generate: {args.yaml_path}: {err}\n")
        return 4

    try:
        artifacts = render(data)
    except ValueError as err:
        sys.stderr.write(f"remote-registry-generate: {args.yaml_path}: {err}\n")
        return 4

    root = Path(args.root_dir)
    json_path = root / "config" / "remote-registry.json"
    sh_path = root / "bin" / "remote-provider-registry.sh"

    targets = [
        ("config/remote-registry.json", json_path, artifacts["config/remote-registry.json"], 0o644),
        ("bin/remote-provider-registry.sh", sh_path, artifacts["bin/remote-provider-registry.sh"], 0o755),
    ]

    if args.check:
        stale: list[str] = []
        for relpath, path, content, _ in targets:
            if not path.is_file():
                stale.append(relpath)
            else:
                try:
                    if path.read_bytes() != content.encode("utf-8"):
                        stale.append(relpath)
                except OSError:
                    stale.append(relpath)
        if stale:
            for rel in stale:
                sys.stderr.write(
                    f"stale: {rel} (run bin/remote-registry-generate.py --write)\n"
                )
            return 1
        return 0

    if args.write:
        for _, path, content, mode in targets:
            _update_file_if_changed(path, content, mode)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
