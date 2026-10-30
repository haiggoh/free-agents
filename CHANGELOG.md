## [0.49.0] — 2026-10-30

Dispatch-briefing guard, from the measured confabulation incident of 2026-10-29
(`docs/dispatch-briefing-confabulation-2026-10-29.md`): an `avoid_family` that names no roster
family ("zai" for "zhipu") silently matched nothing, so the same-family slow lane ran and burned
the whole wall-clock window.

### Added
- `bin/free-agent-tool.py`: `avoid_family` is validated against the live roster before dispatch;
  an unknown name exits non-zero, naming the known families and `--list`. A family that exists
  but leaves nothing available on this machine still degrades with the existing same-family
  warning (that contract is unchanged and test-pinned).
- Tests: `UnknownAvoidFamilyTests` — refusal names the families, case-mismatch refused, known
  family still dispatches, empty roster left to the no-model error. Mutation-checked: disabling
  the guard fails exactly the two refusal tests.
- `skills/offload-to-local/SKILL.md`: "Dispatch reviews: ask only what the payload can answer" —
  the fabrication variant of the stateless-dispatch failure (the skill previously documented only
  the silence variant), the question-bucket rule, never trimming the instrument from the payload,
  and triage-every-finding.

Backend-manager robustness: a local launch can never again stall silently on a dead backend venv.
Root cause of the 2026-10-30 outage (rapid-mlx-0.16.1 pinned while its Homebrew patch keg was
deleted; every launch printed the port then waited out the 480 s readiness deadline): the
manager wrote its pin into a repo file no launcher reads, health was file-presence only, and
`wait_ready` checked the pid only at the deadline.

### Fixed
- **Venv health runs the interpreter** (`venv_health`): a venv whose base python is gone is
  `broken`, not `complete`. One check feeds every route that hands out a backend binary:
  `installed`, shell discovery, all **8** hotswap/rapid-auto launch gates (incl. the CUDA Tier B
  `llama-server` gate), promote, remove.
- **`wait_ready` fails within seconds on a dead pid** instead of at the deadline.
- **Promote/remove write ONLY the machine's live config** (`config/config-lib.sh` resolves it —
  one implementation); no backend version is stamped into or shipped in the repo (guard test).
  A backend update is a user action and leaves the worktree clean.
- **Explicit env overrides win** over the live config (`LA_RAPID_BIN`, `LA_RAPID_AUTO_BIN`,
  `LA_OMLX_BIN`, `LA_CUDA_BIN`, `LA_LLAMA_SERVER`, `LA_EXL2_BIN`, `LA_SGLANG_BIN`); a set-but-broken
  override aborts naming it — never a silent fallthrough.
- **Receipts round-trip** (writer records `version`/`backend`, reader accepts legacy keyless
  receipts), so `remove` works — including a broken venv — and `snapshot` no longer loops.
- **remove/snapshot shared by every venv backend** (incl. broken venvs; live-pinned refused;
  keep-one guard per backend).
- **New venvs survive Python patch upgrades**: outcome-based normalization onto Homebrew's
  `opt/python@X.Y` path after every create/repair (`durability.py` CLI), receipt records
  `python_durability`, warning for `pinned-keg`.
- **`health [--all] [--json]` and `repair <version>`** commands: judge every venv by running it,
  flag `pinned-keg` fragility, repair make-before-break (original kept until the rebuild runs).
- **The daily upgrade launchd job actually works now** (it failed every night 2026-09-29→10-30:
  launchd's PATH lacks the Homebrew prefix, and the script ended `exit 0`): brew discovered at
  call time + `shellenv`, `--check` mode, litellm repair via the `opt/` path, then
  `bin/fa-post-upgrade-check.sh --repair`; non-zero exit + notification on any failure. The repo
  copy of `scripts/daily-package-upgrade.sh` was removed — single source is the user-level script.
- **The weekly `backend-update-check` job is portable**: `launchd install` writes a STABLE
  launcher into `~/.local/share/free-agents/launchd/backend-check.sh` (never the installing copy)
  that resolves brew and the free-agents root (FA_ROOT → csl checkout → newest plugin cache by
  `sort -V`) at run time.

### Added
- **CUDA backends at full parity** — four registered managers (`cuda-vllm`, `cuda-llama-cpp` with
  a real CUDA `llama-server` cmake build, `cuda-exllamav2` = TabbyAPI, `cuda-sglang` with
  `--only-binary flashinfer-python`), versioned side-by-side venvs under
  `<LA_USER_STATE_DIR>/cuda-backends/`, promote gated by a GPU smoke
  (`torch.cuda.is_available()` + `.cuda()` tensor), PyTorch wheel index chosen from nvcc/driver
  (table verified against the live index; `--cuda-version` override fails loudly), `serve=sglang`
  / `serve=exl2` model routing (exl2 dirs no longer misrouted to vLLM), picker shows the CUDA
  backends only on NVIDIA hardware (one dim "not applicable" row elsewhere).
- **About screen** lists only registered backend families, with the real pin from the live config
  and real health (the dead `backend-pin.txt` input and the bare `~/.venvs` glob are gone).
- **`bin/fa-post-upgrade-check.sh [--repair] [--quiet]`** — one portable entry point any upgrade
  job can call; repairs receipt-bearing broken venvs, notifies + exit 1 otherwise.

### Changed
- Picker Backend manager: per-backend version ops declared by the managers (contract-tested),
  Repair row, "cannot run" markers, keep-one guard; `manage-cuda-backend.py` is a thin wrapper
  over the registry (old names `vllm`/`llama-cpp` map to `cuda-*`).
