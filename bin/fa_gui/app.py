"""fa_gui/app.py — the NiceGUI pages (nicegui plan Task 9, user-tested feedback 2026-11-04).

Layout rule (user): ONE vertical list, one item per row, replicating the Textual csl layout
as closely as possible so parity is easy to check. No horizontal link row, no dropdowns —
settings are left/right cycle rows with ◀ ▶ indicators (like csl), model lists are
harmonica-style expand/collapse provider groups. A tile-based theme is a separate optional
version later (toggle in the themes submenu).

Element classes are the test contract: nav-row[data-target], action-row, cycle-row with
cycle-left / cycle-value / cycle-right, gui-search input, group-header[data-group],
model-row[data-model], gui-launch, empty-state. Home answers on / and /home.

NEVER set id on Quasar elements (input/button) — NiceGUI 3.19 drops them from the DOM
(measured 2026-11-04); use classes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import session_picker_model as m


def build() -> None:
    from nicegui import ui
    import picker_core as pc
    import session_menu_state as sms
    from fa_gui.adapter import render_plan
    from fa_gui.jobs import STALE, Loader

    loader = Loader()
    shared: dict = {}

    def get_settings():
        if "settings" not in shared:
            cfg = Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR)
            shared["savers"] = pc.Savers(cfg)
            shared["settings"] = pc.build_settings(None, shared["savers"])
        return shared["settings"]

    def render_rows(scr) -> None:
        """One vertical row per action, Textual-style. Cycle rows for typed settings with
        ◀ ▶ indicators; plain actions as clickable rows; nav rows last."""
        s = get_settings()
        for c in render_plan(scr):
            action = next((a for a in scr.actions()
                           if (a.id or f"{type(scr).__name__}.{a.key}") == c.action_id), None)
            if c.kind in ("select", "toggle") and action is not None and action.choices is not None:
                with ui.row().classes("cycle-row items-center gap-2 w-full"):
                    val = ui.label(_cycle_label(action)).classes("cycle-value")
                    ui.button("◀", on_click=lambda a=action, v=val: (a.step(-1), v.set_text(_cycle_label(a)))).classes("cycle-left")
                    ui.button("▶", on_click=lambda a=action, v=val: (a.step(1), v.set_text(_cycle_label(a)))).classes("cycle-right")
            else:
                # an action whose run() returns a Nav is a navigation row — render it as a
                # real link (the inert-button bug from user testing 2026-11-04)
                res = action.run() if (action is not None and action.run is not None) else None
                if isinstance(res, m.Nav):
                    target = "home" if res.target == "home" else res.target
                    ui.link(c.label, f"/{target}").classes("action-row nav-row").props(f'data-target="{target}"')
                else:
                    ui.button(c.label, on_click=lambda a=action: _run_action(a)).classes("action-row")

    def _cycle_label(action) -> str:
        name = action.name or action.label.split(":")[0]
        try:
            v = action.value() if action.value else None
        except Exception:
            v = None
        if action.choice_label and v is not None:
            v = action.choice_label(v)
        return f"{name}: {v}"

    def _run_action(action) -> None:
        if action is not None and action.run is not None:
            action.run()

    def render_shell(title: str, subtitle: str):
        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-2"):
            ui.label(title).props('id="title"').classes("text-2xl font-bold")
            ui.label(subtitle).classes("text-sm opacity-70")
            return ui.column().classes("w-full gap-1")

    def home_page():
        s = get_settings()
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, loader.cache).screen
        body = render_shell(scr.title, "Claude Code on free inference — local models, or free cloud APIs")
        with body:
            render_rows(scr)
        ui.label(f"free-agents v{m.plugin_version()}").classes("text-xs italic opacity-50 mt-4")

    @ui.page("/")
    @ui.page("/home")
    def home():
        home_page()

    def make_lane(target: str, kind: str, loader_fn) -> None:
        @ui.page(f"/{target}")
        def page():
            s = render_shell(f"{target.capitalize()} sessions", "")
            scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache).screen
            with s:
                render_rows(scr)
                search = ui.input(placeholder="type to filter models").classes("gui-search w-full")
                list_box = ui.column().classes("w-full gap-1")
                state = {"selected": None, "open": set()}

                def do_launch():
                    if not state["selected"]:
                        return
                    stub = os.environ.get("FA_GUI_LAUNCH_STUB")
                    req = m.LaunchRequest(target, [target, "--model", state["selected"]], {}, state["selected"])
                    argv = pc.command_for(req)
                    if stub:
                        Path(stub).write_text(json.dumps({"argv": argv}))

                launch = ui.button("Launch", on_click=do_launch).classes("gui-launch")
                launch.props["disabled"] = True

                def paint(models, query):
                    list_box.clear()
                    with list_box:
                        # group models by provider like the Textual accordion
                        groups: dict[str, list] = {}
                        for mdl in models:
                            prov = getattr(mdl, "provider", "local")
                            groups.setdefault(prov, []).append(mdl)
                        for prov, rows in groups.items():
                            shown_rows = [r for r in rows
                                          if not query or query.lower() in
                                          (getattr(r, "display", "") or getattr(r, "alias", "") or "").lower()]
                            if not shown_rows and query:
                                continue
                            open_ = prov in state["open"]
                            hdr = ui.button(("▾ " if open_ else "▸ ") + f"{prov} ({len(shown_rows)})",
                                            on_click=lambda p=prov: toggle_group(p)).classes("group-header")
                            hdr.props["data-group"] = prov
                            if open_:
                                for mdl in shown_rows:
                                    name = getattr(mdl, "display", None) or getattr(mdl, "alias", "")
                                    mid = getattr(mdl, "model_id", "") or getattr(mdl, "alias", "")
                                    row = ui.button(f"{name}  ·  {mid}",
                                                    on_click=lambda m=mid: select_model(m)).classes("model-row")
                                    row.props["data-model"] = mid
                        if not list_box.default_slot.children:
                            ui.label("No models — every provider needs an API key (open API keys)"
                                    ).props('class="empty-state"')

                def toggle_group(prov):
                    if prov in state["open"]:
                        state["open"].discard(prov)
                    else:
                        state["open"].add(prov)
                    paint(loader.cache.get(kind) or [], search.value or "")

                def select_model(mid):
                    state["selected"] = mid
                    launch.props["disabled"] = False

                def on_search(e):
                    paint(loader.cache.get(kind) or [], e.value)

                search.on_value_change(on_search)

                async def fill():
                    result = await loader.load(kind, loader_fn)
                    if result is STALE:
                        return
                    paint(result, search.value or "")

                ui.timer(0.05, fill, once=True)
                paint(loader.cache.get(kind) or [], "")

    make_lane("local", "local", pc.load_local_models)
    make_lane("remote", "remote", pc.load_remote_agents)
