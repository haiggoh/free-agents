---
name: verify-delegated-work
description: 'Use AFTER a delegate (local model, cloud subagent, or external tool) returns output that modifies state, generates code, or asserts facts. Triggered by the need to validate correctness before trusting the result. Do NOT use for planning, briefing, or runtime checks. Covers ground-truth comparison, mutation testing of generated tests, and the retry ceiling.'
---

# verify-delegated-work — trust nothing, verify everything

A smaller model or automated agent’s default failure mode is **plausible-but-wrong**. It will return a clean diff, a passing test suite, or a confident summary that is subtly incorrect. Verification is not optional; it is the cost of offloading.

## Capture ground truth BEFORE delegating
You cannot verify what you do not know. Before dispatching work that touches files or asserts facts, capture the baseline:
- **File state:** `git diff` for tracked files, otherwise a content hash — use `shasum -a 256`, which is
  present on both macOS and Linux (`md5sum` is GNU coreutils and is missing from most macOS installs).
- **Fact state:** The actual list of files, the current value of a variable, or the output of a diagnostic command.
- **Test state:** The current pass/fail status of the test suite.

Store this baseline in a temporary variable or file. When the delegate returns, compare its output against this baseline, not against your memory of it.

## Judge by observable outcome change
Do not accept "it replied" or "no error" as success. Verify by **observable change**:
- **Code/Files:** Does the diff match the intent? Does the file exist where promised?
- **Tests:** Did the test suite pass *after* the change? If the delegate wrote new tests, **mutation-test them**: break the code intentionally and ensure the new tests fail. If they don’t, the tests are useless.
- **Facts/Summaries:** Does the summary match the ground truth captured in step 1? Spot-check specific claims against the source.

## Verify at the seam the framework reads, with a probe that fails on the old code
A correct part is not a working integration. When a fix is wired INTO something — a framework
driver, a plugin hook, injected config, an env var a launcher exports — assert on the object or
behaviour the framework actually USES at runtime, not on the component the delegate wrote.
Construct the real thing and inspect it: the live instance attribute, the environment the child
process receives, the command the hook really emits. Measured: a delegate set a correct driver
subclass as a CLASS attribute, the framework's constructor overwrote it on the INSTANCE, and the
waypoint closed "done, all tests pass" because the tests checked the subclass. One probe that
built the app and printed the instance attribute showed the default driver.

Then make sure the probe can tell old from new. Run it against the code BEFORE the change and
watch it fail; if it passes on both, it proves nothing. Prefer replaying the real failing input
(the exact command, request or payload from the log or transcript) over a hand-written paraphrase:
in the same audit, two hand-written probes passed on both versions of a guard hook, and only the
replayed command told them apart. Same for a wiring test that greps the source for the right text:
it passes while the line is commented out. Run the wired block and check what it produced.

## The retry ceiling (stop looping)
Verification is cheap, but infinite loops are not. If a delegate fails verification:
1. **Correct the briefing** (was the instruction ambiguous? Was the context missing?) and re-dispatch.
2. **Count failures.** After **N failed verifications** (default N=3), **stop looping**.
3. **Fallback:** Either do the work directly on the cloud model (if quality-critical) or park the item with a clear reason ("failed verification 3x, parked for manual review").

Do not keep retrying the same delegate with the same briefing. The compute is free, but your time is not.

## Dispatched reviews: chunk, serialize, and treat findings as hypotheses
When the verification itself is dispatched (a big diff sent to validators), **chunk it to
≤~650 lines split at commit boundaries and run the chunks ONE at a time.** Measured: eight
PARALLEL validator dispatches of 600–3100-line diffs — only 4 of 8 returned (read timeouts,
503s, 429s, the wall-clock cap); the same reviews re-run as small serialized chunks mostly
succeeded.

Returned findings are **hypotheses, not verdicts** — check each by execution before acting on
it. Measured: a review predicted "this test FAILS" for a defect that actually passed
*vacuously* — right defect, wrong symptom; only running it told the difference. Split what
comes back into **names vs policy**: claims a reviewer could *check* (does this symbol, file or
flag exist? does this command run?) are worth re-verifying and are often right; its judgements
about *whether something is allowed* are unreviewed opinion unless it had the project's
premises. And when a search comes back empty, re-grep the *concept* with several precise
patterns before believing it — one broad term manufactured three phantom "violations" that a
ten-pattern re-check disproved.

**A uniformly weird result means a DISPATCH bug, not a model failure.** If a provider returns
non-JSON, empty output, or the same pathology across attempts, suspect the dispatch pipeline
first — the same models run fine in real sessions. Diagnose the dispatcher (payload shape,
parsing, effort/token settings) before concluding the model "can't do it", and never let one
bad run demote a model: latency alone is not a failure signal either (long waits can be
warm-up, not death).

## Universal application
This skill applies to **any** handoff: local models, cloud subagents, or external scripts. The principle is identical: capture truth, delegate, compare, and enforce a ceiling.

## Branding (📡🦾)

When you begin working under this skill, lead your first substantive reply with the session's lane mark + ` free-agents:` — 📡 on a remote free-API session, 🦾 on a local one.

