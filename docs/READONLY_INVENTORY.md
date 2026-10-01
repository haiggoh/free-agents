# Read-only local-inference inventory

`bin/local-inference-readonly-inventory.zsh` takes an **offline, read-only snapshot of the whole
local-inference environment** and writes it to one new report directory. Use it when you need to
describe the stack's state — for a bug report, before an upgrade, after a migration, or to hand a
second agent a factual picture — **without changing anything**.

It was adopted into the plugin in 0.13.7 (commit `6664f91`, 2026-08-28) after living only in
`~/.claude/scripts`, so it ships with the plugin rather than existing on one machine. It complements
`bin/la-disk-inventory.sh` rather than duplicating it: that one answers *disk → registry model
accounting*; this one inventories the *environment around the models*.

## Usage

```sh
local-inference-readonly-inventory.zsh                 # report under ~/.claude/reports/
local-inference-readonly-inventory.zsh /tmp/inv-report # explicit NEW directory
local-inference-readonly-inventory.zsh --dry-run       # show what it would do; writes nothing
local-inference-readonly-inventory.zsh --help
```

| Option | Effect |
|---|---|
| `REPORT_DIR` | New directory for the report. Must not exist (the script refuses to overwrite). Default `~/.claude/reports/local-inference-inventory-<UTC timestamp>`. |
| `-n`, `--dry-run` | Print the target directory and the list of sections it would collect, then exit 0. Creates nothing. |
| `-h`, `--help` | Usage, options and environment variables. |
| `--` | End of options, for a `REPORT_DIR` that starts with `-`. |

Unknown options exit 2 with a usage line on stderr. Before 0.20.9 the first argument was taken
as `REPORT_DIR` unconditionally, so `--help` ran a full inventory into a directory named `./--help/`.

**Environment:** `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, `TRANSFORMERS_CACHE`, `XDG_CACHE_HOME` add
model-cache roots to the built-in list; `HOME` sets the default report location and the built-in roots.

**Runtime:** about 20 s on a small home directory; several minutes when model caches are large (it
walks model roots and hashes small metadata files, never weights over 16 MiB).

## What it collects

Section names below are the exact headings in `summary.txt` (and what `--dry-run` lists):

- **System:** date/uptime, kernel, macOS version, hardware, memory size, CPU counts, power, memory
  pressure, VM statistics, swap, filesystem capacity and inodes.
- **Activity:** inference/model/download processes (rapid-mlx, vllm-mlx, mlx-lm/vlm, oMLX, llama.cpp,
  downloaders, ComfyUI, …), listening TCP ports, model files those processes hold open.
- **Runtimes:** runtime executables on `PATH`, relevant Homebrew packages, Python interpreters and
  relevant packages — discovered **without importing MLX**.
- **Configuration:** known config and catalog files (with hashes).
- **Models:** model and cache roots, discovered model directories, possible large-file duplicates.
- **Repositories:** identity (remote, branch, commit, dirty state) of local git repos in the stack.

Report files (each a TSV/TXT inside `REPORT_DIR`): `summary.txt`, `warnings.txt`,
`config-files.tsv`, `git-repositories.tsv`, `listening-ports.txt`, `model-roots.tsv`/`.txt`,
`models.tsv`, `weight-files.tsv`, `possible-large-file-duplicates.tsv`, `processes.txt`,
`python-interpreters.txt`, `python-packages.tsv`, `runtime-executables.tsv`, and `SHA256SUMS.txt`
over all of them.

## Safety boundary

Read-only by construction. It does **not**: use `sudo`; contact the network (it forces
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `PIP_NO_INDEX=1`, `HOMEBREW_NO_AUTO_UPDATE=1`,
`DO_NOT_TRACK=1`); install, upgrade or download anything; import MLX or load weights; start or stop a
service; modify Claude Code, runtime, git or model configuration; delete, move, rename or chmod files.
Its only writes are the new report directory and the files inside it.

The report can contain local paths, hostnames and process command lines — review it before sharing.

## Tests

`tests/test_inventory_cli.sh` covers `--help`, `--dry-run`, argument errors and the "creates nothing"
guarantee for each of them, in a temporary `HOME`. It never runs a real inventory.
