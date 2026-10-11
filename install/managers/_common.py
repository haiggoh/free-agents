#!/usr/bin/env python3
"""Common types and exceptions for backend managers.

This module contains shared types and exceptions used by both
the base class and mixin modules to avoid circular imports.
"""

from __future__ import annotations

import contextlib
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BackendInfo:
    """Information about a backend installation.

    Attributes:
        name: Backend identifier (e.g., "rapid-mlx", "vllm-mlx")
        version: Installed version string
        path: Path to the virtual environment
        status: Installation status ("complete", "incomplete", "validated", "error")
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


# Braille spinner for TTY output
_BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@contextlib.contextmanager
def braille_spinner(message: str = "", *, enabled: bool | None = None):
    """Context manager for a braille spinner animation.

    Args:
        message: Optional message to display alongside the spinner
        enabled: Explicitly enable/disable. Defaults to True if stderr is a TTY.

    Usage:
        with braille_spinner("Creating virtual environment..."):
            # long-running operation
            pass
    """
    if enabled is None:
        enabled = sys.stderr.isatty()

    if not enabled:
        yield
        return

    stop_event = threading.Event()
    frame_idx = 0

    def spin():
        nonlocal frame_idx
        while not stop_event.is_set():
            frame = _BRAILLE_FRAMES[frame_idx % len(_BRAILLE_FRAMES)]
            prefix = f"\r{frame} "
            if message:
                sys.stderr.write(f"{prefix}{message}")
            else:
                sys.stderr.write(prefix)
            sys.stderr.flush()
            frame_idx += 1
            time.sleep(0.08)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join()
        # Clear the spinner line
        sys.stderr.write("\r" + " " * (len(message) + 10) + "\r")
        sys.stderr.flush()