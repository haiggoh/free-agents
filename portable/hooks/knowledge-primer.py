"""knowledge-primer.py — SessionStart knowledge lines for free-agents (not a guard).

Loaded by offload-nudge.py via its PRIMER constant (AST-read, never executed) and appended to the
nudge as a "🧠 free-lane knowledge" section. Migrated from the MEMORY.md index 2026-11-18 (group B,
plan 2026-11-18-memory-migration-group-ABCD-plugin-primers.md); originals archived at
~/.claude/reference/memory-history/plugin-primer-20261118/.

This file must stay import-free at module level: the nudge reads PRIMER without running the hook.
"""

PRIMER = """🧠 free-lane knowledge (migrated from the memory index, 2026-11-18):
  - Transcript-derived prompts may go to Google/NVIDIA free routes without a consent check (user 2026-11-12); still secret-scan the payload; other providers are NOT covered.
  - The 600 s stream watchdog kills silent free-lane agents on long test runs; on repeated failures resume inline instead of restarting (user).
  - la-evict.sh --tier 0 is a standing okay any time; RSS LIES on MLX — read wired/Metal; never kill or restart the endpoint you or another session runs on — gate on ANTHROPIC_BASE_URL.
  - Local stack wedge: 500 Stream(gpu,N) while /v1/models answers 200 → probe ONE real generation per port; >10 min/turn → DISPATCH, don't wait.
  - One-shot waits: watch-until + run_in_background, one verdict line, --self-test the predicate first; never sleep/poll a harness-notified task."""
