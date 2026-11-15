# Trust-key probe — which `projects[...]` key suppresses the folder-trust dialog

**Date:** 2026-11-15 · **Executor:** GLM 5.3 Flash free session (Task 1 of plan
`docs/superpowers/plans/2026-11-15-handover-pre-trust.md`) · **CLI:** Claude Code 2.1.325
· **Method:** real CLI launches in a PTY in scratch dirs under `$TMPDIR`; the dialog was
never answered; keys were planted/unset via the plan's write procedure (backup, atomic
replace, verify) on a per-probe basis; backup receipt:
`~/.claude.json.bak.pre-trust-probe-20261115T050429Z` (203585 bytes, taken before any write).

## Truth table

| # | Target | Key state in the consulted config | Result |
|---|---|---|---|
| a | plain git repo `r1` | no key | **DIALOG** |
| b | linked worktree `r1-wt` | no key | **DIALOG** — dialog *names* the worktree path (realpath spelling) |
| c | non-repo folder `plain` | no key | **DIALOG** |
| d | `r1` and `r1-wt` | `projects[<r1 MAIN realpath>]` set | **NO_DIALOG** for both (10 s full-timeout, session alive past trust) |
| e | `r2-wt` | ONLY `projects[<r2-wt worktree realpath>]` set | **NO_DIALOG** — worktree key alone suffices |
| 4a | non-repo `plain` | symlink spelling key (`/var/folders/...`) | **DIALOG** — symlink spelling is NOT consulted |
| 4b | non-repo `plain` | realpath spelling key (`/private/var/folders/...`) | **NO_DIALOG** |
| sub | `r1/sub` (subdirectory) | repo-root key set (from d) | **NO_DIALOG** — ancestor walk to the git root |
| f1 | `r1` with `CLAUDE_CONFIG_DIR=<copy>` | copy WITHOUT the key | **DIALOG** — the env-resolved config is the one consulted |
| f2 | `r1` with `CLAUDE_CONFIG_DIR=<copy>` | copy WITH `projects[<r1>]` = true | **NO_DIALOG** |

## Conclusions (what the implementation must do)

1. **The lookup key is the REALPATH** of the folder — `/private/var/...`, never the
   `/var/...` symlink spelling. The helper must `os.path.realpath()` before keying.
2. **For a linked worktree, EITHER key suppresses the dialog** — the worktree path or
   the main-checkout path. This RESOLVES the plan's [dated] uncertainty: the CLI's
   accept-handler writes the MAIN checkout entry (the [dated] lp-self-heal observation),
   but its trust CHECK accepts any candidate (cwd and the git-common-dir ancestor).
   The helper writes BOTH (either alone would work today; both survives a CLI change
   in which candidate is consulted).
3. **A subdirectory of a trusted repo is trusted** — the walk goes up to the git root.
   Helper keys for a repo target: realpath(git toplevel) and realpath(target) when
   they differ.
4. **`CLAUDE_CONFIG_DIR` is honored when set**: the check reads
   `$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`. (`claude --help` does not
   document this; proven empirically with a config copy — control DIALOG, planted
   NO_DIALOG.) Resolution order for the helper: `--config` > `$CLAUDE_CONFIG_DIR` >
   `~/.claude.json`, as the plan's Global Constraints already prescribe.
5. The dialog display names the cwd/worktree path, but display is not the lookup key
   (row b vs d/e).

## Exact commands (reproducible)

- Probe (one real PTY launch per run, never answers the dialog):
  `python3 tests/manual/probe_trust_dialog.py <dir> --timeout 10 [--show]`
  - reports `DIALOG` when the captured screen contains both "trust" and "folder",
    else `NO_DIALOG`; kills only its own tmux session.
  - Mechanism note: the probe uses a private detached tmux session instead of
    in-process `pty.fork()` — THIS HARNESS'S SANDBOX breaks in-process forkpty for
    spawns of `claude` (fork returns a bogus pid with no reapable child; measured
    pid=3/`ChildProcessError` across sandboxed, unsandboxed, and file-vs-inline
    invocations). The tmux server owns the pty and is a normal native process.
    Outside such a sandbox, plain `pty.fork()` would work equally well.
- Key plant/remove (the plan's write procedure, throwaway form):
  `.superpowers/sdd/2026-11-15-handover-pre-trust/trust_key.py <config> set|unset <key>`
  (backup mode 600 → load → modify one key → temp file + original mode + `os.replace`
  → re-read after 1 s and verify; keeps the 5 newest `.bak.pre-trust-*` backups;
  detects the original indent/trailing-newline style).
- Scratch setup: `$TMPDIR/fa-trust-probe-<stamp>/{r1,r1-wt,r2,r2-wt,plain}` with
  `git init` + `git worktree add` for the worktrees; probes launched with the
  symlink-spelled `$TMPDIR` path, which is also how a real launcher would pass it.

## Cleanup receipt

- Scratch dirs removed; the `CLAUDE_CONFIG_DIR` config COPY deleted (it contained
  account data; never printed).
- tmux: only the probe's own sessions were created; the (probe-started) server exited
  on its own when the last session was killed — no shared server was touched.
- `~/.claude.json`: 4 `projects` entries under the scratch root were left in place
  (plan Task 1 Step 5: the sanctioned exception covers setting one key, nothing else;
  Claude Code itself accumulates such per-folder entries). Count only, values never
  printed. `~/.claude.json.bak.pre-trust-*` backups from the probe writes were rotated
  to the 5 newest by `trust_key.py`.
