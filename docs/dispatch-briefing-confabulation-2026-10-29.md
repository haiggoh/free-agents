# Dispatch briefing limits: when a stateless offload confabulates instead of failing

Measured 2026-10-29, during the check-code 0.4.0 build (a GLM 5.3 free-session run that used
`bin/free-agent-tool.py` twice for independent review). This documents a failure mode that is
**distinct from the one already recorded** in `skills/offload-to-local/SKILL.md` (the lines saying a
stateless dispatch "cannot read the disk" and "would narrate the reads it cannot do and return
nothing") — there the dispatch fails by **returning nothing**; here it failed by **returning
confident fabrications**. Silence is easy to spot and act on. Fabrication is not: it arrives in the
same register as a real finding, and 23 of 25 fabricated findings were plausible enough to need
individual checking against the code before rejection.

## The incident

Two review dispatches were requested (milestone B: the linter catalog; milestone C: the coverage
notice). Every remote preference failed on the first two attempts — Gemini 503/timeouts, NVIDIA
DeepSeek read-timeouts, and the GLM slow lane burning its full 1800 s — consuming the tool's 600 s
wall-clock cap each time. Two contributing mistakes on the requester's side:

1. `avoid_family` was set to `"zai"`, a name that matches nothing on the roster (the roster's family
   name for GLM is **`zhipu`**), so the same-family slow lane was not skipped and consumed most of
   the first window.
2. The payload was large (~1600 attached lines) with thinking enabled, which outran the remote read
   timeouts. Trimming it for the retry worked as a diagnostic but introduced the second mistake
   below.

The reviews ultimately ran locally: a session-private `FA_DISPATCH_PREFS` file putting **Laguna S
2.1 (local, poolside family)** first, then remote StepFun/Nemotron as fallbacks. Both reviews
returned within minutes.

**Yield: 25 findings, 2 real.**

- The 2 real ones came from the review that asked a **closed, enumerated** question: "find every
  consumer of `linter['install']` and check each handles both shapes." One of them was a genuine
  regression no test covered (a `doctor` display path printed a raw dict after the field changed
  shape). This is the shape a dispatch review does well: the answer follows from reading the
  attached file, and each claimed instance can be checked in seconds.
- The other 23 were rejected after checking every one against the real code. The classification of
  *why* is the useful part, and it is not random — it clusters by **what the question demanded that
  the dispatch had no means to supply**.

## Why the 23 were rejected, by root cause

**A. The question needed an instrument the dispatch did not have** (the largest cluster). A
stateless dispatch cannot execute code, run a test suite, check reachability, diff against a
previous version, or observe runtime behaviour. Asked anyway, it answered anyway:

- *"The C3 notice path is unreachable"* (rated critical) — refuted by the code's own short-circuit
  and by tests that pass. Verifying it required **running the tests**, which the dispatch could not
  do and (after payload trimming) could not even read.
- *"The once-per-session key is consumed without printing"* (rated critical) — a claim about
  statement execution order; the call sits *after* the guard, which a trace shows. It needed
  **execution, not reading**.
- *"Biome line-0 findings are mishandled"* / *"the test should exclude L1 and L3"* — both depend on
  runtime facts (which command runs; what a deliberate ±2-line margin admits). One needed **running
  the tool**, the other **tracing a constant the dispatch could read but not execute**.
- *"Pre-existing behaviour is wrong"* (three findings) — could only be judged against the **previous
  version**, which was not attached.

**B. The question asked for a design verdict.** Two findings flagged code for doing exactly what the
spec says (path-matched entries ignore extension; the per-linter notice loop returns one line per
output). One "critical" self-described in its own text as "a design limitation, not a bug per se".
A dispatch has no standing to overrule an intentional design, and asking it to produces noise.

**C. False premises about technologies, stated confidently.** A claim that a path library returns a
non-empty suffix for dotfiles (it returns `""`); a claim that a regex match "could raise"; a claim
that a tool's message could end with brackets that are not its rule id (the output is produced by a
format template that guarantees the trailing rule). Each premise was checkable by reading the
attached code — these are *reasoning* errors, not capability limits, and they are the residue that
the triage step exists to catch.

**D. Speculative, with no triggering input** (two findings): "fragile if a path contains a colon",
"could break if calling code changes". No concrete input, no way to check, no action.

**E. Style and test-philosophy opinions** (three findings), and **one duplicate** of an earlier
finding in the same review.

**The requester's contribution to the fabrication.** Two briefing choices invited it: asking open
questions ("any combination of state where the code prints something it should not") rather than
closed enumerations, and **trimming the attached test file from the payload** to shrink the request
— which removed the instrument that would have answered the largest cluster, so the dispatch
reasoned about tests it could not see instead of saying so.

## The owner's explanation (2026-10-29) — the general rule this incident is an instance of

> briefing an offload dispatch is very different from launching a full session (or a headless
> session or background session fork). the offloaded dispatch agent can only work on exactly what
> you provide it and doesn't have the ability to load additional information or make tool calls. so
> you have to consider that in your offloading choice and briefing. if you ask it something it can't
> do that's likely where it confabulates because it's desperate to try to find some answer for
> something it doesn't have the means of seeing or testing.

The mechanism, stated plainly: **the ask demands an answer, and the dispatch has no way to produce a
true one, so it produces a plausible one.** It does not decline, and it does not signal the gap.
Nothing about the output's tone, structure, or severity labels correlates with whether the finding
is real — one of the two true findings and several of the false ones were both labelled "critical".

## Rules that follow

1. **Classify the question before briefing it.** Three buckets:
   - *Answerable from attached files* → safe for a dispatch. Use **closed enumerations** ("list
     every X in the attached file; for each, state whether it handles Y") and require **file:line +
     the triggering input** for each claim, so triage is seconds per finding.
   - *Needs execution, unseen files, or a version diff* → **not a dispatch.** Either attach
     everything needed to reason from (never trim the instrument that prevents a whole class of
     confabulation) or escalate to a **session** — a full, headless, or background session can drive
     tools; a dispatch cannot.
   - *Needs a design verdict* → don't ask. It will relitigate.
2. **Every finding gets checked against the real code before it is acted on.** With a 2-in-25 hit
   rate, the triage step is not optional; it is where the value is extracted. (Do act on the real
   ones: the doctor regression was invisible to the author's own suite by construction — no test
   covered that display path — which is exactly the blind spot a second pair of eyes exists to
   cover.)
3. **Route operationally:** use the roster's family name for `avoid_family` (verify it via
   `--list`; do not guess from the model's branding), and prefer a **local-first** preference order
   for reviews — remote preferences can collectively exhaust the wall-clock cap before a working
   one is reached.

## References

- `skills/offload-to-local/SKILL.md` — the existing "cannot read the disk" lines (the *silence*
  variant of this failure). This document adds the *fabrication* variant; the skill's guidance
  should be extended to cover it when this is picked up.
- The check-code 0.4.0 feature branch holds the two review artifacts' consumers: the accepted fix
  (red-first test asserting the `doctor` display and that no raw dict leaks) and the asserted
  unbranded-notice rule. The review request/response JSON themselves lived in a scratch directory
  and are not durable; the quoted findings above are transcribed from them.
