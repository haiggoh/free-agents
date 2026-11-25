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

import asyncio
import json
import os
from contextlib import nullcontext
from pathlib import Path

import session_picker_model as m
import picker_core as pc
import catalog_overlay as co

# Cache-kind -> loader, mirroring the Textual picker's LOADERS map (session_picker.py:1371)
# plus its versions:/rcat: prefixes, so every screen that build_screen declares a prefetch
# kind for gets its data loaded on navigation — not just the two lanes.
PREFETCH_LOADERS = {"local": pc.load_local_models, "remote": pc.load_remote_agents,
                    "catalog": pc.load_catalog}


def _loader_for(kind: str):
    if kind.startswith("versions:"):
        return lambda: pc.load_versions(kind)
    if kind.startswith("rcat:"):
        return lambda: pc.load_remote_catalog(kind)
    return PREFETCH_LOADERS.get(kind)


def prompt_payload(target: str, scr) -> dict | None:
    """What a prompt:* dialog shows. Testable core of the dialog layer: for
    prompt:download-review it is the DownloadScreen's own review (aliases, total GB,
    fits) — the same object csl's confirm prompt renders."""
    if target == "prompt:download-review" and hasattr(scr, "review"):
        r = scr.review()
        return {"kind": "download-review", "aliases": list(r.aliases),
                "total_gb": r.total_gb, "fits": r.fits}
    if target.startswith("prompt:missing-cleanup:"):
        return {"kind": "missing-cleanup", "alias": target.split(":", 2)[2]}
    if target == "prompt:session":
        # csl's typed prompt: a text field, letters/digits/-/_ only (empty = ephemeral)
        return {"kind": "input",
                "prompt": "Set session name (letters, digits, - _; empty = ephemeral):"}
    if target == "prompt:oneshot":
        # csl's one-shot field: the lowkey screen turns the text into a --prompt launch
        return {"kind": "input", "prompt": "One-shot prompt:"}
    if target in ("prompt:rt-launchd-install", "prompt:rt-launchd-uninstall"):
        # csl's confirm (session_picker.py prompt:rt-launchd-*): same question + detail line
        verb = "Install" if target.endswith("-install") else "Remove"
        return {"kind": "confirm-launchd",
                "prompt": f"{verb} the weekly LaunchAgent update check?",
                "detail": "A per-user launchd job that checks for backend updates once a week.",
                "target": "tool:" + target.split(":", 1)[1]}
    if target == "prompt:rl-reset":
        # csl's confirm: the shared rate-limiter state file is deleted once per prompt
        return {"kind": "confirm-rl-reset",
                "prompt": "Delete the shared rate-limiter state file?"}
    if target.startswith("prompt:keys:key:"):
        # csl's typed key prompt (hidden field); the write goes through the same Store
        slug = target.split(":", 3)[3]
        return {"kind": "input", "slug": slug, "password": True,
                "prompt": f"API key/token for {slug} (hidden):"}
    if target.startswith("prompt:"):
        return {"kind": "confirm", "prompt": target}
    return None


def dispatch_argv(req) -> list:
    """The argv a dispatched LaunchRequest runs. command_for covers the launch lanes;
    tool: requests carry a ready-made argv (picker_core.tool_argv) — use it verbatim."""
    if req.lane == "tool":
        return req.argv[1:]
    return pc.command_for(req)


# Process-global settings holder, hoisted so module-level handlers (prompt dialogs)
# can read it — build() aliases it into its own scope.
_settings_state: dict = {}


def _runtime_backend() -> str:
    s = _settings_state.get("settings")
    return getattr(s, "runtime_backend", "rapid-mlx") if s else "rapid-mlx"


def confirm_prompt_argv(scr, target: str) -> "list[str] | None":
    """The CLI argv a confirmed prompt:* dialog produces — the real delivery path:
    model confirm() -> LaunchRequest -> picker_core.command_for. None when the dialog
    is declined or the target has no confirm action."""
    if target.startswith("prompt:") and hasattr(scr, "confirm"):
        req = scr.confirm(True)
        if isinstance(req, m.LaunchRequest):
            return pc.command_for(req)
    return None


def _confirm_prompt(scr, target: str, st: dict, dlg) -> None:
    """Yes-button handler: run the model's confirm(True) and dispatch the resulting
    LaunchRequest through the page's launch hook (the FA_GUI_LAUNCH_STUB in tests).
    prompt:rt-remove has no confirm() on the screen — it dispatches the pending
    tool:rt-op:remove:<version> recorded when the row was clicked (csl parity)."""
    if target == "prompt:rt-remove":
        pending = _settings_state.pop("pending_tool", None)
        if pending and st.get("launch_req"):
            argv = pc.tool_argv(pending, _runtime_backend(), _settings_state.get("last_scr"))
            if argv:
                st["launch_req"](m.LaunchRequest("tool", ["tool", *argv], {}, ""))
    elif target in ("prompt:rt-launchd-install", "prompt:rt-launchd-uninstall"):
        # csl's confirm: install/remove the weekly LaunchAgent update check
        _p = prompt_payload(target, scr)
        _argv = pc.tool_argv(_p["target"], _runtime_backend(), _settings_state.get("last_scr")) if _p else None
        if _argv and st.get("launch_req"):
            st["launch_req"](m.LaunchRequest("tool", ["tool", *_argv], {}, ""))
    elif target == "prompt:rl-reset":
        # csl's reset: delete the shared rate-limiter state file (same command the TUI runs)
        if st.get("launch_req"):
            _argv = ["python3", "-c",
                     "import rate_limiter as r,os;p=r.DEFAULT_STATE if not os.environ.get('LA_NVIDIA_THROTTLE_STATE') else r.Path(os.environ['LA_NVIDIA_THROTTLE_STATE']);p.unlink(missing_ok=True);print('state file reset')"]
            st["launch_req"](m.LaunchRequest("tool", ["tool", *_argv], {}, ""))
    elif target.startswith("prompt:missing-cleanup:"):
        alias = target.split(":", 2)[2]
        if st.get("launch_req"):
            csl = os.environ.get("CSL_SELF") or str(pc.BIN / "csl")
            st["launch_req"](m.LaunchRequest("tool", ["tool", csl, "--unregister-models", alias], {}, ""))
        refresh = _settings_state.get("refresh_inventory")
        if refresh is not None:
            refresh("local", pc.load_local_models)
        from nicegui import ui as _ui
        _ui.navigate.to("/local")
    elif target.startswith("prompt:") and hasattr(scr, "confirm"):
        req = scr.confirm(True)
        if isinstance(req, m.LaunchRequest) and st.get("launch_req"):
            st["launch_req"](req)
    dlg.close()


def build() -> None:
    from nicegui import ui
    import session_menu_state as sms
    from fa_gui.adapter import render_plan
    from fa_gui.jobs import STALE, Loader

    ui.add_head_html("""<style>
    @font-face { font-family: 'NotoEmojiMono'; font-style: normal; font-weight: 300 700;
      font-display: swap; src: url('/assets/NotoEmojiMono.ttf') format('truetype'); }
    </style><script>
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
      /* Tokens from the dealt app direction "achromatic-ledger" (seed 666057615, shell open-plane):
         ground #f8f8f8, surface #ffffff, ink #141414, one hue #1d5fbf, gap 12, radius 8/6, body 14px */
      html, body { -webkit-font-smoothing: antialiased !important; text-rendering: optimizeLegibility !important;
                   font-family: -apple-system, 'Apple Color Emoji', 'Segoe UI Emoji', 'Noto Color Emoji', 'Segoe UI', Roboto, sans-serif !important;
                   margin: 0 !important; padding: 0 !important;
                   background: #ffffff !important; color: #141414 !important; font-size: 14px !important; }   /* clean white FFF ground (user 2026-11-08); dark mode swaps via .dark-shell */
      .menu-item { color: #141414 !important; text-decoration: none !important; background: none !important;
                   border: none !important; box-shadow: none !important; padding: 1px 5px; line-height: 1.3; cursor: pointer;
                   font-size: 17px !important; }   /* non-tile text menu items match tile text size (user 2026-11-09) */
      .menu-item:hover { color: #00BFFF !important; }   /* csl sky blue accent (user 2026-11-08) */
      .cycle-arrow { color: #141414 !important; background: none !important; border: none !important;
                     box-shadow: none !important; padding: 0 3px; cursor: pointer; font-weight: bold; }
      .cycle-arrow:hover { color: #00BFFF !important; }
      /* Quasar checkbox wrapper styled as the M3 pill switch. Quasar keeps state on the
         WRAPPER (aria-checked + q-checkbox__inner--truthy), not the hidden native input,
         so the visuals key off [aria-checked="true"] and Quasar's own box is hidden. */
      .switch { width: 34px; height: 18px; border-radius: 9px; background: #bbb; position: relative;
                cursor: pointer; margin: 0; padding: 0; flex: 0 0 auto;
                transition: background-color .18s cubic-bezier(.4,0,.2,1); }
      .switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
                border-radius: 50%; background: #fff !important;
                transition: left .18s cubic-bezier(.4,0,.2,1); pointer-events: none; }   /* no shadow at rest (direction rule) */
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
      .row-action:hover { color: #00BFFF !important; }
      .tile:focus-visible { outline: 2px solid #00FFFF !important; outline-offset: 2px; }   /* csl cyan focus ring */
      /* Phase 5 modern presentation (user 2026-11-08): Metro grid — columns+rows, lanes 2x2 /
         rest 1x1, emoji above wrapping label, card surface; same handlers/targets as rows */
      /* Metro grid + M3-expressive blend (user rulings 2026-11-08/09): fixed 150px square
         cells; grid auto-placement reflows neighbours around 2x2/2x1 spans by itself.
         csl sky-blue hover, cyan focus ring. */
      .tile-grid { display: grid !important; grid-template-columns: repeat(auto-fill, 150px);
                   grid-auto-rows: 150px; gap: 12px; width: 100%; margin-bottom: 8px; }
      .tile { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: flex-end;
              padding: 16px 12px 14px; border-radius: 16px; font-size: 1.0625rem; text-align: center;   /* label 17px, centered horizontally, bottom-aligned (user 2026-11-09) */
              background: #ffffff !important; border: 1px solid rgba(20, 20, 20, 0.10) !important; }
      /* emoji centered inside the tile (both axes), independent of the bottom label; on
         non-2x2 tiles it sits slightly above center so a wrapped 2-line label never overlaps
         it and it never touches the top border (user 2026-11-09) */
      .tile .tile-emoji { position: absolute; top: 42%; left: 50%; transform: translate(-50%, -50%); }
      .tile-lg .tile-emoji { top: 50%; }
      .tile .tile-label { display: block; }
      .tile:not(:has(.tile-emoji)) { justify-content: center; }   /* text-only tiles: text vertically centered (user 2026-11-09) */
      .tile .tile-label { overflow-wrap: anywhere; line-height: 1.25; }
      .tile-lg { grid-column: span 2; grid-row: span 2; }       /* Local / Remote / Go launch: square 2x2 */
      .tile-wide { grid-column: span 2; grid-row: span 1; }     /* Lowkey / backend mgr / API keys / memguard / cloud opts: 2x1 */
      .tile .tile-emoji { font-size: 3.25rem; line-height: 1; }            /* 1x1 */
      .tile-wide .tile-emoji { font-size: 3.75rem; }                       /* 2x1 */
      .tile-lg .tile-emoji { font-size: 7.5rem; }                          /* 2x2 */
      /* theme accent cycles (2026-11-22 parity): a swatch chip for hex-valued choices */
      .cycle-swatch { display: inline-block; width: 1em; height: 1em; border-radius: 3px;
                      margin-right: .4em; vertical-align: middle; border: 1px solid rgba(20,20,20,.2); }
      .tile:hover { background: #f0f9ff !important; border-color: #00BFFF !important; }   /* flat sky tint, no gradient */
      body:has(.dark-shell) .tile { background: #1f1f1f !important; border-color: rgba(255, 255, 255, 0.14) !important; }
      /* 2026-11-21 user pick (lab v5 at real tile size): modern-mode tile emoji is a subdued
         near-black in light mode (sat 20% + brightness 22%, faint color detail shimmers through)
         and a bright near-white in dark mode (sat 10% + brightness 1.9 — brightness >1 clamps
         toward white while the bitmap's own detail scales up first, so rocket/info stay
         recognizable; inverting the bitmap was rejected as unnatural). Compatibility mode
         keeps full-color emoji — the tile grid only exists in modern mode. */
      /* 2026-11-21 theme option (lab v5/v6/v7): the modern-tile emoji treatment is picked per
         appearance-store tile_emoji_style — subdued (default: dark near-black light mode,
         near-white dark mode) | noto (mono line-art variable font, theme ink color) |
         full (Apple color emoji, as before). Scoped to .tile-grid = modern-only. */
      .tile-grid .tile .tile-emoji-subdued { filter: saturate(0.3) brightness(0.22); }
      /* dark mode (user 2026-11-22): brightness>1 blew out the bitmap's highlights, so the
         bitmap stays at full brightness and the white silhouette comes from an overlaid
         solid-white copy (::after) at 0.6 — dark detail survives, the glyph reads bright */
      body:has(.dark-shell) .tile-grid .tile .tile-emoji-subdued { filter: none; }
      body:has(.dark-shell) .tile-grid .tile .tile-emoji-subdued::after {
        content: attr(data-e); position: absolute; inset: 0;
        filter: saturate(0.3) brightness(0) invert(1); opacity: 0.75; }
      /* noto mono line art (variable font, wght 300-700): a CSS monochrome glyph, so it takes
         the theme's ink color directly — near-black in light, white in dark (dark-shell CSS) */
      .tile-grid .tile .tile-emoji-noto { font-family: 'NotoEmojiMono'; font-weight: 350; color: #141414; }
      body:has(.dark-shell) .tile-grid .tile .tile-emoji-noto { color: #f8f8f8; }
      /* "full" (the default look for compatibility and a pickable option): no rule — Apple color emoji */
      body:has(.dark-shell) .tile:hover { background: #16324a !important; border-color: #00BFFF !important; }
      .menu-item { font-size: 1.0625rem; }   /* non-tile text menu items match tile text (17px, user 2026-11-09) */
      /* quit icon swaps ⏻ -> ⏼ on hover (user 2026-11-09) */
      .tile-quit .icon-on { display: inline !important; }
      .tile-quit .icon-off { display: none !important; }
      .tile-quit:hover .icon-on { display: none !important; }
      .tile-quit:hover .icon-off { display: inline !important; }
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

    # Phase 6: translucent white background — exactly ONE tint layer (#tint-layer div, styled
    # per page from the appearance store so the slider is CSS-live); roots go transparent only
    # while the layer exists (body:has(#tint-layer)). The window itself is made transparent via
    # native window_args in main.py (native mode only). Foreground never gets a global opacity.
    ui.add_head_html("""<style>
      html:has(#tint-layer), body:has(#tint-layer),
      body:has(#tint-layer) #app, body:has(#tint-layer) .q-layout,
      body:has(#tint-layer) .q-page-container, body:has(#tint-layer) .q-page,
      body:has(#tint-layer) .nicegui-content { background: transparent !important; }
      #tint-layer { position: fixed; inset: 0; z-index: -1; pointer-events: none; }
      /* Phase 8 dark tokens (applied only while a .dark-shell page is shown) */
      body:has(.dark-shell) { background: #141414 !important; color: #f8f8f8 !important; }
      body:has(.dark-shell) .menu-item { color: #f8f8f8 !important; }
      body:has(.dark-shell) .menu-item:hover, body:has(.dark-shell) .row-action:hover { color: #7ea6e0 !important; }
      body:has(.dark-shell) .cycle-arrow { color: #f8f8f8 !important; }
      body:has(.dark-shell) .cycle-arrow:hover { color: #7ea6e0 !important; }
      body:has(.dark-shell) .cycle-name, body:has(.dark-shell) .cycle-value { color: #f8f8f8 !important; }
    </style>""", shared=True)

    loader = Loader()

    def _refresh_inventory(kind: str, fn) -> None:
        """Repopulate an inventory cache synchronously after a write (a cache-miss reload
        can return Stale and render the lane empty). Registered for module-level handlers."""
        loader.cache[kind] = fn()
    _settings_state["refresh_inventory"] = _refresh_inventory
    # Process-global, shared INTENTIONALLY: the settings object + its savers (field-level
    # locked merge is the cross-frontend write path) and the loader's inventory cache.
    settings_state = _settings_state  # alias the module-level holder
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

    def _modern() -> bool:
        """Phase 5: presentation=modern in the GUI appearance store renders tiles instead of
        menu rows. Read per render (cheap, tmp-file backed); any store error -> compatibility."""
        try:
            from fa_gui import appearance as ap
            return ap.load(ap.store_path())["presentation"] == "modern"
        except Exception:
            return False

    def _tile_emoji_style() -> str:
        """The modern-tile emoji treatment: subdued (default) | noto (mono line art) | full.
        Read per render like _modern(); any store error -> the default."""
        try:
            from fa_gui import appearance as ap
            return ap.load(ap.store_path()).get("tile_emoji_style", "subdued")
        except Exception:
            return "subdued"

    def _tile_label(parent, text, quit_icons: bool = False):
        """Metro tile content: emoji and label wrapped in ONE centered .tile-inner group —
        emoji above the text; a 2-line label grows the group and the emoji rides higher
        automatically (user 2026-11-09). quit_icons swaps ⏻/⏼ on hover."""
        import html as _html
        parts = str(text).split(" ", 1)
        head, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        with parent:
            _style = _tile_emoji_style()
            cls = "" if _style == "full" else f" tile-emoji-{_style}"
            emo = head
            if _style == "noto":
                emo = emo.replace("️", "")   # VS16: the FE0F sequences are not in the mono font
            if quit_icons:
                ui.html('<span class="tile-emoji icon-on">⏻</span>'
                        '<span class="tile-emoji icon-off">⏼</span>')
                ui.html(f'<span class="tile-label">{_html.escape(rest or str(text))}</span>')
            elif head and any(ord(ch) >= 0x2000 for ch in head):
                # data-e carries the ORIGINAL glyph (VS16 included) for the dark-mode
                # white-silhouette overlay, which stacks a solid copy via ::after
                ui.html(f'<span class="tile-emoji{cls}" data-e="{_html.escape(head)}">'
                        f'{_html.escape(emo)}</span>')
                ui.html(f'<span class="tile-label">{_html.escape(rest)}</span>')
            else:
                ui.label(str(text)).classes("tile-label")

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
            scr2 = pc.build_screen(nav, get_settings(), loader.cache, savers=settings_state.get('savers')).screen
            body.clear()
            with body:
                render_rows(scr2, st)
        st["rerender"] = _rerender
        st["nav"] = nav

    # Tile size policy lives in fa_gui/tiles.py (unit-tested); imported here for rendering.
    from fa_gui.tiles import tile_cls as _tile_cls_raw

    def _tile_cls(scr, c, action):
        return _tile_cls_raw(type(scr).__name__, c, action)

    def render_download_entries(body, scr, st: dict) -> None:
        """The DownloadScreen's catalog list: one row per entry, click toggles the queue
        (csl's Enter/Space), a leading ✓ marks queued rows. Rendered here because the
        entries are NOT part of the screen's actions() — the TUI draws them as its own
        table. A click re-renders so the ✓ markers and the review row's count update."""
        from nicegui import ui as _ui
        with body:
            _ui.label("Catalog (click to queue)").classes("group-header")
            nav = _ui.button("✏️ Edit overlay catalog", on_click=lambda: _ui.navigate.to("/catalog"))
            nav.props["data-nav"] = "catalog"
            for alias, e in sorted(scr.entries.items()):
                queued = alias in scr.queue
                row = _ui.label(f"{'✓ ' if queued else '   '}{alias}  ·  {e.size_gb:.1f} GB  [{e.state}]")
                row.classes("menu-item model-row")
                row.props["data-entry"] = alias

                def _toggle(a=alias):
                    # the queue lives in per-client state: the screen is rebuilt on every
                    # render, so a queue stored on it would vanish between clicks
                    q = st.setdefault("download_queue", [])
                    if a in q:
                        q.remove(a)
                    else:
                        q.append(a)
                    scr.queue = list(q)
                    if st.get("rerender"):
                        st["rerender"]()
                row.on("click", _toggle)
                if queued:
                    row.classes(add="queued")

    def render_rows(scr, st: dict) -> None:
        # csl parity (m.rendered_actions): lane screens fold model/settings rows under two
        # collapsible headers — "🔩 Model settings" / "🛠️ Advanced", triangle before the name,
        # both folded by default. The GUI keeps the rows in the DOM and folds with display:none.
        boxes: dict = {}
        collapsed = st.setdefault("collapsed", {"model": True, "settings": True})
        plan = render_plan(scr)
        st["action_ids"] = {c.action_id for c in plan}   # dependent-row detection in _cycle
        # Phase 5 Metro grid (user 2026-11-08): in modern mode, nav/action rows go into a
        # responsive tile GRID (columns+rows, lanes 2x2 / rest 1x1, emoji above wrapping label);
        # cycle/switch rows stay as compact list rows below the grid. Compatibility mode is
        # untouched (no grid at all).
        modern = _modern()
        if modern:
            # Two passes (user 2026-11-09): tiles into the grid first; every other row
            # (cycles, switches, group headers, choose-model) as text rows AFTER the tiles.
            # Grid auto-placement reflows neighbours around 2x2/2x1 spans by itself.
            grid = ui.element("div").classes("tile-grid")
            tiles, rows = [], []
            for c in plan:
                action = next((a for a in scr.actions()
                               if (a.id or f"{type(scr).__name__}.{a.key}") == c.action_id), None)
                cls = _tile_cls(scr, c, action) if c.kind in ("nav", "button") else None
                if cls is not None:
                    tiles.append((c, action, cls))
                else:
                    rows.append((c, action))
            # user 2026-11-09: API keys goes before filter settings (filter row is a button
            # without a nav target on the remote picker — match it by label too)
            tiles.sort(key=lambda t: 1 if (t[0].target == "remote_filters"
                                           or "Filter settings" in str(t[0].label or "")) else 0)  # stable
            for c, action, cls in tiles:
                with grid:
                    _render_row(c, action, st, tile_cls=cls)
            # text rows after the tiles; group headers keep the harmonica fold (user 2026-11-09:
            # collapsing of model options / advanced options must still work in modern mode)
            boxes = {}
            for c, action in rows:
                if c.kind == "group":
                    gid = c.group
                    is_collapsed = collapsed.get(gid, True)
                    emoji = "🔩" if gid == "model" else "🛠️"
                    hdr = ui.label(f"{emoji} {'▸' if is_collapsed else '▾'} {c.label}").classes(
                        "menu-item group-header row-action")
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
                target = boxes.get(c.group) if c.group else None
                with (target if target is not None else nullcontext()):
                    _render_row(c, action, st, tile_cls=None)
            return
        grid = None
        for c in plan:
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
            # top-level, so Back/Switch can never end up hidden inside a folded group.
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
        if action is not None and action.choices and action.labels:
            if all(isinstance(c, str) and c.startswith("#") for c in action.choices):
                cur = action.value() if callable(getattr(action, "value", None)) else None
                if isinstance(cur, str) and cur.startswith("#"):
                    # theme accent rows: swatch chip + name + hex, overriding whatever the
                    # label says (a rich 'Yellow' span has no chip; a custom hex has no name)
                    i = action.choices.index(cur) if cur in action.choices else None
                    name = action.labels[i] if i is not None else cur
                    return (f'<span class="cycle-swatch" style="background:{cur}"></span>'
                            f'{name} <span style="opacity:.6">{cur}</span>')
        if action is None:
            return None
        lbl = action.label
        if lbl is None or not hasattr(lbl, "spans") or not hasattr(lbl, "plain"):
            return None
        plain = lbl.plain
        if ": " not in plain:
            return _rich_html(lbl)
        plain = lbl.plain
        if ": " not in plain:
            return _rich_html(lbl)
        i = plain.rindex(": ")
        j = i + 1
        while j < len(plain) and plain[j] == " ":
            j += 1
        return _rich_html(lbl[j:])

    def _render_row(c, action, st: dict, tile_cls: str | None = "") -> None:
            # a row is a tile ONLY in modern mode AND when the policy gave it a class (None = text row)
            use_tile = _modern() and tile_cls is not None and c.kind in ("nav", "button")
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
                        crow.props["data-action"] = c.action_id   # stable row identity across rerenders
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
                # ui.link for real SPA navigation; .menu-item CSS overrides the link look.
                # Phase 5: modern presentation renders the same link as a large emoji tile.
                if c.target == "quit":
                    # user 2026-11-08: Quit is an ACTION, not a page — close the window and
                    # gently shut down the server this window launched (no placeholder page)
                    def _quit_app():
                        from nicegui import app as _app
                        try:
                            # flag first so a test can prove the close was requested even
                            # when window.close() is a no-op on a non-script-opened tab
                            ui.run_javascript("window.__fa_quit = true; window.close()")
                        except Exception:
                            pass
                        _app.shutdown()
                    if use_tile:
                        row = ui.row().classes("tile tile-quit menu-item action-row nav-row")
                        _tile_label(row, c.label, quit_icons=True)
                    else:
                        row = ui.label(c.label).classes("menu-item action-row nav-row")
                    row.props["data-target"] = "quit"
                    row.on("click", _quit_app)
                elif use_tile:
                    row = ui.link("", f"/{target}").classes(
                        "tile menu-item action-row nav-row" + (f" {tile_cls}" if tile_cls else ""))
                    _tile_label(row, c.label)
                    row.props["data-target"] = target
                else:
                    row = ui.link(c.label, f"/{target}").classes("menu-item action-row nav-row")
                    row.props["data-target"] = target
            else:
                lbl = action.label if action is not None else None
                if use_tile:
                    # Phase 5: action rows render as Metro tiles in modern mode; rich labels
                    # (e.g. Go launch with styled model name) go in as the tile label
                    row = ui.row().classes("tile menu-item action-row" + (f" {tile_cls}" if tile_cls else ""))
                    if lbl is not None and hasattr(lbl, "spans"):
                        with row:
                            ui.html(_rich_html(lbl)).classes("tile-label")
                    else:
                        _tile_label(row, c.label)
                elif lbl is not None and hasattr(lbl, "spans") and hasattr(lbl, "plain"):
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
        elif isinstance(res, m.Nav) and res.target.startswith("tool:rt-op:remove:"):
            # csl confirms a REMOVE through prompt:rt-remove before running it — same here
            _settings_state["pending_tool"] = res.target
            from nicegui import ui as _ui
            _ui.navigate.to("/prompt:rt-remove")
        elif isinstance(res, m.Nav) and res.target == "tool:rl-reset":
            # csl's run_tool: rl-reset confirms through prompt:rl-reset first
            from nicegui import ui as _ui
            _ui.navigate.to("/prompt:rl-reset")
        elif isinstance(res, m.Nav) and res.target.startswith("tool:"):
            # tool: rows RUN a command in csl (never navigate) — dispatch the argv
            argv = pc.tool_argv(res.target, get_settings().runtime_backend,
                                settings_state.get("last_scr"))
            if argv and st.get("launch_req"):
                st["launch_req"](m.LaunchRequest("tool", ["tool", *argv], {}, ""))
        elif isinstance(res, m.Nav):
            # an action that navigates (e.g. Download's Review → prompt:download-review):
            # route it; the catch-all renders prompt:* as a dialog
            from nicegui import ui as _ui
            _ui.navigate.to(f"/{res.target}")

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
        # rows whose label EMBEDS the value (cloud-config route/timeout tiers: labels, no
        # choices) have no getter to update in place — the fresh screen rebuilds the label
        if action.labels and not action.choices and st.get("rerender"):
            st["rerender"]()
            return
        nav = st.get("nav")
        if st.get("rerender") and nav is not None:
            # Rebuild ONLY when the fresh screen's RENDERED control set differs — dependent
            # rows (Classifier exists only while auto_mode == classifier) require the rebuild,
            # while a pure value cycle updates the label in place (keeps focus, no flicker).
            # Compare render_plan-to-render_plan: the plan synthesizes group rows and drops
            # hidden/pending ones, so raw actions() would ALWAYS differ (rerender every key).
            fresh_scr = pc.build_screen(nav, get_settings(), loader.cache, savers=settings_state.get('savers')).screen
            fresh_ids = {c.action_id for c in render_plan(fresh_scr)}
            if fresh_ids != st.get("action_ids"):
                st["rerender"]()
                return
        # in-place label update (also covers untyped steppers whose label embeds the value
        # only when no rerender is wired; with rerender wired, a changed id set rebuilds)
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
        # Phase 6/8: the single tint layer, live from the appearance store on every page;
        # dark mode tints dark and marks the shell so the dark tokens apply
        from fa_gui import appearance as _ap
        _doc = _ap.load(_ap.store_path())
        _dark = bool(_doc["dark_mode"])
        if _doc["background_translucent"]:
            _tint = "20, 20, 20" if _dark else "255, 255, 255"   # dark tint = dealt ink #141414
            # user 2026-11-08: opacity default is capability-dependent — 80% with active
            # native blur, 95% without (50 was unreadable); an explicit saved value wins
            _blur_active = (_doc["background_blur_requested"]
                            and os.environ.get("LA_GUI_NATIVE") == "1")
            _pct = _ap.resolve_opacity(_doc, blur_active=_blur_active)
            # blur darkens the backdrop — compensate so more translucent never looks darker
            _alpha = _ap.tint_alpha(_pct, blur_active=_blur_active)
            ui.element("div").props('id="tint-layer"').style(
                f"background: rgba({_tint}, {_alpha:.3f})")
        outer = ("w-full max-w-3xl mx-auto p-4 gap-1 shell-root" + (" lane-shell" if lane else "")
                 + (" dark-shell" if _dark else ""))   # tighter overall spacing (user 2026-11-05)
        with ui.column().classes(outer):
            # user 2026-11-08: title 28px / subtitle 17px (ratio ~1.6 golden), subtitle matches
            # the menu action-item size, and one empty title-height row of space after them
            # title 26px + tight tracking so it fits ONE line incl. its two emoji even in a
            # narrow window (user 2026-11-09: at 28px it was one character too long)
            ui.label(title).props('id="title"').classes("text-[26px] font-bold tracking-[-0.02em]")
            if subtitle:
                ui.label(subtitle).classes("text-[17px] opacity-70").props('id="subtitle"')
            ui.element("div").classes("title-spacer").style("height: 26px")   # empty spacer row, title-height
            return ui.column().classes("w-full gap-1" + (" lane-body" if lane else ""))

    def _apply_appearance_live(store, key: str, st: dict) -> None:
        """Apply an appearance change IN PLACE, no page reload (user 2026-11-20: the
        reload flashed white). Toggles the dark-shell class and re-tints #tint-layer via
        JS; presentation swaps rows/tiles via the existing rerender hook."""
        from fa_gui import appearance as _ap2
        doc2 = _ap2.load(store)
        dark = bool(doc2.get("dark_mode"))
        blur_active = bool(doc2.get("background_blur_requested")) and os.environ.get("LA_GUI_NATIVE") == "1"
        pct = _ap2.resolve_opacity(doc2, blur_active=blur_active)
        alpha = _ap2.tint_alpha(pct, blur_active=blur_active)
        tint = "20, 20, 20" if dark else "255, 255, 255"
        style = str(doc2.get("tile_emoji_style", _ap2.DEFAULTS["tile_emoji_style"]))
        style_cls = "" if style == "full" else f" tile-emoji-{style}"
        js = (f"var r=document.querySelector('.shell-root');"
              f"if(r) r.classList.toggle('dark-shell', {'true' if dark else 'false'});"
              f"var t=document.getElementById('tint-layer');"
              f"if(t) t.style.background='rgba({tint}, {alpha:.3f})';"
              # any REAL tile on this page (e.g. the theme menu's Back-to-home tile) is a
              # live preview of the tile-emoji treatment (2026-11-22, dummy preview removed)
              f"document.querySelectorAll('.tile-grid .tile-emoji').forEach("
              f"function(p){{ p.className='tile-emoji{style_cls}'; }});")
        ui.run_javascript(js)
        if key == "presentation" and st.get("rerender"):
            # tiles-vs-rows swaps the current page's layout (on the theme menu the real
            # Back-to-home tile changes shape) — the theme page's rerender below rebuilds
            # rows AND the appearance section, so nothing vanishes (2026-11-21 regression)
            st["rerender"]()

    def render_appearance(body, st: dict) -> None:
        """Phase 4 UI: a GUI-only appearance section on the theme screen, bound to the
        fa_gui.appearance store (never to core Actions — it stays out of render_plan).
        Rows reuse the .cycle-row / .switch classes so styling and keyboard behavior match."""
        from fa_gui import appearance as ap
        store = ap.store_path()
        _doc_now = ap.load(store)
        _is_tiles = _doc_now.get("presentation") == "modern"
        doc = ap.load(store)
        with body:
            # a sub-section HEADLINE, not an action row: no menu-item class (no hover), spaced
            ui.label("🖼 GUI appearance").classes("group-header mt-5 mb-2").props('data-group="gui_appearance"')

            def _appearance_cycle(key: str, label: str, choices, clamp: bool = False):
                step = 5 if clamp else 1

                def _disp(k, v):
                    # user 2026-11-22: the presentation value reads "Tiles menu", not "modern"
                    return {"compatibility": "Compatibility", "modern": "Tiles menu"}.get(v, v) if k == "presentation" else v

                with ui.row().classes("cycle-row items-center gap-1 w-full appearance-row") as crow:
                    crow.props["data-key"] = key
                    name_lbl = ui.label(f"{label}:").classes("cycle-name row-action")
                    left = ui.label("<").classes("cycle-arrow cycle-left")
                    _init = (ap.resolve_opacity(doc, blur_active=(bool(doc["background_blur_requested"])
                                  and os.environ.get("LA_GUI_NATIVE") == "1"))
                             if key == "background_opacity_pct" else doc[key])
                    val = ui.label(str(_disp(key, _init))).classes("cycle-value row-action")
                    right = ui.label(">").classes("cycle-arrow cycle-right")
                    # csl parity: clicking the value (or the name) acts like the right arrow
                    val.on("click", lambda e=None, k=key, v=val, ch=choices, cl=clamp: _step(5 if cl else 1, k, v, ch, cl))
                    name_lbl.on("click", lambda e=None, k=key, v=val, ch=choices, cl=clamp: _step(5 if cl else 1, k, v, ch, cl))

                    def _step(d, k=key, v=val, ch=choices, cl=clamp):
                        cur_doc = ap.load(store)
                        if k == "background_opacity_pct":
                            # absent = capability-auto: start from the RESOLVED value so the
                            # first arrow press saves that value +/- the step (user 2026-11-08)
                            cur = ap.resolve_opacity(
                                cur_doc,
                                blur_active=(bool(cur_doc["background_blur_requested"])
                                             and os.environ.get("LA_GUI_NATIVE") == "1"))
                        else:
                            cur = cur_doc.get(k)
                        if cl:
                            nxt = min(ch[-1], max(ch[0], cur + d))
                        else:
                            i = ch.index(cur) if cur in ch else 0
                            nxt = ch[(i + d) % len(ch)]
                        ap.save(store, {k: nxt})
                        v.set_text(str(_disp(k, nxt)))
                        _apply_appearance_live(store, k, st)

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

            def _appearance_switch(key: str, label: str, launch_only: bool = False):
                with ui.row().classes("cycle-row items-center gap-2 w-full appearance-row") as crow:
                    crow.props["data-key"] = key
                    name_lbl = ui.label(f"{label}:").classes("cycle-name row-action")
                    val = ui.label("ON" if doc[key] else "OFF").classes("cycle-value cycle-value-bin row-action")
                    if launch_only:
                        # window args are read once at startup — honest label, no fake live toggle
                        ui.label("(next launch)").classes("launch-hint text-xs opacity-50")

                    def _set(on: bool, k=key, v=val):
                        # launch_only keys (translucent, blur, follow-system) change the
                        # WINDOW, which is read once at startup — saving is live, the effect
                        # is honest: "(next launch)". Never re-tint or re-material the window
                        # from a click (user 2026-11-22: follow-system flipped the bg live).
                        patch = {k: bool(on)}
                        if k == "background_translucent" and on:
                            # user 2026-11-08: blur request defaults ON with translucency,
                            # unless the user already made an explicit choice
                            try:
                                raw = json.loads(store.read_text()) if store.is_file() else {}
                            except ValueError:
                                raw = {}
                            if "background_blur_requested" not in raw:
                                patch["background_blur_requested"] = True
                        ap.save(store, patch)
                        v.set_text("ON" if on else "OFF")
                        if not launch_only:
                            _apply_appearance_live(store, k, st)

                    sw = ui.checkbox("", value=bool(doc[key]),
                                     on_change=lambda e, k=key, v=val: _set(e.value, k, v)).classes("switch")

                    def _row_toggle(k=key, v=val, s=sw):
                        # csl parity: clicking the name or the value acts like the switch
                        new_on = not ap.load(store).get(k, False)
                        s.set_value(new_on)
                        _set(new_on, k, v)

                    name_lbl.on("click", _row_toggle)
                    val.on("click", _row_toggle)

            _appearance_cycle("presentation", "Presentation", list(ap.PRESENTATIONS))
            _appearance_switch("background_translucent", "Translucent background", launch_only=True)
            _appearance_cycle("background_opacity_pct", "Opacity %", list(range(0, 101, 5)), clamp=True)  # 0% = raw material (user 2026-11-19)
            _appearance_switch("background_blur_requested", "Request native blur", launch_only=True)
            _appearance_switch("dark_mode", "🌙 Dark mode")   # Phase 8 store key; CSS tokens already exist (dark-shell); also drives the vibrancy material (native)
            _appearance_switch("follow_system_appearance", "🌗 Follow system appearance", launch_only=True)
            if _is_tiles:      # tile-emoji treatment only exists in the tiles menu (user 2026-11-22)
                _appearance_cycle("tile_emoji_style", "Tile emoji", list(ap.TILE_EMOJI_STYLES))

    @ui.page("/")
    @ui.page("/home")
    async def home(client):
        # first launch must not look dead: build the UI only once the websocket is up
        # (user 2026-11-04: menu rendered but no hover/click on first launch)
        await client.connected()
        loader.leave_page(client=client.id)     # Phase 2: this client's old page is gone
        st = cstate(client)
        s = get_settings()
        scr = pc.build_screen(m.Nav("home", m.HOME_OWNED), s, loader.cache, savers=settings_state.get('savers')).screen
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
            scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache, savers=settings_state.get('savers')).screen
            settings_state["last_scr"] = scr   # prompt:* dialogs opened from a lane need a screen
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
                        Path(stub).write_text(json.dumps({"argv": dispatch_argv(req)}))
                st["launch_req"] = _launch_req

                def do_launch(mid, mdl=None):
                    # csl launches the model directly on row click — no Launch button (user 2026-11-04)
                    _launch_req(m.LaunchRequest(target, [target, "--model", mid], {}, mid))

                def paint(models, query):
                    if target == "remote":
                        # remote_filters parity (2026-11-22): the lane paints what csl's
                        # RemoteScreen would show — the settings filters apply at paint time
                        models = m.visible_agents(get_settings(), models)
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
                                    row.on("mouseenter", lambda d=mdl: info.set_text(_lane_info(d)))
                        if target == "local":
                            for alias in list(pc.MISSING_MODELS):
                                if query and query.lower() not in alias.lower():
                                    continue
                                mrow = ui.label(f"    {alias}  —  weights missing from disk").classes("menu-item model-row missing-row")
                                mrow.props["data-missing"] = alias
                                mrow.on("click", lambda a=alias: ui.navigate.to(f"/prompt:missing-cleanup:{a}"))
                        if not any_shown:
                            msg = ("No local models on disk yet — download some first"
                                   if target == "local" else
                                   "No models — every provider needs an API key (open API keys)")
                            ui.label(msg).props('class="empty-state"')

                def _lane_info(mdl) -> str:
                    # csl parity (main 0.62.0 C8): the hover box IS the lane screen's own
                    # info_text (notes, thinking constraint, provider-limits facts, context) —
                    # one source for both frontends. The lane data can land after this page's
                    # screen was built, so an alias the screen does not know yet gets a fresh
                    # screen from the current cache; _info_text is only the last resort.
                    alias = getattr(mdl, "alias", "")

                    def knows(s) -> bool:
                        return bool(alias) and (alias in (getattr(s, "models", None) or {}) or any(
                            getattr(a, "alias", "") == alias for a in (getattr(s, "agents", None) or [])))
                    s_ = scr
                    if hasattr(s_, "info_text") and alias and not knows(s_):
                        s_ = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache,
                                             savers=settings_state.get('savers')).screen
                    if hasattr(s_, "info_text") and knows(s_):
                        return s_.info_text(alias)
                    return _info_text(mdl)

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

    @ui.page("/catalog")
    async def catalog_page(client):
        # Task 4: the local model-list REGISTRATION screen — view/edit the per-user
        # overlay store (co.NAME) through catalog_overlay's validated read/write,
        # and refresh the download screen's catalog cache after a write.
        from nicegui import ui as _ui
        await client.connected()
        loader.leave_page(client=client.id)
        st = cstate(client)
        if st.get("launch_req") is None:
            st["launch_req"] = lambda req: None
        store = co.overlay_path(Path(os.environ.get("LA_SESSION_MENU_CONFIG_DIR") or ".")) or co.store_path()
        body = render_shell("Local model catalog (overlay store)", "")
        with body:
            _ui.label(str(store)).classes("text-xs opacity-70")
            rows_box = _ui.column().classes("w-full gap-1")

            def paint_rows():
                rows_box.clear()
                with rows_box:
                    try:
                        rows = co.read_rows(store)
                    except co.OverlayWriteError as e:
                        _ui.label(f"invalid store: {e}").classes("text-red-500")
                        rows = []
                    for r in rows:
                        with _ui.row().classes("menu-item w-full"):
                            _ui.label(f"{r['alias']}  ·  {r['name']}  ·  {r['size_gb']} GB  [{r['serve']}]")
                            async def _on_rm(a=r["alias"]):
                                await remove_row(a)
                            rm = _ui.button("✕", on_click=_on_rm)
                            rm.props["data-remove"] = r["alias"]
            async def _refresh_catalog_cache():
                # repopulate OFF the UI thread after the pop: a cache-miss load on the next
                # /download can return Stale (leave_page race in jobs.Loader) and render the
                # catalog empty. The pop happens before paint_rows, so the row shows at once
                # and the (blocking) inventory subprocess no longer delays the click response.
                loader.cache["catalog"] = await asyncio.to_thread(pc.load_catalog)
            async def add_row(alias, name, repo, size, groups, serve):
                repo = repo.strip()
                try:
                    co.add_row(store, {"alias": alias.strip(), "name": name.strip(),
                                       "repo": repo, "rev": "main",
                                       # download-models.sh rejects rows without a subdir;
                                       # default to the repo basename (its own convention)
                                       "subdir": (repo.rsplit("/", 1)[-1] or alias.strip()),
                                       "size_gb": size.strip(),
                                       "groups": groups.strip(), "serve": serve.strip()})
                except co.OverlayWriteError as e:
                    _ui.notify(f"invalid row: {e}", type="negative")
                    return
                loader.cache.pop("catalog", None)
                paint_rows()
                await _refresh_catalog_cache()
            async def remove_row(alias):
                co.remove_row(store, alias)
                loader.cache.pop("catalog", None)
                paint_rows()
                await _refresh_catalog_cache()
            with _ui.row().classes("w-full gap-2"):
                a = _ui.input("alias").props('data-add="alias"')
                n = _ui.input("display name").props('data-add="name"')
                rp = _ui.input("hf repo").props('data-add="repo"')
                sz = _ui.input("size GB").props('data-add="size"')
                gr = _ui.input("groups (comma)").props('data-add="groups"')
                sv = _ui.input("serve (rapid|vllm-mlx|…)").props('data-add="serve"')
                async def _on_add():
                    await add_row(a.value, n.value, rp.value, sz.value, gr.value, sv.value)
                _ui.button("Add", on_click=_on_add)
            paint_rows()

    @ui.page("/{full_path:path}")
    async def screen_or_catchall(client, full_path: str):
        # registered LAST so it never shadows the real routes. Known screen targets render
        # through the shared core (same rows the Textual picker shows); unknown paths get
        # the catch-all with Back-to-Home — never a dead end.
        await client.connected()
        target = full_path
        scr = None
        if target.startswith("warn:"):
            # csl's warn: rows append the text to state_warnings (a notice); the GUI
            # renders the notice page with the message and a way back (2026-11-22)
            _msg = target[len("warn:"):]
            _b = render_shell("Warning", "")
            with _b:
                ui.label(_msg).classes("menu-item text-red-500").props('data-warn="msg"')
                ui.link("🏠 Back to Home", "/home").classes("menu-item nav-row").props('data-target="home"')
            return
        if target not in ("quit", "back"):          # not pages — handled by the launcher
            try:
                build = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache, savers=settings_state.get('savers'))
                # prefetch the screen's declared inventory kinds (csl parity: goto() does the
                # same via _prefetch) so the screen renders with DATA, not empty rows
                for kind in build.prefetch:
                    fn = _loader_for(kind)
                    if fn is not None:
                        await loader.load(kind, fn, client=client.id)
                        # csl re-reads the local list after an auto-sync registry change
                        if kind == "local" and get_settings().local_registry.get("auto_sync"):
                            await loader.load("local", pc.load_local_models, client=client.id)
                scr = pc.build_screen(m.Nav(target, m.HOME_OWNED), get_settings(), loader.cache, savers=settings_state.get('savers')).screen
                if isinstance(scr, m.LowkeyScreen):
                    # csl keeps the name on the screen model; the prompt:session dialog is a
                    # FRESH client on the prompt route, so the name lives process-globally
                    # and is injected at build (it then rides the launch argv: --session NAME)
                    scr.session_name = settings_state.get("lowkey_session_name", "")
            except Exception:
                scr = None
        # the launch/confirm dispatch hook, set on EVERY page (prompt:* dialogs confirm
        # on their own route — FA_GUI_LAUNCH_STUB in tests)
        st = cstate(client)
        if st.get("launch_req") is None:
            def _launch_req(req):
                stub = os.environ.get("FA_GUI_LAUNCH_STUB")
                if stub:
                    Path(stub).write_text(json.dumps({"argv": dispatch_argv(req)}))
            st["launch_req"] = _launch_req
        if target.startswith("prompt:"):
            # prompt:* dialogs (csl's typed confirmations): the payload comes from the
            # screen that EMITTED the prompt (the last built screen) —
            # build_screen itself has no prompt: target
            scr = settings_state.get("last_scr") or scr
        if target.startswith("prompt:") and scr is not None:
            from nicegui import ui as _ui
            _st = cstate(client)   # the Yes handler dispatches through this client's launch hook
            payload = prompt_payload(target, scr)
            with _ui.dialog() as dlg, _ui.card().classes("w-full max-w-md"):
                _ui.label(target).classes("text-lg font-bold")
                if payload and payload.get("kind") == "missing-cleanup":
                    _ui.label(f"Remove '{payload['alias']}' from the local registry?")
                    _ui.label("weights missing from disk — the #missing cleanup offer")
                elif payload and payload.get("kind") == "download-review":
                    _ui.label(f"{len(payload['aliases'])} queued, {payload['total_gb']:.1f} GB"
                              + ("" if payload["fits"] else " — DOES NOT FIT in free space"))
                    _ui.label(", ".join(payload["aliases"]))
                if payload and payload.get("kind") == "confirm-rl-reset":
                    _ui.label(payload["prompt"])
                    _ui.label("Every free-API lane forgets its recorded request history and cooldowns.")
                elif payload and payload.get("kind") == "confirm-launchd":
                    _ui.label(payload["prompt"])
                    _ui.label(payload["detail"])

                    def _launchd_no():
                        # csl's No: nothing runs, and the screen says so
                        _ui.notify("launchd unchanged — nothing was run")
                        dlg.close()
                    with _ui.row():
                        _ui.button("Yes", on_click=lambda: _confirm_prompt(scr, target, _st, dlg))
                        _ui.button("No", on_click=_launchd_no)
                    dlg.open()
                    return
                elif payload and payload.get("kind") == "input":
                    # typed prompt (prompt:session / prompt:oneshot / prompt:keys:key):
                    # Yes validates exactly like csl and applies the value
                    _ui.label(payload["prompt"])
                    field = _ui.input("text").classes("w-full")
                    if payload.get("password"):
                        field.props('type="password"')
                    warn = _ui.label("").classes("text-red-500")

                    def _save_api_key(slug: str, value: str) -> str:
                        # csl's path: install/setup-api-keys.py's Store (LA_API_KEYS_DIR)
                        import importlib.util
                        from pathlib import Path as _P
                        spec = importlib.util.spec_from_file_location(
                            "setup_api_keys",
                            _P(__file__).resolve().parents[2] / "install/setup-api-keys.py")
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        prov = next((p for p in mod.PROVIDERS if p.slug == slug), None)
                        if prov is None:
                            return f"Unknown provider: {slug}"
                        keys_dir = _P(os.environ.get("LA_API_KEYS_DIR", "~/.api_keys")).expanduser()
                        with mod.Store(keys_dir) as store:
                            missing = [n for n in prov.files if store.state(n) == "missing"]
                            if not missing:
                                return f"{prov.name}: key already saved"
                            try:
                                mod.validate_value(missing[0], value)
                                store.save_new(missing[0], value)
                            except mod.StoreError as exc:
                                return f"{missing[0]}: {exc}"
                        return f"Saved {missing[0]} for {prov.name}"

                    def _submit_input():
                        import re
                        val = (field.value or "").strip()
                        if target == "prompt:session":
                            if val and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", val):
                                warn.set_text("session name rejected: use letters, digits, - or _")
                                return
                            settings_state["lowkey_session_name"] = val
                            _ui.navigate.to("/lowkey")
                        elif target.startswith("prompt:keys:key:"):
                            slug = target.split(":", 3)[3]
                            if not val:
                                warn.set_text("skipped — no key saved")
                                return
                            msg = _save_api_key(slug, val)
                            warn.set_text(msg)
                            if msg.startswith("Saved"):
                                _ui.navigate.to("/api_keys")
                                dlg.close()
                            return
                        else:   # prompt:oneshot: the screen turns the text into a --prompt launch
                            res = scr.one_shot(val) if hasattr(scr, "one_shot") else None
                            if res is None:
                                warn.set_text("one-shot needs a prompt (and a selected model)")
                                return
                            if _st.get("launch_req"):
                                _st["launch_req"](res)
                        dlg.close()

                    field.on("keydown", lambda e: e.action == "Enter" and _submit_input())
                    with _ui.row():
                        _ui.button("Yes", on_click=_submit_input)
                        _ui.button("No", on_click=dlg.close)
                    dlg.open()
                    return
                else:
                    _ui.label("Confirm?")
                with _ui.row():
                    _ui.button("Yes", on_click=lambda: _confirm_prompt(scr, target, _st, dlg))
                    _ui.button("No", on_click=dlg.close)
            dlg.open()
            return
        if scr is None:
            with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3"):
                ui.label(f"/{full_path}").classes("text-2xl font-bold")
                ui.label("This screen is not built yet.").classes("text-sm opacity-70")
                ui.link("🏠 Back to Home", "/home").classes("menu-item")
            return
        loader.leave_page(client=client.id)     # Phase 2: this client's old page is gone
        st = cstate(client)
        if scr is not None:
            # process-global: ui.navigate.to re-runs the page with a FRESH websocket client,
            # so per-client state (st) is empty on the prompt: route — prompt:* dialogs
            # read their payload from here
            settings_state["last_scr"] = scr
        if full_path == "lowkey":
            st["toggle_list"] = lambda: None   # placeholder; the chooser toggle is set below
        body = render_shell(scr.title, getattr(scr, 'policy', '') if isinstance(getattr(scr, 'policy', ''), str) else "")
        with body:
            render_rows(scr, st)
        if full_path == "theme_config":
            render_appearance(body, st)         # Phase 4: GUI-only appearance section
        if isinstance(scr, m.DownloadScreen):
            scr.queue = list(st.get("download_queue", []))   # re-attach the per-client queue
            for _a in scr.actions():
                if _a.key == "c":
                    _a.enabled = bool(scr.queue)   # review row is disabled until the queue is non-empty
            render_download_entries(body, scr, st)   # queue rows (csl's entry list)
        if full_path == "lowkey":
            # csl parity (QC R1): the lowkey model chooser — local models + API agents,
            # grouped; clicking a row SELECTS (no launch, like csl) and the selection is
            # persisted process-globally so the session-name dialog round-trip keeps it
            _sel = settings_state.get("lowkey_selection")
            if _sel is not None:
                try:
                    scr.accordion.select(_sel)
                    scr.activate_selected()
                except Exception:
                    pass
            chooser = ui.column().classes("w-full gap-1 lane-list")
            chooser.props["style"] = "display:none"

            def _select(item_id: str) -> None:
                settings_state["lowkey_selection"] = item_id
                try:
                    scr.accordion.select(item_id)
                    scr.activate_selected()
                except Exception:
                    pass
                _paint_chooser()

            def _paint_chooser():
                chooser.clear()
                with chooser:
                    _sel_now = settings_state.get("lowkey_selection")
                    for mdl in (loader.cache.get("local") or []):
                        alias = getattr(mdl, "alias", "")
                        r = ui.label(f"    {alias}").classes(
                            "menu-item model-row" + (" selected" if _sel_now == alias else ""))
                        r.props["data-model"] = alias
                        r.on("click", lambda a=alias: _select(a))
                    by_prov: dict = {}
                    for ag in (loader.cache.get("remote") or []):
                        by_prov.setdefault(getattr(ag, "provider", "?"), []).append(ag)
                    for prov, agents in by_prov.items():
                        for ag in agents:
                            iid = f"{prov}:{ag.model_id or ag.alias}"
                            r = ui.label(f"    {ag.display or ag.alias}").classes(
                                "menu-item model-row" + (" selected" if _sel_now == iid else ""))
                            r.props["data-model"] = iid
                            r.on("click", lambda a=iid: _select(a))

            def _toggle_chooser():
                visible = chooser.props.get("style", "") != "display:none"
                chooser.props["style"] = "display:none" if visible else ""
                if visible is False:
                    _paint_chooser()

            st["toggle_list"] = _toggle_chooser
            for _kind, _fn in (("local", pc.load_local_models), ("remote", pc.load_remote_agents)):
                try:
                    await loader.load(_kind, _fn, client=client.id)
                except Exception:
                    pass
            _paint_chooser()
        if full_path == "api_keys":
            # csl parity: the provider list with live Saved/Missing status, the signup
            # link and the contextual Add key (csl's accordion rows on this screen)
            with ui.column().classes("w-full gap-1"):
                for slug, name, _sc, url, files in scr.PROVIDERS:
                    try:
                        status = scr._get_provider_status(slug, files)
                    except Exception:
                        status = "Unknown"
                    row = ui.row().classes("menu-item items-center gap-2").props(
                        f'data-provider="{slug}"')
                    with row:
                        ui.label(f"{name}:  {status}").classes("grow")
                        if url:
                            ui.link("Signup", url).classes("text-xs")
                        ui.button("Add key",
                                  on_click=lambda s=slug: ui.navigate.to(f"/prompt:keys:key:{s}"))
        if full_path.startswith("runtime_versions:"):
            # csl parity: the version list csl's VersionsScreen shows as an OptionList —
            # clicking a row runs the op for that version (remove confirms first)
            _op = full_path.split(":", 1)[1]
            _kind = getattr(scr, "inventory_kind", None)
            _entries = (loader.cache.get(_kind) or []) if _kind else []
            vlist = ui.column().classes("w-full gap-1")
            with vlist:
                for e in _entries:
                    _v = getattr(e, "version", "") or str(e)
                    _status = getattr(e, "status", "")
                    _lbl = f"    {_v}" + (f"  ·  {_status}" if _status else "")
                    if getattr(e, "pinned", False):
                        _lbl += "  📌"
                    r = ui.label(_lbl).classes("menu-item model-row")
                    r.props["data-version"] = _v

                    def _run_op(v=_v):
                        if _op == "remove":
                            # csl confirms a REMOVE through prompt:rt-remove first
                            _settings_state["pending_tool"] = f"tool:rt-op:remove:{v}"
                            ui.navigate.to("/prompt:rt-remove")
                        else:
                            _argv = pc.tool_argv(f"tool:rt-op:{_op}:{v}", scr.backend, scr)
                            if _argv and st.get("launch_req"):
                                st["launch_req"](m.LaunchRequest("tool", ["tool", *_argv], {}, ""))
                    r.on("click", _run_op)
        wire_rerender(body, m.Nav(full_path, m.HOME_OWNED), st)
        if full_path == "theme_config":
            # wrap AFTER wire_rerender (it sets st["rerender"]): a presentation switch must
            # rebuild rows AND the appearance section, so the live apply never wipes the
            # settings (2026-11-21 regression) and the menu's real tile preview changes
            # shape (2026-11-22)
            def _rerender_theme():
                scr2 = pc.build_screen(m.Nav(full_path, m.HOME_OWNED), get_settings(),
                                       loader.cache, savers=settings_state.get('savers')).screen
                body.clear()
                with body:
                    render_rows(scr2, st)
                render_appearance(body, st)
            st["rerender"] = _rerender_theme
        if isinstance(scr, m.DownloadScreen):
            # wrap AFTER wire_rerender (it sets st["rerender"]): the plain rebuild only
            # restores action rows — re-attach the queue and re-render the entry rows too
            _base_rerender = st.get("rerender")
            def _dl_rerender(_b=body, _t=full_path, _s=st):
                # full rebuild from a FRESH screen (the base rerender would use the stale
                # one whose review row was built disabled with an empty queue)
                scr2 = pc.build_screen(m.Nav(_t, m.HOME_OWNED), get_settings(), loader.cache,
                                       savers=settings_state.get('savers')).screen
                scr2.queue = list(_s.get("download_queue", []))
                for _a in scr2.actions():
                    if _a.key == "c":
                        _a.enabled = bool(scr2.queue)
                _b.clear()
                with _b:
                    render_rows(scr2, _s)
                    render_download_entries(_b, scr2, _s)
            if _base_rerender is not None:
                st["rerender"] = _dl_rerender
