# DRAFT — JoyIA gateway feature request: pass `safeguards` / `safeguard_results` through unchanged

**Status: DRAFT — not sent.** Prepared 2026-10-25 per the [decided] rule in
`docs/superpowers/specs/2026-10-23-cloud-screen-and-token-profiles-design.md` (§A3): the
pass-through request is drafted once the eligibility probe reads "not-eligible" — and it does
(verdict cached at `~/.local/state/free-agents/eligibility.json`, 2026-10-24).

**For the user:** review, edit, and send through the JoyIA team's channel (the same one as the
two tunnel defects — see waypoint `file-the-two-reproducible`). Nothing below is submitted
anywhere by this session.

---

**Subject: Gateway drops Anthropic auto-mode `safeguards` fields — auto mode's safety checks get
billed as normal token usage on our gateway sessions**

Hi JoyIA team,

When a Claude Code session runs in **auto permission mode** through your gateway, every
non-read-only tool call triggers a second model request: Claude Code's server-side safety check
(auto-mode classifier). On a native Anthropic connection those checks ride **inside** the
session's own model requests as the documented `safeguards` request field /
`safeguard_results` response field, and are free on Pro/Max/Team plans
(https://docs.claude.com/en/docs/claude-code/auto-mode-classifier-billing).

Our measurements show the gateway does not pass those fields through, so Claude Code falls back
to sending its classifier as a **separate, full-priced request**:

- A 9-day least-squares fit of our gateway ledger (2026-09-15..26) puts the residual at
  **$0.0265 per non-read-only tool call** (rms ≈ $2.85/day), consistent with a second
  claude-sonnet-5 request; auto-mode classifier error dumps from the same period measured
  **60–97k tokens per call**.
- User-authorized probes on 2026-10-24 (3 runs, ≈ $0.10 total) reproduced it exactly: the daily
  ledger's `estimated_classifier_usd` read **0.0795 = 3 × $0.0265**, and the tool call executed
  with no `system` notice — a successful, silent, billed classifier round-trip.
- Claude Code's stream-json, init message and debug log carry no server-review state, so from
  the client side this is indistinguishable from "eligible" — the cost only shows in the ledger.

**The ask:** please pass the `safeguards` request field and the `safeguard_results` response
field through unchanged, so the safety checks ride inside the session's own requests again and
stop billing as normal token usage.

**Interim workaround we use:** starting gateway sessions in `bypassPermissions` with explicit
allow/deny rules avoids the classifier calls entirely — but that trades away auto mode's safety
net, which we would rather keep.

Happy to share the raw ledger rows and probe transcripts if useful.
