# Task 4 Brief: Implement OMLXManager

## Task Context
This is Task 4 of the Unified Backend Management plan. Create an OMLXManager class that inherits from BackendManager.

## Requirements (from plan)

### Files to Create:
- Create: `install/managers/omlx.py` - OMLXManager class
- Create: `tests/test_omlx_manager.py` - Tests

### Key Interfaces:
- `OMLXManager` class registered as "omlx" via `@register_manager`
- Inherits from `BackendManager`
- `BACKEND_NAME = "omlx"`
- `VENV_PREFIX = "omlx"` (not a venv - oMLX is a standalone binary via Homebrew)
- `DEFAULT_VERSION = "0.7.0"`
- Not a PyPI package - distributed via Homebrew (jundot/omlx) and GitHub

### Must Implement:
- `get_installed_versions()` - check Homebrew installation and manual installs
- `get_available_versions(include_prereleases)` - fetch from GitHub releases
- `get_latest_version(include_prereleases)` - return latest
- `install_version(version, python, refresh_deps, dry_run)` - install via Homebrew
- `validate_environment(version)` - verify binary exists and version matches
- `get_packages_to_check()` - ["omlx"] (not a PyPI package)
- `get_package_source(package)` - "homebrew"
- `get_version_source()` - "github"

### Global Constraints:
- Python 3.11+
- oMLX installed via Homebrew (not venv)
- All code must support `--help` and `--dry-run`

## Test Cases (must pass):
```python
def test_omlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "omlx" in list_managers()
    mgr = get_manager("omlx")
    assert isinstance(mgr, OMLXManager)
    assert mgr.BACKEND_NAME == "omlx"

def test_omlx_get_available_versions():
    mgr = OMLXManager()
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b'[{"tag_name": "v0.7.0"}, {"tag_name": "v0.6.4"}]'
        versions = mgr.get_available_versions()
        assert "0.7.0" in versions

def test_omlx_uses_homebrew():
    mgr = OMLXManager()
    assert mgr.get_package_source("omlx") == "homebrew"
    assert mgr.get_version_source() == "github"
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-4-report.md`