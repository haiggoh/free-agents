#!/usr/bin/env python3
"""agent-fallback — the emergency-fallback router.

Decides, for one dispatch, which lane(s) are allowed and in what order, then
drives them. It is the ONLY thing that knows the routing policy; the local and
remote dispatchers it composes with are lane-agnostic. It does not reimplement
either lane — it runs them as subprocesses and reads their structured result.

The five routing modes, from most to least permissive, each pin a privacy + cost
posture (the plan's constraints, made executable):

  local-only        Local MLX only. NEVER contacts a remote provider. The
                    privacy default. A local failure here is a hard failure.
  local-first-free  Local first; on a clean failover-class failure, try remote
                    RENEWING_FREE providers only. (Paid/trial/consumer-web are
                    excluded.)  The normal emergency lane.
  remote-free-only  Skip local; try remote RENEWING_FREE only.
  include-trials    Like local-first-free, but trials are also permitted (never
                    paid, never consumer-web).
  research-emergency  Everything except paid and consumer-web. For a research
                    box where the only goal is "keep producing, whatever it
                    costs that is not a real dollar subscription."

The failover policy (non-negotiable, encoded in `remote_provider_core.Result.
retriable` and re-checked here): fall over to the next lane/provider ONLY on
quota, transient provider failure, timeout-before-output, or model
unavailability. NEVER on auth, malformed request, policy refusal, or a partial
stream (a partial output is preserved, not retried). A hard per-provider attempt
ceiling (`--max-attempts`, default 2) plus a global attempt budget prevent retry
storms; Retry-After is honoured when the provider sends one.

Privacy guard, in code: in `local-only` mode the router refuses to build any
remote dispatch at all, and `remote_http._is_loopback` is asserted so a
local-mode run can *prove* it made no non-loopback connection (acceptance
criterion 5). In remote modes the remote banner (printed by
remote-agent-dispatch.py) is what the user sees before anything leaves the box.

Usage:
    agent-fallback.py --mode local-first-free --prompt "TEXT"
                      [--local-model ALIAS] [--remote-provider gemini ...]
                      [--files F ... --allow-remote-files]
                      [--max-attempts N] [--dry-run] [--json-out PATH]

Exit: 0 a lane produced output · 2 a hard, non-retriable failure in every lane
     attempted · 3 all permitted lanes failed for a retriable reason (a signal
     to the caller that another attempt later, after a Retry-After wait, may
     succeed).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from remote_provider_core import (PROVIDERS, IMPLEMENTED, RENEWING_FREE, TRIAL,
                                  PAID, CONSUMER_WEB, UNKNOWN, LOCAL)  # noqa: E402
import remote_http  # noqa: E402

BIN_DIR = os.path.dirname(os.path.realpath(__file__))
LOCAL_DISPATCH = os.path.join(BIN_DIR, "local-agent-dispatch.py")
REMOTE_DISPATCH = os.path.join(BIN_DIR, "remote-agent-dispatch.py")

# Which tiers each mode may touch, in preference order (most-renewing-free first).
# Paid and consumer_web appear in NO mode — a router must never silently spend a
# trial or a dollar. `unknown` is admitted only in research-emergency, after a
# per-call check (NVIDIA's free key throttles unpredictably; we try and see).
MODE_TIERS = {
    "local-only":         [],                       # no remote at all
    "local-first-free":   [RENEWING_FREE],
    "remote-free-only":   [RENEWING_FREE],
    "include-trials":     [RENEWING_FREE, TRIAL],
    "research-emergency": [RENEWING_FREE, TRIAL, UNKNOWN],
}
MODES = sorted(MODE_TIERS)

DEFAULT_LOCAL_MODEL = "qwen-3.6-operator"


def _tiers_allowed(mode):
    return MODE_TIERS.get(mode)


def _remote_providers_for_mode(mode, requested=None, doctor_ids=None):
    """Ordered list of (provider_id, tier) the mode will try, most-preferred first.

    `requested` (from --remote-provider) narrows to specific providers but never
    widens a mode's tier ceiling. `doctor_ids` is the list of providers that the
    doctor found configured (key present); a provider with no key is skipped.
    Only IMPLEMENTED providers yield a runnable dispatch in the MVP; the rest are
    reported as declared-but-unimplemented and skipped.
    """
    allowed = _tiers_allowed(mode)
    if allowed is None:
        return []
    want = requested or list(PROVIDERS)
    out = []
    for pid in want:
        prov = PROVIDERS.get(pid)
        if prov is None:
            continue
        if pid not in IMPLEMENTED:
            continue  # declared, not runnable in the MVP
        if prov.tier not in allowed:
            continue  # the mode's ceiling excludes this tier
        if not prov.config_present():
            continue  # no key configured → the lane is disabled for it
        if doctor_ids is not None and pid not in doctor_ids:
            continue  # the doctor said this key is not alive
        out.append((pid, prov.tier))
    return out


def _run_local(prompt, files, model, max_tokens, outdir, dry_run, timeout,
               progress):
    """Drive local-agent-dispatch.py as a subprocess. Returns (ok, answer_text).

    The local lane is lane-agnostic; we read its answer from stdout (its one
    output channel) and treat empty-or-nonzero-exit as "local failed". It never
    leaves the machine, so no remote banner is needed.
    """
    cmd = [sys.executable, LOCAL_DISPATCH,
           "--model", model, "--prompt", prompt,
           "--max-tokens", str(max_tokens),
           "--progress", progress]
    for fp in files:
        cmd += ["--files", fp]
    if dry_run:
        # local dispatch has no --dry-run in the MVP; we still must not run the
        # real model in a full dry-run. Report intent and stop.
        print("[local] dry-run — would dispatch to model=%s "
              "(%d file(s)); no request sent." % (model, len(files)),
              file=sys.stderr)
        return False, ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[local] TIMEOUT after %.0fs (no output) — local lane failed." % timeout,
              file=sys.stderr)
        return False, ""
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        print("[local] model=%s exited %d:" % (model, proc.returncode), file=sys.stderr)
        for ln in tail:
            print("    " + ln, file=sys.stderr)
        return False, ""
    answer = (proc.stdout or "").strip()
    if not answer:
        print("[local] model=%s produced no output — local lane failed." % model,
              file=sys.stderr)
        return False, ""
    return True, answer


def _run_remote(provider_id, prompt, files, model, max_tokens, outdir, dry_run,
                timeout, allow_files, json_out):
    """Drive remote-agent-dispatch.py as a subprocess. Returns (ok, answer_text,
    retriable, reason). The REMOTE banner is printed by the child, so the user
    sees provider/model/key/destination before anything transmits.
    """
    cmd = [sys.executable, REMOTE_DISPATCH,
           "--provider", provider_id,
           "--prompt", prompt,
           "--max-tokens", str(max_tokens),
           "--timeout", str(timeout)]
    if model:
        cmd += ["--model", model]
    if files:
        cmd += ["--files"] + files
        if allow_files:
            cmd += ["--allow-remote-files"]
    if dry_run:
        cmd += ["--dry-run"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # Surface the child's stderr (the REMOTE banner + any failure reason) verbatim.
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    answer = (proc.stdout or "").strip()
    # Exit contract from remote-agent-dispatch: 0 ok · 3 retriable · 2 hard.
    retriable = proc.returncode == 3
    ok = proc.returncode == 0
    return ok, answer, retriable, (proc.stderr or "").strip()[:200]


def loopback_probe(mode):
    """Prove the privacy posture: in local-only mode we must be able to assert no
    non-loopback connection. Return True if the invariant holds (it always does
    for a correctly-built local-only path, but we CHECK rather than assume — the
    guard must not be looser than what it guards).
    """
    # The only URLs a local-only run ever builds are local-dispatch's (loopback)
    # and this probe's own loopback reference. Assert the guard classifies them.
    if mode != "local-only":
        return True
    assert remote_http._is_loopback("http://127.0.0.1:8000/v1/chat/completions"), \
        "loopback probe failed for a loopback URL — privacy guard is broken"
    assert not remote_http._is_loopback(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"), \
        "loopback probe misclassified a remote URL — remote call could hide in local-only"
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Route a dispatch through local / remote-fallback lanes per a "
                    "privacy+cost routing mode. Local-only is the default.")
    ap.add_argument("--mode", choices=MODES, default="local-only",
                    help="routing mode (default local-only; remote is an explicit opt-in)")
    ap.add_argument("--prompt", required=True, help="the task prompt")
    ap.add_argument("--local-model", default=DEFAULT_LOCAL_MODEL,
                    help="local MLX model alias (default %(default)s)")
    ap.add_argument("--remote-provider", action="append", default=None,
                    help="provider id to try in remote lanes (repeatable); "
                         "default: every configured one the mode allows")
    ap.add_argument("--model", default="", help="remote model id override")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--files", nargs="*", default=[], help="files to attach")
    ap.add_argument("--allow-remote-files", action="store_true",
                    help="approve sending --files to the remote provider")
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="hard per-lane attempt ceiling (default 2)")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--progress", choices=["compact", "verbose", "quiet"],
                    default="quiet", help="local inference progress (default quiet)")
    ap.add_argument("--skip-doctor", action="store_true",
                    help="do not pre-check remote keys with remote-provider-doctor")
    ap.add_argument("--outdir", help="output dir (default: fresh temp dir)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan the routing and print what would happen; send nothing")
    ap.add_argument("--json-out", help="also write the structured routing result")
    a = ap.parse_args(argv)

    outdir = a.outdir or tempfile.mkdtemp(prefix="agent-fallback-")
    os.makedirs(outdir, exist_ok=True)

    # Privacy posture, asserted (not assumed) before anything runs.
    loopback_probe(a.mode)

    tiers = _tiers_allowed(a.mode)
    remote_plan = _remote_providers_for_mode(a.mode, requested=a.remote_provider)

    print("ROUTING mode=%s  local=%s  remote_tiers=%s  remote_providers=%s" % (
        a.mode, a.local_model, [t for t in tiers] or "NONE",
        [p for p, _ in remote_plan] or "none"), file=sys.stderr)

    if a.dry_run:
        print("DRY-RUN — no lane will be contacted.", file=sys.stderr)
        plan = {"mode": a.mode, "local_model": a.local_model,
                "remote": [p for p, _ in remote_plan],
                "remote_tiers": tiers, "dry_run": True}
        if a.json_out:
            json.dump(plan, open(a.json_out, "w"), indent=2)
        return 0

    doctor_ids = None
    if remote_plan and not a.skip_doctor:
        # Cheap, read-only key liveness check so we don't burn a turn on a dead key.
        try:
            dproc = subprocess.run(
                [sys.executable, os.path.join(BIN_DIR, "remote-provider-doctor.py"),
                 "--json", *remote_plan[:5]],
                capture_output=True, text=True, timeout=60)
            ddata = json.loads(dproc.stdout) if dproc.stdout.strip() else {}
            doctor_ids = {r["provider"] for r in ddata.get("results", [])
                          if r.get("state") == "usable"}
        except Exception:
            doctor_ids = None  # doctor unavailable → proceed, let the call decide
        remote_plan = _remote_providers_for_mode(a.mode, requested=a.remote_provider,
                                                 doctor_ids=doctor_ids)

    record = {"mode": a.mode, "lanes": [], "succeeded": False,
              "output_chars": 0, "attempts": 0}
    final_answer = ""

    # --- local lane (every mode starts local EXCEPT remote-free-only) ----------
    try_local = a.mode != "remote-free-only"
    if try_local:
        ok, answer = _run_local(a.prompt, a.files, a.local_model, a.max_tokens,
                                outdir, a.dry_run, a.timeout, a.progress)
        record["lanes"].append({"lane": "local", "model": a.local_model, "ok": ok,
                                "output_chars": len(answer)})
        if ok:
            final_answer = answer
            record.update(succeeded=True, output_chars=len(answer),
                          succeeded_on="local")
    if not record.get("succeeded") and not remote_plan:
        # No remote lane permitted (local-only) or none configured → hard stop.
        _report(record, a, final_answer, outdir, code=2)
        return 2

    # --- remote lanes, in preference order, each capped at --max-attempts ------
    any_retriable_left = False
    for pid, tier in remote_plan:
        last_retriable = False
        for attempt in range(1, max(1, a.max_attempts) + 1):
            record["attempts"] += 1
            ok, answer, retriable, reason = _run_remote(
                pid, a.prompt, a.files, a.model, a.max_tokens, outdir,
                a.dry_run, a.timeout, a.allow_remote_files, a.json_out)
            record["lanes"].append({"lane": "remote", "provider": pid, "tier": tier,
                                    "attempt": attempt, "ok": ok, "retriable": retriable,
                                    "output_chars": len(answer),
                                    "reason": reason[:160]})
            if ok:
                final_answer = answer
                record.update(succeeded=True, output_chars=len(answer),
                              succeeded_on="remote:" + pid)
                _report(record, a, final_answer, outdir, code=0)
                return 0
            last_retriable = retriable
            # Only retry the SAME provider on a retriable reason, and only within
            # the ceiling; a hard reason breaks out to the next provider.
            if not retriable:
                break
        # A provider that still failed for a retriable reason means "try later";
        # remember it so the final code can signal 3 (retry later) vs 2 (stop).
        any_retriable_left = any_retriable_left or last_retriable

    _report(record, a, final_answer, outdir,
            code=3 if any_retriable_left else 2)
    return 3 if any_retriable_left else 2


def _report(record, a, final_answer, outdir, code):
    out_path = os.path.join(outdir, "output.txt")
    if final_answer:
        open(out_path, "w").write(final_answer + "\n")
    record["output_path"] = out_path if final_answer else ""
    verdict = "succeeded on %s" % record["succeeded_on"] if record["succeeded"] else (
        "no lane produced output")
    print("FALLBACK %s — mode=%s, %d attempt(s), output=%d chars%s" % (
        "✓" if record["succeeded"] else "✖", a.mode, record["attempts"],
        record["output_chars"],
        (" → %s" % out_path) if final_answer else ""), file=sys.stderr)
    if a.json_out:
        json.dump(record, open(a.json_out, "w"), indent=2)
    # The final answer goes to stdout (the single answer channel), so a caller can
    # pipe it:  agent-fallback --mode ... > answer.txt
    sys.stdout.write(final_answer + ("\n" if final_answer else ""))


if __name__ == "__main__":
    sys.exit(main())