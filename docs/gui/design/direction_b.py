"""docs/gui/design/direction_b.py — Task 8 direction B: "Sunny Desk" (light, playful,
warm). Static NiceGUI page over synthetic Home + Remote data.
Identity: 📡 🦾; accent = local theme yellow #FFD700. Proportional UI font; monospace only
for alias ids."""
from nicegui import ui

ACCENT = "#FFD700"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
UI = "ui-sans-serif, -apple-system, 'Segoe UI', sans-serif"

MODELS = [("NVIDIA", [("Mistral Large 4 (Le Chonk)", "mistralai/mistral-large-4-1t-a52b", "1.05T"),
                      ("GLM 5.3 Flash", "z-ai/glm-5.3-flash", "321B")]),
          ("Gemini", [("Gemini Flash", "gemini-flash", "unknown")])]


def _theme():
    # direction B is light-first; ?theme=dark shows the dark variant for comparison
    return "dark" if getattr(getattr(ui.context, "request", None), "query_params", {}).get("theme") == "dark" else "light"


def _bg():
    return "#0d1117" if _theme() == "dark" else "#FFF8E1"


def _ink():
    return "#e6edf3" if _theme() == "dark" else "#3e2723"


def _dim():
    return "#8b949e" if _theme() == "dark" else "#6d4c41"


def _card():
    return "#161b22" if _theme() == "dark" else "#FFFFFF"


@ui.page("/")
def home():
    with ui.column().classes("w-full max-w-3xl mx-auto p-8 gap-4").style(f"font-family:{UI}; background:{_bg()}; color:{_ink()}; min-height:100vh"):
        ui.label("📡🦾 Free Agents").classes("text-4xl font-black")
        ui.label("Claude Code on free inference — local models on this Mac, or free cloud APIs").classes("text-base").style(f"color:{_dim()}")
        with ui.row().classes("gap-3 flex-wrap"):
            for label, icon in (("Local sessions", "🖥"), ("Remote free API", "📡"), ("API keys", "🔑"), ("Backend manager", "🛠")):
                with ui.card().classes("p-4 w-44").style(f"background:{_card()}; border-radius:16px; border:3px solid {ACCENT}"):
                    ui.label(f"{icon}  {label}").classes("text-base font-semibold")
        ui.label("free-agents v0.53.1").classes("text-xs italic mt-6").style(f"color:{_dim()}")


@ui.page("/remote")
def remote():
    with ui.column().classes("w-full max-w-3xl mx-auto p-8 gap-3").style(f"font-family:{UI}; background:{_bg()}; color:{_ink()}; min-height:100vh"):
        ui.label("📡 Remote API Session Picker").classes("text-3xl font-black")
        ui.input(placeholder="type to filter models").classes("w-full").style(f"font-family:{UI}; background:{_card()}; border-radius:12px")
        ui.label("2 model(s) visible (hidden: 0)").classes("text-sm").style(f"color:{_dim()}")
        for prov, rows in MODELS:
            with ui.card().classes("w-full p-3 mt-2").style(f"background:{_card()}; border-radius:14px; border-left:6px solid {ACCENT}"):
                ui.label(f"{prov} ({len(rows)})").classes("text-lg font-bold")
                for name, mid, size in rows:
                    with ui.row().classes("items-baseline gap-2"):
                        ui.label(name).classes("text-base")
                        ui.label(size).classes("text-xs px-2 rounded-full").style(f"background:{ACCENT}")
                        ui.label(mid).classes("text-xs").style(f"font-family:{MONO}; color:{_dim()}")
        ui.label("Hover a model for notes · Enter launches").classes("text-sm mt-3").style(f"color:{_dim()}")


ui.run(title="direction B", reload=False, show=False, show_welcome_message=False, host="127.0.0.1", port=8802)
