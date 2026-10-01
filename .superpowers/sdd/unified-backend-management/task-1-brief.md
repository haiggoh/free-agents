# Task 1 Brief: Design Backend Manager Abstraction

## Task Context
This is Task 1 of the Unified Backend Management plan. The goal is to create a common `BackendManager` abstraction that all backend managers (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) will inherit from.

## Requirements (from plan)

### Files to Create:
- `install/managers/base.py` - BackendManager base class
- `install/managers/__init__.py` - Registry for managers
- `install/manage-backend.py` - Unified CLI entry point skeleton
- `tests/test_manage_backend.py` - Tests for base class

### Key Interfaces:
- `BackendManager` abstract base class with:
  - `BACKEND_NAME`, `VENV_PREFIX`, `DEFAULT_VERSION` class attributes
  - `get_installed_versions()` → list[str]
  - `get_available_versions(include_prereleases)` → list[str]
  - `get_latest_version(include_prereleases)` → str
  - `install_version(version, python, refresh_deps, dry_run)` → BackendInfo
  - `validate_environment(version)` → BackendInfo
  - `get_packages_to_check()` → list[str]
  - `check_updates()` → list[tuple[str, str, str]]
  - `_get_latest_package_version(package)` → str (abstract)
  - `_version_newer(latest, installed)` → bool
  - `run_command()` helper

- `BackendInfo` dataclass: name, version, path, status, metadata
- `VersionInfo` dataclass: version, source, release_date, url, changelog
- Registry in `__init__.py` with `register_manager`, `get_manager`, `list_managers`

### Global Constraints:
- Python 3.11+
- All versioned environments in `~/.venvs/<backend>-<version>/`
- All update checks read-only (notify only)
- No breaking changes to existing launchers
- All code must support `--help` and `--dry-run`
- Pin promotion requires clean git worktree

## Test Cases (must pass):
```python
def test_backend_manager_base_class():
    mgr = ConcreteManager()
    assert mgr.BACKEND_NAME == "test"
    assert hasattr(mgr, 'install_version')
    # ... etc

def test_backend_info_dataclass():
    info = BackendInfo(name="test", version="1.0.0", path="/tmp/test", status="installed")
    assert info.name == "test"

def test_version_info_dataclass():
    vinfo = VersionInfo(version="1.0.0", source="pypi", release_date="2026-01-01")
    assert vinfo.version == "1.0.0"
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-1-report.md`

Report must include:
- Commits made
- Test results (command + output)
- Self-review findings
- Any concerns
