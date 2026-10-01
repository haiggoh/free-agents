# Task 6 Brief: Implement LitellmManager

## Task Context
This is Task 6 of the Unified Backend Management plan. Create a LitellmManager class that inherits from BackendManager.

## Requirements (from plan)

### Files to Create:
- Create: `install/managers/litellm.py` - LitellmManager class
- Create: `tests/test_litellm_manager.py` - Tests

### Key Interfaces:
- `LitellmManager` class registered as "litellm" via `@register_manager`
- Inherits from `BackendManager`
- `BACKEND_NAME = "litellm"`
- `VENV_PREFIX = "litellm"`
- `DEFAULT_VERSION = "1.60.0"` (recent stable)
- `PACKAGE_NAME = "litellm"`
- `PYPI_URL = "https://pypi.org/pypi/litellm/json"`

### Must Implement:
- `get_installed_versions()` - scan `~/.venvs/litellm-*`
- `get_available_versions(include_prereleases)` - fetch from PyPI
- `get_latest_version(include_prereleases)` - return latest
- `install_version(version, python, refresh_deps, dry_run)` - create venv, install
- `validate_environment(version)` - validate binary, pip check, check config
- `get_packages_to_check()` - ["litellm", "litellm-proxy"]
- `_get_latest_package_version(package)` - fetch from PyPI
- `is_proxy()` - return True (litellm is a proxy)
- `get_config_path()` - find litellm config.yaml

### Global Constraints:
- Python 3.11+
- All versioned environments in `~/.venvs/<backend>-<version>/`
- litellm is a proxy, not a local inference engine
- All code must support `--help` and `--dry-run`

## Test Cases (must pass):
```python
def test_litellm_manager_registration():
    from install.managers import get_manager, list_managers
    assert "litellm" in list_managers()
    mgr = get_manager("litellm")
    assert isinstance(mgr, LitellmManager)
    assert mgr.BACKEND_NAME == "litellm"

def test_litellm_proxy_config():
    mgr = LitellmManager()
    assert mgr.is_proxy() == True
    assert mgr.get_config_path() is not None
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-6-report.md`