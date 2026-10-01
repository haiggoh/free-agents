# Task 5 Brief: Implement LlamaCppManager

## Task Context
This is Task 5 of the Unified Backend Management plan. Create a LlamaCppManager class that inherits from BackendManager.

## Requirements (from plan)

### Files to Create:
- Create: `install/managers/llama_cpp.py` - LlamaCppManager class
- Create: `tests/test_llama_cpp_manager.py` - Tests

### Key Interfaces:
- `LlamaCppManager` class registered as "llama-cpp" via `@register_manager`
- Inherits from `BackendManager`
- `BACKEND_NAME = "llama-cpp"`
- `VENV_PREFIX = "llama-cpp"` (binary installation, not venv)
- `DEFAULT_VERSION = "b5000"` (recent stable build)
- Not a PyPI package - distributed via GitHub releases (ggml-org/llama.cpp)

### Must Implement:
- `get_installed_versions()` - check `~/.local/llama-cpp/` and Homebrew
- `get_available_versions(include_prereleases)` - fetch from GitHub releases
- `get_latest_version(include_prereleases)` - return latest
- `install_version(version, python, refresh_deps, dry_run)` - download and extract binary
- `validate_environment(version)` - verify binary exists and version matches
- `get_packages_to_check()` - ["llama-cpp"] (binary, not PyPI)
- `get_package_source(package)` - "github_binary"

### Global Constraints:
- Python 3.11+
- Binary downloaded from GitHub releases (macOS ARM64)
- All code must support `--help` and `--dry-run`

## Test Cases (must pass):
```python
def test_llama_cpp_manager_registration():
    from install.managers import get_manager, list_managers
    assert "llama-cpp" in list_managers()
    mgr = get_manager("llama-cpp")
    assert isinstance(mgr, LlamaCppManager)
    assert mgr.BACKEND_NAME == "llama-cpp"

def test_llama_cpp_binary_download():
    mgr = LlamaCppManager()
    assert mgr.get_package_source("llama-cpp") == "github_binary"
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-5-report.md`