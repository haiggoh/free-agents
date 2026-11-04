"""fa_gui/app.py — the NiceGUI pages (nicegui plan Task 9: Home + Local + Remote lanes on the
real shared core). Vertical slice: navigation, searchable catalogue, typed settings that
persist to the state file, launch through picker_core.command_for.

Element ids are the test contract: nav-<target>, search, model-row[data-model], launch,
setting-<action_id>, empty-state. The interim labels-only page (Tasks 3/4) is replaced here."""
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

    def nav_bar(target: str) -> None:
        with ui.row().classes("gap-3 items-center"):
            for t, label in (("home", "🏠 Home"), ("local", "🖥 Local"), ("remote", "📡 Remote")):
                ui.link(label, f"/{t}").props(f'id="nav-{t}"')

    def render_actions(scr) -> None:
        """render_plan controls; typed setting rows become real selects that persist via Savers."""
        s = get_settings()
        for c in render_plan(scr):
            action = next((a for a in scr.actions() if (a.id or f"{type(scr).__name__}.{a.key}") == c.action_id), None)
            if c.kind in ("select", "toggle") and action is not None and action.choices is not None:
                def make_put(a):
                    def put(v):
                        if a.put:
                            a.put(v)
                    return put
                sel = ui.select(list(action.choices), label=c.label)
                sel.props(f'id="setting-{c.action_id}"')
                sel.on_value_change(lambda e, a=action: make_put(a)(e.value))
            else:
                ui.label(c.label)

    def render_shell(target: str, title: str, subtitle: str):
        s = get_settings()
        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3"):
            ui.label(title).props('id="title"').classes("text-2xl font-bold")
            ui.label(subtitle).classes("text-sm opacity-70")
            nav_bar(target)
            return s

    @ui.page("/")
    def home():
        s = get_settings()
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, loader.cache).screen
        render_shell("home", scr.title, "Claude Code on free inference — local models, or free cloud APIs")
        render_actions(scr)
        ui.label(f"free-agents v{m.plugin_version()}").classes("text-xs italic opacity-50 mt-4")

    def make_lane(target: str, kind: str, loader_fn) -> None:
        @ui.page(f"/{target}")
        def page():
            # sync page function: NiceGUI 3.x renders async pages empty on first paint; the
            # async work (inventory load) runs in the ui.timer callback below.
            _lane_body(target)

        def _lane_body(target: str):
            s = render_shell(target, f"{target.capitalize()} sessions", "")
            scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), s, loader.cache).screen
            render_actions(scr)
            search = ui.input(placeholder="type to filter models").classes("gui-search w-full")
            # NEVER set id on Quasar elements (input/button) — NiceGUI 3.19 drops them from
            # the DOM (measured via playwright 2026-11-04); tests select by class instead.
            list_box = ui.column().classes("w-full gap-1").props('id="model-list"')
            state = {"selected": None}

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

            def select_model(mid):
                state["selected"] = mid
                launch.props["disabled"] = not mid

            def paint(models, query):
                list_box.clear()
                shown = 0
                with list_box:
                    for mdl in models:
                        name = getattr(mdl, "display", None) or getattr(mdl, "alias", "")
                        mid = getattr(mdl, "model_id", "") or getattr(mdl, "alias", "")
                        if query and query.lower() not in f"{name} {mid}".lower():
                            continue
                        shown += 1
                        row = ui.label(f"{name}  ·  {mid}").props(f'class="model-row" data-model="{mid}"')
                        row.on("click", lambda e, m=mid: select_model(m))
                    if not shown:
                        ui.label("No models — every provider needs an API key (open API keys)"
                                ).props('id="empty-state"')

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
