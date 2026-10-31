# Launch incidents 2026-10-31 — why the handover launch steps look the way they do

Recorded so the SKILL.md body stays lean; the imperative steps live in SKILL.md §"Handover order".

1. **Headless launch is wrong for a handover.** Launching `remote-session.sh` from the agent's
   Bash tool (background) produces a session nobody can type into. A handover must open a REAL
   Terminal window. (Offloading is the opposite shape: headless background session while the user
   stays in the current one — see the `offload` skill.)
2. **`remote-session.sh` is not on the login shell's PATH.** It lives in the plugin's `bin/`
   (on the Bash tool's PATH only). A Terminal window running the bare name fails with
   `zsh: command not found: remote-session.sh`. Resolve first: `RS="$(command -v remote-session.sh)"`
   and pass the absolute path into the window command.
3. **Verify with a port/LISTEN check, not pgrep.** `pgrep -f "remote-session.sh <alias>"` also
   matches the sudo-in-terminal.sh driver's own argv, so it "verifies" a launch that never
   started. `lsof -nP -iTCP:4141-4151 -sTCP:LISTEN` plus `handover-verify.sh` (below) are the real
   checks.
4. **`handover-verify.sh --expect` must match a text line from the doc, not its path.**
   `--prompt-file` sends the file's CONTENTS as the first prompt; the path never appears in the
   transcript, so `--expect '<handoff path>'` reported NOT_VERIFIED on a correct handover
   (observed 2026-10-31: expected path → NOT_VERIFIED after 120 s; expected the doc's title line
   "0.49.3 hotfix + 0.50.0" → VERIFIED).
5. **Effort must be set at launch.** A launch without `--effort` ran the successor at medium —
   not the launcher default and not the user's stored per-model setting (max). 0.49.3 makes
   `remote-session.sh` default to the stored last-launch effort for the alias when the flag is
   omitted; the skill passes `--effort` explicitly anyway.
6. **Invoking the skill IS the launch authorization.** One session stopped short of launching
   ("I've stopped short of launching the successor … I'd rather you start it yourself") — wrong
   when `free-session-handover` is explicitly invoked: name the concerns (blind-trust mode,
   branch pushes) in the handoff, but carry out the launch. Defer only when the user explicitly
   asks for the handover doc without starting the session.
