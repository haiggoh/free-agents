#!/usr/bin/env python3
"""
Quota telemetry for free-agents remote providers.

Implements the normalized quota record from the addendum (§6) and
provider-specific tracking rules (§10). Emits provenance-labeled
statusline segments (A/R/E/?) per §7.

This module is import-only; running it directly prints usage info.
"""

import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Constants ────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# Provenance levels (addendum §7)
PROVENANCE_AUTHORITATIVE = "authoritative"  # provider gave limit+used in response
PROVENANCE_RECONCILED = "reconciled"        # official denominator + complete local ledger
PROVENANCE_ESTIMATED = "estimated"          # local estimate or shared-resource lower bound
PROVENANCE_UNKNOWN = "unknown"              # no trustworthy percentage

# Sources (addendum §6)
SOURCE_RESPONSE_HEADER = "response-header"
SOURCE_USAGE_API = "usage-api"
SOURCE_DASHBOARD_IMPORT = "dashboard-import"
SOURCE_LOCAL_LEDGER = "local-ledger"
SOURCE_ERROR = "error"

# Dimensions (addendum §6)
DIM_REQUESTS = "requests"
DIM_TOKENS = "tokens"
DIM_CREDITS = "credits"
DIM_COMPUTE_UNITS = "compute_units"
ALL_DIMENSIONS = (DIM_REQUESTS, DIM_TOKENS, DIM_CREDITS, DIM_COMPUTE_UNITS)

# Default ledger location (addendum §9)
DEFAULT_LEDGER_DIR = Path.home() / ".local" / "share" / "local-agent"
DEFAULT_LEDGER_PATH = DEFAULT_LEDGER_DIR / "remote-usage.jsonl"
DEFAULT_STATE_PATH = DEFAULT_LEDGER_DIR / "remote-quota-state.json"

# ─── Data Structures ─────────────────────────────────────────────────────────


@dataclass
class DimensionQuota:
    """One quota dimension (requests, tokens, credits, compute_units)."""
    limit: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None

    def used_fraction(self) -> Optional[float]:
        """Return used/limit if both known, else None."""
        if self.limit is not None and self.used is not None and self.limit > 0:
            return self.used / self.limit
        return None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ComputeUnitsQuota(DimensionQuota):
    """Quota dimension with unit specifier."""
    unit: Optional[str] = None  # e.g., "Neurons", "compute_units"


@dataclass
class QuotaRecord:
    """
    Normalized quota record per addendum §6.

    {
      "provider_id": "...",
      "credential_id": "...",
      "model_id": "...",
      "scope": "project|organization|account|user-model|ip-model|unknown",
      "window": "minute|hour|day|rolling-24h|month|billing-cycle|unknown",
      "reset_at": "...",
      "dimensions": {
        "requests": {"limit": null, "used": null, "remaining": null},
        "tokens":   {"limit": null, "used": null, "remaining": null},
        "credits":  {"limit": null, "used": null, "remaining": null},
        "compute_units": {"unit": null, "limit": null, "used": null, "remaining": null}
      },
      "binding_dimension": null,
      "used_fraction": null,
      "provenance": "authoritative|reconciled|estimated|unknown",
      "source": "response-header|usage-api|dashboard-import|local-ledger|error",
      "observed_at": "..."
    }
    """
    provider_id: str
    credential_id: str
    model_id: str
    scope: str = "unknown"
    window: str = "unknown"
    reset_at: str = ""
    dimensions: dict = field(default_factory=dict)  # dimension -> DimensionQuota
    binding_dimension: Optional[str] = None
    used_fraction: Optional[float] = None
    provenance: str = PROVENANCE_UNKNOWN
    source: str = SOURCE_RESPONSE_HEADER
    observed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        # Ensure all dimensions exist
        for dim in ALL_DIMENSIONS:
            if dim not in self.dimensions:
                if dim == DIM_COMPUTE_UNITS:
                    self.dimensions[dim] = ComputeUnitsQuota()
                else:
                    self.dimensions[dim] = DimensionQuota()

        # Compute used_fraction and binding_dimension if not set
        if self.used_fraction is None or self.binding_dimension is None:
            self._recompute_binding()

    def _recompute_binding(self):
        """Compute used_fraction = max of known used/limit fractions (addendum §8)."""
        max_frac = None
        binding = None
        for dim_name, dim_quota in self.dimensions.items():
            if isinstance(dim_quota, ComputeUnitsQuota):
                frac = dim_quota.used_fraction()
            else:
                frac = dim_quota.used_fraction() if hasattr(dim_quota, 'used_fraction') else None
            if frac is not None:
                if max_frac is None or frac > max_frac:
                    max_frac = frac
                    binding = dim_name
        self.used_fraction = max_frac
        self.binding_dimension = binding

    def to_dict(self) -> dict:
        """Serialize to dict matching addendum §6 exactly."""
        dims = {}
        for dim_name, dim_quota in self.dimensions.items():
            if isinstance(dim_quota, ComputeUnitsQuota):
                dims[dim_name] = dim_quota.to_dict()
            else:
                dims[dim_name] = dim_quota.to_dict()

        return {
            "schema_version": SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "credential_id": self.credential_id,
            "model_id": self.model_id,
            "scope": self.scope,
            "window": self.window,
            "reset_at": self.reset_at,
            "dimensions": dims,
            "binding_dimension": self.binding_dimension,
            "used_fraction": self.used_fraction,
            "provenance": self.provenance,
            "source": self.source,
            "observed_at": self.observed_at,
        }

    def to_statusline(self, max_len: int = 60) -> str:
        """
        Format for statusline per addendum §7 examples:
        [Groq/Qwen3.8 A daily 41% · TPD]
        [Gemini/Flash R daily 63% · resets 00:00 PT]
        [Z.AI/GLM-4.7-Flash ? free · quota undisclosed]
        """
        # Provenance label
        prov_label = {
            PROVENANCE_AUTHORITATIVE: "A",
            PROVENANCE_RECONCILED: "R",
            PROVENANCE_ESTIMATED: "E",
            PROVENANCE_UNKNOWN: "?",
        }.get(self.provenance, "?")

        # Model short name
        model_short = self.model_id.split("/")[-1] if "/" in self.model_id else self.model_id

        # Percentage
        if self.used_fraction is not None and self.provenance != PROVENANCE_UNKNOWN:
            pct = f"{int(round(self.used_fraction * 100))}%"
        else:
            pct = "?%"

        # Window label
        window_label = self.window
        if self.reset_at:
            try:
                reset_dt = datetime.fromisoformat(self.reset_at.replace("Z", "+00:00"))
                window_label += f" · resets {reset_dt.strftime('%H:%M %Z')}"
            except Exception:
                pass

        # Binding dimension hint
        dim_hint = ""
        if self.binding_dimension:
            dim_hint = f" · {self.binding_dimension.upper()[:3]}"

        # Build: [Provider/Model PROV window% · hint]
        provider_display = self.provider_id.capitalize()
        if provider_display == "Openrouter":
            provider_display = "OpenRouter"
        elif provider_display == "Cloudflare":
            provider_display = "Cloudflare"
        elif provider_display == "Llm7":
            provider_display = "LLM7"

        status = f"[{provider_display}/{model_short} {prov_label} {window_label} {pct}{dim_hint}]"

        if len(status) > max_len:
            # Truncate model name if needed
            available = max_len - len(f"[{provider_display}/  {prov_label} {window_label} {pct}{dim_hint}]")
            if available > 3:
                model_short = model_short[:available - 1] + "…"
                status = f"[{provider_display}/{model_short} {prov_label} {window_label} {pct}{dim_hint}]"

        return status


# ─── Ledger I/O ───────────────────────────────────────────────────────────────


def _ensure_ledger_dir(path: Path = DEFAULT_LEDGER_DIR) -> Path:
    """Create ledger directory if needed."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_ledger(record: QuotaRecord, ledger_path: Path = DEFAULT_LEDGER_PATH) -> None:
    """Append a QuotaRecord to the JSONL ledger (addendum §9)."""
    _ensure_ledger_dir(ledger_path.parent)
    with ledger_path.open("a") as f:
        f.write(json.dumps(record.to_dict()) + "\n")


def write_usage_event(
    provider: str,
    credential: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    requests: int = 1,
    provider_reported: Optional[dict] = None,
    authoritative_headers: Optional[dict] = None,
    reset_at: Optional[str] = None,
    window: Optional[str] = None,
    scope: str = "account",
    provenance: Optional[str] = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> None:
    """
    Convenience: write a usage event (simpler than full QuotaRecord).
    Called by remote-agent-dispatch.py after successful call.

    If provider_reported is given, provenance becomes AUTHORITATIVE.
    Otherwise, provenance defaults to ESTIMATED.

    Note: provider-specific header parsing is done by the caller
    (remote-agent-dispatch.py) to avoid circular imports. This function
    just writes a pre-built QuotaRecord or builds a basic one.
    """
    record = QuotaRecord(
        provider_id=provider,
        credential_id=credential,
        model_id=model,
        scope=scope,
        window=window or "unknown",
        source=SOURCE_RESPONSE_HEADER,
    )
    # Populate from provider_reported if available
    has_quota_data = False
    if provider_reported:
        for dim_name, dim_data in provider_reported.items():
            if dim_name in record.dimensions:
                has_quota_data = True
                if dim_name == DIM_COMPUTE_UNITS:
                    record.dimensions[dim_name] = ComputeUnitsQuota(
                        unit=dim_data.get("unit"),
                        limit=dim_data.get("limit"),
                        used=dim_data.get("used"),
                        remaining=dim_data.get("remaining"),
                    )
                else:
                    record.dimensions[dim_name] = DimensionQuota(
                        limit=dim_data.get("limit"),
                        used=dim_data.get("used"),
                        remaining=dim_data.get("remaining"),
                    )
    if has_quota_data:
        record.provenance = PROVENANCE_AUTHORITATIVE
    elif provenance:
        record.provenance = provenance
    else:
        record.provenance = PROVENANCE_ESTIMATED
    if reset_at:
        record.reset_at = reset_at
    record._recompute_binding()
    write_ledger(record, ledger_path)


def write_parsed_record(record: QuotaRecord, ledger_path: Path = DEFAULT_LEDGER_PATH) -> None:
    """
    Write a pre-parsed QuotaRecord (from provider-specific parser) to the ledger.
    Called by remote-agent-dispatch.py after parsing headers with quota.providers.
    """
    write_ledger(record, ledger_path)


def read_ledger(ledger_path: Path = DEFAULT_LEDGER_PATH, max_lines: int = 0) -> list[QuotaRecord]:
    """Read quota records from JSONL ledger (newest first if max_lines > 0)."""
    if not ledger_path.exists():
        return []
    records = []
    with ledger_path.open() as f:
        lines = f.readlines()
    if max_lines > 0:
        lines = lines[-max_lines:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            records.append(_dict_to_record(data))
        except json.JSONDecodeError:
            continue
    return records


def _dict_to_record(data: dict) -> QuotaRecord:
    """Reconstruct QuotaRecord from dict (handles nested dimensions)."""
    # Remove schema_version if present (not a constructor arg)
    data = {k: v for k, v in data.items() if k != "schema_version"}
    dims = {}
    for dim_name, dim_data in data.get("dimensions", {}).items():
        if dim_name == DIM_COMPUTE_UNITS:
            dims[dim_name] = ComputeUnitsQuota(**dim_data)
        else:
            dims[dim_name] = DimensionQuota(**dim_data)
    data = {**data, "dimensions": dims}
    return QuotaRecord(**data)


# ─── State Rebuilding ─────────────────────────────────────────────────────────


def rebuild_state(ledger_path: Path = DEFAULT_LEDGER_PATH,
                  state_path: Path = DEFAULT_STATE_PATH,
                  max_age_days: int = 30) -> dict:
    """
    Rebuild derived quota state from ledger (addendum §9).

    Returns dict: {provider_id: {credential_id: QuotaRecord}} — latest per provider+credential.
    """
    _ensure_ledger_dir(state_path.parent)
    records = read_ledger(ledger_path)

    # Filter by age
    cutoff = time.time() - (max_age_days * 86400)
    recent = []
    for r in records:
        try:
            obs_time = datetime.fromisoformat(r.observed_at.replace("Z", "+00:00")).timestamp()
            if obs_time >= cutoff:
                recent.append(r)
        except Exception:
            recent.append(r)  # keep if unparseable

    # Latest per provider+credential
    latest: dict[str, dict[str, QuotaRecord]] = {}
    for r in recent:
        key = r.provider_id
        cred = r.credential_id
        if key not in latest:
            latest[key] = {}
        if cred not in latest[key] or r.observed_at > latest[key][cred].observed_at:
            latest[key][cred] = r

    # Write state file
    state_data = {}
    for provider, creds in latest.items():
        state_data[provider] = {}
        for cred, record in creds.items():
            state_data[provider][cred] = record.to_dict()

    with state_path.open("w") as f:
        json.dump({"schema_version": SCHEMA_VERSION, "providers": state_data}, f, indent=2)

    return latest


def load_state(state_path: Path = DEFAULT_STATE_PATH) -> dict:
    """Load derived state from JSON file."""
    if not state_path.exists():
        return {}
    with state_path.open() as f:
        data = json.load(f)
    result = {}
    for provider, creds in data.get("providers", {}).items():
        result[provider] = {}
        for cred, record_data in creds.items():
            result[provider][cred] = _dict_to_record(record_data)
    return result


# ─── Statusline Emission ──────────────────────────────────────────────────────


def emit_statusline(provider_ids: Optional[list[str]] = None,
                    state_path: Path = DEFAULT_STATE_PATH,
                    max_total_len: int = 120) -> list[str]:
    """
    Emit statusline segments for given providers (or all with state).

    Returns list of JSON strings: each {"label":"quota","text":"[...]","level":"ok|warn|crit"}
    """
    state = load_state(state_path)
    if not state:
        return []

    segments = []
    total_len = 0

    # Determine which providers to show
    if provider_ids is None:
        provider_ids = sorted(state.keys())

    for pid in provider_ids:
        if pid not in state:
            continue
        creds = state[pid]
        if not creds:
            continue
        # Show the credential with the highest used_fraction (most exhausted)
        best_record = max(creds.values(), key=lambda r: r.used_fraction or 0)
        status_text = best_record.to_statusline()

        # Determine level based on used_fraction
        level = "ok"
        if best_record.used_fraction is not None:
            if best_record.used_fraction >= 0.9:
                level = "crit"
            elif best_record.used_fraction >= 0.7:
                level = "warn"

        segment = {"label": "quota", "text": status_text, "level": level}
        seg_json = json.dumps(segment)
        if total_len + len(seg_json) > max_total_len:
            break
        segments.append(seg_json)
        total_len += len(seg_json)

    return segments


def emit_statusline_json(provider_ids: Optional[list[str]] = None,
                         state_path: Path = DEFAULT_STATE_PATH) -> str:
    """Emit all quota statusline segments as a JSON array."""
    segments = emit_statusline(provider_ids, state_path)
    return "[" + ",".join(segments) + "]"


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Quota telemetry for free-agents remote providers.")
    ap.add_argument("--statusline", action="store_true",
                    help="emit quota statusline segments as JSON array")
    ap.add_argument("--providers", nargs="*",
                    help="provider ids to include in statusline (default: all with state)")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild derived state from ledger")
    ap.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR,
                    help=f"ledger directory (default: {DEFAULT_LEDGER_DIR})")
    ap.add_argument("--max-age-days", type=int, default=30,
                    help="max age for state rebuild (default: 30)")
    ap.add_argument("--show-ledger", action="store_true",
                    help="print recent ledger entries")
    ap.add_argument("--ledger-lines", type=int, default=20,
                    help="number of ledger lines to show (default: 20)")
    a = ap.parse_args(argv)

    ledger_path = a.ledger_dir / "remote-usage.jsonl"
    state_path = a.ledger_dir / "remote-quota-state.json"

    if a.rebuild:
        latest = rebuild_state(ledger_path, state_path, a.max_age_days)
        print(f"Rebuilt state for {len(latest)} providers from {ledger_path}")
        return 0

    if a.show_ledger:
        records = read_ledger(ledger_path, a.ledger_lines)
        for r in records:
            print(json.dumps(r.to_dict(), indent=2))
        return 0

    if a.statusline:
        print(emit_statusline_json(a.providers, state_path))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())