#!/usr/bin/env python3
"""Common types and exceptions for backend managers.

This module contains shared types and exceptions used by both
the base class and mixin modules to avoid circular imports.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
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


@dataclass(frozen=True)
class VenvHealth:
    """Whether a backend environment can actually RUN. state: complete | incomplete | broken."""
    state: str
    reason: str = ""


def venv_health(target: Path, binary_name: str, *, runner=subprocess.run) -> VenvHealth:
    """Judge an installed backend by running it, never by file presence alone.

    incomplete = the backend binary is missing or not executable; broken = it is present but its
    interpreter is gone or cannot start. A Homebrew upgrade deletes the patch keg a venv was built
    on and leaves every file in place (measured 2026-10-30, rapid-mlx-0.16.1): only running the
    interpreter tells the two apart. Binary-install backends (no bin/python) are judged on the
    binary and its #! line alone.
    """
    binary = target / "bin" / binary_name
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return VenvHealth("incomplete", f"missing or not executable: {binary}")
    try:
        with binary.open("rb") as handle:
            first = handle.readline(4096)
    except OSError as exc:
        return VenvHealth("broken", f"cannot read {binary}: {exc}")
    if first.startswith(b"#!"):
        words = first[2:].decode("utf-8", "replace").split()
        interpreter = Path(words[0]) if words else None
        if interpreter is not None and not (interpreter.exists() and os.access(interpreter, os.X_OK)):
            return VenvHealth("broken", f"{binary} names a missing interpreter: {interpreter}")
    python = target / "bin" / "python"
    if not os.path.lexists(python):
        return VenvHealth("complete", "")
    if not python.exists():
        return VenvHealth("broken", f"{python} -> {os.readlink(python)} (base interpreter is gone)")
    try:
        probe = runner([str(python), "-c", "import encodings, sys"],
                       capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return VenvHealth("broken", f"{python} cannot start: {exc}")
    if probe.returncode != 0:
        tail = (probe.stderr or probe.stdout or "").strip().splitlines()[-1:] or [f"exit {probe.returncode}"]
        return VenvHealth("broken", f"{python} cannot start: {tail[0]}")
    return VenvHealth("complete", "")


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