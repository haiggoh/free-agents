# Task 8 Brief: Update install-backend.sh

## Task Context
This is Task 8 of the Unified Backend Management plan. Update the canonical installer script to use the new unified CLI.

## Requirements (from plan)

### Files to Modify:
- Modify: `install/install-backend.sh` - Updated canonical installer

### Must Implement:
- Install all backends via `manage-backend.py` CLI
- Support `--all` flag to install all backends
- Support `--backend` flag for specific backend
- Support `--dry-run` flag
- Support version flags for each backend
- Show update check commands at the end

### Global Constraints:
- Python 3.11+
- All versioned environments in `~/.venvs/<backend>-<version>/`
- No breaking changes to existing launchers

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-8-report.md`