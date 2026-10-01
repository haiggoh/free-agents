# Task 2 Brief: Implement RapidMLXManager (refactor existing)

## Task Context
This is Task 2 of the Unified Backend Management plan. Refactor the existing `manage-rapid-mlx.py` into a `RapidMLXManager` class that inherits from `BackendManager`.

## Requirements (from plan)

### Files to Create/Modify:
- Create: `install/managers/rapid_mlx.py` - RapidMLXManager class
- Modify: `install/manage-rapid-mlx.py` - Thin wrapper for backward compatibility
- Create: `tests/test_rapid_mlx_manager.py` - Tests

### Key Interfaces:
- `RapidMLXManager` class registered as "rapid-mlx" via `@register_manager`
- Inherits from `BackendManager`
- `BACKEND_NAME = "rapid-mlx"`
- `VENV_PREFIX = "rapid-mlx"`
- `DEFAULT_VERSION = "0.15.3"`
- `PACKAGE_NAME = "rapid-mlx"`
- `PYPI_URL = "https://pypi.org/pypi/rapid-mlx/json"`

### Must Implement:
- `get_installed_versions()` - scan `~/.venvs/rapid-mlx-*`
- `get_available_versions(include_prereleases)` - fetch from PyPI
- `get_latest_version(include_prereleases)` - return latest
- `install_version(version, python, refresh_deps, dry_run)` - create venv, install
- `validate_environment(version)` - validate binary, pip check
- `get_packages_to_check()` - ["rapid-mlx", "mlx", "mlx-lm"]
- `_get_latest_package_version(package)` - fetch from PyPI
- `promote_pins(version, dry_run)` - promote repository pins (Rapid-MLX specific)
- `version_key(value)` - parse version for sorting

### Global Constraints:
- Python 3.11+
- All versioned environments in `~/.venvs/<backend>-<version>/`
- Pin promotion requires clean git worktree
- All code must support `--help` and `--dry-run`

## Test Cases (must pass):
```python
def test_rapid_mlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "rapid-mlx" in list_managers()
    mgr = get_manager("rapid-mlx")
    assert isinstance(mgr, RapidMLXManager)
    assert mgr.BACKEND_NAME == "rapid-mlx"
    assert mgr.VENV_PREFIX == "rapid-mlx"

def test_rapid_mlx_get_installed_versions():
    mgr = RapidMLXManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)

def test_rapid_mlx_get_available_versions():
    # Mock PyPI response
    versions = mgr.get_available_versions()
    assert isinstance(versions, list)

def test_rapid_mlx_get_packages_to_check():
    mgr = RapidMLXManager()
    packages = mgr.get_packages_to_check()
    assert "rapid-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages

def test_rapid_mlx_version_key():
    mgr = RapidMLXManager()
    versions = ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-2-report.md`