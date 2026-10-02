# Task 11 Brief: Configuration Integration

## Task Context
This is Task 11 of the Unified Backend Management plan. Update config-lib.sh and config.example.sh to work with the new unified backend management system.

## Requirements (from plan)

### Files to Modify:
- Modify: `config/config-lib.sh` - Add unified backend discovery
- Modify: `config/config.example.sh` - Document new backend config options

### Must Implement:
- Add `la_discover_backend_binary()` function that uses `manage-backend.py`
- Update `LA_SERVE_BACKENDS` to include all backends
- Add backend-specific discovery logic for each backend type
- Ensure backward compatibility with existing `LA_RAPID_BIN`, etc.

### Global Constraints:
- No breaking changes to existing launcher scripts
- All code must support `--help` and `--dry-run`

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-11-report.md`