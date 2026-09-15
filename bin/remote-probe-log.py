#!/usr/bin/env python3
"""remote-probe-log.py - accumulate real generation attempts against remote model routes.

WHY THIS EXISTS, and the rule it encodes: a single success or a single failure proves
nothing about a remote lane. NVIDIA returned a healthy catalog and a 503 "Service
temporarily overloaded" within minutes of each other on 2026-09-15; either observation
alone would have been misleading. So this tool NEVER issues a verdict from one run. It
appends timestamped attempts to a JSONL log and reports RATES over accumulated history,
with an explicit "insufficient evidence" state until a route has enough samples.

What it tests that a catalog listing cannot: catalog presence is not generation access,
and NVIDIA reports tools:"unknown" for every row, so tool capability is unknowable from
the catalog. This sends a real (tiny) generation request and, with --tools, a real
tool-call request, which is the only thing that answers those questions.

Usage:
  remote-probe-log.py probe <alias> [<alias>...]   attempt each route once, append results
  remote-probe-log.py probe --all-nvidia           attempt every nvidia route once
  remote-probe-log.py report [--provider P]        rates over accumulated history
  remote-probe-log.py report --json                machine-readable
  remote-probe-log.py --help

RATE LIMITING - read this before raising any concurrency here. NVIDIA's limit is NOT a
daily quota (that is the point of preferring it); it is a REQUESTS-PER-MINUTE ceiling,
measured at 40/min. So probing many routes must be PACED, never parallel and never a
burst: this tool sleeps between attempts and refuses a delay that could exceed the
ceiling. A burst does not merely get throttled, it poisons the evidence - a 429 recorded
against a model says nothing about that model, only about our own request rate.

Environment:
  LA_PROBE_LOG        history file (default ~/.claude/logs/remote-probe-history.jsonl)
  LA_PROBE_MIN_RUNS   samples required before a verdict is offered (default 5)
  LA_PROBE_DELAY_S    seconds between attempts (default 2.0; floor 1.5 = 40/min)
  LA_PROBE_RPM_LIMIT  provider requests-per-minute ceiling to respect (default 40)
  LA_API_KEYS_DIR     credential dir (default ~/.api_keys)

Cost: each probe spends a HANDFUL of tokens on the provider's free allowance (max_tokens
is deliberately tiny). It spends no Anthropic gateway budget.
"""
import argparse, datetime, json, os, pathlib, subprocess, sys, time, urllib.error, urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
ROSTER = REPO / "config" / "remote-agents.sh"
DEFAULT_LOG = pathlib.Path(os.environ.get(
    "LA_PROBE_LOG", os.path.expanduser("~/.claude/logs/remote-probe-history.jsonl")))
MIN_RUNS = int(os.environ.get("LA_PROBE_MIN_RUNS", "5"))
# NVIDIA's real constraint is 40 requests/MINUTE, not a daily quota. Pacing is therefore a
# correctness requirement, not politeness: a burst earns 429s that get recorded against
# individual models and corrupt the very history this tool exists to accumulate.
RPM_LIMIT = float(os.environ.get("LA_PROBE_RPM_LIMIT", "40"))
MIN_DELAY = 60.0 / RPM_LIMIT if RPM_LIMIT > 0 else 1.5
DELAY_S = max(float(os.environ.get("LA_PROBE_DELAY_S", "2.0")), MIN_DELAY)

ENDPOINTS = {   # provider -> (url, key-file, auth style)
    "nvidia": ("https://integrate.api.nvidia.com/v1/chat/completions", "nvidia", "bearer"),
    "groq":   ("https://api.groq.com/openai/v1/chat/completions", "groq", "bearer"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini", "bearer"),
}

def roster():
    """Rows from the shell roster, so this tool never keeps a second copy of the list."""
    out = subprocess.run(["bash", "-c", f'source "{ROSTER}"; printf "%s\\n" "${{LA_REMOTE_AGENTS[@]}}"'],
                         capture_output=True, text=True)
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("|")
        if len(parts) >= 5:
            rows.append({"alias": parts[0], "provider": parts[1], "model": parts[2], "tier": parts[4]})
    return rows

def read_key(name):
    d = pathlib.Path(os.environ.get("LA_API_KEYS_DIR", os.path.expanduser("~/.api_keys")))
    p = d / name
    if not p.is_file():
        return None
    return p.read_text().strip().splitlines()[0].strip()

def probe_one(row, want_tools=False, timeout=90):
    """One real generation attempt. Returns a result record; never raises."""
    prov = row["provider"]
    rec = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "alias": row["alias"], "provider": prov, "model": row["model"],
           "kind": "tools" if want_tools else "chat"}
    if prov not in ENDPOINTS:
        rec.update(ok=False, status="unsupported", detail="no probe endpoint for this provider")
        return rec
    url, keyfile, _ = ENDPOINTS[prov]
    key = read_key(keyfile)
    if not key:
        rec.update(ok=False, status="nokey", detail=f"no credential at {keyfile}")
        return rec
    body = {"model": row["model"], "max_tokens": 24, "temperature": 0,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}]}
    if prov == "nvidia":
        # NIM reasoning models return reasoning_content, which is not an Anthropic thinking
        # block; the session-side fix disables it, so probe the same configuration we ship.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if want_tools:
        body["messages"] = [{"role": "user", "content": "Call the ping tool with value 1."}]
        body["tools"] = [{"type": "function", "function": {
            "name": "ping", "description": "ping",
            "parameters": {"type": "object", "properties": {"value": {"type": "integer"}},
                           "required": ["value"]}}}]
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json",
                                         "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("error", {})
            detail = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        except Exception:
            pass
        # A 429 is a statement about OUR request rate, not about this model. Tag it
        # distinctly so `report` can exclude it from a route's success rate instead of
        # letting our own pacing mistake look like a broken model.
        status = "rate_limited" if e.code == 429 else f"http{e.code}"
        rec.update(ok=False, status=status, detail=str(detail)[:200])
        return rec
    except Exception as e:                     # timeout, DNS, TLS, malformed body
        rec.update(ok=False, status="error", detail=f"{type(e).__name__}: {e}"[:200])
        return rec
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        rec.update(ok=False, status="badshape", detail=str(payload)[:200])
        return rec
    if want_tools:
        calls = msg.get("tool_calls") or []
        rec.update(ok=bool(calls), status="toolcall" if calls else "notoolcall",
                   detail=(calls[0].get("function", {}).get("name") if calls else
                           str(msg.get("content"))[:120]))
    else:
        text = (msg.get("content") or "").strip()
        rec.update(ok=bool(text), status="ok" if text else "empty", detail=text[:120])
    return rec

def append(recs, logpath):
    logpath.parent.mkdir(parents=True, exist_ok=True)
    with open(logpath, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

def history(logpath):
    if not logpath.is_file():
        return []
    rows = []
    for line in logpath.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn final line must not lose the whole history
    return rows

def report(rows, provider=None):
    agg = {}
    for r in rows:
        if provider and r.get("provider") != provider:
            continue
        k = (r.get("alias"), r.get("kind"))
        a = agg.setdefault(k, {"runs": 0, "ok": 0, "last": None, "statuses": {},
                               "excluded": 0})
        # Our own rate-limit hits are not evidence about the ROUTE, so they stay in the
        # status breakdown (visible) but leave the rate's denominator alone.
        if r.get("status") == "rate_limited":
            a["excluded"] += 1
        else:
            a["runs"] += 1
            a["ok"] += 1 if r.get("ok") else 0
        a["last"] = r.get("at")
        s = r.get("status", "?")
        a["statuses"][s] = a["statuses"].get(s, 0) + 1
    return agg

def verdict(a):
    """A rate, and an explicit refusal to conclude from too few samples."""
    if a["runs"] == 0 and a.get("excluded"):
        return f"no usable samples ({a['excluded']} rate-limited)"
    if a["runs"] < MIN_RUNS:
        return f"insufficient evidence ({a['runs']}/{MIN_RUNS} runs)"
    rate = a["ok"] / a["runs"]
    if rate >= 0.9:  return f"reliable ({a['ok']}/{a['runs']})"
    if rate >= 0.5:  return f"FLAKY ({a['ok']}/{a['runs']})"
    if rate > 0:     return f"mostly failing ({a['ok']}/{a['runs']})"
    return f"failing ({a['ok']}/{a['runs']})"

def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("probe", add_help=False)
    p.add_argument("aliases", nargs="*")
    p.add_argument("--all-nvidia", action="store_true")
    p.add_argument("--tools", action="store_true", help="probe tool-calling instead of plain chat")
    p.add_argument("--delay", type=float, default=DELAY_S,
                   help=f"seconds between attempts (floored at {MIN_DELAY:.1f}s = "
                        f"{RPM_LIMIT:.0f}/min)")
    p.add_argument("--log", default=str(DEFAULT_LOG))
    r = sub.add_parser("report", add_help=False)
    r.add_argument("--provider")
    r.add_argument("--json", action="store_true")
    r.add_argument("--log", default=str(DEFAULT_LOG))
    args, unknown = ap.parse_known_args(argv)
    if args.help or not args.cmd or unknown:
        print(__doc__)
        return 2 if unknown else 0

    if args.cmd == "probe":
        rows = roster()
        if args.all_nvidia:
            targets = [x for x in rows if x["provider"] == "nvidia"]
        else:
            byalias = {x["alias"]: x for x in rows}
            targets = []
            for a in args.aliases:
                if a not in byalias:
                    print(f"unknown alias: {a}", file=sys.stderr); return 2
                targets.append(byalias[a])
        if not targets:
            print("nothing to probe; give an alias or --all-nvidia", file=sys.stderr); return 2
        recs = []
        delay = max(args.delay, MIN_DELAY)
        if len(targets) > 1:
            print(f"  pacing {len(targets)} probes at {delay:.1f}s apart "
                  f"(provider ceiling {RPM_LIMIT:.0f} req/min) — "
                  f"~{len(targets) * delay / 60:.1f} min total\n")
        for i, t in enumerate(targets):
            if i:
                time.sleep(delay)
            rec = probe_one(t, want_tools=args.tools)
            recs.append(rec)
            mark = "✓" if rec["ok"] else "✗"
            print(f"  {mark} {t['alias']:<24} {rec['status']:<12} {str(rec.get('detail',''))[:70]}")
        append(recs, pathlib.Path(args.log))
        print(f"\n{len(recs)} attempt(s) appended to {args.log}")
        print("ONE run is not evidence — `report` aggregates; a verdict needs "
              f"{MIN_RUNS}+ samples per route.")
        return 0

    agg = report(history(pathlib.Path(args.log)), args.provider)
    if args.json:
        print(json.dumps({f"{k[0]}::{k[1]}": {**v, "verdict": verdict(v)}
                          for k, v in agg.items()}, indent=2))
        return 0
    if not agg:
        print("no probe history yet. Run: remote-probe-log.py probe --all-nvidia")
        return 0
    print(f"\n  Remote route history  (verdict needs {MIN_RUNS}+ runs; "
          "one result never decides)\n")
    print(f"  {'ALIAS':<26}{'KIND':<7}{'VERDICT':<32}{'LAST SEEN':<22}STATUSES")
    for (alias, kind), a in sorted(agg.items()):
        st = ",".join(f"{k}:{v}" for k, v in sorted(a["statuses"].items()))
        print(f"  {alias:<26}{kind:<7}{verdict(a):<32}{(a['last'] or '?'):<22}{st}")
    print()
    return 0

if __name__ == "__main__":
    sys.exit(main())
