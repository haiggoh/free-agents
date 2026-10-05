#!/usr/bin/env python3
"""Common types and exceptions for backend managers.

This module contains shared types and exceptions used by both
the base class and mixin modules to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BackendInfo:
    """Information about a backend installation.

    Attributes:
        name: Backend identifier (e.g., "rapid-mlx", "vllm-mlx")
        version: Installed version string
        path: Path to the virtual environment
        status: Installation status ("installed", "incomplete", "validated", "error")
        metadata: Additional backend-specific information
    """
    name: str
    version: str
    path: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VersionInfo:
    """Version metadata from a package registry.

    Attributes:
        version: Version string
        source: Registry source (e.g., "pypi", "github")
        release_date: Release date in ISO format
        url: Download/registry URL
        changelog: Optional changelog URL or text
    """
    version: str
    source: str
    release_date: str
    url: str
    changelog: str = ""


class ManagerError(RuntimeError):
    """A fail-closed manager error suitable for a concise CLI message."""
    pass