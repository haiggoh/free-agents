# Task 3 Brief: Implement VLLMMLXManager

## Task Context
This is Task 3 of the Unified Backend Management plan. Create a VLLMMLXManager class that inherits from BackendManager.

## Requirements (from plan)

### Files to Create:
- Create: `install/managers/vllm_mlx.py` - VLLMMLXManager class
- Create: `tests/test_vllm_mlx_manager.py` - Tests

### Key Interfaces:
- `VLLMMLXManager` class registered as "vllm-mlx" via `@register_manager`
- Inherits from `BackendManager`
- `BACKEND_NAME = "vllm-mlx"`
- `VENV_PREFIX = "vllm-mlx"`
- `DEFAULT_VERSION = "0.5.0"`
- `PACKAGE_NAME = "vllm-mlx"`
- `PYPI_URL = "https://pypi.org/pypi/vllm-mlx/json"`

### Must Implement:
- `get_installed_versions()` - scan `~/.venvs/vllm-mlx-*`
- `get_available_versions(include_prereleases)` - fetch from PyPI, fallback to GitHub releases
- `get_latest_version(include_prereleases)` - return latest
- `install_version(version, python, refresh_deps, dry_run, source)` - create venv, install from PyPI or GitHub
- `validate_environment(version)` - validate binary, pip check
- `get_packages_to_check()` - ["vllm-mlx", "mlx", "mlx-lm", "mlx-vlm"]
- `_get_latest_package_version(package)` - fetch from PyPI
- `needs_fork_patches(version)` - check if version needs local fork patches (v0.4.x needs patches, v0.5.0+ integrated)

### Global Constraints:
- Python 3.11+
- All versioned environments in `~/.venvs/<backend>-<version>/`
- vllm-mlx local patches don't apply to v0.5.0+ (already integrated)
- All code must support `--help` and `--dry-run`

## Test Cases (must pass):
```python
def test_vllm_mlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "vllm-mlx" in list_managers()
    mgr = get_manager("vllm-mlx")
    assert isinstance(mgr, VLLMMLXManager)
    assert mgr.BACKEND_NAME == "vllm-mlx"
    assert mgr.VENV_PREFIX == "vllm-mlx"

def test_vllm_mlx_get_installed_versions():
    mgr = VLLMMLXManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)

def test_vllm_mlx_get_available_versions():
    mgr = VLLMMLXManager()
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b'{"releases": {"0.5.0": [], "0.4.1": []}}'
        versions = mgr.get_available_versions()
        assert isinstance(versions, list)
        assert "0.5.0" in versions

def test_vllm_mlx_get_packages_to_check():
    mgr = VLLMMLXManager()
    packages = mgr.get_packages_to_check()
    assert "vllm-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages
    assert "mlx-vlm" in packages

def test_vllm_mlx_patch_handling():
    """Test that version-specific patch sets are handled correctly."""
    mgr = VLLMMLXManager()
    assert mgr.needs_fork_patches("0.4.1") == True
    assert mgr.needs_fork_patches("0.5.0") == False
```

## Report File
Write report to: `/Users/bra0002h/ClaudeWorkspace/local-agents/.superpowers/sdd/unified-backend-management/task-3-report.md`