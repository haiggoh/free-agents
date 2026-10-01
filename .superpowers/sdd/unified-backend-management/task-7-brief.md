# Task 7 Brief: Unified CLI Entry Point (Complete Implementation)

## Task Context
This is Task 7 of the Unified Backend Management plan. Complete the `manage-backend.py` CLI with all subcommands.

## Requirements (from plan)

### Files to Create/Modify:
- Modify: `install/manage-backend.py` - Full implementation
- Modify: `install/manage-rapid-mlx.py` - Thin wrapper (already done in Task 2)
- Create: `tests/test_manage_backend_cli.py` - CLI integration tests

### Must Implement:
- Full CLI with subcommands: list, releases, installed, install, validate, updates, info, promote, remove
- `--backend` required for all subcommands except `list`
- `--dry-run` support
- `--json` output option
- Interactive version selection for install
- Pin promotion for backends that support it (Rapid-MLX)
- JSON output format

### Global Constraints:
- All code must support `--help` and `--dry-run`
- No breaking changes to existing launchers

## Test Cases (must pass):
```python
def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Manage local-agents inference backends" in result.output

def test_cli_list_backends():
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--backend", "rapid-mlx"])
    assert result.exit_code == 0

def test_cli_releases():
    runner = CliRunner()
    result = runner.invoke(main, ["releases", "--backend", "rapid-mlx", "--limit", "5"])
    assert result.exit_code == 0

def test_cli_check_updates():
    runner = CliRunner()
    result = runner.invoke(main, ["check-updates", "--backend", "rapid-mlx"])
    assert result.exit_code == 0
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-7-report.md`