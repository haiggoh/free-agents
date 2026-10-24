#!/usr/bin/env python3
"""Framework-free tests for the local-offload savings ledger.

Usage: python3 tests/test_savings_ledger.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "savings-ledger.py")
spec = importlib.util.spec_from_file_location("sl", SCRIPT)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

passed = failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS:", label)
    else:
        failed += 1
        print("  FAIL:", label)


def run(argv, ledger_dir):
    # FREE_AGENTS_LEDGER_DIR since 0.32.1 — the old LOCAL_AGENTS_LEDGER_DIR name is no longer
    # read, and driving the subprocess with it silently recorded test events in the real ledger.
    env = dict(os.environ, FREE_AGENTS_LEDGER_DIR=ledger_dir)
    return subprocess.run([sys.executable, SCRIPT] + argv, capture_output=True,
                          text=True, env=env)


print("== normalize_model: the shapes a real cloud model id arrives in ==")
# Each of these is a form that a bare-name lookup misses. The [1m] case is the one that
# actually shipped as a 0.00 bug in an earlier tool here.
for raw, want in [
    ("claude-opus-5", "claude-opus-5"),
    ("claude-opus-5[1m]", "claude-opus-5"),
    ("anthropic.claude-opus-5", "claude-opus-5"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ("claude-opus-4-8-fast", "claude-opus-4-8"),
    ("CLAUDE-OPUS-5", "claude-opus-5"),
    ("  claude-opus-5  ", "claude-opus-5"),
    ("some/route/claude-sonnet-5", "claude-sonnet-5"),
]:
    check(sl.normalize_model(raw) == want, f"{raw!r} -> {want!r}")
check(sl.normalize_model("") == "" and sl.normalize_model(None) == "",
      "empty / None normalize to empty rather than raising")


print("== price: the central honesty rule ==")
rates, as_of, label = sl.load_rates()
saved, rin, rout, src = sl.price("claude-opus-5", 1_000_000, 1_000_000, rates, label)
check(saved == 30.0, "1M in + 1M out on claude-opus-5 = $5 + $25 = $30")
check(rin == 5.0 and rout == 25.0, "the rates used are recorded on the event")
check(src == label and as_of in src, "rate_source names the table AND its as-of date")

saved, rin, rout, src = sl.price("claude-opus-5[1m]", 1_000_000, 0, rates, label)
check(saved == 5.0, "a [1m]-suffixed id prices correctly — the regression that shipped as 0.00")

saved, rin, rout, src = sl.price("totally-made-up", 1_000_000, 1_000_000, rates, label)
check(saved is None, "an unknown model prices as None, NOT 0.0")
check(src == "unknown", "and rate_source says 'unknown' so a reader can tell")
check(rin is None and rout is None, "no rates are invented for an unknown model")

saved, _, _, _ = sl.price("claude-opus-5", 0, 0, rates, label)
check(saved == 0.0, "a genuinely zero-token dispatch IS 0.0 — distinct from None")


print("== record: writes an append-only event with every documented field ==")
d = tempfile.mkdtemp()
r = run(["record", "--model", "local-op", "--in", "9123", "--out", "412",
         "--priced-against", "claude-opus-5[1m]", "--session", "S1", "--tool", "t"], d)
check(r.returncode == 0, "record exits 0")
lines = open(os.path.join(d, "savings.jsonl")).read().strip().splitlines()
check(len(lines) == 1, "one line per dispatch")
e = json.loads(lines[0])
for key in ("ts", "session", "model", "input_tokens", "output_tokens", "priced_against",
            "rate_in", "rate_out", "rate_source", "saved_usd", "tool"):
    check(key in e, f"event carries {key}")
check(e["priced_against"] == "claude-opus-5", "priced_against is stored normalized")
check(e["ts"].endswith("Z"), "timestamp is UTC (Z-suffixed), so day grouping is unambiguous")

print("== record: the DEFAULT counterfactual is the primary spoof (0.33.0) ==")
d5 = tempfile.mkdtemp()
r5 = run(["record", "--model", "local-op", "--in", "1000000", "--out", "1000000",
          "--session", "S9"], d5)
check(r5.returncode == 0, "record without --priced-against exits 0")
e5 = json.loads(open(os.path.join(d5, "savings.jsonl")).read().strip().splitlines()[0])
check(e5["priced_against"] == "claude-opus-5-5",
      "the default is claude-opus-5-5 — the primary spoof, not the retired opus-5")
check(e5["saved_usd"] == 24.0,
      "1M in + 1M out at 5-5 rates = $4 + $20 = $24 (opus-5 would say $30)")

run(["record", "--model", "local-op", "--in", "1", "--out", "1", "--session", "S1"], d)
check(len(open(os.path.join(d, "savings.jsonl")).read().strip().splitlines()) == 2,
      "a second record APPENDS rather than replacing")


print("== record: an unpriced event is stored as null and announced ==")
d2 = tempfile.mkdtemp()
r = run(["record", "--model", "local-op", "--in", "100", "--out", "10",
         "--priced-against", "no-such-model"], d2)
check("UNPRICED" in r.stdout, "the CLI says out loud that the event is unpriced")
e = json.loads(open(os.path.join(d2, "savings.jsonl")).read().strip())
check(e["saved_usd"] is None, "saved_usd is null on disk, not 0")


print("== rollup: derived, grouped by (session, UTC day), never hand-maintained ==")
d3 = tempfile.mkdtemp()
for sess, n in (("A", 3), ("B", 2)):
    for _ in range(n):
        run(["record", "--model", "m", "--in", "1000", "--out", "100", "--session", sess], d3)
r = run(["rollup"], d3)
check(r.returncode == 0, "rollup exits 0")
roll = json.load(open(os.path.join(d3, "rollup.json")))
check(roll["contract"] == 1, "rollup declares a contract version")
check(roll["source_events"] == 5, "rollup records how many events it was built from")
by_session = {row["session"]: row for row in roll["rows"]}
check(by_session["A"]["events"] == 3 and by_session["B"]["events"] == 2,
      "grouped per session")
check(all(len(row["day"]) == 10 for row in roll["rows"]), "each row is keyed to a UTC day")
check(sum(r_["events"] for r_ in roll["rows"]) == roll["source_events"],
      "the rows account for every event — nothing dropped")
# Rebuilding from the same log must give the same answer; that's what "derived" means.
before = [dict(r_) for r_ in roll["rows"]]
run(["rollup"], d3)
after = json.load(open(os.path.join(d3, "rollup.json")))["rows"]
check(before == after, "rollup is idempotent — rebuilding does not double-count")


print("== rollup: unpriced events are counted separately, not folded into the total ==")
d4 = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "1000000", "--out", "0", "--session", "X",
     "--priced-against", "claude-opus-5"], d4)
run(["record", "--model", "m", "--in", "1000000", "--out", "0", "--session", "X",
     "--priced-against", "nope"], d4)
run(["rollup"], d4)
row = json.load(open(os.path.join(d4, "rollup.json")))["rows"][0]
check(row["events"] == 2, "both events counted")
check(row["unpriced_events"] == 1, "the unpriced one is tallied separately")
check(row["saved_usd"] == 5.0, "the unpriced event contributes 0 to the money total")
check(row["input_tokens"] == 2_000_000, "but its TOKENS still count")


print("== report: period windows come from the same lines ==")
d5 = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "1000000", "--out", "1000000", "--session", "S",
     "--priced-against", "claude-opus-5"], d5)
out = json.loads(run(["report", "--json"], d5).stdout)
check(out["saved_usd"] == 30.0 and out["events"] == 1, "all-time report totals correctly")
check(json.loads(run(["report", "--today", "--json"], d5).stdout)["events"] == 1,
      "--today includes an event recorded just now")
for flag in ("--week", "--month"):
    check(json.loads(run(["report", flag, "--json"], d5).stdout)["events"] == 1,
          f"{flag} includes today's event")
check(json.loads(run(["report", "--since", "2099-01-01", "--json"], d5).stdout)["events"] == 0,
      "--since a future date excludes everything")
txt = run(["report"], d5).stdout
check("$30.00" in txt, "text report prints the money total")
check(run(["report"], tempfile.mkdtemp()).stdout.strip().endswith("no dispatches recorded"),
      "an empty ledger reports plainly instead of erroring")

d6 = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "10", "--out", "1", "--priced-against", "nope"], d6)
check("floor" in run(["report"], d6).stdout,
      "a report containing unpriced events warns that the total is a floor")


print("== robustness ==")
d7 = tempfile.mkdtemp()
os.makedirs(d7, exist_ok=True)
with open(os.path.join(d7, "savings.jsonl"), "w") as fh:
    fh.write('{"ts":"2026-08-14T00:00:00Z","input_tokens":10,"output_tokens":1,"saved_usd":1.0}\n')
    fh.write("{ this is not json\n")            # e.g. a line half-written when a process died
    fh.write('{"ts":"2026-08-14T00:00:01Z","input_tokens":10,"output_tokens":1,"saved_usd":2.0}\n')
out = json.loads(run(["report", "--json"], d7).stdout)
check(out["events"] == 2 and out["saved_usd"] == 3.0,
      "a corrupt line is skipped — it does not make the whole ledger unreadable")

r = run(["rates", "--json"], tempfile.mkdtemp())
rates_out = json.loads(r.stdout)
check(rates_out["as_of"] == sl.RATES_AS_OF, "rates output carries its as-of date")
check("claude-opus-5" in rates_out["rates"], "rates output lists the models")

rf = os.path.join(tempfile.mkdtemp(), "rates.json")
json.dump({"as_of": "2030-01-01", "rates": {"my-model": {"in": 100.0, "out": 200.0}}}, open(rf, "w"))
d8 = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "1000000", "--out", "0",
     "--priced-against", "my-model", "--rates-file", rf], d8)
e = json.loads(open(os.path.join(d8, "savings.jsonl")).read().strip())
check(e["saved_usd"] == 100.0, "--rates-file overrides the built-in table")
check("2030-01-01" in e["rate_source"], "and the event records which table priced it")


print("== output-only events: a missing prompt-token count is named, not hidden ==")
# MEASURED on the local backend: it reports prompt_tokens: 0 even for a ~21,600-char
# prompt. Offload savings live mostly on the input side, so a silent 0 would understate
# the total by orders of magnitude while looking like a real number.
d9 = tempfile.mkdtemp()
r = run(["record", "--model", "m", "--in", "0", "--out", "4",
         "--input-tokens-source", "unavailable", "--input-chars", "21628"], d9)
check("understated" in r.stdout, "record warns that the saving counts output only")
e = json.loads(open(os.path.join(d9, "savings.jsonl")).read().strip())
check(e["input_tokens_source"] == "unavailable", "the event is flagged, not silently zeroed")
check(e["input_chars"] == 21628, "the raw character count is kept as a repriceable fact")

run(["record", "--model", "m", "--in", "500", "--out", "10"], d9)
run(["rollup"], d9)
row = json.load(open(os.path.join(d9, "rollup.json")))["rows"][0]
check(row["output_only_events"] == 1, "rollup tallies output-only events separately")
check(row["events"] == 2, "and still counts every event")

out = json.loads(run(["report", "--json"], d9).stdout)
check(out["output_only_events"] == 1, "report --json exposes the output-only count")
check(out["unmeasured_input_chars"] == 21628, "report --json exposes unmeasured chars")
txt = run(["report"], d9).stdout
check("OUTPUT side only" in txt, "text report names the shortfall")
check("21,628" in txt, "text report quantifies what was not measured")

d10 = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "100", "--out", "10"], d10)
check("OUTPUT side only" not in run(["report"], d10).stdout,
      "a fully-measured ledger shows no shortfall warning")
check(json.loads(run(["report", "--json"], d10).stdout)["output_only_events"] == 0,
      "and reports zero output-only events")

# --- per-session savings, and the legacy-location merge (0.34.0) -----------------------
# The ledger moved from ~/.claude/local-agents to ~/.claude/free-agents in 3da0899 and took the
# history with it: new events went to the new file while the old one held everything before, so
# every report read an empty file and understated savings forever (measured 2026-10-24: 35 events
# stranded, `report --since 2026-01-01` returning 0). Both are now read; appends stay live-only.


def run_home(argv, home):
    """Run with HOME pointed at a fixture, so the ledger location is the REAL default path."""
    env = {k: v for k, v in os.environ.items() if k != "FREE_AGENTS_LEDGER_DIR"}
    env["HOME"] = home
    return subprocess.run([sys.executable, SCRIPT] + argv, env=env,
                          capture_output=True, text=True)


h = tempfile.mkdtemp()
live = os.path.join(h, ".claude", "free-agents", "savings.jsonl")
legacy = os.path.join(h, ".claude", "local-agents", "savings.jsonl")
os.makedirs(os.path.dirname(live))
os.makedirs(os.path.dirname(legacy))
with open(legacy, "w") as fh:
    fh.write(json.dumps({"ts": "2026-10-01T10:00:00Z", "session": "legacy-sess",
                         "model": "m", "input_tokens": 10, "output_tokens": 5,
                         "saved_usd": 0.5}) + "\n")
run_home(["record", "--model", "m", "--in", "20", "--out", "10", "--session", "live-sess"], h)

out = json.loads(run_home(["report", "--json"], h).stdout)
check(out["events"] == 2, "events from BOTH the live and the legacy location are counted")
# Compare against the live file ALONE: the merge must add exactly the legacy event's $0.50.
# (Asserting "$0.50 is in the total" would pass trivially; asserting it is MORE than the live-only
# total is what proves the legacy event was actually read.)
live_only = json.loads(subprocess.run(
    [sys.executable, SCRIPT, "report", "--json"],
    env=dict(os.environ, FREE_AGENTS_LEDGER_DIR=os.path.dirname(live), HOME=h),
    capture_output=True, text=True).stdout)
check(live_only["events"] == 1, "the live directory alone holds just the new event")
check(abs(out["saved_usd"] - (live_only["saved_usd"] + 0.5)) < 1e-9,
      "the merged total exceeds the live-only total by the legacy event's saving")
check(len(out["by_session"]) == 2, "by_session lists each session from both files")
check(sorted(r["session"] for r in out["by_session"]) == ["legacy-sess", "live-sess"],
      "a legacy-location session is not silently dropped")
check(abs(sum(r["saved_usd"] for r in out["by_session"]) - out["saved_usd"]) < 1e-9,
      "by_session reconciles to the total it is drawn from")
check(sorted(r["saved_usd"] for r in out["by_session"])[::-1]
      == [r["saved_usd"] for r in out["by_session"]],
      "by_session is ordered biggest saving first")

check(os.path.exists(live) and os.path.exists(legacy),
      "an append does not move or delete the legacy file")
check(sum(1 for _ in open(legacy)) == 1, "the legacy file keeps exactly its own events")
check(sum(1 for _ in open(live)) == 1, "a new event lands in the live file")

# An explicit directory pins it: a caller that names the location must not get a surprise merge.
pinned = tempfile.mkdtemp()
run(["record", "--model", "m", "--in", "1", "--out", "1"], pinned)
legacy_home = tempfile.mkdtemp()
os.makedirs(os.path.join(legacy_home, ".claude", "local-agents"))
with open(os.path.join(legacy_home, ".claude", "local-agents", "savings.jsonl"), "w") as fh:
    fh.write(json.dumps({"ts": "2026-10-02T10:00:00Z", "model": "m", "input_tokens": 1,
                         "output_tokens": 1, "saved_usd": 9.0}) + "\n")
env = dict(os.environ, FREE_AGENTS_LEDGER_DIR=pinned, HOME=legacy_home)
q = subprocess.run([sys.executable, SCRIPT, "report", "--json"], env=env,
                   capture_output=True, text=True)
check(json.loads(q.stdout)["events"] == 1,
      "an explicit FREE_AGENTS_LEDGER_DIR is honoured alone, with no legacy merge")

print("\n%d passed, %d failed" % (passed, failed))
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
