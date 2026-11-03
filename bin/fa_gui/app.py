"""fa_gui/app.py — the NiceGUI pages.

Interim (plan Task 3/4): renders the shared core's HomeScreen — its title, identity line and
action rows straight from session_picker_model — so the launcher qualification exercises a real
UI-to-core round trip. Task 9 replaces this with the designed lanes through fa_gui.adapter.
"""
from __future__ import annotations

import session_picker_model as m


def build() -> None:
    from nicegui import ui

    @ui.page("/")
    def index():
        home = m.HomeScreen(m.Settings())
        with ui.column().classes("w-full max-w-3xl mx-auto p-8 gap-2"):
            ui.label(home.title).classes("text-2xl font-semibold")
            ui.label(home.policy).classes("text-sm opacity-70")
            for action in home.actions():
                ui.label(action.label).classes("text-base")
            ui.label(home.version_line).classes("text-xs opacity-50 italic mt-4")
