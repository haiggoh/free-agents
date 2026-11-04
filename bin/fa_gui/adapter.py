"""fa_gui/adapter.py — the NiceGUI adapter's PURE render plan (nicegui plan Task 7).

No nicegui import here: render_plan(screen) -> list[RenderedControl] is a pure function over
the shared core's screen model, so the capability-coverage tests run without the GUI venv and
the NiceGUI binding (adapter_ui) is a thin layer on top later (Task 9).

Every action of every screen must appear in render_plan OR in GUI_PENDING — that is the
drift guard: a core action omitted or wired inertly in the GUI fails
tests/test_gui_capability_coverage.py visibly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import session_picker_model as m

# semantic action id -> reason it is not rendered yet (must still exist on some screen —
# a stale entry fails the coverage test).
GUI_PENDING: dict[str, str] = {}


@dataclass
class RenderedControl:
    action_id: str          # a.id or f"{type(screen).__name__}.{a.key}"
    kind: str               # "select" | "toggle" | "button" | "nav"
    label: str
    enabled: bool = True


def semantic_id(screen: m.Screen, a: m.Action) -> str:
    return a.id or f"{type(screen).__name__}.{a.key}"


def render_plan(screen: m.Screen) -> list[RenderedControl]:
    """Map every action of the screen to a control. Typed setting rows (choices set) become
    select/toggle — never a button that cycles."""
    out = []
    for a in screen.actions():
        if a.section == "hidden":
            continue        # hidden rows (e.g. the duplicate lane shortcut) never render — 2026-11-04 user test
        sid = semantic_id(screen, a)
        if sid in GUI_PENDING:
            continue
        if a.choices is not None:
            kind = "toggle" if len(a.choices) == 2 and set(a.choices) <= {False, True} else "select"
        elif a.section == "nav":
            kind = "nav"
        else:
            kind = "button"
        out.append(RenderedControl(action_id=sid, kind=kind, label=a.label, enabled=a.enabled))
    return out
