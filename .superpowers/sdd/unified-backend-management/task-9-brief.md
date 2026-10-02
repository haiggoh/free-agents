# Task 9 Brief: Consolidate Update Checks

## Task Context
This is Task 9 of the Unified Backend Management plan. Create a unified update check script that checks all backends.

## Requirements (from plan)

### Files to Create/Modify:
- Create: `scripts/check-backend-updates.py` - Python implementation for complex logic
- Modify: `scripts/local-stack-update-check.sh` → `scripts/check-backend-updates.sh` (wrapper)
- Modify: `scripts/daily-package-upgrade.sh` (remove backend-specific logic)
- Create: `tests/test_update_check.py` - Tests

### Must Implement:
- Check all backends for updates via `manage-backend.py`
- Log results with timestamps
- macOS notification for outdated packages
- JSON report saved to log directory
- Exit code: 0 = up to date, 1 = updates available, 2 = error

### Global Constraints:
- All update checks read-only (notify only, never auto-upgrade)
- Weekly launchd schedule

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-9-report.md`