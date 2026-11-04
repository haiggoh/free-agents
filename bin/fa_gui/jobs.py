"""fa_gui/jobs.py — async loading for the NiceGUI frontend (nicegui plan Task 9).

`Loader.load(kind, fn)` runs blocking core loaders (subprocess inventory calls) off the UI
thread and shares the Textual app's cache semantics: a kind loads once per app run (no reload
on Back). A result whose kind-generation or page-generation is no longer current returns
`Stale` and must not be applied — the guard against late inventory painting over a newer view.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class Stale:
    """Marker: this load's result is no longer current (a newer load or a page change won)."""

    def __repr__(self) -> str:
        return "Stale()"


STALE = Stale()


class Loader:
    def __init__(self, cache: dict | None = None):
        self.cache: dict = cache if cache is not None else {}
        self._kind_gen: dict[str, int] = {}
        self._page_gen: int = 0

    def leave_page(self) -> None:
        """Call when navigating away: results in flight for the old page come back Stale."""
        self._page_gen += 1

    async def load(self, kind: str, fn: Callable[[], Any]) -> Any:   # T | Stale
        """Return the cached value for `kind` if present, else run `fn` off-thread.

        The result is applied only if no newer load for the same kind started and no page
        change happened meanwhile; otherwise STALE is returned.
        """
        if kind in self.cache:
            return self.cache[kind]
        kind_gen = self._kind_gen.get(kind, 0) + 1
        self._kind_gen[kind] = kind_gen
        page_gen = self._page_gen
        result = await asyncio.to_thread(fn)
        if self._kind_gen.get(kind, 0) != kind_gen or self._page_gen != page_gen:
            return STALE
        self.cache[kind] = result
        return result
