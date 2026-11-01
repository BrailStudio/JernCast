"""The JernCast window (0.0.2): compact dark cards, live preview, four webcam layouts."""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import (IS_WINDOWS, OUTPUT_SIZES, SERVICES, Settings, assets_dir, data_dir, find_ffmpeg,
                   format_time, list_devices, manage_broadcast_url, working_encoders)
from .layouts import LAYOUT_NAMES, available_layouts, layout_scene_name, project_from_settings
from .session import Session
from .winutil import HOTKEYS, Hotkey, list_monitors, list_windows

log = logging.getLogger("jerncast")

BG = "#161616"
CARD = "#212120"
FIELD = "#171716"
BORDER = "#34332f"
TEXT = "#ebe9e4"
MUTED = "#8f8c85"
BLUE = "#3b82f6"
RED = "#c1272d"
GREEN = "#2ea05f"
ORANGE = "#e0a030"
ENTIRE_SCREEN = "Entire screen"
NONE = "None"
SYSTEM_SOUND = "System sound"
CORNERS = ["bottom-right", "bottom-left", "top-right", "top-left"]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.ffmpeg = find_ffmpeg()
        self.events: queue.Queue = queue.Queue()
        self.monitors = list_monitors()
        self.windows: list[str] = []
        self.webcams: list[str] = []
        self.mics: list[str] = []
        self.encoders = ["libx264"]
        self.project = project_from_settings(self.settings)
        self.session = (Session(self.ffmpeg, self.settings, self.project, self.monitors, self.encoders)
                        if self.ffmpeg else None)
        self.busy = False
        self.note = ""
        self.hotkeys: list[Hotkey] = []
        self.meter_canvases: dict[str, list] = {}
        self.peaks: dict[str, float] = {}
        self.preview_image = None
        self.preview_mtime = 0.0
        self.auto_stopped = False
        self._ticks = 0

        root.title(f"JernCast {__version__}")
        root.configure(bg=BG)
        root.minsize(900, 660)
        self._set_icon()
        self._style()
        self._vars()
        self._build()
        self.entry_server.configure(state="normal" if self.settings.stream_service == "Custom" else "readonly")
        self._update_info()
        self._refresh_layout_choices()
        self._apply_compact()
        self._refresh_recent()
        self._set_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self._exit)

        if self.session is None:
            self.note = "FFmpeg was not found. Put ffmpeg.exe next to JernCast.exe."
            for button in (self.rec_btn, self.live_btn):
                button.state(["disabled"])
        else:
            threading.Thread(target=self._probe, daemon=True).start()
        root.after(100, self._tick)

    # ------------------------------------------------------------------ look
    def _set_icon(self) -> None:
        try:
            self.icon = tk.PhotoImage(file=str(assets_dir() / "jerncast-icon.png"))
            self.root.iconphoto(True, self.icon)
        except Exception:
            self.icon = None
        if IS_WINDOWS:
            try:
                self.root.iconbitmap(str(assets_dir() / "jerncast.ico"))
            except Exception:
                pass

    def _style(self) -> None:
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
        st.configure(".", background=CARD, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER,
                     lightcolor=CARD, darkcolor=CARD, troughcolor=FIELD, font=("Segoe UI", 9))
        st.configure("TFrame", background=CARD)
        st.configure("Bg.TFrame", background=BG)
        st.configure("TLabel", background=CARD, foreground=TEXT)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Bg.TLabel", background=BG, foreground=TEXT)
        st.configure("BgMuted.TLabel", background=BG, foreground=MUTED)
        st.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 13, "bold"))
        st.configure("Card.TLabel", foreground=TEXT, font=("Segoe UI", 10, "bold"))
        st.configure("Warn.TLabel", foreground=ORANGE)
        st.configure("Ok.TLabel", foreground=GREEN)
        st.configure("TCheckbutton", background=CARD, foreground=TEXT)
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                     arrowcolor=MUTED, padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, padding=3)
        st.configure("TButton", background="#2d2c2a", foreground=TEXT, padding=5, borderwidth=1)
        st.map("TButton", background=[("active", "#3a3936"), ("disabled", "#262524")])
        st.configure("Seg.TButton", padding=(14, 3))
        st.configure("Rec.TButton", background=RED, foreground="white", font=("Segoe UI", 12, "bold"), padding=10)
        st.map("Rec.TButton", background=[("active", "#d93a40"), ("disabled", "#3a2224")])
        st.configure("Live.TButton", background="#2d2c2a", foreground=TEXT, font=("Segoe UI", 12, "bold"), padding=10)
        st.configure("On.TButton", background=BLUE, foreground="white")
        st.map("On.TButton", background=[("active", "#5594ff")])
        st.configure("Stop.TButton", background="#7a1f24", foreground="white", font=("Segoe UI", 12, "bold"), padding=10)

    def _vars(self) -> None:
        s = self.settings
        self.v_screen = tk.StringVar(value=s.screen)
        self.v_window = tk.StringVar(value=s.window or ENTIRE_SCREEN)
        self.v_fps = tk.IntVar(value=s.fps)
        self.v_duration = tk.StringVar(value=str(s.duration_min or ""))
        self.v_folder = tk.StringVar(value=s.folder)
        self.v_prefix = tk.StringVar(value=s.prefix)
        self.v_service = tk.StringVar(value=s.stream_service)
        self.v_server = tk.StringVar(value=s.stream_server)
        self.v_key = tk.StringVar(value=s.stream_key)
        self.v_show_key = tk.BooleanVar(value=False)
        self.v_mic = tk.StringVar(value=s.mic or NONE)
        self.v_mic_on = tk.BooleanVar(value=s.mic_on)
        self.v_desktop = tk.StringVar(value=SYSTEM_SOUND if s.desktop == "loopback" else (s.desktop or NONE))
        self.v_desktop_on = tk.BooleanVar(value=s.desktop_on)
        self.v_layout = tk.StringVar(value=LAYOUT_NAMES.get(s.layout, LAYOUT_NAMES[1]))
        self.v_webcam = tk.StringVar(value=s.webcam or NONE)
        self.v_corner = tk.StringVar(value=s.cam_corner)
        self.v_encoder = tk.StringVar(value=s.encoder)
        self.v_output = tk.StringVar(value=next((k for k, v in OUTPUT_SIZES.items() if v == s.canvas), "720p"))
        self.v_quality = tk.StringVar(value=s.quality)
        self.v_kbps = tk.StringVar(value=str(s.kbps))
        self.v_cursor = tk.BooleanVar(value=s.show_cursor)
        self.v_mp4 = tk.BooleanVar(value=s.mp4)
        self.v_remember = tk.BooleanVar(value=s.remember_key)
        self.v_hk_rec = tk.StringVar(value=s.hotkey_record)
        self.v_hk_live = tk.StringVar(value=s.hotkey_stream)
        self.status = tk.StringVar(value="Ready")
        self.info = tk.StringVar(value="")
        self.key_hint = tk.StringVar(value="")

    # ---------------------------------------------------------------- layout
    def _card(self, parent, title: str):
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        ttk.Label(outer, text="  " + title, style="Card.TLabel").pack(anchor="w", pady=(8, 2), padx=6)
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        body.columnconfigure(1, weight=1)
        return outer, body

    def _row(self, body, row, label, widget, **grid):
        ttk.Label(body, text=label, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        widget.grid(row=row, column=1, sticky=grid.pop("sticky", "ew"), **grid)

    def _combo(self, parent, var, values, command=None, width=22, readonly=True, **kw):
        box = ttk.Combobox(parent, textvariable=var, values=values, width=width,
                           state="readonly" if readonly else "normal", **kw)
        if command:
            box.bind("<<ComboboxSelected>>", lambda _e: command())
        return box

    def _meter(self, parent, source_id: str):
        canvas = tk.Canvas(parent, width=130, height=8, bg=FIELD, highlightthickness=0)
        self.meter_canvases.setdefault(source_id, []).append(canvas)
        return canvas

    def _build(self) -> None:
        root = self.root
        head = ttk.Frame(root, style="Bg.TFrame")
        head.pack(fill="x", padx=14, pady=(10, 6))
        if self.icon is not None:
            self.small_icon = self.icon.subsample(7, 7)
            tk.Label(head, image=self.small_icon, bg=BG).pack(side="left", padx=(0, 8))
        ttk.Label(head, text="JernCast", style="Title.TLabel").pack(side="left")
        self.pill = tk.Label(head, textvariable=self.status, bg=GREEN, fg="white", padx=10, pady=1,
                             font=("Segoe UI", 9, "bold"))
        self.pill.pack(side="left", padx=10)
        self.compact_btn = ttk.Button(head, text="Compact", command=self._toggle_compact)
        self.compact_btn.pack(side="right")
        ttk.Label(head, textvariable=self.info, style="BgMuted.TLabel").pack(side="right", padx=14)

        self.main = ttk.Frame(root, style="Bg.TFrame")
        self.main.pack(fill="both", expand=True, padx=14)

        # ---- compact view: live picture
        self.compact_view = ttk.Frame(self.main, style="Bg.TFrame")
        self.preview = tk.Canvas(self.compact_view, bg="#050505", highlightthickness=1,
                                 highlightbackground=BORDER, width=480, height=270)
        self.preview.pack(pady=(4, 6))
        bar = ttk.Frame(self.compact_view, style="Bg.TFrame")
        bar.pack(fill="x")
        for text, sid in (("Mic", "mic"), ("Desktop", "desktop")):
            ttk.Label(bar, text=text, style="BgMuted.TLabel").pack(side="left", padx=(4, 4))
            self._meter(bar, sid).pack(side="left", padx=(0, 12))
        ttk.Button(bar, text="Webcam", command=self._toggle_webcam).pack(side="right")
        self.layout_buttons = ttk.Frame(bar, style="Bg.TFrame")
        self.layout_buttons.pack(side="right", padx=8)

        # ---- full view: cards
        self.cards = ttk.Frame(self.main, style="Bg.TFrame")
        left = ttk.Frame(self.cards, style="Bg.TFrame")
        right = ttk.Frame(self.cards, style="Bg.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right.pack(side="left", fill="both", expand=True, padx=(7, 0))
        self._build_capture(left)
        self._build_stream(left)
        self._build_recent(left)
        self._build_audio(right)
        self._build_advanced(right)

        bottom = ttk.Frame(root, style="Bg.TFrame")
        bottom.pack(fill="x", padx=14, pady=(8, 12), side="bottom")
        self.rec_btn = ttk.Button(bottom, text="Start Recording", style="Rec.TButton", command=self._toggle_record)
        self.rec_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.live_btn = ttk.Button(bottom, text="Go live", style="Live.TButton", command=self._toggle_live)
        self.live_btn.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(bottom, text="Manage Broadcast", command=self._manage_broadcast).pack(side="left", padx=(6, 0))

    def _build_capture(self, parent) -> None:
        card, body = self._card(parent, "Capture")
        card.pack(fill="x", pady=(0, 10))
        self.cb_screen = self._combo(body, self.v_screen, [m.name for m in self.monitors], self._choices_changed)
        self._row(body, 0, "Screen", self.cb_screen)
        self.cb_window = self._combo(body, self.v_window, [ENTIRE_SCREEN], self._choices_changed,
                                     postcommand=self._refresh_windows)
        self._row(body, 1, "Window", self.cb_window)
        seg = ttk.Frame(body)
        for fps in (30, 60):
            ttk.Radiobutton(seg, text=str(fps), value=fps, variable=self.v_fps, style="Toolbutton",
                            command=self._choices_changed).pack(side="left", padx=(0, 4))
        self._row(body, 2, "FPS", seg, sticky="w")
        dur = ttk.Frame(body)
        ttk.Entry(dur, textvariable=self.v_duration, width=6).pack(side="left")
        ttk.Label(dur, text="minutes (empty = until you stop)", style="Muted.TLabel").pack(side="left", padx=6)
        self._row(body, 3, "Duration", dur, sticky="w")
        folder = ttk.Frame(body)
        ttk.Entry(folder, textvariable=self.v_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(folder, text="...", width=3, command=self._browse).pack(side="left", padx=(4, 0))
        self._row(body, 4, "Output folder", folder)
        self._row(body, 5, "Filename", ttk.Entry(body, textvariable=self.v_prefix))

    def _build_stream(self, parent) -> None:
        card, body = self._card(parent, "Stream")
        card.pack(fill="x", pady=(0, 10))
        self._row(body, 0, "Service", self._combo(body, self.v_service, list(SERVICES), self._service_changed))
        self.entry_server = ttk.Entry(body, textvariable=self.v_server)
        self._row(body, 1, "Server", self.entry_server)
        key = ttk.Frame(body)
        self.entry_key = ttk.Entry(key, textvariable=self.v_key, show="*")
        self.entry_key.pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(key, text="Show", variable=self.v_show_key, command=self._show_key).pack(side="left", padx=4)
        self._row(body, 2, "Stream key", key)
        self.key_label = ttk.Label(body, textvariable=self.key_hint, style="Warn.TLabel")
        self.key_label.grid(row=3, column=1, sticky="w", pady=(2, 0))
        self.v_key.trace_add("write", lambda *_: self._update_key_hint())
        self._update_key_hint()

    def _build_recent(self, parent) -> None:
        card, body = self._card(parent, "Recent recordings")
        card.pack(fill="both", expand=True)
        self.recent_list = tk.Listbox(body, height=4, bg=FIELD, fg=TEXT, selectbackground=BLUE, highlightthickness=0,
                                      bd=0, activestyle="none", font=("Segoe UI", 9))
        self.recent_list.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.recent_list.bind("<Double-Button-1>", lambda _e: self._open_recent())
        row = ttk.Frame(body)
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(row, text="Open", command=self._open_recent).pack(side="left")
        ttk.Button(row, text="Open folder", command=self._open_folder).pack(side="left", padx=6)

    def _build_audio(self, parent) -> None:
        card, body = self._card(parent, "Audio & Webcam")
        card.pack(fill="x", pady=(0, 10))
        self.cb_mic = self._combo(body, self.v_mic, [NONE], self._choices_changed)
        self._row(body, 0, "Mic device", self.cb_mic)
        mic = ttk.Frame(body)
        ttk.Checkbutton(mic, text="On", variable=self.v_mic_on, command=lambda: self._audio_on("mic")).pack(side="left")
        self.mic_scale = self._scale(mic, 0, 200, self.settings.mic_volume, lambda v: self._volume("mic", v))
        self.mic_scale.pack(side="left", padx=6)
        self._meter(mic, "mic").pack(side="left")
        self._row(body, 1, "Mic level", mic, sticky="w")
        self.cb_desktop = self._combo(body, self.v_desktop, [SYSTEM_SOUND, NONE], self._choices_changed)
        self._row(body, 2, "Desktop sound", self.cb_desktop)
        desk = ttk.Frame(body)
        ttk.Checkbutton(desk, text="On", variable=self.v_desktop_on,
                        command=lambda: self._audio_on("desktop")).pack(side="left")
        self.desk_scale = self._scale(desk, 0, 200, self.settings.desktop_volume, lambda v: self._volume("desktop", v))
        self.desk_scale.pack(side="left", padx=6)
        self._meter(desk, "desktop").pack(side="left")
        self._row(body, 3, "Desktop level", desk, sticky="w")
        self.cb_layout = self._combo(body, self.v_layout, [LAYOUT_NAMES[1]], self._layout_changed)
        self._row(body, 4, "Layout", self.cb_layout)
        self.cb_webcam = self._combo(body, self.v_webcam, [NONE], self._choices_changed)
        self._row(body, 5, "Webcam device", self.cb_webcam)
        self._row(body, 6, "Position", self._combo(body, self.v_corner, CORNERS, self._choices_changed, width=14),
                  sticky="w")
        self.cam_scale = self._scale(body, 10, 50, self.settings.cam_size, lambda v: self._cam_size(v), length=150)
        self._row(body, 7, "Size", self.cam_scale, sticky="w")
        self.layout_hint = ttk.Label(body, text="Layouts switch live with Ctrl+Alt+1 to 4.", style="Muted.TLabel")
        self.layout_hint.grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _scale(self, parent, lo, hi, value, command, length=110):
        scale = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", showvalue=False, length=length, bg=CARD,
                         troughcolor=FIELD, highlightthickness=0, bd=0, sliderlength=14, activebackground=BLUE)
        scale.set(value)
        scale.configure(command=command)
        return scale

    def _build_advanced(self, parent) -> None:
        card, body = self._card(parent, "Advanced")
        card.pack(fill="both", expand=True)
        self.cb_encoder = self._combo(body, self.v_encoder, ["auto"], self._choices_changed, width=14)
        self._row(body, 0, "Encoder", self.cb_encoder, sticky="w")
        self._row(body, 1, "Output", self._combo(body, self.v_output, list(OUTPUT_SIZES), self._choices_changed, width=8),
                  sticky="w")
        self._row(body, 2, "Quality", self._combo(body, self.v_quality, ["high", "medium", "low"],
                                                  self._choices_changed, width=8), sticky="w")
        self._row(body, 3, "Top bitrate (kbps)", self._combo(
            body, self.v_kbps, ["2500", "3500", "4500", "6000", "8000"], self._choices_changed, width=8,
            readonly=False), sticky="w")
        hk = ttk.Frame(body)
        ttk.Label(hk, text="Record", style="Muted.TLabel").pack(side="left")
        self._combo(hk, self.v_hk_rec, HOTKEYS, self._hotkeys_changed, width=5).pack(side="left", padx=(4, 10))
        ttk.Label(hk, text="Live", style="Muted.TLabel").pack(side="left")
        self._combo(hk, self.v_hk_live, HOTKEYS, self._hotkeys_changed, width=5).pack(side="left", padx=4)
        self._row(body, 4, "Hotkeys", hk, sticky="w")
        ttk.Checkbutton(body, text="Show cursor", variable=self.v_cursor, command=self._choices_changed).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(body, text="Convert recordings to MP4 when finished", variable=self.v_mp4,
                        command=self._choices_changed).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(body, text="Remember stream key on this PC", variable=self.v_remember,
                        command=self._choices_changed).grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Button(body, text="Open logs folder", command=self._open_logs).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # ----------------------------------------------------------- compact mode
    def _toggle_compact(self) -> None:
        self.settings.compact = not self.settings.compact
        self._apply_compact()
        self.settings.save()

    def _apply_compact(self) -> None:
        compact = self.settings.compact
        self.cards.pack_forget()
        self.compact_view.pack_forget()
        if compact:
            self.compact_view.pack(fill="both", expand=True)
            self.compact_btn.configure(text="Full view")
            self.root.minsize(560, 480)
            self.root.geometry("600x560")
            self._set_preview_wanted(True)
        else:
            self.cards.pack(fill="both", expand=True)
            self.compact_btn.configure(text="Compact")
            self.root.minsize(900, 660)
            self.root.geometry("1000x740")
            self._set_preview_wanted(False)
        self._refresh_layout_buttons()

    def _set_preview_wanted(self, wanted: bool) -> None:
        s = self.session
        if s is None:
            return
        if wanted and not s.running:
            self._bg("Starting the preview...", lambda: s.set_preview(True), self._started)
        elif not wanted and s.preview_wanted:
            self._bg("", lambda: s.set_preview(False), self._started)
        else:
            s.preview_wanted = wanted

    def _refresh_layout_buttons(self) -> None:
        for child in self.layout_buttons.winfo_children():
            child.destroy()
        for number in available_layouts(self.settings):
            ttk.Button(self.layout_buttons, text=str(number), width=3,
                       command=lambda n=number: self._set_layout(n)).pack(side="left", padx=1)

    def _draw_preview(self) -> None:
        s = self.session
        if s is None or not self.settings.compact:
            return
        path = s.preview_path()
        canvas = self.preview
        try:
            if path is not None and path.exists() and path.stat().st_mtime != self.preview_mtime:
                self.preview_mtime = path.stat().st_mtime
                image = tk.PhotoImage(file=str(path))
                self.preview_image = image
                canvas.delete("all")
                canvas.create_image(240, 135, image=image)
            elif path is None and self.preview_image is not None:
                self.preview_image = None
                self.preview_mtime = 0.0
                canvas.delete("all")
                canvas.create_text(240, 135, text="Starting the preview...", fill=MUTED)
        except (tk.TclError, OSError):
            pass                                  # the picture was being replaced; try again next time

    # --------------------------------------------------------- reading choices
    def _collect(self) -> Settings:
        s = self.settings
        s.screen = self.v_screen.get()
        window = self.v_window.get()
        s.window = "" if window in ("", ENTIRE_SCREEN) else window
        s.fps = int(self.v_fps.get())
        s.duration_min = int(self.v_duration.get()) if self.v_duration.get().strip().isdigit() else 0
        s.folder = self.v_folder.get().strip() or s.folder
        s.prefix = self.v_prefix.get().strip() or "JernCast"
        s.stream_service = self.v_service.get()
        s.stream_server = self.v_server.get().strip()
        s.stream_key = self.v_key.get().strip()
        s.mic = "" if self.v_mic.get() == NONE else self.v_mic.get()
        s.mic_on = self.v_mic_on.get()
        s.desktop = "" if self.v_desktop.get() == NONE else (
            "loopback" if self.v_desktop.get() == SYSTEM_SOUND else self.v_desktop.get())
        s.desktop_on = self.v_desktop_on.get()
        s.webcam = "" if self.v_webcam.get() == NONE else self.v_webcam.get()
        s.cam_corner = self.v_corner.get()
        s.encoder = self.v_encoder.get()
        s.canvas = OUTPUT_SIZES.get(self.v_output.get(), "1280x720")
        s.quality = self.v_quality.get()
        s.kbps = int(self.v_kbps.get()) if self.v_kbps.get().strip().isdigit() else 4500
        s.show_cursor = self.v_cursor.get()
        s.mp4 = self.v_mp4.get()
        s.remember_key = self.v_remember.get()
        number = next((n for n, name in LAYOUT_NAMES.items() if name == self.v_layout.get()), 1)
        s.layout = number if number in available_layouts(s) else 1
        return s

    def _choices_changed(self) -> None:
        """Something in the window changed: save it, and rebuild the sources if nothing is running."""
        s = self._collect()
        s.save()
        sess = self.session
        capturing = sess is not None and (sess.recording or sess.streaming)
        if not capturing:
            self.project = project_from_settings(s)
            if sess is not None:
                sess.project = self.project
                if sess.running:                      # only the preview is running: restart it with the new choices
                    wanted = s.compact
                    self._bg("", lambda: (sess.set_preview(False), wanted and sess.set_preview(True)),
                             self._started)
        else:
            self.note = "Saved. Source, quality and size changes apply the next time you start."
        self._refresh_layout_choices()
        self._update_info()

    def _refresh_layout_choices(self) -> None:
        numbers = available_layouts(self.settings)
        self.cb_layout.configure(values=[LAYOUT_NAMES[n] for n in numbers])
        if self.settings.layout not in numbers:
            self.settings.layout = 1
        self.v_layout.set(LAYOUT_NAMES[self.settings.layout])
        self._refresh_layout_buttons()

    def _update_info(self) -> None:
        s = self.settings
        place = s.window or (s.screen.split(" - ")[0] if s.screen else "Primary screen")
        encoder = "Auto encoder" if s.encoder == "auto" else s.encoder
        self.base_info = f"{place}  →  {s.canvas.replace('x', '×')}  ·  {s.fps} fps  ·  {encoder}"
        self.info.set(self.note or self.base_info)

    def _update_key_hint(self) -> None:
        if self.v_key.get().strip():
            self.key_hint.set("Ready to go live")
            self.key_label.configure(style="Ok.TLabel")
        else:
            self.key_hint.set("Stream key required to go live")
            self.key_label.configure(style="Warn.TLabel")

    def _show_key(self) -> None:
        self.entry_key.configure(show="" if self.v_show_key.get() else "*")

    def _service_changed(self) -> None:
        service = self.v_service.get()
        if SERVICES.get(service):
            self.v_server.set(SERVICES[service])
        self.entry_server.configure(state="normal" if service == "Custom" else "readonly")
        self._choices_changed()

    def _refresh_windows(self) -> None:
        self.windows = list_windows()
        self.cb_window.configure(values=[ENTIRE_SCREEN] + self.windows)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.v_folder.get() or str(Path.home()))
        if chosen:
            self.v_folder.set(chosen)
            self._choices_changed()

    # ----------------------------------------------------- live changes
    def _layout_changed(self) -> None:
        number = next((n for n, name in LAYOUT_NAMES.items() if name == self.v_layout.get()), 1)
        self._set_layout(number)

    def _set_layout(self, number: int) -> None:
        if number not in available_layouts(self.settings):
            self.note = "Choose a webcam first to use the webcam layouts."
            return
        self.settings.layout = number
        self.v_layout.set(LAYOUT_NAMES[number])
        name = layout_scene_name(number)
        if self.session is not None:
            try:
                self.session.switch_scene(name)
            except KeyError:
                self.project.current = name
        self.settings.save()

    def _toggle_webcam(self) -> None:
        if not self.settings.webcam:
            self.note = "Choose a webcam first (Audio & Webcam card)."
            return
        self._set_layout(1 if self.settings.layout != 1 else 2)

    def _volume(self, which: str, value) -> None:
        if which == "mic":
            self.settings.mic_volume = int(float(value))
        else:
            self.settings.desktop_volume = int(float(value))
        if self.session is not None and which in self.project.sources:
            self.session.set_volume(which, float(value) / 100.0)

    def _audio_on(self, which: str) -> None:
        on = (self.v_mic_on if which == "mic" else self.v_desktop_on).get()
        s = self.session
        if s is not None and s.running:
            if s.engine is not None and which in s.engine.plan.meter_files:
                s.set_muted(which, not on)               # live: silence it, or bring it back
                setattr(self.settings, "mic_on" if which == "mic" else "desktop_on", on)
                self.settings.save()
                return
            self.note = "That input was off when you started. It will be on the next time you start."
        self._choices_changed()

    def _cam_size(self, value) -> None:
        self.settings.cam_size = int(float(value))
        if not (self.session and self.session.running):
            self.project = project_from_settings(self.settings)
            if self.session is not None:
                self.session.project = self.project
        self.settings.save()

    def _hotkeys_changed(self) -> None:
        if self.v_hk_rec.get() == self.v_hk_live.get():
            self.note = "Pick two different hotkeys."
            return
        self._choices_changed()
        self._set_hotkeys()

    # --------------------------------------------------------- background work
    def _probe(self) -> None:
        cams, mics = list_devices(self.ffmpeg)
        encoders = working_encoders(self.ffmpeg)
        self.events.put(("devices", cams, mics, encoders))

    def _apply_devices(self, cams, mics, encoders) -> None:
        self.webcams, self.mics, self.encoders = cams, mics, encoders
        if self.session is not None:
            self.session.encoders[:] = encoders
        self.cb_webcam.configure(values=[NONE] + cams)
        self.cb_mic.configure(values=[NONE] + mics)
        self.cb_desktop.configure(values=[SYSTEM_SOUND, NONE] + mics)
        self.cb_encoder.configure(values=["auto"] + encoders)
        if not self.settings.mic and mics:
            guess = next((m for m in mics if "icrophone" in m or "Mic" in m), mics[0])
            self.v_mic.set(guess)
        self._choices_changed()

    def _bg(self, label: str, work, done=None) -> None:
        """Run `work()` off the window thread, then call done(result, error) back on it."""
        if self.busy:
            return
        self.busy = True
        self.note = label
        self._set_buttons_enabled(False)

        def run():
            try:
                self.events.put(("done", done, work(), None))
            except Exception as exc:
                log.exception("background action failed")
                self.events.put(("done", done, None, exc))

        threading.Thread(target=run, daemon=True).start()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in (self.rec_btn, self.live_btn):
            button.state(["!disabled"] if enabled else ["disabled"])

    def _tick(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "devices":
                    self._apply_devices(*event[1:])
                elif kind == "record":
                    self._toggle_record()
                elif kind == "stream":
                    self._toggle_live()
                elif kind == "layout":
                    self._set_layout(event[1])
                elif kind == "done":
                    self._finish_bg(*event[1:])
        except queue.Empty:
            pass
        if self.session is not None:
            self._ticks += 1
            self._draw_meters(self.session.meters())
            if self._ticks % 3 == 0:
                self._draw_preview()
            if self._ticks % 10 == 0:
                for message in self.session.poll():
                    messagebox.showwarning("JernCast", message[:1500])
                self._update_buttons()
                self._check_duration()
            self._update_status()
        self.root.after(100, self._tick)

    def _finish_bg(self, done, result, error) -> None:
        self.busy = False
        self.note = ""
        self._set_buttons_enabled(True)
        if done is not None:
            done(result, error)
        elif error is not None:
            messagebox.showerror("JernCast", str(error)[:1500])
        self._update_buttons()

    def _check_duration(self) -> None:
        s = self.session
        limit = self.settings.duration_min * 60
        if s.recording and limit and s.record_seconds() >= limit and not self.auto_stopped and not self.busy:
            self.auto_stopped = True
            self._toggle_record()

    def _update_buttons(self) -> None:
        s = self.session
        self.rec_btn.configure(text="Stop Recording" if s.recording else "Start Recording",
                               style="Stop.TButton" if s.recording else "Rec.TButton")
        self.live_btn.configure(text="Stop Live" if s.streaming else "Go live",
                                style="On.TButton" if s.streaming else "Live.TButton")

    def _update_status(self) -> None:
        s = self.session
        if s.streaming and s.recording:
            text, color = f"LIVE + REC  {format_time(s.stream_seconds())}", RED
        elif s.streaming:
            text, color = f"LIVE  {format_time(s.stream_seconds())}", RED
        elif s.recording:
            text, color = f"REC  {format_time(s.record_seconds())}", RED
        else:
            text, color = "Ready", GREEN
        self.info.set(self.note or getattr(self, "base_info", ""))
        self.status.set(text)
        self.pill.configure(bg=color)

    def _draw_meters(self, levels: dict) -> None:
        for sid, canvases in self.meter_canvases.items():
            frac = max(0.0, min(1.0, (levels.get(sid, -100.0) + 60.0) / 60.0))
            frac = max(frac, self.peaks.get(sid, 0.0) - 0.04)
            self.peaks[sid] = frac
            for canvas in canvases:
                canvas.delete("all")
                width = int(130 * frac)
                canvas.create_rectangle(0, 0, min(width, 91), 8, fill=GREEN, width=0)
                if width > 91:
                    canvas.create_rectangle(91, 0, min(width, 117), 8, fill="#e5c13a", width=0)
                if width > 117:
                    canvas.create_rectangle(117, 0, width, 8, fill=RED, width=0)

    # ---------------------------------------------------------------- actions
    def _toggle_record(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.recording:
            def done(path, error):
                self.note = f"Saved: {path.name}" if path else "Nothing was saved."
                if path:
                    self._remember_recording(path)
                if error:
                    messagebox.showerror("JernCast", str(error)[:1500])
            self._bg("Saving...", s.stop_recording, done)
        else:
            self._choices_changed()
            self.auto_stopped = False
            self._bg("Starting...", s.start_recording, self._started)

    def _toggle_live(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.streaming:
            self._bg("Stopping the stream...", s.stop_stream, self._started)
        else:
            self._choices_changed()
            self._bg("Connecting to the stream...", s.start_stream, self._started)

    def _started(self, _result, error) -> None:
        if error is not None:
            messagebox.showerror("JernCast", str(error)[:1500])

    def _remember_recording(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self.settings.recent if p != str(path)]
        self.settings.recent = recent[:8]
        self.settings.save()
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.recent_list.delete(0, "end")
        files = [p for p in self.settings.recent if os.path.exists(p)]
        for p in files:
            self.recent_list.insert("end", Path(p).name)
        if not files:
            self.recent_list.insert("end", "No recordings yet. Press Start Recording.")
        self._recent_files = files

    def _open_path(self, target: Path) -> None:
        try:
            if IS_WINDOWS:
                os.startfile(str(target))                       # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            log.exception("could not open %s", target)

    def _open_recent(self) -> None:
        sel = self.recent_list.curselection()
        if sel and sel[0] < len(self._recent_files):
            self._open_path(Path(self._recent_files[sel[0]]))

    def _open_folder(self) -> None:
        target = Path(os.path.expanduser(self.settings.folder))
        target.mkdir(parents=True, exist_ok=True)
        self._open_path(target)

    def _open_logs(self) -> None:
        self._open_path(data_dir() / "logs")

    def _manage_broadcast(self) -> None:
        url = manage_broadcast_url(self.settings.stream_server)
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("JernCast", "Manage Broadcast opens your streaming service's control page. "
                                "It knows YouTube and Twitch; pick one under Stream.")

    def _set_hotkeys(self) -> None:
        for hotkey in self.hotkeys:
            hotkey.stop()
        self.hotkeys = []
        wanted = [(self.settings.hotkey_record, ("record",)), (self.settings.hotkey_stream, ("stream",))]
        wanted += [(f"Ctrl+Alt+{n}", ("layout", n)) for n in (1, 2, 3, 4)]
        problems = []
        for key, event in wanted:
            hotkey = Hotkey(key, lambda e=event: self.events.put(e))
            if not hotkey.start_and_wait() and IS_WINDOWS:
                problems.append(key)
            self.hotkeys.append(hotkey)
        if problems:
            self.note = f"{', '.join(problems)} is used by another program; pick another key."

    def _exit(self) -> None:
        if self.busy:
            messagebox.showinfo("JernCast", "Please wait a moment.")
            return
        s = self.session
        if s is not None and (s.recording or s.streaming):
            if not messagebox.askyesno("JernCast", "You are recording or live. Stop everything and quit?"):
                return
            self.status.set("Saving...")
            self.root.update_idletasks()
        for hotkey in self.hotkeys:
            hotkey.stop()
        if s is not None:
            try:
                s.shutdown()
            except Exception:
                log.exception("shutdown failed")
        try:
            self._collect().save()
        finally:
            self.root.destroy()
