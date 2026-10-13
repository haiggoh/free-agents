# Classifier qualification campaign — 2026-10-13

Full local-roster campaign: 27 candidates + devstral-small2 control, Stage 1 against the
genuine A/B fixture pair (`~/.cache/local-agents/rapid-auto-fixtures/9065…/`), each model
served by a server-only replica of `launch-claude-agent-rapid-auto.sh`'s serve block
(rapid-mlx **0.15.3**, the production pin) on :8002. Raw verdicts:
`~/.claude/logs/classifier-campaign-results.jsonl`, per-model JSON + server logs in `/tmp/qual/`.

## Verdict

**`granite-4.2-30b` is the only fully qualified classifier on the current pin.**
`DIRECT_PASS`: 99.065% changed-prefix reuse (trimmable `KVCache` layers — the only model
that got them), changed-request 6.9s, exact replay 1.8s, contract clean, no adapter.
It is the recommended classifier going forward.

## Engine findings that reframe prior qualifications

- **Devstral's qualification is VOID on the current pin.** rapid-mlx 0.15.3 (#3836,
  "anchor hybrid checkpoints at message boundaries") changed the trim physics: on 0.15.0
  devstral measures 98.86% changed-prefix reuse (reproduced to the decimal on 2026-10-13);
  on 0.15.3 AND 0.16.1 it measures 73.476% with a **73s changed-request** — over the 45s
  classifier deadline. The original qualification was measured on the pre-anchor engine.
- **Trim behavior is engine-internal, per model — not readable from model family.**
  Only granite-4.2-30b received trimmable `KVCache` layers (token-level LCP trim,
  `non_trimmable=False`). `ArraysCache` / `RotatingKVCache` models are non-trimmable:
  a changed request is a full MISS (zero reuse, full ~38k re-prefill). Mistral-family
  models get checkpoint-trim (reuse to the last message boundary = 73.476% on this
  fixture, then a slow dense re-prefill of the remainder). Ground truth is the serve
  log's `non_trimmable=` / `layer_types=` lines — nothing else predicts it.
- **Capability on this pin = trimmable OR fast-enough full prefill inside 45s.**
  Reuse percentage is a mechanism readout; the binding criterion is per-call latency
  vs Claude Code's classifier patience.

## Fallback tier (zero reuse, full re-prefill fits 45s — workable but near-ceiling)

qwen-3.6-35b-a3b (34.3s) · qwen-3.6-35b-text (35.3s) · ornith-1.5-35b (36.2s) ·
kat-coder-optiq (36.1s) · kat-coder-6xl (36.0s) · kat-coder-oq6e (36.6s) ·
kat-coder-oq8e (35.7s) · nemotron35-lightning (36.9s) · agentworld-35b (35.5s) ·
gemma-4-26b (43.3s, borderline).

## Not capable

- **Over deadline:** devstral-small2 (73.1s), ministral14-reasoning (47.8s — 2.8s over,
  73.476% checkpoint reuse), qwen-3.6-operator (215s), qwen-3.8-operator (222s),
  qwen-3.8-oq6 (228s), gemma-4-31b-optiq (300s), ternary-bonsai-27b (222s),
  gemma4-12b-frontierdistill (111s).
- **Contract violations** (malformed severity verdicts): minicpm5-1b, granite-4.2-3b,
  muse-glimmer-30b, qwen-80b-thinking (whose rendered cache entry is 13 tokens — nothing
  reusable even before the contract failure), kimi-vl-thinking.
- **Cannot serve under the classifier serve flags:** nemotron-omni-8bit (VL model,
  text-only serve), laguna-fast (`mlx_lm.models.laguna` absent from 0.15.3), llama-scout
  (`huggingface_hub` config-validation bug in the pinned venv: `attn_temperature_tuning`
  expected bool, got int), mistral-small4 (Metal OOM at generation — 67.5GB weights + KV
  do not fit).

±thinking alias pairs were deduplicated: the serve block pins `--no-thinking
--no-reasoning-parser`, so both members of a pair serve identically for this role.

## Open follow-ups

1. `classifier-qualify.py` should read the engine's `LCP` / `non_trimmable` /
   `layer_types` lines: this campaign's HARNESS_FAILURE verdicts were all models whose
   evidence was present in that log shape — zero-reuse with mechanism, not missing data.
2. The Stage-2 fixture (`max_tokens=8192` capture) is absent from the fixture directory —
   Stage 2 (contract + warm latency) could not run this campaign. Capture one from a live
   auto-mode session before the next qualification round.
3. Engine options for the devstral route: repin to 0.15.2 (pre-anchor, restores 98.86%)
   at the cost of 0.15.5+ agent cache fixes — or file upstream (raullenchai/Rapid-MLX)
   about trimmability control for the classifier-prefix pattern. Upstream's 0.16 line
   keeps the boundary behavior (open issues #4459 / #4451 are adjacent work).
