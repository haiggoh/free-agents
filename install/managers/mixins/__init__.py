"""Mixin modules for backend managers.

This package provides reusable mixin classes that encapsulate common functionality
shared across backend managers (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm).
"""

from .version_parsing import VersionParsingMixin
from .venv_mixin import VenvMixin
from .pypi_source import PyPISourceMixin
from .github_source import GitHubSourceMixin
from .binary_install import BinaryInstallMixin

__all__ = [
    "VersionParsingMixin",
    "VenvMixin",
    "PyPISourceMixin",
    "GitHubSourceMixin",
    "BinaryInstallMixin",
]