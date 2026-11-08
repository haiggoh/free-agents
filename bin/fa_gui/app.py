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
from contextlib import nullcontext
from pathlib import Path

import session_picker_model as m


def build() -> None:
    from nicegui import ui
    import picker_core as pc
    import session_menu_state as sms
    from fa_gui.adapter import render_plan
    from fa_gui.jobs import STALE, Loader

    ui.add_head_html("""<script>
      // Phase 3: Enter/Space/arrows on a focused .cycle-row must not also scroll the page
      // or double-fire — swallow them at capture time when the target is inside a cycle row.
      document.addEventListener('keydown', function(e){
        var t = e.target;
        var r = t && t.closest ? t.closest('.cycle-row') : null;
        if (r && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowLeft' || e.key === 'ArrowRight'))
          e.preventDefault();
      }, true);
    </script>
    <style>
      html, body { -webkit-font-smoothing: antialiased !important; text-rendering: optimizeLegibility !important;
                   font-family: -apple-system, 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', 'Segoe UI', Roboto, sans-serif !important;
                   margin: 0 !important; padding: 0 !important; }   /* lane shell is 100vh — no page scroll */
      .menu-item { color: #000 !important; text-decoration: none !important; background: none !important;
                   border: none !important; box-shadow: none !important; padding: 1px 5px; line-height: 1.3; cursor: pointer; }
      .menu-item:hover { color: #0b57d0 !important; }   /* M3-ish blue, not the default link blue */
      .cycle-arrow { color: #000 !important; background: none !important; border: none !important;
                     box-shadow: none !important; padding: 0 3px; cursor: pointer; font-weight: bold; }
      .cycle-arrow:hover { color: #0b57d0 !important; }
      /* Quasar checkbox wrapper styled as the M3 pill switch. Quasar keeps state on the
         WRAPPER (aria-checked + q-checkbox__inner--truthy), not the hidden native input,
         so the visuals key off [aria-checked="true"] and Quasar's own box is hidden. */
      .switch { width: 34px; height: 18px; border-radius: 9px; background: #bbb; position: relative;
                cursor: pointer; margin: 0; padding: 0; flex: 0 0 auto;
                transition: background-color .18s cubic-bezier(.4,0,.2,1); }
      .switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
                border-radius: 50%; background: #fff !important; box-shadow: 0 1px 3px rgba(0,0,0,.4) !important;
                transition: left .18s cubic-bezier(.4,0,.2,1); pointer-events: none; }
      .switch[aria-checked="true"] { background: #000; }
      .switch[aria-checked="true"]::after { left: 18px; }
      .switch .q-checkbox__inner { display: none !important; }
      .version-footer { position: fixed !important; bottom: 6px !important; left: 0; right: 0; text-align: center; }
      .btn-flat { box-shadow: none !important; border-radius: 4px !important; padding: 2px 8px !important;
                  text-transform: none !important; }
      /* M3 primary action (filled dark, white text) — only for Go launch / Choose model */
      .btn-primary { background: #000 !important; color: #fff !important; border: none !important;
                     border-radius: 20px !important; padding: 6px 16px !important; box-shadow: 0 1px 2px rgba(0,0,0,.2) !important; }
      .btn-primary:hover { background: #333 !important; }
      .cycle-row { display: flex; align-items: center; line-height: 1.25; }
      /* ON/OFF value chip: fixed width so a 2- vs 3-letter word never shifts the switch (user 2026-11-05) */
      .cycle-value-bin { display: inline-block; min-width: 3ch; }
      /* the action item itself is the control (csl parity): hover like a link, click = toggle / ▶ */
      .row-action { cursor: pointer; }
      .row-action:hover { color: #0b57d0 !important; }
      /* lane pages: fixed-height shell — the model list scrolls internally, the infobox
         stays pinned to the bottom instead of being overlapped (user 2026-11-05).
         EMERGENCY ONLY: if the list area would shrink below ~4 rows (groups expanded +
         short window), the shell itself scrolls — the window scrolls as a last resort,
         the norm stays fixed-shell (user 2026-11-05 clarification). */
      .lane-shell { height: 100vh; overflow-y: auto; display: flex; flex-direction: column; }
      .lane-body { flex: 1 0 auto; min-height: 0; display: flex; flex-direction: column; }
      .lane-list { flex: 0 1 auto; min-height: 136px; max-height: 60vh; overflow-y: auto; gap: 0 !important; }
      #infobox { margin-top: auto; }                      /* pinned to the window bottom in the norm */
      /* tighter rows inside the model list than everywhere else (user 2026-11-05) */
      .lane-list .model-row { padding: 0 4px !important; line-height: 1.15 !important; }
      .lane-list .group-header { padding: 0 4px !important; line-height: 1.2 !important; }
      /* cap the Quasar wrapper chain too — q-page's own 100vh min-height is what made the
         document 32px taller than the shell and scrollable */
      body:has(.lane-shell) .q-layout, body:has(.lane-shell) .q-page-container,
      body:has(.lane-shell) .q-page, body:has(.lane-shell) .nicegui-content { height: 100vh; min-height: 0 !important; overflow: hidden; }
    </style>""",
 shared=True)

    loader = Loader()
    # Process-global, shared INTENTIONALLY: the settings object + its savers (field-level
    # locked merge is the cross-frontend write path) and the loader's inventory cache.
    settings_state: dict = {}
    # Continuation plan Phase 2: everything else is PER CLIENT (keyed by the websocket
    # client id), so one browser tab never advances another's state. toggle_list /
    # launch_req / rerender are REASSIGNED on every page build; `collapsed` is setdefault
    # — fold state persists per tab across navigations (deliberate: csl keeps accordion
    # state for the session too).
    client_states: dict = {}

    def cstate(client) -> dict:
        return client_states.setdefault(client.id, {})

    def get_settings():
        if "settings" not in settings_state:
            cfg = Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or sms.REPO_CONFIG_DIR)
            settings_state["savers"] = pc.Savers(cfg)
            settings_state["settings"] = pc.build_settings(None, settings_state["savers"])
        return settings_state["settings"]

    def _cycle_value(action):
        # label text must match what csl shows on toggle (user 2026-11-04): choice_label first,
        # bools → ON/OFF BEFORE the int-keyed tables (False == 0 matched Auto-mode labels),
        # then the model's own label tables (Auto-mode 0/1/2 → Off/Classifier/…, etc.)
        try:
            v = action.value() if action.value else None
        except Exception:
            v = None
        if action.choice_label and v is not None:
            v = action.choice_label(v)
        elif isinstance(v, bool):
            v = "ON" if v else "OFF"
        elif v is not None:
            lname = (action.name or action.label or "").lower()
            if "classifier" in lname and v in getattr(m, "CLASSIFIER_SOURCE_LABELS", {}):
                v = m.CLASSIFIER_SOURCE_LABELS[v]
            elif "auto" in lname and v in getattr(m, "AUTO_MODE_LABELS", {}):
                v = m.AUTO_MODE_LABELS[v]
        if v is None or v == "":
            # untyped stepper (no value fn, e.g. memguard percent rows): show the value part
            # of the row label (label was built with the current value at screen construction)
            lbl = action.label if action is not None else ""
            plain = lbl.plain if hasattr(lbl, "plain") else str(lbl)
            if isinstance(v, str) and v == "" or v is None:
                if ": " in plain:
                    return plain.rsplit(": ", 1)[1].strip()
                # unset optional value (temperature "" / None): the SAME label csl shows
                return m.NOT_SET_LABEL
        return v

    def _binary_on(action) -> bool:
        # None counts as ON: rows like thinking store None for their ON/default state
        # (csl label "ON (default)"); only an explicit False/0 is OFF
        try:
            v = action.value() if action.value else None
        except Exception:
            v = None
        return v not in (False, 0)

    def _binary_label(action) -> str:
        return "ON" if _binary_on(action) else "OFF"

    def _binary_target(action, on: bool):
        # the choice whose on-ness matches the request — e.g. thinking (None, False): ON -> None
        for c in (action.choices or ()):
            if (c not in (False, 0)) == bool(on):
                return c
        return True if on else False

    def wire_rerender(body, nav, st: dict) -> None:
        """Rebuild a page's rows from a FRESH screen (untyped steppers embed the value in
        their label, which is stale after a step — the csl re-renders the same way)."""
        def _rerender():
            scr2 = pc.build_screen(nav, get_settings(), loader.cache).screen
            body.clear()
            with body:
                render_rows(scr2, st)
        st["rerender"] = _rerender

    def render_rows(scr, st: dict) -> None:
        # csl parity (m.rendered_actions): lane screens fold model/settings rows under two
        # collapsible headers — "🔩 Model settings" / "🛠️ Advanced", triangle before the name,
        # both folded by default. The GUI keeps the rows in the DOM and folds with display:none.
        boxes: dict = {}
        collapsed = st.setdefault("collapsed", {"model": True, "settings": True})
        for c in render_plan(scr):
            action = next((a for a in scr.actions()
                           if (a.id or f"{type(scr).__name__}.{a.key}") == c.action_id), None)
            if c.kind == "group":
                gid = c.group
                is_collapsed = collapsed.get(gid, True)
                emoji = "🔩" if gid == "model" else "🛠️"
                hdr = ui.label(f"{emoji} {'▸' if is_collapsed else '▾'} {c.label}").classes("menu-item group-header row-action")
                hdr.props["data-group"] = gid
                box = ui.column().classes("w-full gap-1")
                box.props["style"] = "display:none" if is_collapsed else ""

                def _toggle_group(g=gid, h=hdr, b=box, e=emoji, lbl=c.label):
                    collapsed[g] = not collapsed.get(g, True)
                    h.set_text(f"{e} {'▸' if collapsed[g] else '▾'} {lbl}")
                    b.props["style"] = "display:none" if collapsed[g] else ""
                hdr.on("click", _toggle_group)
                boxes[gid] = box
                continue
            # rows carry their group in c.group — nav/lanes/top rows have "" and stay
            # top-level, so Back/Switch can never end up hidden inside a folded group
            target = boxes.get(c.group) if c.group else None
            with (target if target is not None else nullcontext()):
                _render_row(c, action, st)

    def _rich_html(t) -> str:
        """rich Text -> HTML with inline span styles (theme swatches: 'on #FFD700' -> background).
        The GUI used to str() these labels, flattening the swatches away (0.35.0-class bug)."""
        import html as _html
        pieces = []
        pos = 0
        for sp in sorted(t.spans, key=lambda s: s.start):
            if sp.start > pos:
                pieces.append(_html.escape(t.plain[pos:sp.start]))
            seg = _html.escape(t.plain[sp.start:sp.end])
            css = []
            toks = str(sp.style).split()
            i = 0
            while i < len(toks):
                tok = toks[i]
                if tok == "on" and i + 1 < len(toks):          # rich writes "on #FFD700" as two tokens
                    css.append(f"background:{toks[i + 1]}")
                    i += 2
                    continue
                if tok == "bold":
                    css.append("font-weight:bold")
                elif tok.startswith("#"):
                    css.append(f"color:{tok}")
                i += 1
            pieces.append(f'<span style="{";".join(css)}">{seg}</span>' if css else seg)
            pos = max(pos, sp.end)
        pieces.append(_html.escape(t.plain[pos:]))
        return "".join(pieces)

    def _label_value_html(action):
        """The value part of a rich cycle-row label (after the last ': '), as HTML —
        keeps the swatch spans. Returns None for plain labels. (Duck-typed: the GUI venv
        has no rich — the model imports it only under TYPE_CHECKING.)"""
        lbl = action.label if action is not None else None
        if lbl is None or not hasattr(lbl, "spans") or not hasattr(lbl, "plain"):
            return None
        plain = lbl.plain
        if ": " not in plain:
            return _rich_html(lbl)
        i = plain.rindex(": ")
        j = i + 1
        while j < len(plain) and plain[j] == " ":
            j += 1
        return _rich_html(lbl[j:])

    def _render_row(c, action, st: dict) -> None:
            if c.kind in ("select", "toggle") and action is not None and action.step is not None:
                choices = list(action.choices) if action.choices is not None else []
                binary = len(choices) == 2 and set(choices) <= {False, True, 0, 1, None}
                if binary:
                    # native checkbox styled as the M3 pill switch; Quasar keeps state in the
                    # wrapper's aria-checked (the hidden native input never checks), and the CSS
                    # keys off .switch[aria-checked="true"] for the track fade + knob slide
                    with ui.row().classes("cycle-row items-center gap-2 w-full"):
                        name = (action.name or action.label.split(":")[0]) if action else c.label
                        name_lbl = ui.label(f"{name}:").classes("cycle-name row-action")
                        ui_label_val = ui.label(_binary_label(action)).classes("cycle-value cycle-value-bin row-action")
                        sw = ui.checkbox("", value=_binary_on(action),
                                         on_change=lambda e, a=action, v=ui_label_val: _toggle_binary(a, e.value, v)).classes("switch")
                        sw.props["aria-label"] = name
                        def _row_toggle(a=action, s=sw, v=ui_label_val):
                            # click on the item == the switch: sync its visual, apply the value.
                            # _toggle_binary puts the EXACT choice, so a double fire is a no-op.
                            new_on = not _binary_on(a)
                            s.set_value(new_on)
                            _toggle_binary(a, new_on, v)
                        name_lbl.on("click", _row_toggle)
                        ui_label_val.on("click", _row_toggle)
                else:
                    # csl order: <name>: < value >  — arrow between end of item and the state
                    name = (action.name or action.label.split(":")[0]) if action else c.label
                    with ui.row().classes("cycle-row items-center gap-1 w-full") as crow:
                        name_lbl = ui.label(f"{name}:").classes("cycle-name row-action")
                        left = ui.label("<").classes("cycle-arrow cycle-left")
                        val_html = _label_value_html(action) if action is not None else None
                        if val_html is not None:
                            # rich value (theme swatch): HTML, rebuilt on every cycle
                            val = ui.element("div").classes("cycle-value row-action")
                            with val:
                                ui.html(val_html)
                        else:
                            val = ui.label(str(_cycle_value(action))).classes("cycle-value row-action")
                        right = ui.label(">").classes("cycle-arrow cycle-right")
                        left.on("click", lambda a=action, v=val: _cycle(a, v, -1, st))
                        right.on("click", lambda a=action, v=val: _cycle(a, v, +1, st))
                        # csl parity: clicking the action item itself == Enter/click == ▶ (+1)
                        name_lbl.on("click", lambda a=action, v=val: _cycle(a, v, +1, st))
                        val.on("click", lambda a=action, v=val: _cycle(a, v, +1, st))
                    # Phase 3 keyboard contract: the row is focusable; while IT is focused,
                    # ArrowLeft/Right step and Enter/Space activate (+1 like click). Keys in
                    # the search input never reach here (the handler is on the row only).
                    crow.props('tabindex="0"')
                    crow.on("keydown", lambda e, a=action, v=val: _cycle_key(e, a, v, st))
            elif c.kind == "nav":
                # Continuation plan Phase 1: the route comes from RenderedControl.target
                # (Action.nav_target metadata) — run() is NEVER called at render time
                # (the auto-sync mutation of 2026-11-05 must not recur via a nav row).
                # Tool panels that have a real GUI screen get mapped to it (lowkey, download)
                target = {"tool:lowkey": "lowkey", "tool:download": "download"}.get(c.target, c.target)
                # ui.link for real SPA navigation; .menu-item CSS overrides the link look
                row = ui.link(c.label, f"/{target}").classes("menu-item action-row nav-row")
                row.props["data-target"] = target
            else:
                lbl = action.label if action is not None else None
                if lbl is not None and hasattr(lbl, "spans") and hasattr(lbl, "plain"):
                    # rich label (theme swatch rows): keep the span colors as HTML
                    row = ui.html(_rich_html(lbl)).classes("menu-item action-row")
                else:
                    row = ui.label(c.label).classes("menu-item action-row")
                if action is not None and action.key == "c" and st.get("toggle_list"):
                    row.on("click", lambda: st["toggle_list"]())   # "Choose model" toggles the list
                else:
                    row.on("click", lambda a=action: _run_action(a, st))

    def _run_action(action, st: dict) -> None:
        if action is None or action.run is None:
            return
        res = action.run()
        # "Go launch" returns a LaunchRequest — run it exactly like a model-row click
        if isinstance(res, m.LaunchRequest) and st.get("launch_req"):
            st["launch_req"](res)

    def _cycle_key(e, action, val, st: dict) -> None:
        """Phase 3: keyboard on a FOCUSED cycle row — Left steps -1, Right/Enter/Space
        activate +1 (same as a click; the model's own docstring: Enter/click/Right = +1,
        Left = -1). Attached to the row element only, so keys typed into the search input
        or any other field never reach it."""
        key = (e.args or {}).get("key")
        if key == "ArrowLeft":
            _cycle(action, val, -1, st)
        elif key in ("ArrowRight", "Enter", " "):
            _cycle(action, val, +1, st)

    def _cycle(action, val, d: int, st: dict) -> None:
        if action.step:
            action.step(d)
        if action is not None and getattr(action, "value", None) is None and st.get("rerender"):
            # untyped stepper: its label embeds the value and is stale after the step —
            # rebuild the page's rows from a fresh screen so the label (and dependents) update
            st["rerender"]()
            return
        html = _label_value_html(action) if action is not None else None
        if html is not None and not hasattr(val, "set_text"):
            val.clear()                       # rich value: rebuild the swatch HTML
            with val:
                ui.html(html)
        else:
            val.set_text(str(_cycle_value(action)))

    def _toggle_binary(action, on, val=None) -> None:
        # set the EXACT choice via put when the row is typed — stepping a (None, False) list
        # can land on None and label it wrong; the checkbox's own aria-checked drives the tween
        if action.put is not None:
            action.put(_binary_target(action, on))
        elif action.step:
            for _ in range(len(action.choices or ()) + 1):
                if _binary_on(action) == bool(on):
                    break
                action.step(1)
        if val is not None:
            val.set_text(_binary_label(action))   # ON/OFF next to the switch, in sync with it

    def render_shell(title: str, subtitle: str, lane: bool = False):
        outer = "w-full max-w-3xl mx-auto p-4 gap-1" + (" lane-shell" if lane else "")   # tighter overall spacing (user 2026-11-05)
        with ui.column().classes(outer):
            ui.label(title).props('id="title"').classes("text-2xl font-bold")
            if subtitle:
                ui.label(subtitle).classes("text-sm opacity-70").props('id="subtitle"')
            return ui.column().classes("w-full gap-1" + (" lane-body" if lane else ""))

    def render_appearance(body, st: dict) -> None:
        """Phase 4 UI: a GUI-only appearance section on the theme screen, bound to the
        fa_gui.appearance store (never to core Actions — it stays out of render_plan).
        Rows reuse the .cycle-row / .switch classes so styling and keyboard behavior match."""
        from fa_gui import appearance as ap
        store = ap.store_path()
        doc = ap.load(store)
        with body:
            ui.label("🖼 GUI appearance").classes("menu-item group-header").props('data-group="gui_appearance"')

            def _appearance_cycle(key: str, label: str, choices, clamp: bool = False):
                step = 5 if clamp else 1
                with ui.row().classes("cycle-row items-center gap-1 w-full appearance-row") as crow:
                    crow.props["data-key"] = key
                    ui.label(f"{label}:").classes("cycle-name row-action")
                    left = ui.label("<").classes("cycle-arrow cycle-left")
                    val = ui.label(str(doc[key])).classes("cycle-value row-action")
                    right = ui.label(">").classes("cycle-arrow cycle-right")

                    def _step(d, k=key, v=val, ch=choices, cl=clamp):
                        cur = ap.load(store)[k]
                        if cl:
                            nxt = min(ch[-1], max(ch[0], cur + d))
                        else:
                            i = ch.index(cur) if cur in ch else 0
                            nxt = ch[(i + d) % len(ch)]
                        ap.save(store, {k: nxt})
                        v.set_text(str(nxt))

                    left.on("click", lambda e=None: _step(-step))
                    right.on("click", lambda e=None: _step(+step))
                    crow.props('tabindex="0"')

                    def _key(e, s=step):
                        k = (e.args or {}).get("key")
                        if k == "ArrowLeft":
                            _step(-s)
                        elif k in ("ArrowRight", "Enter", " "):
                            _step(+s)

                    crow.on("keydown", _key)

            def _appearance_switch(key: str, label: str):
                with ui.row().classes("cycle-row items-center gap-2 w-full appearance-row") as crow:
                    crow.props["data-key"] = key
                    ui.label(f"{label}:").classes("cycle-name row-action")
                    val = ui.label("ON" if doc[key] else "OFF").classes("cycle-value cycle-value-bin row-action")

                    def _set(e, k=key, v=val):
                        ap.save(store, {k: bool(e.value)})
                        v.set_text("ON" if e.value else "OFF")

                    ui.checkbox("", value=bool(doc[key]), on_change=_set).classes("switch")

            _appearance_cycle("presentation", "Presentation", list(ap.PRESENTATIONS))
            _appearance_switch("background_translucent", "Translucent background")
            _appearance_cycle("background_opacity_pct", "Opacity %", list(range(50, 101, 5)), clamp=True)
            _appearance_switch("background_blur_requested", "Request native blur")

    @ui.page("/")
    @ui.page("/home")
    async def home(client):
        # first launch must not look dead: build the UI only once the websocket is up
        # (user 2026-11-04: menu rendered but no hover/click on first launch)
        await client.connected()
        loader.leave_page(client=client.id)     # Phase 2: this client's old page is gone
        st = cstate(client)
        s = get_settings()
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, loader.cache).screen
        body = render_shell(scr.title, getattr(scr, 'policy', '') if isinstance(getattr(scr, 'policy', ''), str) else "")
        with body:
            render_rows(scr, st)
        wire_rerender(body, m.Nav("home", m.HOME_OWNED), st)
        ui.label(f"free-agents v{m.plugin_version()}").classes("text-xs italic opacity-50 version-footer")

    def make_lane(target: str, kind: str, loader_fn) -> None:
        @ui.page(f"/{target}")
        async def page(client):
            await client.connected()
            loader.leave_page(client=client.id)     # Phase 2: this client's old page is gone
            st = cstate(client)
            scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache).screen
            body = render_shell(scr.title, getattr(scr, 'policy', '') if isinstance(getattr(scr, 'policy', ''), str) else "", lane=True)
            st["toggle_list"] = lambda: None     # placeholder; reassigned below before any click can fire
            with body:
                render_rows(scr, st)
            wire_rerender(body, m.Nav(target, m.HOME_OWNED), st)
            with body:
                search = ui.input(placeholder="type to filter models").classes("gui-search w-full")
                list_box = ui.column().classes("w-full gap-1 lane-list")
                list_box.props["style"] = "display:none"     # hidden until "Choose model" (csl parity)
                info = ui.label("").classes("text-xs opacity-70 border-t pt-2 mt-2 w-full").props('id="infobox"')
                state = {"open": set()}

                def _launch_req(req):
                    # the delivery hook: model-row clicks and the "Go launch" action both land here
                    stub = os.environ.get("FA_GUI_LAUNCH_STUB")
                    if stub:
                        Path(stub).write_text(json.dumps({"argv": pc.command_for(req)}))
                st["launch_req"] = _launch_req

                def do_launch(mid, mdl=None):
                    # csl launches the model directly on row click — no Launch button (user 2026-11-04)
                    _launch_req(m.LaunchRequest(target, [target, "--model", mid], {}, mid))

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

                st["toggle_list"] = toggle_list

                search.on_value_change(lambda e: paint(loader.cache.get(kind) or [], e.value))

                async def fill():
                    result = await loader.load(kind, loader_fn, client=client.id)
                    if result is STALE:
                        return
                    paint(result, search.value or "")

                ui.timer(0.05, fill, once=True)
                paint(loader.cache.get(kind) or [], "")

    make_lane("local", "local", pc.load_local_models)
    make_lane("remote", "remote", pc.load_remote_agents)

    @ui.page("/{full_path:path}")
    async def screen_or_catchall(client, full_path: str):
        # registered LAST so it never shadows the real routes. Known screen targets render
        # through the shared core (same rows the Textual picker shows); unknown paths get
        # the catch-all with Back-to-Home — never a dead end.
        await client.connected()
        target = full_path
        scr = None
        if target not in ("quit", "back"):          # not pages — handled by the launcher
            try:
                scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache).screen
            except Exception:
                scr = None
        if scr is None:
            with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3"):
                ui.label(f"/{full_path}").classes("text-2xl font-bold")
                ui.label("This screen is not built yet.").classes("text-sm opacity-70")
                ui.link("🏠 Back to Home", "/home").classes("menu-item")
            return
        loader.leave_page(client=client.id)     # Phase 2: this client's old page is gone
        st = cstate(client)
        body = render_shell(scr.title, getattr(scr, 'policy', '') if isinstance(getattr(scr, 'policy', ''), str) else "")
        with body:
            render_rows(scr, st)
        if full_path == "theme_config":
            render_appearance(body, st)         # Phase 4: GUI-only appearance section
        wire_rerender(body, m.Nav(full_path, m.HOME_OWNED), st)
