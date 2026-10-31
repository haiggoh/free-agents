# offload-to-local — in-depth reference (moved out of SKILL.md for the 1500-word budget)

The imperative rules live in `../SKILL.md`. This file holds the measured incidents, dated
rosters, worked examples and advanced material that used to sit inline.

## Dated rosters and model picks (verify live — ids rot)

- Default dispatch order (maintainer, 2026-10-21): Gemini 3.8 Flash → DeepSeek V4.1 Flash
  (NVIDIA) → GLM 5.3 → GLM 5.3 Flash → Nemotron 3 Ultra → local DeepSeek V4 Flash →
  local Laguna S → local KAT Coder. Kimi K3 is off it: NVIDIA retired it 2026-10-15 (410 Gone);
  other providers want paid credits (402). Override: `config/dispatch-preferences.local.json` or
  `FA_DISPATCH_PREFS`.
- Concrete picks at drafting time — always re-verify with `bin/remote-session.sh --list` and
  `bin/la-roles.sh`: remote big/smart: Gemini 3.8/3.6 Flash (separate daily quotas), Nemotron 3
  Ultra 550B-A55B, GLM 5.3, DeepSeek V4.1 Flash; utility-tier remote: Nemotron 3.5 Lightning
  30B, Groq gpt-oss-120b. Local: KAT-Coder 2.5 / Qwen 3.8 class aliases.

## Dispatch warnings (measured)

- `remote-agent-dispatch.py --help` lists gemini|nvidia|groq|openrouter|cerebras|cloudflare|github,
  but only some are implemented — the rest fail fast with "declared but not implemented"
  (verified 2026-09-17: gemini works; nvidia did not then). Check before planning a lane.
- ALWAYS pass `--model`: the built-in default can be a retired id (measured: defaulted to
  gemini-2.0-flash → HTTP 404).
- Give enough `--max-tokens`: a big model may think a lot and return a truncated answer that
  looks terse (measured: a 700-token cap cut the reply mid-word).
- `--dry-run` sends nothing; `--files` needs `--allow-remote-files`.

## "Remote does NOT mean big"

Several NVIDIA roster entries are 20–35B — the same class as local. Dispatching one remotely
buys no capability but spends a finite daily quota and sends the prompt off the machine.
Filter by SIZE, not lane: remote only when genuinely bigger than local; prefer the local
equivalent when one exists (the picker's local-capable filter); never spend big-model quota on
utility work.

## Worked examples (measured)

- Dependency-pair extraction: 15 free-text notes → one dispatch (~1.2k prompt / ~1k completion,
  ~1 min, $0) returned 19 pairs, all 19 verified against source, correctly excluding three
  near-misses (see-also cross-ref, sibling marked also-blocked, parenthetical aside).
- Dispatch-review confabulation (2026-10-29): a review dispatch returned 25 findings, 2 real,
  23 fabricated-but-plausible (several labelled "critical"); taxonomy in
  `docs/dispatch-briefing-confabulation-2026-10-29.md`. A stateless dispatch has no tools/disk/
  test runner — asked what its inputs cannot answer, it confabulates in the same register as a
  real finding. Returning nothing is the honest failure.
- Mutation-test example: deleting a `where !known.contains` guard did NOT fail
  `testBaselineDoesNotOverwriteArchived` because `record(for:)` returns `.first` — the test only
  bit once it asserted `records.count == 1` before unwrapping.
- The escape-hatch trap: four delegatable steps, four `cloud:` annotations (two citing each of
  "quality-critical" / "not worth offloading"), zero local dispatches — each individually arguable.
- A hook re-routed a review needing file reads to a stateless dispatch, which returned narration
  and no findings (hook substitution does not verify the task shape).

## Advanced: parallel local session for a big isolated chunk

Worth it for a big, isolated, verifiable chunk (e.g. mechanical refactor + tests), not for small
or tightly-coupled work. Launch `bin/new-local-window.sh <alias>` as a side worker. Isolate it
(own worktree/branch or disjoint file set); supervise and review its diff before merging — local
models hallucinate paths and botch schemas; check the live concurrency setting; a local session
is slower per turn than a dispatch, so this suits long background chunks; a free remote session
is faster when the work may leave the machine. Launch cost exceeds a dispatch — only for
substantial chunks.

## Why stream long generations

`librarian-dispatch.py` (SSE) has a stall watchdog that aborts a hung local server instead of
blocking forever, no timeout death on multi-minute runs, and live progress. Don't wrap
dispatches in `timeout` — GNU-only (absent on stock macOS); the watchdog covers it.
