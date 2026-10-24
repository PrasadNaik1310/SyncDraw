
"""SyncDraw client UI (CustomTkinter). Presentation only, no network code.

SyncDrawUI is mixed into SyncDrawClient (client/main.py), which owns the
socket, the receive thread and the message queue. Everything here runs on
the Tkinter GUI thread.
"""

import time
import tkinter as tk
from tkinter import colorchooser

import customtkinter as ctk

from client.theme import THEME, font, icon_font

# Size limits match the server's DRAW validation (pen 1-20, eraser <= 100).
PEN_MIN, PEN_MAX, PEN_STEP = 1, 20, 1
ERASER_MIN, ERASER_MAX, ERASER_STEP = 5, 100, 5

SUMMARY_HINT = "Draw something, then tap Summarize to get an AI description of the board."
PREVIEW_BOX = 56  # logical px


class Tooltip:
    """Small tooltip shown next to a widget on hover."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.cancel()
        self.after_id = self.widget.after(500, self.show)

    def cancel(self):
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self):
        self.after_id = None
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() // 2 - 12
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip,
            text=self.text,
            bg="#111827",
            fg="#F9FAFB",
            font=font("font_small"),
            padx=8,
            pady=4,
        ).pack()

    def hide(self, _event=None):
        self.cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class PinDialog(ctk.CTkToplevel):
    """Small modal dialog with a masked PIN entry."""

    def __init__(self, master):
        super().__init__(master, fg_color=THEME["panel"])
        self.value = None
        self.title("Become Admin")
        self.resizable(False, False)
        self.transient(master)

        ctk.CTkLabel(
            self,
            text="🔑 Admin PIN",
            font=font("font_title", "bold"),
            text_color=THEME["text"],
        ).pack(padx=24, pady=(20, 2), anchor="w")

        ctk.CTkLabel(
            self,
            text="Enter the shared PIN to start drawing.",
            font=font("font_small"),
            text_color=THEME["muted"],
        ).pack(padx=24, anchor="w")

        self.entry = ctk.CTkEntry(
            self,
            show="*",
            width=260,
            height=36,
            corner_radius=THEME["radius_small"],
            font=font(),
        )
        self.entry.pack(padx=24, pady=14)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(0, 20))

        ctk.CTkButton(
            row,
            text="Cancel",
            width=110,
            height=34,
            corner_radius=THEME["radius_small"],
            fg_color="transparent",
            border_width=1,
            border_color=THEME["panel_border"],
            text_color=THEME["text"],
            hover_color=THEME["hover"],
            font=font(),
            command=self.destroy,
        ).pack(side="left")

        ctk.CTkButton(
            row,
            text="Unlock",
            width=110,
            height=34,
            corner_radius=THEME["radius_small"],
            fg_color=THEME["accent"],
            hover_color=THEME["accent_hover"],
            font=font("font_body", "bold"),
            command=self.submit,
        ).pack(side="right")

        self.bind("<Return>", lambda e: self.submit())
        self.bind("<Escape>", lambda e: self.destroy())

        # Centre over the main window.
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.wm_geometry(f"+{max(0, x)}+{max(0, y)}")

        # CTkToplevel needs a moment on Windows before it can take focus.
        self.after(150, self.take_focus)

    def take_focus(self):
        # Stay above the main window even if it is "always on top".
        self.attributes("-topmost", True)
        self.lift()
        self.entry.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def submit(self):
        self.value = self.entry.get()
        self.destroy()


def ask_pin(master):
    """Show the PIN dialog; returns the PIN, or None if cancelled."""
    dialog = PinDialog(master)
    master.wait_window(dialog)
    return dialog.value


class SimpleDialog(ctk.CTkToplevel):
    """Small modal: title, message, optional entry and two buttons."""

    def __init__(self, master, title, heading, message, ok_text,
                 cancel_text="Cancel", entry_default=None, danger=False):
        super().__init__(master, fg_color=THEME["panel"])
        self.value = None
        self.title(title)
        self.resizable(False, False)
        self.transient(master)

        ctk.CTkLabel(
            self,
            text=heading,
            font=font("font_title", "bold"),
            text_color=THEME["text"],
        ).pack(padx=24, pady=(20, 2), anchor="w")

        ctk.CTkLabel(
            self,
            text=message,
            font=font("font_small"),
            text_color=THEME["muted"],
            wraplength=300,
            justify="left",
        ).pack(padx=24, anchor="w")

        self.entry = None
        if entry_default is not None:
            self.entry = ctk.CTkEntry(
                self,
                width=280,
                height=36,
                corner_radius=THEME["radius_small"],
                font=font(),
            )
            self.entry.insert(0, entry_default)
            self.entry.pack(padx=24, pady=(14, 0))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=20)

        if cancel_text:
            ctk.CTkButton(
                row,
                text=cancel_text,
                width=120,
                height=34,
                corner_radius=THEME["radius_small"],
                fg_color="transparent",
                border_width=1,
                border_color=THEME["panel_border"],
                text_color=THEME["text"],
                hover_color=THEME["hover"],
                font=font(),
                command=self.destroy,
            ).pack(side="left")

        ctk.CTkButton(
            row,
            text=ok_text,
            width=120,
            height=34,
            corner_radius=THEME["radius_small"],
            fg_color=THEME["error"] if danger else THEME["accent"],
            hover_color="#B91C1C" if danger else THEME["accent_hover"],
            font=font("font_body", "bold"),
            command=self.submit,
        ).pack(side="right")

        self.bind("<Return>", lambda e: self.submit())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.wm_geometry(f"+{max(0, x)}+{max(0, y)}")
        self.after(150, self.take_focus)

    def take_focus(self):
        self.attributes("-topmost", True)
        self.lift()
        (self.entry or self).focus_force()
        if self.entry is not None:
            self.entry.select_range(0, "end")
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def submit(self):
        self.value = self.entry.get() if self.entry is not None else True
        self.destroy()


def ask_name(master, default):
    """Startup dialog for the display name; falls back to `default`."""
    dialog = SimpleDialog(
        master,
        "Welcome to SyncDraw",
        "👋 Welcome to SyncDraw",
        "Enter your display name (max 20 characters).",
        "Join",
        cancel_text=None,
        entry_default=default,
    )
    master.wait_window(dialog)
    name = (dialog.value or "").strip()
    return name or default


def ask_confirm(master, heading, message, ok_text):
    """Confirmation dialog; True only if the user confirms."""
    dialog = SimpleDialog(master, "Confirm", heading, message, ok_text, danger=True)
    master.wait_window(dialog)
    return dialog.value is True


class SyncDrawUI:
    """Layout and presentation methods for SyncDrawClient."""

    # ---------- build ----------

    def build_ui(self):
        root = self.root
        ctk.set_appearance_mode("light")
        root.title("SyncDraw — Viewer")
        root.minsize(*THEME["min_size"])

        # Start centred and fully on screen (CTk scales the size but not
        # the position, so x/y are physical pixels). Leave room for the
        # title bar and taskbar; shrink on small screens.
        s = ctk.ScalingTracker.get_window_scaling(root)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(THEME["start_size"][0], int((sw - 40) / s))
        h = min(THEME["start_size"][1], int((sh - 140) / s))
        x = max(0, int((sw - w * s) / 2))
        y = max(0, int((sh - 90 - h * s) / 2))
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.configure(fg_color=THEME["bg"])
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(1, weight=1)

        # Board coordinates are logical pixels; the canvas uses physical
        # pixels on HiDPI screens. Convert so all clients line up.
        self.canvas_scale = ctk.ScalingTracker.get_widget_scaling(root)
        ctk.ScalingTracker.add_widget(self.on_scaling_change, root)

        self.toast_after_id = None
        self.dots_after_id = None
        self.dots = 0
        self.summary_panel_visible = True
        self.role_known = False

        # Presence: online users and other users' cursors.
        self.users = {}            # client_id -> user dict from the server
        self.presence_known = False
        self.remote_cursors = {}   # client_id -> {"tag", "x", "y", "seen"}
        self.cursor_tag_count = 0

        self.build_header()
        self.build_tool_rail()
        self.build_canvas_area()
        self.build_summary_panel()
        self.build_toast()  # last, so it stacks above everything
        self.bind_shortcuts()
        self.root.after(1000, self.sweep_remote_cursors)

    def build_header(self):
        pad = THEME["pad"]
        header = ctk.CTkFrame(
            self.root,
            height=THEME["header_height"],
            corner_radius=0,
            fg_color=THEME["panel"],
        )
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="🎨 SyncDraw",
            font=font("font_title", "bold"),
            text_color=THEME["text"],
        ).grid(row=0, column=0, padx=(16, pad), pady=pad, sticky="w")

        # Centre: connection status pill and board version.
        center = ctk.CTkFrame(header, fg_color="transparent")
        center.grid(row=0, column=1)

        self.status_pill = ctk.CTkLabel(
            center,
            text="",
            height=28,
            corner_radius=14,
            font=font("font_small", "bold"),
            padx=10,
        )
        self.status_pill.pack(side="left")
        self.set_status("Connecting…", "warn")

        self.version_label = ctk.CTkLabel(
            center,
            text="v0",
            text_color=THEME["muted"],
            font=font("font_small"),
        )
        self.version_label.pack(side="left", padx=(8, 0))

        # Right: summary toggle, role badge, admin, admin button, theme.
        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=2, padx=(pad, 16), sticky="e")

        self.summary_toggle = ctk.CTkButton(
            right,
            text="✨ Summary",
            width=110,
            height=34,
            corner_radius=THEME["radius_small"],
            fg_color=THEME["accent_soft"],
            hover_color=THEME["hover"],
            text_color=THEME["text"],
            font=font(),
            command=self.toggle_summary_panel,
        )
        self.summary_toggle.pack(side="left", padx=(0, 12))

        self.role_badge = ctk.CTkLabel(
            right,
            text="",
            height=28,
            corner_radius=14,
            font=font("font_small", "bold"),
            padx=10,
        )
        self.role_badge.pack(side="left")

        self.admin_label = ctk.CTkLabel(
            right,
            text="No admin",
            text_color=THEME["muted"],
            font=font("font_small"),
        )
        self.admin_label.pack(side="left", padx=10)

        self.admin_button = ctk.CTkButton(
            right,
            text="🔑 Become Admin",
            width=150,
            height=34,
            corner_radius=THEME["radius_small"],
            font=font("font_body", "bold"),
            command=self.on_admin_button,
        )
        self.admin_button.pack(side="left")

        self.theme_switch = ctk.CTkSwitch(
            right,
            text="🌙",
            width=56,
            progress_color=THEME["accent"],
            text_color=THEME["text"],
            font=font(),
            command=self.toggle_theme,
        )
        self.theme_switch.pack(side="left", padx=(14, 0))

    def divider(self, parent):
        ctk.CTkFrame(
            parent,
            height=1,
            width=44,
            fg_color=THEME["divider"],
        ).pack(pady=10)

    def build_tool_rail(self):
        pad = THEME["pad"]
        size = THEME["tool_button"]
        sw = THEME["swatch"]

        rail = ctk.CTkFrame(
            self.root,
            width=THEME["rail_width"],
            corner_radius=THEME["radius"],
            fg_color=THEME["panel"],
            border_width=1,
            border_color=THEME["panel_border"],
        )
        rail.grid(row=1, column=0, sticky="ns", padx=(pad, 0), pady=pad)

        # Pen / eraser toggle buttons.
        self.tool_buttons = {}
        for tool, icon, tip in (
            ("pen", "✏️", "Pen (P)"),
            ("eraser", "🧽", "Eraser (E)"),
        ):
            button = ctk.CTkButton(
                rail,
                text=icon,
                width=size,
                height=size,
                corner_radius=THEME["radius_small"],
                font=icon_font(),
                hover_color=THEME["hover"],
                command=lambda t=tool: self.set_tool(t),
            )
            button.pack(padx=12, pady=(12 if tool == "pen" else 6, 0))
            Tooltip(button, tip)
            self.tool_buttons[tool] = button

        self.divider(rail)

        # Colour swatches, two per row.
        grid = ctk.CTkFrame(rail, fg_color="transparent")
        grid.pack(padx=6)

        self.swatch_buttons = {}
        for i, (name, color) in enumerate(THEME["swatches"]):
            button = ctk.CTkButton(
                grid,
                text="",
                width=sw,
                height=sw,
                corner_radius=sw // 2,
                fg_color=color,
                hover_color=color,
                border_color=THEME["accent"],
                command=lambda c=color: self.set_color(c),
            )
            button.grid(row=i // 2, column=i % 2, padx=3, pady=3)
            Tooltip(button, name)
            self.swatch_buttons[color] = button

        self.custom_color_button = ctk.CTkButton(
            rail,
            text="🎨",
            width=size,
            height=32,
            corner_radius=THEME["radius_small"],
            font=icon_font(),
            fg_color="transparent",
            hover_color=THEME["hover"],
            border_width=1,
            border_color=THEME["panel_border"],
            text_color=THEME["text"],
            command=self.pick_custom_color,
        )
        self.custom_color_button.pack(pady=(6, 0))
        Tooltip(self.custom_color_button, "Custom colour")

        self.divider(rail)

        # Undo / Clear (admin only; the server enforces it too).
        actions = ctk.CTkFrame(rail, fg_color="transparent")
        actions.pack()

        self.undo_button = ctk.CTkButton(
            actions,
            text="↶",
            width=28,
            height=30,
            corner_radius=6,
            border_spacing=0,
            font=font("font_title", "bold"),
            fg_color="transparent",
            hover_color=THEME["hover"],
            text_color=THEME["text"],
            text_color_disabled=THEME["disabled"],
            command=self.request_undo,
        )
        self.undo_button.grid(row=0, column=0, padx=1)
        Tooltip(self.undo_button, "Undo last stroke (Ctrl+Z)")

        self.clear_button = ctk.CTkButton(
            actions,
            text="🗑",
            width=28,
            height=30,
            corner_radius=6,
            border_spacing=0,
            font=icon_font("font_body"),
            fg_color="transparent",
            hover_color=THEME["error_soft"],
            text_color=THEME["error"],
            text_color_disabled=THEME["disabled"],
            command=self.request_clear,
        )
        self.clear_button.grid(row=0, column=1, padx=1)
        Tooltip(self.clear_button, "Clear board for everyone")

        self.divider(rail)

        # One size slider for the active tool.
        self.size_slider = ctk.CTkSlider(
            rail,
            orientation="vertical",
            width=16,
            height=100,
            from_=PEN_MIN,
            to=PEN_MAX,
            number_of_steps=(PEN_MAX - PEN_MIN) // PEN_STEP,
            progress_color=THEME["accent"],
            button_color=THEME["accent"],
            button_hover_color=THEME["accent_hover"],
            command=self.on_size_slider,
        )
        self.size_slider.pack(pady=(0, 8))
        Tooltip(self.size_slider, "Size ( [ / ] )")

        box = round(PREVIEW_BOX * self.canvas_scale)
        self.size_preview = tk.Canvas(
            rail,
            width=box,
            height=box,
            highlightthickness=0,
            bg=self.themed("panel"),
        )
        self.size_preview.pack()

        self.size_label = ctk.CTkLabel(
            rail,
            text="",
            text_color=THEME["muted"],
            font=font("font_small"),
        )
        self.size_label.pack(pady=(2, 12))

    def build_canvas_area(self):
        pad = THEME["pad"]
        radius = THEME["radius"]

        area = ctk.CTkFrame(self.root, fg_color="transparent")
        area.grid(row=1, column=1, sticky="nsew", padx=pad, pady=pad)
        area.grid_columnconfigure(0, weight=1)
        area.grid_rowconfigure(1, weight=1)

        # Viewer banner sits above the canvas, never on it.
        self.viewer_banner = ctk.CTkLabel(
            area,
            text="",
            height=34,
            corner_radius=THEME["radius_small"],
            fg_color=THEME["accent_soft"],
            text_color=THEME["text"],
            font=font(),
            anchor="w",
            padx=12,
        )
        self.viewer_banner.grid(row=0, column=0, sticky="ew", pady=(0, THEME["pad_small"]))

        # Soft shadow: a darker frame peeking out below/right of the card.
        shadow = ctk.CTkFrame(area, corner_radius=radius, fg_color=THEME["canvas_shadow"])
        shadow.grid(row=1, column=0, sticky="nsew")

        card = ctk.CTkFrame(
            shadow,
            corner_radius=radius,
            fg_color=THEME["canvas_bg"],
            border_width=1,
            border_color=THEME["panel_border"],
        )
        card.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 3))

        # Plain white tk.Canvas: the eraser paints white, so no patterns.
        self.canvas = tk.Canvas(
            card,
            bg=THEME["canvas_bg"],
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True, padx=radius // 2 + 1, pady=radius // 2 + 1)

        self.canvas.bind("<Button-1>", self.start_draw)
        self.canvas.bind("<B1-Motion>", self.draw)
        self.canvas.bind("<ButtonRelease-1>", self.stop_draw)
        self.canvas.bind("<Motion>", self.track_mouse)
        self.canvas.bind("<Leave>", self.hide_eraser_cursor)

    def build_summary_panel(self):
        pad = THEME["pad"]

        panel = ctk.CTkFrame(
            self.root,
            width=THEME["panel_width"],
            corner_radius=THEME["radius"],
            fg_color=THEME["panel"],
            border_width=1,
            border_color=THEME["panel_border"],
        )
        panel.grid(row=1, column=2, sticky="ns", padx=(0, pad), pady=pad)
        panel.pack_propagate(False)
        self.summary_panel = panel

        # Online users (presence data from PRESENCE_UPDATE).
        title_row = ctk.CTkFrame(panel, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(12, 2))

        ctk.CTkLabel(
            title_row,
            text="●",
            text_color=THEME["ok"],
            font=font("font_body"),
        ).pack(side="left", padx=(0, 6))

        self.online_title = ctk.CTkLabel(
            title_row,
            text="Online (0)",
            font=font("font_body", "bold"),
            text_color=THEME["text"],
        )
        self.online_title.pack(side="left")

        self.online_list = ctk.CTkScrollableFrame(
            panel,
            height=96,
            corner_radius=10,
            fg_color=THEME["card"],
        )
        self.online_list.pack(fill="x", padx=14)

        ctk.CTkFrame(panel, height=1, fg_color=THEME["divider"]).pack(fill="x", padx=14, pady=(12, 0))

        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 0))

        ctk.CTkLabel(
            top,
            text="✨ AI Summary",
            font=font("font_title", "bold"),
            text_color=THEME["text"],
        ).pack(side="left")

        hide = ctk.CTkButton(
            top,
            text="»",
            width=28,
            height=28,
            corner_radius=THEME["radius_small"],
            fg_color="transparent",
            hover_color=THEME["hover"],
            text_color=THEME["muted"],
            font=font("font_title"),
            command=self.toggle_summary_panel,
        )
        hide.pack(side="right")
        Tooltip(hide, "Hide panel")

        ctk.CTkLabel(
            panel,
            text="Gemini describes what's on the board.",
            text_color=THEME["muted"],
            font=font("font_small"),
        ).pack(padx=14, anchor="w")

        self.summary_button = ctk.CTkButton(
            panel,
            text="Summarize board",
            height=38,
            corner_radius=THEME["radius_small"],
            fg_color=THEME["accent"],
            hover_color=THEME["accent_hover"],
            font=font("font_body", "bold"),
            command=self.request_summary,
        )
        self.summary_button.pack(fill="x", padx=14, pady=(12, 2))

        # Animated "Summarizing..." indicator.
        self.summary_status = ctk.CTkLabel(
            panel,
            text="",
            height=20,
            text_color=THEME["accent"],
            font=font("font_small", "bold"),
        )
        self.summary_status.pack(padx=14, anchor="w")

        self.summary_card = ctk.CTkFrame(
            panel,
            corner_radius=10,
            fg_color=THEME["card"],
        )
        self.summary_card.pack(fill="both", expand=True, padx=14, pady=(2, 14))

        self.summary_text = ctk.CTkTextbox(
            self.summary_card,
            wrap="word",
            fg_color=THEME["card"],
            border_width=0,
            font=font(),
        )
        self.summary_text.pack(fill="both", expand=True, padx=6, pady=(8, 0))

        self.summary_footer = ctk.CTkLabel(
            self.summary_card,
            text="",
            text_color=THEME["muted"],
            font=font("font_small"),
            anchor="w",
        )
        self.summary_footer.pack(fill="x", padx=12, pady=(0, 8))

        self.show_summary_card("hint", SUMMARY_HINT)

    def build_toast(self):
        self.toast = ctk.CTkFrame(
            self.root,
            corner_radius=THEME["radius"],
            fg_color=THEME["toast_bg"],
        )
        self.toast_label = ctk.CTkLabel(
            self.toast,
            text="",
            text_color=THEME["toast_text"],
            font=font(),
            wraplength=320,
            justify="left",
        )
        self.toast_label.pack(padx=14, pady=10)

    def bind_shortcuts(self):
        # Drawing shortcuts (admin only); [ and ] resize the active tool.
        for key in ("p", "P"):
            self.root.bind(f"<Key-{key}>", lambda e: self.on_shortcut(lambda: self.set_tool("pen")))
        for key in ("e", "E"):
            self.root.bind(f"<Key-{key}>", lambda e: self.on_shortcut(lambda: self.set_tool("eraser")))
        self.root.bind("<bracketleft>", lambda e: self.on_shortcut(lambda: self.change_size(-1)))
        self.root.bind("<bracketright>", lambda e: self.on_shortcut(lambda: self.change_size(1)))
        for key in ("<Control-z>", "<Control-Z>"):
            self.root.bind(key, lambda e: self.on_shortcut(self.request_undo))

    # ---------- helpers ----------

    def themed(self, key):
        """Resolve a (light, dark) theme colour for plain Tk widgets."""
        value = THEME[key]
        if isinstance(value, tuple):
            return value[1] if ctk.get_appearance_mode() == "Dark" else value[0]
        return value

    def to_board(self, x, y):
        """Canvas pixels -> board coordinates (logical px, never negative)."""
        s = self.canvas_scale
        return max(0, round(x / s)), max(0, round(y / s))

    def on_scaling_change(self, widget_scaling, _window_scaling):
        """Window moved to a screen with different DPI: rescale strokes."""
        factor = widget_scaling / self.canvas_scale
        if abs(factor - 1) < 1e-6:
            return

        self.canvas.scale("stroke", 0, 0, factor, factor)
        for item in self.canvas.find_withtag("stroke"):
            width = float(self.canvas.itemcget(item, "width"))
            self.canvas.itemconfigure(item, width=width * factor)

        self.canvas_scale = widget_scaling
        box = round(PREVIEW_BOX * widget_scaling)
        self.size_preview.configure(width=box, height=box)
        self.update_size_preview()
        self.update_eraser_cursor()

    def toggle_theme(self):
        # Chrome only: the canvas stays white in both modes.
        ctk.set_appearance_mode("dark" if self.theme_switch.get() else "light")
        # Plain "☀" (no emoji variant): renders as a sun in Tk on Windows.
        self.theme_switch.configure(text="☀" if self.theme_switch.get() else "🌙")
        self.size_preview.configure(bg=self.themed("panel"))
        self.update_size_preview()

    # ---------- header ----------

    def set_status(self, text, kind):
        """kind: "ok" (green), "warn" (amber) or "error" (red)."""
        self.status_pill.configure(
            text=f"● {text}",
            fg_color=THEME[f"{kind}_soft"],
            text_color=THEME[kind],
        )

    def refresh_version(self):
        text = f"v{self.last_version}"
        if self.version_label.cget("text") != text:
            self.version_label.configure(text=text)

    def on_admin_button(self):
        if self.role == "admin":
            self.release_admin()
        else:
            self.request_admin()

    # ---------- role ----------

    def on_shortcut(self, action):
        # Drawing shortcuts only work for the admin.
        if self.role == "admin":
            action()

    def apply_role(self):
        """Show the role and enable drawing controls only for the admin."""
        is_admin = self.role == "admin"
        self.root.title(f"SyncDraw — {'Admin' if is_admin else 'Viewer'}")

        if is_admin:
            # 🖊 renders full-size in Segoe UI; ✏️ comes out tiny there.
            self.role_badge.configure(
                text="🖊 Admin (you)",
                fg_color=THEME["accent_soft"],
                text_color=THEME["accent"],
            )
            self.admin_button.configure(
                text="Release Admin",
                fg_color="transparent",
                hover_color=THEME["hover"],
                border_width=1,
                border_color=THEME["panel_border"],
                text_color=THEME["text"],
            )
        else:
            self.role_badge.configure(
                text="👁 Viewer",
                fg_color=THEME["chip"],
                text_color=THEME["muted"],
            )
            self.admin_button.configure(
                text="🔑 Become Admin",
                fg_color=THEME["accent"],
                hover_color=THEME["accent_hover"],
                border_width=0,
                text_color="#FFFFFF",
            )

        # The badge already says "(you)", so don't repeat it here.
        if is_admin:
            self.admin_label.configure(text="")
        elif self.admin_id is None:
            self.admin_label.configure(text="No admin")
        else:
            self.admin_label.configure(text=f"Admin: {self.admin_id}")

        # Viewer banner above the canvas.
        if is_admin:
            self.viewer_banner.grid_remove()
        else:
            if self.admin_id is None:
                text = "👁  View only — no admin yet. Click Become Admin to draw."
            else:
                text = f"👁  View only — {self.admin_id} is drawing"
            self.viewer_banner.configure(text=text)
            self.viewer_banner.grid()

        # Whole tool rail is disabled for viewers.
        state = "normal" if is_admin else "disabled"
        for widget in (
            *self.tool_buttons.values(),
            *self.swatch_buttons.values(),
            self.custom_color_button,
            self.undo_button,
            self.clear_button,
            self.size_slider,
        ):
            widget.configure(state=state)

        # A disabled CTkSlider keeps its colours, so grey it out by hand.
        slider_color = THEME["accent"] if is_admin else THEME["disabled"]
        self.size_slider.configure(button_color=slider_color, progress_color=slider_color)

        # Stop any drag in progress and hide the eraser preview.
        self.last_point = None
        self.refresh_tool_buttons()
        self.refresh_swatches()
        self.update_size_preview()
        self.update_canvas_cursor()
        self.update_eraser_cursor()

    def announce_role_change(self, old_role, old_admin):
        # The first ROLE_UPDATE after connecting only sets the state.
        if not self.role_known:
            self.role_known = True
            return

        if self.role == "admin" and old_role != "admin":
            self.show_toast("You are now the admin — start drawing", "ok")
        elif old_role == "admin" and self.role != "admin":
            self.show_toast("You released admin", "info")
        elif self.admin_id != old_admin:
            if self.admin_id is None:
                self.show_toast("Admin released — anyone can claim it", "info")
            else:
                self.show_toast(f"{self.admin_id} is now admin", "info")

    # ---------- tools ----------

    def set_tool(self, tool):
        self.tool = tool

        # One slider: its range and value follow the active tool.
        if tool == "pen":
            self.size_slider.configure(
                from_=PEN_MIN,
                to=PEN_MAX,
                number_of_steps=(PEN_MAX - PEN_MIN) // PEN_STEP,
            )
            self.size_slider.set(self.pen_width)
        else:
            self.size_slider.configure(
                from_=ERASER_MIN,
                to=ERASER_MAX,
                number_of_steps=(ERASER_MAX - ERASER_MIN) // ERASER_STEP,
            )
            self.size_slider.set(self.eraser_size.get())

        self.refresh_tool_buttons()
        self.update_size_preview()
        self.update_canvas_cursor()
        self.update_eraser_cursor()

    def refresh_tool_buttons(self):
        is_admin = self.role == "admin"

        for tool, button in self.tool_buttons.items():
            if tool == self.tool:
                button.configure(
                    fg_color=THEME["accent"] if is_admin else THEME["disabled"],
                    text_color="#FFFFFF",
                    text_color_disabled="#FFFFFF",
                    hover_color=THEME["accent_hover"],
                )
            else:
                button.configure(
                    fg_color="transparent",
                    text_color=THEME["text"],
                    text_color_disabled=THEME["disabled"],
                    hover_color=THEME["hover"],
                )

    def set_color(self, color):
        self.color = color
        # Picking a colour switches back to the pen.
        self.set_tool("pen")
        self.refresh_swatches()

    def refresh_swatches(self):
        is_admin = self.role == "admin"

        # Selected swatch gets a ring; viewers see greyed-out swatches.
        for color, button in self.swatch_buttons.items():
            button.configure(
                fg_color=color if is_admin else THEME["disabled"],
                hover_color=color if is_admin else THEME["disabled"],
                border_width=3 if color == self.color and is_admin else 0,
            )

        custom = self.color not in self.swatch_buttons and is_admin
        self.custom_color_button.configure(
            border_width=3 if custom else 1,
            border_color=self.color if custom else THEME["panel_border"],
        )

    def pick_custom_color(self):
        if self.role != "admin":
            return

        rgb, _hex = colorchooser.askcolor(
            color=self.color,
            parent=self.root,
            title="Custom colour",
        )
        if rgb is None:
            return

        # The server accepts exactly "#RRGGBB" hex, so normalise.
        self.set_color("#{:02X}{:02X}{:02X}".format(
            *(max(0, min(255, int(round(v)))) for v in rgb)
        ))

    def on_size_slider(self, value):
        size = int(round(value))

        if self.tool == "pen":
            self.pen_width = size
        else:
            self.eraser_size.set(size)

        self.update_size_preview()
        self.update_eraser_cursor()

    def change_size(self, direction):
        """[ / ] shortcut: pen in steps of 1, eraser in steps of 5."""
        if self.tool == "pen":
            self.pen_width = max(PEN_MIN, min(PEN_MAX, self.pen_width + direction * PEN_STEP))
            self.size_slider.set(self.pen_width)
        else:
            size = self.eraser_size.get() + direction * ERASER_STEP
            self.eraser_size.set(max(ERASER_MIN, min(ERASER_MAX, size)))
            self.size_slider.set(self.eraser_size.get())

        self.update_size_preview()
        self.update_eraser_cursor()

    def update_size_preview(self):
        """Live preview of the current size and colour."""
        canvas = self.size_preview
        canvas.delete("all")

        size = self.pen_width if self.tool == "pen" else self.eraser_size.get()
        box = int(canvas.cget("width"))
        r = min(size * self.canvas_scale, box - 6) / 2
        c = box / 2

        if self.role != "admin":
            canvas.create_oval(c - r, c - r, c + r, c + r, fill=self.themed("disabled"), outline="")
        elif self.tool == "pen":
            canvas.create_oval(c - r, c - r, c + r, c + r, fill=self.color, outline="")
        else:
            canvas.create_oval(c - r, c - r, c + r, c + r, fill="#FFFFFF", outline="grey", dash=(3, 3))

        self.size_label.configure(text=f"{size} px")

    # ---------- canvas cursor ----------

    def update_canvas_cursor(self):
        if self.role != "admin":
            cursor = "arrow"
        elif self.tool == "eraser":
            cursor = "none"  # the dashed circle is the cursor
        else:
            cursor = "crosshair"

        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            self.canvas.configure(cursor="dot")

    def track_mouse(self, event):
        self.mouse_pos = (event.x, event.y)
        self.update_eraser_cursor()

        # Everyone (admins and viewers) shares their pointer.
        self.send_cursor(*self.to_board(event.x, event.y))

    def hide_eraser_cursor(self, event=None):
        self.mouse_pos = None
        self.canvas.delete("eraser_cursor")
        self.send_cursor(hidden=True)

    def update_eraser_cursor(self):
        """Draw a dashed circle under the mouse while erasing."""
        self.canvas.delete("eraser_cursor")

        if (
            self.tool != "eraser"
            or self.role != "admin"
            or self.mouse_pos is None
        ):
            return

        x, y = self.mouse_pos
        r = self.eraser_size.get() * self.canvas_scale / 2
        self.canvas.create_oval(
            x - r, y - r, x + r, y + r,
            outline="grey",
            dash=(4, 4),
            tags="eraser_cursor",
        )

    # ---------- summary panel ----------

    def toggle_summary_panel(self):
        if self.summary_panel_visible:
            self.summary_panel.grid_remove()
            self.summary_toggle.configure(fg_color="transparent")
        else:
            self.summary_panel.grid()
            self.summary_toggle.configure(fg_color=THEME["accent_soft"])
        self.summary_panel_visible = not self.summary_panel_visible

    def show_summary_card(self, kind, text, footer=""):
        """kind: "hint", "result", "warn" or "error"."""
        bg, fg = {
            "hint": (THEME["card"], THEME["muted"]),
            "result": (THEME["card"], THEME["text"]),
            "warn": (THEME["warn_soft"], THEME["warn"]),
            "error": (THEME["error_soft"], THEME["error"]),
        }[kind]

        self.summary_card.configure(fg_color=bg)
        self.summary_text.configure(state="normal", fg_color=bg, bg_color=bg, text_color=fg)
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")
        self.summary_footer.configure(text=footer, bg_color=bg)

    def summary_busy(self, busy):
        """Disable the button and animate dots while waiting."""
        self.summary_button.configure(state="disabled" if busy else "normal")

        if self.dots_after_id is not None:
            self.root.after_cancel(self.dots_after_id)
            self.dots_after_id = None

        if busy:
            self.dots = 0
            self.animate_dots()
        else:
            self.summary_status.configure(text="")

    def animate_dots(self):
        self.summary_status.configure(text="✨ Summarizing" + "." * (self.dots % 4))
        self.dots += 1
        self.dots_after_id = self.root.after(THEME["dots_ms"], self.animate_dots)

    # ---------- presence: online list and remote cursors ----------

    def update_presence(self, users):
        """Online list from PRESENCE_UPDATE (no version, no sync)."""
        old = self.users
        self.users = {u.get("client_id"): u for u in users if isinstance(u, dict)}

        # Join/leave toasts (not for the first list after connecting).
        if self.presence_known:
            for cid, user in self.users.items():
                if cid not in old and cid != self.client_id:
                    self.show_toast(f"{user.get('name', cid)} joined", "info")
            for cid, user in old.items():
                if cid not in self.users and cid != self.client_id:
                    self.show_toast(f"{user.get('name', cid)} left", "info")
        self.presence_known = True

        # Users who left lose their cursor.
        for cid in list(self.remote_cursors):
            if cid not in self.users:
                self.remove_remote_cursor(cid)

        self.refresh_online_list()

    def clear_presence(self):
        self.users = {}
        self.presence_known = False
        for cid in list(self.remote_cursors):
            self.remove_remote_cursor(cid)
        self.refresh_online_list()

    def refresh_online_list(self):
        for child in self.online_list.winfo_children():
            child.destroy()

        self.online_title.configure(text=f"Online ({len(self.users)})")

        # Admin first, then by name.
        ordered = sorted(
            self.users.values(),
            key=lambda u: (u.get("role") != "admin", str(u.get("name", "")).lower()),
        )

        for user in ordered:
            row = ctk.CTkFrame(self.online_list, fg_color="transparent")
            row.pack(fill="x", pady=1)

            ctk.CTkLabel(
                row,
                text="●",
                width=14,
                text_color=user.get("color", THEME["muted"]),
                font=font("font_body"),
            ).pack(side="left")

            you = " (you)" if user.get("client_id") == self.client_id else ""
            ctk.CTkLabel(
                row,
                text=f"{user.get('name', '?')}{you}",
                text_color=THEME["text"],
                font=font("font_small", "bold" if you else "normal"),
                anchor="w",
            ).pack(side="left", padx=(4, 0))

            is_admin = user.get("role") == "admin"
            ctk.CTkLabel(
                row,
                text="🖊 Admin" if is_admin else "👁 Viewer",
                height=20,
                corner_radius=10,
                padx=6,
                fg_color=THEME["accent_soft"] if is_admin else THEME["chip"],
                text_color=THEME["accent"] if is_admin else THEME["muted"],
                font=font("font_small"),
            ).pack(side="right", padx=(0, 4))

    def update_remote_cursor(self, message):
        """Move (or create) another user's cursor. Best effort only."""
        cid = message.get("client_id")
        if not isinstance(cid, str) or cid == self.client_id:
            return

        if message.get("hidden"):
            self.remove_remote_cursor(cid)
            return

        x, y = message.get("x"), message.get("y")
        if type(x) not in (int, float) or type(y) not in (int, float):
            return

        s = self.canvas_scale
        px, py = x * s, y * s
        cursor = self.remote_cursors.get(cid)

        if cursor is None:
            cursor = self.create_remote_cursor(cid, px, py)
        else:
            # Move the existing items instead of recreating them.
            self.canvas.move(cursor["tag"], px - cursor["x"], py - cursor["y"])
            cursor["x"], cursor["y"] = px, py

        cursor["seen"] = time.monotonic()
        self.canvas.tag_raise("remote_cursor")
        self.canvas.tag_raise("eraser_cursor")

    def create_remote_cursor(self, cid, px, py):
        user = self.users.get(cid, {})
        color = user.get("color", "#6B7280")
        name = user.get("name", cid)

        # Safe internal tag: client IDs are chosen by clients and could
        # contain characters Tk treats as tag operators.
        self.cursor_tag_count += 1
        tag = f"cursor_{self.cursor_tag_count}"
        tags = ("remote_cursor", tag)

        # Small arrow pointer plus a name label.
        k = self.canvas_scale
        arrow = [(0, 0), (0, 15), (4, 11), (7, 18), (10, 17), (7, 10), (12, 10)]
        self.canvas.create_polygon(
            [c for x, y in arrow for c in (px + x * k, py + y * k)],
            fill=color, outline="#FFFFFF", width=1, tags=tags,
        )
        text = self.canvas.create_text(
            px + 14 * k, py + 20 * k,
            text=name, anchor="nw", fill="#FFFFFF",
            font=font("font_small", "bold"), tags=tags,
        )
        x1, y1, x2, y2 = self.canvas.bbox(text)
        box = self.canvas.create_rectangle(
            x1 - 4, y1 - 1, x2 + 4, y2 + 1,
            fill=color, outline="", tags=tags,
        )
        self.canvas.tag_lower(box, text)

        cursor = {"tag": tag, "x": px, "y": py, "seen": time.monotonic()}
        self.remote_cursors[cid] = cursor
        return cursor

    def remove_remote_cursor(self, cid):
        cursor = self.remote_cursors.pop(cid, None)
        if cursor is not None:
            self.canvas.delete(cursor["tag"])

    def sweep_remote_cursors(self):
        """Remove cursors with no update for 5 seconds."""
        now = time.monotonic()
        for cid, cursor in list(self.remote_cursors.items()):
            if now - cursor["seen"] > 5:
                self.remove_remote_cursor(cid)
        self.root.after(1000, self.sweep_remote_cursors)

    # ---------- toasts ----------

    def show_toast(self, text, kind="info"):
        """Small notification in the bottom-right corner (~3 s)."""
        icon = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "error": "⛔"}[kind]
        self.toast_label.configure(text=f"{icon}  {text}")
        self.toast.place(relx=1.0, rely=1.0, x=-20, y=-20, anchor="se")
        self.toast.lift()

        if self.toast_after_id is not None:
            self.root.after_cancel(self.toast_after_id)

        self.toast_after_id = self.root.after(THEME["toast_ms"], self.hide_toast)

    def hide_toast(self):
        self.toast_after_id = None
        self.toast.place_forget()

    def show_server_error(self, text):
        self.show_toast(text, "error")
