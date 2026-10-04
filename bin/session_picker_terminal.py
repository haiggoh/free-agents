#!/usr/bin/env python3
"""session_picker_terminal.py — Apple Terminal compatibility adapter for Textual.

This module provides a narrow driver adapter that suppresses the DECRQM 2048
(in-band window resize) capability query on Apple Terminal, which otherwise
renders a visible stray 'p' because Apple Terminal doesn't consume the query.

The fix is minimal and scoped:
  * Only affects the 2048 query on positively identified Apple Terminal
  * Preserves Textual's existing 2026 (sync mode) guard
  * Does not disable mouse, resize handling, or other terminal features
  * Uses Textual's supported driver_class parameter for clean integration
  * Unknown/other terminals get normal behavior

Tested against Textual 3.7.1. If upstream fixes this, remove this adapter.
"""

from __future__ import annotations

import os
from textual.drivers.linux_driver import LinuxDriver


class AppleTerminalSafeDriver(LinuxDriver):
    """LinuxDriver subclass that skips the 2048 query on Apple Terminal.

    Textual 3.7.1's LinuxDriver emits two DECRQM queries in start_application_mode:
      1. 2026 (sync mode) - already guarded for Apple Terminal
      2. 2048 (in-band window resize) - unconditional, causes stray 'p' on Apple Terminal

    This subclass only overrides _query_in_band_window_resize to be a no-op
    when TERM_PROGRAM=Apple_Terminal. All other behavior is inherited.
    """

    def _query_in_band_window_resize(self) -> None:
        """Skip the 2048 DECRQM query on Apple Terminal to avoid stray 'p'.

        Apple Terminal doesn't consume this query and renders the trailing 'p'
        visibly. Other terminals handle it correctly (consume or ignore).
        """
        if os.environ.get("TERM_PROGRAM", "") == "Apple_Terminal":
            # Apple Terminal: suppress the 2048 query entirely
            return
        # Non-Apple: delegate to parent implementation
        super()._query_in_band_window_resize()


def get_driver_class() -> type[LinuxDriver] | None:
    """Return the appropriate driver class for the current terminal.

    Returns AppleTerminalSafeDriver only when:
      - We're on a Unix-like system (LinuxDriver is the normal driver)
      - TERM_PROGRAM is explicitly Apple_Terminal

    Returns None otherwise, letting Textual choose its default driver
    (which handles Windows, custom TEXTUAL_DRIVER, etc. correctly).
    """
    # Only apply on Unix-like systems where LinuxDriver would be selected
    if os.name != "posix":
        return None

    # Only activate for positively identified Apple Terminal
    if os.environ.get("TERM_PROGRAM", "") != "Apple_Terminal":
        return None

    return AppleTerminalSafeDriver