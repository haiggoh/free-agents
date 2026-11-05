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

# Sections whose rows are pure navigation: their run() only RETURNS a Nav, so the GUI may call
# it safely to learn the target and render a link. Mutating run-only rows must NOT be in here —
# the GUI never calls run() on a "button" row at render time (auto-sync bug, 2026-11-05).
NAV_SECTIONS = {"nav", "lanes", "tools", "appearance"}


@dataclass
class RenderedControl:
    action_id: str          # a.id or f"{type(screen).__name__}.{a.key}"
    kind: str               # "select" | "toggle" | "button" | "nav" | "group"
    label: str
    enabled: bool = True
    group: str = ""         # "model" | "settings" for kind=="group" header rows


# Lane screens fold their model/settings rows under two collapsible headers, exactly like
# m.rendered_actions does for csl (order: top rows, MODEL_GROUP + model rows, SETTINGS_GROUP +
# settings rows, lanes, nav last). Unlike csl — which OMITS folded rows from its list — the
# GUI always includes the group contents and folds them with CSS (display:none).
LANE_SCREENS = (m.LocalScreen, m.RemoteScreen, m.LowkeyScreen)


def semantic_id(screen: m.Screen, a: m.Action) -> str:
    return a.id or f"{type(screen).__name__}.{a.key}"


def render_plan(screen: m.Screen) -> list[RenderedControl]:
    """Map every action of the screen to a control. Typed setting rows (choices set) become
    select/toggle — never a button that cycles."""
    acts = [a for a in screen.actions() if a.section != "hidden"]   # hidden rows never render — 2026-11-04 user test
    if isinstance(screen, LANE_SCREENS):
        nav = [a for a in acts if a.section == "nav"]
        lanes = [a for a in acts if a.section == "lanes"]
        model = [a for a in acts if a.section == "model"]
        adv = [a for a in acts if a.section == "settings"]
        top = [a for a in acts if a.section not in ("nav", "lanes", "model", "settings")]
        ordered = list(top)
        if model:
            ordered.append(m.MODEL_GROUP)
            ordered += model
        if adv:
            ordered.append(m.SETTINGS_GROUP)
            ordered += adv
        ordered += lanes + nav          # nav always last (csl rule 2026-10-30)
    else:
        ordered = acts
    out = []
    for a in ordered:
        if a.section == "hidden":
            continue
        sid = semantic_id(screen, a)
        if sid in GUI_PENDING:
            continue
        if a is m.MODEL_GROUP or a is m.SETTINGS_GROUP:
            gid = "model" if a is m.MODEL_GROUP else "settings"
            out.append(RenderedControl(action_id=sid, kind="group",
                                       label="Model settings" if gid == "model" else "Advanced",
                                       group=gid))
            continue
        if a.choices is not None:
            kind = "toggle" if len(a.choices) == 2 and set(a.choices) <= {False, True} else "select"
        elif a.section in NAV_SECTIONS:
            kind = "nav"
        else:
            kind = "button"
        out.append(RenderedControl(action_id=sid, kind=kind, label=a.label, enabled=a.enabled))
    return out
