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
        # Continuation plan Phase 2: page generations are PER CLIENT — one browser window
        # navigating away must not stale another window's in-flight load. Key "" is the
        # legacy client-less generation (old single-client contract).
        self._page_gen: dict[str, int] = {}
        # Identical concurrent loads for the same kind are COALESCED: one fn run, every
        # waiter gets the same result (Phase 2 — duplicate cache-miss loads were not coalesced).
        self._inflight: dict[str, asyncio.Task] = {}

    def leave_page(self, client: str | None = None) -> None:
        """Call when navigating away: results in flight for that client's old page come back
        Stale. No argument = the legacy client-less generation only."""
        key = client or ""
        self._page_gen[key] = self._page_gen.get(key, 0) + 1

    async def load(self, kind: str, fn: Callable[[], Any], client: str | None = None) -> Any:   # T | Stale
        """Return the cached value for `kind` if present, else run `fn` off-thread (coalesced
        with any identical in-flight load).

        The result is applied only if no newer load for the same kind started and the calling
        client's page generation did not change meanwhile; otherwise STALE is returned.
        """
        if kind in self.cache:
            return self.cache[kind]
        key = client or ""
        page_gen = self._page_gen.get(key, 0)
        kind_gen = self._kind_gen.get(kind, 0)
        task = self._inflight.get(kind)
        creator = task is None
        if creator:
            kind_gen += 1
            self._kind_gen[kind] = kind_gen
            task = asyncio.ensure_future(asyncio.to_thread(fn))
            self._inflight[kind] = task
        result = await task
        if self._page_gen.get(key, 0) != page_gen:
            return STALE
        if creator:
            self._inflight.pop(kind, None)
            if self._kind_gen.get(kind, 0) != kind_gen:
                return STALE
            self.cache[kind] = result
        return result
