"""fa_gui/app.py — the NiceGUI pages (nicegui plan Task 9, user-tested feedback 2026-11-04).

Layout rules (user 2026-11-04, M3-flavored): ONE vertical list, one item per row, replicating
the Textual csl layout as closely as possible for parity. Menu rows are plain black text that
turns blue ONLY on hover, never underlined. Cycle rows are bare inline < and > icons (no box,
left arrow BEFORE the option); binary toggles get a switch (circle in oval), no arrows. Flat
small buttons, no soft shadows. Unknown routes render an empty page with a Back-to-Home row —
never a 404/dead end.

Element classes are the test contract: .nav-row[data-target], .action-row, .cycle-row with
.cycle-arrow/.cycle-value, .gui-search input, .group-header[data-group], .model-row[data-model],
.gui-launch, .empty-state. Home answers on / and /home.

NEVER set id on Quasar elements (input/button) — NiceGUI 3.19 drops them from the DOM
(measured 2026-11-04); use classes. ui.add_head_html in build() needs shared=True with @ui.page.
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

    ui.add_head_html("""<style>
      .menu-item { color: #000 !important; text-decoration: none !important; background: none !important;
                   border: none !important; box-shadow: none !important; padding: 2px 6px; cursor: pointer; }
      .menu-item:hover { color: #0b57d0 !important; }   /* M3-ish blue, not the default link blue */
      .cycle-arrow { color: #000 !important; background: none !important; border: none !important;
                     box-shadow: none !important; padding: 0 3px; cursor: pointer; font-weight: bold; }
      .cycle-arrow:hover { color: #0b57d0 !important; }
      .switch { width: 34px; height: 18px; border-radius: 9px; background: #bbb; position: relative;
                cursor: pointer; display: inline-block; vertical-align: middle; }
      .switch.on { background: #000; }
      .switch .knob { width: 14px; height: 14px; border-radius: 50%; background: #fff; position: absolute;
                      top: 2px; left: 2px; }
      .switch.on .knob { left: 18px; }
      .btn-flat { box-shadow: none !important; border-radius: 4px !important; padding: 2px 8px !important;
                  text-transform: none !important; }
      /* M3 primary action (filled dark, white text) — only for Go launch / Choose model */
      .btn-primary { background: #000 !important; color: #fff !important; border: none !important;
                     border-radius: 20px !important; padding: 6px 16px !important; box-shadow: 0 1px 2px rgba(0,0,0,.2) !important; }
      .btn-primary:hover { background: #333 !important; }
      .cycle-row { display: flex; align-items: center; }
      /* switch: knob always bright white + subtle M3 shadow so it is visible on any track */
      .switch .knob { background: #fff !important; box-shadow: 0 1px 3px rgba(0,0,0,.4) !important; }
    </style>""", shared=True)

    loader = Loader()
    shared: dict = {}

    def get_settings():
        if "settings" not in shared:
            cfg = Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR)
            shared["savers"] = pc.Savers(cfg)
            shared["settings"] = pc.build_settings(None, shared["savers"])
        return shared["settings"]

    def cycle_label(action) -> str:
        # label text must match what csl shows on toggle (user 2026-11-04): choice_label first,
        # then the model's own label tables (Auto-mode 0/1/2 → Off/Classifier/…, etc.)
        name = action.name or action.label.split(":")[0]
        try:
            v = action.value() if action.value else None
        except Exception:
            v = None
        if action.choice_label and v is not None:
            v = action.choice_label(v)
        elif v is not None:
            for table in (getattr(m, "AUTO_MODE_LABELS", {}), getattr(m, "CLASSIFIER_SOURCE_LABELS", {})):
                if v in table:
                    v = table[v]
                    break
            else:
                if isinstance(v, bool):
                    v = "ON" if v else "OFF"
        return f"{name}: {v}"

    def render_rows(scr) -> None:
        for c in render_plan(scr):
            action = next((a for a in scr.actions()
                           if (a.id or f"{type(scr).__name__}.{a.key}") == c.action_id), None)
            if c.kind in ("select", "toggle") and action is not None and action.choices is not None:
                choices = list(action.choices)
                binary = len(choices) == 2 and set(choices) <= {False, True, 0, 1}
                if binary:
                    # switch AFTER the action item, inline in the same row; the knob child div
                    # is what makes the circle visible (it was missing entirely before)
                    on = bool(action.value()) if action.value else False
                    with ui.row().classes("cycle-row items-center gap-2 w-full"):
                        ui.label(cycle_label(action)).classes("cycle-value")
                        sw = ui.element("div").classes(f"switch{' on' if on else ''}")
                        with sw:
                            ui.element("div").classes("knob")
                        sw.on("click", lambda a=action, s=sw: _toggle_binary(a, s))
                else:
                    with ui.row().classes("cycle-row items-center gap-1 w-full"):
                        left = ui.label("<").classes("cycle-arrow cycle-left")
                        val = ui.label(cycle_label(action)).classes("cycle-value")
                        right = ui.label(">").classes("cycle-arrow cycle-right")
                        left.on("click", lambda a=action, v=val: _cycle(a, v, -1))
                        right.on("click", lambda a=action, v=val: _cycle(a, v, +1))
            else:
                res = action.run() if (action is not None and action.run is not None) else None
                if isinstance(res, m.Nav):
                    target = "home" if res.target == "home" else res.target
                    # ui.link for real SPA navigation; .menu-item CSS overrides the link look
                    row = ui.link(c.label, f"/{target}").classes("menu-item action-row nav-row")
                    row.props["data-target"] = target
                else:
                    row = ui.label(c.label).classes("menu-item action-row")
                    if action is not None and action.key == "c" and shared.get("toggle_list"):
                        row.on("click", lambda: shared["toggle_list"]())   # "Choose model" toggles the list
                    else:
                        row.on("click", lambda a=action: _run_action(a))

    def _run_action(action) -> None:
        if action is not None and action.run is not None:
            action.run()

    def _cycle(action, val, d: int) -> None:
        if action.step:
            action.step(d)
        val.set_text(cycle_label(action))

    def _toggle_binary(action, sw) -> None:
        if action.step:
            action.step(1)
        sw.set_class("on" if (action.value() if action.value else False) else "")

    def render_shell(title: str, subtitle: str):
        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-2"):
            ui.label(title).props('id="title"').classes("text-2xl font-bold")
            if subtitle:
                ui.label(subtitle).classes("text-sm opacity-70").props('id="subtitle"')
            return ui.column().classes("w-full gap-1")

    @ui.page("/")
    @ui.page("/home")
    async def home(client):
        # first launch must not look dead: build the UI only once the websocket is up
        # (user 2026-11-04: menu rendered but no hover/click on first launch)
        await client.connected()
        s = get_settings()
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, loader.cache).screen
        body = render_shell(scr.title, scr.policy if isinstance(scr.policy, str) else "")
        with body:
            render_rows(scr)
        ui.label(f"free-agents v{m.plugin_version()}").classes("text-xs italic opacity-50 mt-4")

    def make_lane(target: str, kind: str, loader_fn) -> None:
        @ui.page(f"/{target}")
        async def page(client):
            await client.connected()
            scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache).screen
            body = render_shell(scr.title, scr.policy if isinstance(scr.policy, str) else "")
            shared["toggle_list"] = lambda: None     # placeholder; reassigned below before any click can fire
            with body:
                render_rows(scr)
                search = ui.input(placeholder="type to filter models").classes("gui-search w-full")
                list_box = ui.column().classes("w-full gap-1")
                list_box.props["style"] = "display:none"     # hidden until "Choose model" (csl parity)
                info = ui.label("").classes("text-xs opacity-70 border-t pt-2 mt-2 w-full").props('id="infobox"')
                state = {"open": set()}

                def do_launch(mid, mdl=None):
                    # csl launches the model directly on row click — no Launch button (user 2026-11-04)
                    stub = os.environ.get("FA_GUI_LAUNCH_STUB")
                    req = m.LaunchRequest(target, [target, "--model", mid], {}, mid)
                    argv = pc.command_for(req)
                    if stub:
                        Path(stub).write_text(json.dumps({"argv": argv}))

                def paint(models, query):
                    list_box.clear()
                    with list_box:
                        groups: dict[str, list] = {}
                        for mdl in models:
                            groups.setdefault(getattr(mdl, "provider", "local"), []).append(mdl)
                        any_shown = False
                        for prov, rows in groups.items():
                            shown_rows = [r for r in rows if not query or query.lower() in
                                          (getattr(r, "display", "") or getattr(r, "alias", "") or "").lower()]
                            if not shown_rows and query:
                                continue
                            any_shown = True
                            open_ = prov in state["open"]
                            hdr = ui.label(("▾ " if open_ else "▸ ") + f"{prov} ({len(shown_rows)})").classes("menu-item group-header")
                            hdr.props["data-group"] = prov
                            hdr.on("click", lambda p=prov: toggle_group(p))
                            if open_:
                                for mdl in shown_rows:
                                    name = getattr(mdl, "display", None) or getattr(mdl, "alias", "")
                                    mid = getattr(mdl, "model_id", "") or getattr(mdl, "alias", "")
                                    row = ui.label(f"    {name}  ·  {mid}").classes("menu-item model-row")
                                    row.props["data-model"] = mid
                                    row.on("click", lambda m=mid, d=mdl: do_launch(m, d))
                                    row.on("mouseenter", lambda d=mdl: info.set_text(_info_text(d)))
                        if not any_shown:
                            msg = ("No local models on disk yet — download some first"
                                   if target == "local" else
                                   "No models — every provider needs an API key (open API keys)")
                            ui.label(msg).props('class="empty-state"')

                def _info_text(mdl) -> str:
                    # the floating infobox content, like csl's model info box
                    parts = [getattr(mdl, "display", "") or getattr(mdl, "alias", "")]
                    prov = getattr(mdl, "provider", "")
                    if prov:
                        parts.append(f"provider: {prov}")
                    tier = getattr(mdl, "tier", "")
                    if tier:
                        parts.append(f"tier: {tier}")
                    notes = getattr(mdl, "notes", "") or ""
                    if notes:
                        parts.append(str(notes))
                    return " · ".join(parts)

                def toggle_group(prov):
                    state["open"].symmetric_difference_update({prov})
                    paint(loader.cache.get(kind) or [], search.value or "")

                def toggle_list():
                    visible = list_box.props.get("style", "") != "display:none"
                    list_box.props["style"] = "display:none" if visible else ""

                shared["toggle_list"] = toggle_list

                search.on_value_change(lambda e: paint(loader.cache.get(kind) or [], e.value))

                async def fill():
                    result = await loader.load(kind, loader_fn)
                    if result is STALE:
                        return
                    paint(result, search.value or "")

                ui.timer(0.05, fill, once=True)
                paint(loader.cache.get(kind) or [], "")

    make_lane("local", "local", pc.load_local_models)
    make_lane("remote", "remote", pc.load_remote_agents)

    @ui.page("/{full_path:path}")
    def not_built(full_path: str):
        # registered LAST so it never shadows the real routes; never a dead end in testing
        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3"):
            ui.label(f"/{full_path}").classes("text-2xl font-bold")
            ui.label("This screen is not built yet.").classes("text-sm opacity-70")
            back = ui.link("🏠 Back to Home", "/home").classes("menu-item")
