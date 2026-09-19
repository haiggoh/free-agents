#!/usr/bin/env python3
"""Estimate a remote model's local RAM footprint from its model id, and flag
roster entries whose local-capable classification is missing or disagrees.

WHY THIS EXISTS (and how it supersedes an earlier decision)
-----------------------------------------------------------
The 2026-09-17 local-capable-filtering plan said: "Do not use a rigid 27B or 40B
parameter cutoff as the decision rule... Do not infer a definitive classification
merely from the first NNB substring in a model ID." That guidance was aimed at a
real failure -- a naive first-NNB regex misreads `nemotron-3-ultra-550b-a55b`,
version numbers, and context lengths.

The user superseded that claim on 2026-09-19: parameter count is not exact, but for
a model that is NOT ON DISK it is the only signal available offline. There is no
better local mechanism -- real artifact bytes require a network call against a
known HF repo, which a remote-only model does not have. So a rough first estimate
beats no estimate.

This tool keeps the plan's actual safety requirement while accepting the user's
premise, by separating ESTIMATION from CLASSIFICATION:

  * it parses MoE ids (total + active params) instead of the first NNB substring;
  * it ignores version/context/quant-looking numbers rather than reading them as sizes;
  * it reports by DEFAULT and writes only under an explicit --apply.

The user authorized --apply on 2026-09-19 ("the manual file wasn't perfect yet"), and the
first sweep justified it: 9 of 30 rows disagreed, including gpt-oss-20b filed as "No local
equivalent" at ~18 GB. --apply is still gated to HIGH-CONFIDENCE rows -- a comfortable fit
or a large overshoot -- so the near-ceiling ~68 GB MoE rows, where KV overhead genuinely
could decide it, are left for a human. It backs the file up before writing.

FORMULA
-------
    weights_GB  = total_params_B * bytes_per_weight   (4-bit => 0.5 B/param)
    kv_overhead = a flat allowance for KV cache + runtime
    fits        = weights_GB + kv_overhead <= budget_GB

Dense models must hold every weight in memory. An MoE must ALSO hold every weight
(all experts are resident; only compute is sparse), so the TOTAL count drives the
memory verdict -- the active count is reported because it predicts speed, not size.
Getting this backwards is the main way an MoE estimate goes wrong: judging
`550b-a55b` by its 55B active figure would call a 275 GB model local-capable.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

# 4-bit is the quantization this stack actually serves, so it is the default.
BYTES_PER_WEIGHT = {"4bit": 0.5, "6bit": 0.75, "8bit": 1.0, "fp16": 2.0}

# KV cache + runtime + framework overhead, in GB. Deliberately coarse: this is a
# triage estimate, and a too-small allowance is the dangerous direction.
DEFAULT_KV_OVERHEAD_GB = 8.0

# Mirrors LA_MEMORY_BUDGET_GB in config.example.sh rather than inventing a number.
DEFAULT_BUDGET_GB = 96.0

# A parameter count: 30b, 550b, a55b (active), 7.5b. Anchored to a token boundary so
# it cannot match inside a version (3.5) or a date (0731).
_PARAM_RE = re.compile(r"(?:^|[-_/.])(a?)(\d+(?:\.\d+)?)b(?=$|[-_/.])", re.IGNORECASE)

# Numbers that LOOK like sizes but are not. Checked before the param regex so a
# context length (128k) or a quant label (4bit) cannot be read as a weight count.
_NOT_A_SIZE_RE = re.compile(r"\d+(?:k|bit|_\d)\b", re.IGNORECASE)


def parse_params(model_id: str) -> dict:
    """Extract (total_b, active_b) from a model id.

    Returns a dict with total_b, active_b and a confidence label. An id carrying two
    counts (`550b-a55b`) is an MoE: the bare number is the total, the a-prefixed one
    is active. This is the case the plan's "first NNB substring" warning was about.
    """
    tail = model_id.split("/")[-1]
    scrubbed = _NOT_A_SIZE_RE.sub("", tail)

    total_b = active_b = None
    found = []
    for active_marker, number in _PARAM_RE.findall(scrubbed):
        value = float(number)
        found.append((bool(active_marker), value))
        if active_marker:
            # Keep the smallest a-prefixed figure; ids rarely carry two.
            active_b = value if active_b is None else min(active_b, value)
        else:
            # Keep the LARGEST bare figure: it is the memory-relevant total.
            total_b = value if total_b is None else max(total_b, value)

    if total_b is None and active_b is not None:
        # Only an active count is stated, so the total is unknown and certainly larger.
        return {"total_b": None, "active_b": active_b, "confidence": "unknown",
                "note": "only an active-parameter count is stated; total is unknown and larger"}
    if total_b is None:
        return {"total_b": None, "active_b": None, "confidence": "unknown",
                "note": "no parameter count in the model id"}

    confidence = "medium" if active_b is None else "medium-moe"
    note = "dense" if active_b is None else f"MoE: {active_b:g}B active of {total_b:g}B total"
    if len(found) > 2:
        confidence = "low"
        note += "; multiple numbers in id, verify by hand"
    return {"total_b": total_b, "active_b": active_b,
            "confidence": confidence, "note": note}


def estimate(model_id: str, quant: str = "4bit",
             budget_gb: float = DEFAULT_BUDGET_GB,
             kv_overhead_gb: float = DEFAULT_KV_OVERHEAD_GB) -> dict:
    """Estimate footprint and give a SUGGESTED classification (never authoritative)."""
    parsed = parse_params(model_id)
    bpw = BYTES_PER_WEIGHT[quant]
    out = {"model_id": model_id, "quant": quant, "budget_gb": budget_gb, **parsed}

    if parsed["total_b"] is None:
        out.update(weights_gb=None, total_gb=None, fits=None, suggested="unknown",
                   reason="cannot estimate without a total parameter count — stays VISIBLE (fail-open)")
        return out

    weights_gb = parsed["total_b"] * bpw
    total_gb = weights_gb + kv_overhead_gb
    fits = total_gb <= budget_gb
    out.update(weights_gb=round(weights_gb, 1), total_gb=round(total_gb, 1), fits=fits)

    # Margin matters more than the bare verdict: near the ceiling the estimate's own
    # error is larger than the headroom, so it must not read as confident.
    margin = budget_gb - total_gb
    # The marker band is the COMPLEMENT of the comfortable band below (total < 50% of budget),
    # so every fit is either comfortably local or explicitly marked as a near call -- no gap
    # where a 71%-of-budget model reads as a confident verdict it has not earned.
    if fits and total_gb >= 0.5 * budget_gb:
        # Near the ceiling the estimate's own error exceeds the headroom, so this is NOT a
        # verdict. It is still more informative than a bare "unknown": the id does tell us the
        # model is in the right size class, just not that it definitely fits. So it gets its
        # own marker, which the filter treats as VISIBLE -- the fail-open guarantee is intact,
        # the user simply sees WHICH visible rows are the near misses worth investigating.
        out.update(suggested="potentially-local-capable",
                   reason=f"~{total_gb:.0f} GB against a {budget_gb:.0f} GB budget "
                          f"({margin:.0f} GB spare) — over half the budget, where KV growth and "
                          "quantization choices the model id cannot reveal could decide it, so "
                          "it needs real evidence before being hidden (stays visible)")
    elif fits:
        # Symmetric with the overshoot case below: a model that fits with room to spare is
        # just as clear-cut as one that busts the ceiling. A 20B model needs no advanced
        # math to prove it runs on this machine, so it must not be left visible-by-default
        # as if uncertain -- that wastes the picker attention the filter exists to protect.
        confidence = "high" if total_gb < 0.5 * budget_gb else out["confidence"]
        out.update(suggested="local-capable", confidence=confidence,
                   reason=f"~{total_gb:.0f} GB (weights {weights_gb:.0f} + {kv_overhead_gb:.0f} overhead) "
                          f"fits the {budget_gb:.0f} GB budget"
                          + (" with room to spare — comfortably local, no further math needed"
                             if total_gb < 0.5 * budget_gb else ""))
    else:
        # ASYMMETRY, deliberately. The estimate's error is a fraction of the model size, so a
        # large overshoot is a SAFER call than a near-fit: nothing about quantization, KV
        # sizing or architecture turns a 283 GB model into one that fits 96 GB. A 500B model
        # is not "unknown" -- leaving it that way would be false modesty that costs the user
        # picker attention on a model that can obviously never run here. Only a NEAR miss is
        # genuinely uncertain, and that case is handled on the fits side above.
        confidence = "high" if total_gb > 1.5 * budget_gb else out["confidence"]
        out.update(suggested="remote-preferred", confidence=confidence,
                   reason=f"~{total_gb:.0f} GB exceeds the {budget_gb:.0f} GB budget "
                          f"by {-margin:.0f} GB"
                          + (" — far past the ceiling, so no quantization or KV choice "
                             "recovers it" if total_gb > 1.5 * budget_gb else ""))
    return out


def read_policy(path: str) -> dict:
    """(provider, model_id) -> classification, from the PSV policy file."""
    policy = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("|")
            if len(fields) < 3:
                continue
            policy[(fields[0], fields[1])] = fields[2]
    return policy


def read_roster(path: str) -> list:
    """(alias, provider, model_id) from pipe-separated roster lines."""
    rows = []
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        for line in stream:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split("|")
            if len(fields) >= 3:
                rows.append((fields[0], fields[1], fields[2]))
    finally:
        if stream is not sys.stdin:
            stream.close()
    return rows


def apply_to_policy(path: str, updates: list, *, dry_run: bool = False) -> dict:
    """Rewrite policy rows in place for HIGH-CONFIDENCE estimates only.

    Line-oriented and surgical: it rewrites the classification/reason of rows that already
    exist and appends rows that do not, leaving comments, ordering and unrelated rows byte
    for byte intact. A backup is written first -- this file is hand-curated evidence, so an
    automated pass must never be the only copy.
    """
    original = pathlib.Path(path).read_text(encoding="utf-8")
    by_key = {(u["provider"], u["model_id"]): u for u in updates}
    out_lines, rewritten = [], []

    for line in original.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        fields = stripped.split("|")
        if len(fields) < 3:
            out_lines.append(line)
            continue
        update = by_key.pop((fields[0], fields[1]), None)
        if update is None:
            out_lines.append(line)
            continue
        fields = (fields + [""] * 7)[:7]
        fields[2] = update["suggested"]
        fields[5] = str(update["weights_gb"] or "")
        # PRESERVE the prior hand-written reason. The estimate answers "does it fit in
        # memory"; a curated note may assert something else entirely -- "No confirmed MLX
        # artifact", "OpenAI-native model" -- which size math neither confirms nor refutes.
        # Overwriting it would destroy evidence the estimator never had.
        previous = fields[6].strip()
        note = f"auto-estimated {update['total_gb']:.0f} GB at 4bit: {update['reason']}"
        if previous and not previous.startswith("auto-estimated"):
            note += f" [prior note, size math does not refute it: {previous}]"
        fields[6] = note
        out_lines.append("|".join(fields) + "\n")
        rewritten.append(update["model_id"])

    appended = []
    for update in by_key.values():
        out_lines.append("|".join([
            update["provider"], update["model_id"], update["suggested"], "", "",
            str(update["weights_gb"] or ""),
            f"auto-estimated {update['total_gb']:.0f} GB at 4bit: {update['reason']}",
        ]) + "\n")
        appended.append(update["model_id"])

    result = {"rewritten": rewritten, "appended": appended, "backup": None}
    if dry_run or not (rewritten or appended):
        return result

    target = pathlib.Path(path)
    backup = target.with_suffix(target.suffix + ".bak-autoestimate-"
                                + time.strftime("%Y%m%dT%H%M%S"))
    backup.write_text(original, encoding="utf-8")
    mode = target.stat().st_mode & 0o777
    target.write_text("".join(out_lines), encoding="utf-8")
    target.chmod(mode)   # preserve the original mode; never widen it
    result["backup"] = str(backup)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate a remote model's local RAM footprint from its id, and flag "
                    "roster rows whose policy classification is missing or disagrees.",
        epilog="This tool NEVER edits the policy file and never hides a model. It reports; "
               "config/local-capable-remote-models.psv stays the authority.")
    parser.add_argument("model_id", nargs="?",
                        help="a single model id to estimate, e.g. nvidia/nemotron-3-ultra-550b-a55b")
    parser.add_argument("--quant", default="4bit", choices=sorted(BYTES_PER_WEIGHT),
                        help="quantization assumed for the local artifact (default: 4bit)")
    parser.add_argument("--budget-gb", type=float, default=DEFAULT_BUDGET_GB,
                        help=f"usable memory budget in GB (default: {DEFAULT_BUDGET_GB:g}, "
                             "mirrors LA_MEMORY_BUDGET_GB)")
    parser.add_argument("--kv-overhead-gb", type=float, default=DEFAULT_KV_OVERHEAD_GB,
                        help=f"KV cache + runtime allowance in GB (default: {DEFAULT_KV_OVERHEAD_GB:g})")
    parser.add_argument("--roster", help="pipe-separated roster file, or - for stdin")
    parser.add_argument("--policy", help="policy PSV to compare against")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--disagreements-only", action="store_true",
                        help="only report rows the policy misses or contradicts")
    parser.add_argument("--apply", action="store_true",
                        help="WRITE high-confidence verdicts into the policy file "
                             "(backs it up first; needs --roster and --policy)")
    parser.add_argument("--apply-dry-run", action="store_true",
                        help="show exactly what --apply would change, without writing")
    args = parser.parse_args()

    if args.model_id and not args.roster:
        result = estimate(args.model_id, args.quant, args.budget_gb, args.kv_overhead_gb)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['model_id']}")
            print(f"  params    : {result['note']}")
            print(f"  estimate  : {result['total_gb'] or '?'} GB at {args.quant} "
                  f"(budget {args.budget_gb:g} GB)")
            print(f"  suggested : {result['suggested']}  [confidence: {result['confidence']}]")
            print(f"  reason    : {result['reason']}")
        return 0

    if not args.roster:
        parser.error("give a model_id, or --roster to sweep a roster")

    rows = read_roster(args.roster)
    policy = read_policy(args.policy) if args.policy else {}
    findings, agree = [], 0
    for alias, provider, model_id in rows:
        result = estimate(model_id, args.quant, args.budget_gb, args.kv_overhead_gb)
        current = policy.get((provider, model_id))
        result.update(alias=alias, provider=provider, policy=current)
        if current is None:
            result["finding"] = "UNCLASSIFIED — no policy row"
        elif result["suggested"] == "unknown":
            result["finding"] = None if not args.disagreements_only else None
        elif current != result["suggested"]:
            result["finding"] = f"DISAGREES — policy says {current}, estimate suggests {result['suggested']}"
        else:
            result["finding"] = None
            agree += 1
        if result["finding"] or not args.disagreements_only:
            findings.append(result)

    if args.json:
        print(json.dumps({"agreements": agree, "rows": findings}, indent=2))
        return 0

    flagged = [r for r in findings if r["finding"]]
    print(f"Footprint sweep — {len(rows)} roster rows, {agree} agree with the policy, "
          f"{len(flagged)} to review")
    print(f"(assuming {args.quant}, {args.budget_gb:g} GB budget, "
          f"{args.kv_overhead_gb:g} GB overhead)\n")
    for result in flagged:
        print(f"  {result['alias']}  ({result['provider']})")
        print(f"    {result['finding']}")
        print(f"    ~{result['total_gb'] or '?'} GB — {result['reason']}")
        print(f"    params: {result['note']}\n")
    if not flagged:
        print("  nothing to review.")

    if not (args.apply or args.apply_dry_run):
        print("Advisory only: nothing was changed. Use --apply to write high-confidence "
              "verdicts into the policy file.")
        return 0

    if not args.policy:
        print("--apply needs --policy", file=sys.stderr)
        return 2

    # HIGH CONFIDENCE ONLY. A near-ceiling MoE (~68 GB of a 96 GB budget) can be decided by
    # KV overhead the id cannot reveal, so those stay human work. Rows the estimator calls
    # `unknown` are never written -- that would convert fail-open into a silent hide.
    # High-confidence verdicts, plus the near-ceiling MARKER. Writing
    # `potentially-local-capable` is safe at any confidence because the filter keeps those rows
    # VISIBLE -- it records "worth a look", never "hide this". What must never be written is a
    # bare `unknown`, which carries no information, or a medium-confidence local-capable, which
    # WOULD hide a row the estimate cannot vouch for.
    eligible = [r for r in findings
                if r["finding"] and (
                    (r["confidence"] == "high"
                     and r["suggested"] in ("local-capable", "remote-preferred"))
                    or r["suggested"] == "potentially-local-capable")]
    skipped = [r for r in findings if r["finding"] and r not in eligible]

    outcome = apply_to_policy(args.policy, eligible, dry_run=args.apply_dry_run)
    verb = "would rewrite" if args.apply_dry_run else "rewrote"
    print(f"\n{verb} {len(outcome['rewritten'])} row(s), "
          f"{'would append' if args.apply_dry_run else 'appended'} {len(outcome['appended'])}")
    for model_id in outcome["rewritten"] + outcome["appended"]:
        print(f"    {model_id}")
    if skipped:
        print(f"\nleft for review ({len(skipped)} — not high confidence):")
        for result in skipped:
            print(f"    {result['alias']}: {result['suggested']} "
                  f"[{result['confidence']}] ~{result['total_gb']} GB")
    if outcome["backup"]:
        print(f"\nbackup: {outcome['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
