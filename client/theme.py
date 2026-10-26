
"""All colours, fonts and sizes for the SyncDraw client UI.

Colours are (light, dark) pairs, the format CustomTkinter expects.
The drawing canvas always stays white in both modes.
"""

import sys

if sys.platform == "win32":
    FONT_FAMILY = "Segoe UI"
    ICON_FAMILY = "Segoe UI Emoji"  # full-size emoji glyphs in Tk
elif sys.platform == "darwin":
    FONT_FAMILY = "SF Pro Display"
    ICON_FAMILY = "Apple Color Emoji"
else:
    FONT_FAMILY = "Helvetica"
    ICON_FAMILY = "Helvetica"

THEME = {
    "font_family": FONT_FAMILY,
    "icon_family": ICON_FAMILY,
    "font_title": 18,
    "font_body": 13,
    "font_small": 11,
    "font_icon": 20,

    "radius": 12,
    "radius_small": 8,
    "pad": 10,
    "pad_small": 6,

    "header_height": 56,
    "rail_width": 72,
    "tool_button": 48,
    "swatch": 26,
    "panel_width": 300,
    "min_size": (1000, 680),
    "start_size": (1200, 720),

    # Chrome colours (light, dark).
    "bg": ("#EEF1F5", "#16181D"),
    "panel": ("#FFFFFF", "#22252C"),
    "panel_border": ("#DDE1E7", "#30343C"),
    "text": ("#111827", "#E5E7EB"),
    "muted": ("#6B7280", "#9CA3AF"),
    "divider": ("#E5E7EB", "#30343C"),
    "hover": ("#EEF2F7", "#2C3038"),
    "disabled": ("#D1D5DB", "#3A3F48"),
    "card": ("#F6F8FB", "#1B1E24"),
    "chip": ("#F3F4F6", "#2C3038"),

    "accent": "#3B82F6",
    "accent_hover": "#2563EB",
    "accent_soft": ("#DBEAFE", "#1E3A5F"),

    "ok": "#16A34A",
    "ok_soft": ("#DCFCE7", "#14321F"),
    "warn": "#D97706",
    "warn_soft": ("#FEF3C7", "#3A2A0E"),
    "error": "#DC2626",
    "error_soft": ("#FEE2E2", "#3B1A1A"),

    "toast_bg": ("#111827", "#F3F4F6"),
    "toast_text": ("#F9FAFB", "#111827"),

    # The canvas is never themed: the eraser paints this colour.
    "canvas_bg": "#FFFFFF",
    "canvas_shadow": ("#D9DEE5", "#0E1013"),

    "swatches": [
        ("Black", "#000000"),
        ("Grey", "#6B7280"),
        ("Red", "#EF4444"),
        ("Orange", "#F97316"),
        ("Yellow", "#EAB308"),
        ("Green", "#16A34A"),
        ("Blue", "#2563EB"),
        ("Purple", "#9333EA"),
    ],

    "toast_ms": 3000,
    "dots_ms": 400,
}


def font(size_key="font_body", weight="normal"):
    """Return a (family, size, weight) tuple usable by CTk and Tk."""
    return (THEME["font_family"], THEME[size_key], weight)


def icon_font(size_key="font_icon"):
    """Font for icon-only buttons (emoji)."""
    return (THEME["icon_family"], THEME[size_key])
