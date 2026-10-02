# Task 10 Brief: Launchd Integration

## Task Context
This is Task 10 of the Unified Backend Management plan. Update launchd to use the new unified backend update check.

## Requirements (from plan)

### Files to Modify:
- Modify: `~/Library/LaunchAgents/com.haiggoh.local-stack-update-check.plist` → `com.haiggoh.backend-update-check.plist`
- Create: `install/launchd/com.haiggoh.backend-update-check.plist` (template in repo)

### Must Implement:
- Update launchd plist to run `check-backend-updates.sh` weekly
- Keep same schedule (Monday 10:17 AM)
- Log to `~/.claude/logs/backend-update-check.log`

### Global Constraints:
- Use launchd (not cron) per CLAUDE.md rule
- Read-only update checks (notify only)

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-10-report.md`