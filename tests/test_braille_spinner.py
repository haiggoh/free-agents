#!/usr/bin/env python3
"""Tests for braille spinner TTY behavior."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

# Import the spinner
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
from managers._common import braille_spinner


def test_spinner_disabled_when_not_tty():
    """Test that spinner doesn't output when stderr is not a TTY."""
    # Mock stderr to not be a TTY
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: False

    with patch('sys.stderr', fake_stderr):
        with braille_spinner("Testing..."):
            pass

    # Should produce no output
    output = fake_stderr.getvalue()
    assert output == "", f"Expected no output when not TTY, got: {repr(output)}"
    print("PASS: Spinner produces no output when stderr is not a TTY")
    return True


def test_spinner_enabled_when_tty():
    """Test that spinner outputs when stderr is a TTY."""
    # Mock stderr to be a TTY
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: True

    with patch('sys.stderr', fake_stderr):
        with braille_spinner("Testing..."):
            pass

    # Should produce some output (the spinner frames and clearing)
    output = fake_stderr.getvalue()
    assert len(output) > 0, "Expected output when TTY, got empty"
    # Should contain the clear sequence
    assert "\r" in output, "Expected carriage return for spinner animation"
    print("PASS: Spinner produces output when stderr is a TTY")
    return True


def test_spinner_explicit_disabled():
    """Test that explicit enabled=False disables spinner even on TTY."""
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: True

    with patch('sys.stderr', fake_stderr):
        with braille_spinner("Testing...", enabled=False):
            pass

    output = fake_stderr.getvalue()
    assert output == "", f"Expected no output when explicitly disabled, got: {repr(output)}"
    print("PASS: Explicit enabled=False disables spinner")
    return True


def test_spinner_explicit_enabled():
    """Test that explicit enabled=True enables spinner even on non-TTY."""
    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: False

    with patch('sys.stderr', fake_stderr):
        with braille_spinner("Testing...", enabled=True):
            pass

    output = fake_stderr.getvalue()
    assert len(output) > 0, "Expected output when explicitly enabled"
    print("PASS: Explicit enabled=True enables spinner on non-TTY")
    return True


if __name__ == "__main__":
    from pathlib import Path
    passed = 0
    failed = 0

    for test_func in [test_spinner_disabled_when_not_tty, test_spinner_enabled_when_tty,
                      test_spinner_explicit_disabled, test_spinner_explicit_enabled]:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAIL: {test_func.__name__} raised {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)