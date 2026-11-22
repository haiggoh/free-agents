# HANDOFF — provider buckets, limiter fixes, swap pre-flight, effort defaults (2026-11-22)

**Read this file first, then follow it exactly: adopt its standing rules for the whole session,
then continue autonomously from its Next action.**

## 1. Why this handoff exists
The investigating session (Claude Opus 5.5, paid gateway) finished a two-day investigation and
wrote the spec and the implementation plan; the user ordered the handover to a free session at max
effort for the implementation. Nothing is wrong or half-done in code: NO production file has been
changed yet. Do not re-investigate what the spec already proves — build on it.

## 2. State
- Worktree: `~/ClaudeWorkspace/.wt/fa-provider-buckets`, branch `fix/limiter-provider-buckets`,
  based on `origin/main` @ `75d1801` (free-agents 0.61.0). Committed here: this handoff, the spec,
  the plan, the evidence rig. Check `git log --oneline -3`.
- Spec (WHAT and WHY, with evidence): `docs/superpowers/specs/2026-11-22-provider-buckets-and-limiter-fixes-design.md`
- Plan (HOW, tasks 0–12, authority matrix, QC milestones): `docs/superpowers/plans/2026-11-22-provider-buckets-and-limiter-fixes.md`
- Evidence rig (real LiteLLM 1.110.2 vs a fake OpenAI upstream, captured responses, the proven
  hook-patch experiment): `docs/measurements/2026-11-22-bridge-experiment/`
- Baseline: main suite `2 failed, 1856 passed, 50 skipped` (details + rule in plan Phase 0 Step 4).
- Not changed and NOT yours to change: cost-tracker (a concurrent session owns it); the branch
  `fix/swap-skip-preflight` / worktree `.wt/fa-swap-override` (superseded by plan Task 7 — leave it;
  the user deletes it).

## 3. The goal, verbatim (user, 2026-11-21/22)
> "please check the new free-agents update for the new nvidia rate limiter system if its working as
> intended and if its logic is sound and if its codebase is free of bugs. I noticed an increase in
> transient api errors since the update […] if the preflight for model swaps goes outside of the rate
> limiter, it will always overshoot the overall rate limit by adding request on top of it which our
> api key doesn't allow. that was always a logic error in the design. […] I think we should adapt our
> existing nvidia rate limiter to create a separate bucket for each of those providers that knows
> their specific rate limits and handles them in the same way. their known max token limits also
> have to be accounted for […] the infoboxes in both api key setup and the model selector will have
> to give information about rate limit, max tokens and daily quota or weekly quota for those
> providers so the user knows what they're picking, but importantly beyond the infoboxes, those facts
> have to be known by the offload model selection priority ladder as well, so it can fall back
> outside of nvidia if needed but knows the limitations in doing so"
>
> "default setting for handovers should be whatever is saved as the current default for launching a
> session on that lane […] offloads should also follow the same setting"
>
> "I want to add one more filter option: 'only show large context models' which excludes models with
> 256k maximum context window size or less […] on by default for the remote lane. we'll also have to
> update the model and provider list to make sure the other providers beside nvidia all have the
> information documented well enough so all filters are correctly applicable and actually get applied"
>
> Decisions (spec §2): limits in a new `config/provider-limits.json`; queueing is the usual outcome,
> generous load-stretched timeouts, balanced use of free resources over fast turnover; sessions and
> handovers ordered + spill, offloads capacity-weighted spread; Groq gpt-oss (128k) never for
> sessions; quotas = local counters + header sync; LiteLLM: map errors only (no pin, keep
> upgrading); effort: explicit > saved lane default > per-alias last-launch > provider default.
> Binding playbooks: `~/.claude/plans/_PLAYBOOKS/Playbook-milestone-qc-offload-test-proof.md`,
> `~/.claude/plans/_PLAYBOOKS/Playbook-split-monolithic-test-suites.md`.

## 4. Standing rules (numbered, blunt — the plan's 12 executor rules also apply)
1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed
   (record them as waypoints via `waypoints.py add`, never hand-edit the store).
2. Work only in `~/ClaudeWorkspace/.wt/fa-provider-buckets`. Never switch the branch of
   `~/ClaudeWorkspace/free-agents`.
3. Commit every milestone, staged by path (never `git add -A`), with YOUR model's co-author trailer
   from memory `commit-attribution-by-model` (open the table; do not derive it) below
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Push the branch only at plan Task 12;
   **never merge, tag, release, bump the version or run `claude plugin update`.**
4. TDD every change (fails for the right reason, then passes); mutation-check each new guard;
   never weaken a test to get green.
5. Review each QC milestone with an independent dispatch (`bin/free-agent-tool.py`, out-of-family
   from you) and check every finding against the real code: reviewers hallucinate file names and
   line numbers (the investigation's reviewer cited la_proxy_hooks.py:358 for code at :396).
6. Resolve model and roster facts when you need them, from the catalog/registry, never from memory.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything
   listening on 4141–4151 — one of them is the proxy YOU run on. Your test proxies: dynamic ports
   > 4199, stopped by their own pid.
8. Never touch the live limiter state (`~/.claude/free-agents/.nvidia_throttle_state`,
   `~/.claude/free-agents/limiter/`); every test sets `LA_NVIDIA_THROTTLE_STATE`/`FA_LIMITER_DIR`
   to a temp dir. You yourself run THROUGH that live limiter — a test that writes it throttles you.
9. Record progress in the plan's `## Progress log` at each receipt, in the same commit.
10. A dying session's "hand off to a new session" instruction is consumed by the successor it
    launched — that is YOU. Continue directly; never launch yet another session.
11. Cold LiteLLM proxy starts took ≈3 minutes in the investigation (niced, fresh install): never
    treat a slow start as a failure before the progress-based readiness bound says so.
12. The 600 s stream watchdog kills a silent free-lane agent: run long suites in the background
    (`run_in_background`) and keep producing output; on repeated watchdog kills, resume inline.

## 5. Next action
Plan `docs/superpowers/plans/2026-11-22-provider-buckets-and-limiter-fixes.md` → **Phase 0, Step 1**
(three strategy sentences), then Steps 2–4 (read-only preflight + baseline), then Task 1.
