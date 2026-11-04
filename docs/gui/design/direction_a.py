"""docs/gui/design/direction_a.py — Task 8 direction A: "Terminal Night" (dark, terminal-
inspired, emoji-forward). Static NiceGUI page over synthetic Home + Remote data.
Identity: 📡 radio (remote) / 🦾 arm (local); accent = remote theme lime #ADFF2F.
Proportional UI font everywhere; monospace ONLY for alias ids / commands."""
from nicegui import ui

ACCENT = "#ADFF2F"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
UI = "ui-sans-serif, -apple-system, 'Segoe UI', sans-serif"

MODELS = [("NVIDIA", [("Mistral Large 4 (Le Chonk)", "mistralai/mistral-large-4-1t-a52b", "1.05T"),
                      ("GLM 5.3 Flash", "z-ai/glm-5.3-flash", "321B")]),
          ("Gemini", [("Gemini Flash", "gemini-flash", "unknown")])]


def _theme():
    # direction A is dark-first; ?theme=light shows the light variant for comparison
    return "light" if getattr(getattr(ui.context, "request", None), "query_params", {}).get("theme") == "light" else "dark"


def _bg():
    return "#f6f8fa" if _theme() == "light" else "#0d1117"


def _ink():
    return "#1f2328" if _theme() == "light" else "#e6edf3"


def _dim():
    return "#656d76" if _theme() == "light" else "#8b949e"


@ui.page("/")
def home():
    with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3").style(f"font-family:{UI}; background:{_bg()}; color:{_ink()}; min-height:100vh"):
        ui.label("📡🦾 Free Agents — Session Launcher").classes("text-3xl font-bold").style(f"color:{ACCENT}")
        ui.label("Claude Code on free inference — local MLX models on this Mac, or free cloud APIs").classes("text-sm").style(f"color:{_dim()}")
        for label in ("🖥  Local sessions (12 on disk)", "📡  Remote free API sessions", "🔑  API keys — install / set up", "🛠  Backend manager"):
            ui.label(label).classes("text-base px-3 py-1 rounded").style("border-left: 3px solid " + ACCENT)
        ui.label("free-agents v0.53.1").classes("text-xs italic mt-4").style(f"color:{_dim()}")


@ui.page("/remote")
def remote():
    with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-3").style(f"font-family:{UI}; background:{_bg()}; color:{_ink()}; min-height:100vh"):
        ui.label("📡 Remote API Session Picker").classes("text-2xl font-bold").style(f"color:{ACCENT}")
        ui.input(placeholder="type to filter models").classes("w-full").style(f"font-family:{UI}")
        ui.label("2 model(s) visible (hidden: 0)").classes("text-xs").style(f"color:{_dim()}")
        for prov, rows in MODELS:
            ui.label(f"▾ {prov} ({len(rows)})").classes("text-base font-semibold mt-2").style(f"color:{ACCENT}")
            for name, mid, size in rows:
                with ui.row().classes("items-baseline gap-2 px-2"):
                    ui.label(name).classes("text-base")
                    ui.label(f"· {size}").classes("text-xs").style(f"color:{_dim()}")
                    ui.label(mid).classes("text-xs").style(f"font-family:{MONO}; color:{_dim()}")
        ui.label("Hover a model for notes · Enter launches").classes("text-xs mt-3").style(f"color:{_dim()}")


ui.run(title="direction A", reload=False, show=False, show_welcome_message=False, host="127.0.0.1", port=8901)
