"""The JernCast window (0.0.2): clear dark cards, light on memory, live preview in compact mode."""
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
from .core import (IS_WINDOWS, OUTPUT_SIZES, SERVICES, Settings, assets_dir, cached_working_encoders,
                   data_dir, find_ffmpeg, format_time, list_devices, manage_broadcast_url, pick_microphone)
from .layouts import LAYOUT_NAMES, available_layouts, layout_scene_name, project_from_settings
from .session import Session
from .widgets import LevelBar, Pill, RoundButton, Segmented, Slider, Switch, ui_scale
from .winutil import HOTKEYS, Hotkey, list_monitors, list_windows

log = logging.getLogger("jerncast")

BG = "#0e1014"
SURFACE = "#151922"
FIELD = "#1d2330"
BORDER = "#252b38"
TEXT = "#e8ecf4"
MUTED = "#8a93a6"
ACCENT = "#4c8dff"
REC = "#e5484d"
OK = "#2fbf71"
WARN = "#f0a93b"
TRACK = "#2c3342"
ENTIRE_SCREEN = "Entire screen"
NONE = "None"
SYSTEM_SOUND = "System sound"
CORNERS = ["bottom-right", "bottom-left", "top-right", "top-left"]
FONT = "Segoe UI"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.k = ui_scale(root)
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
        self.base_info = ""
        self.hotkeys: list[Hotkey] = []
        self.meters: dict[str, list[LevelBar]] = {}
        self.peaks: dict[str, float] = {}
        self.preview_photo = None
        self.preview_mtime = 0.0
        self.auto_stopped = False
        self.advanced_open = False
        self._ticks = 0
        self._recent_files: list[str] = []

        root.title(f"JernCast {__version__}")
        root.configure(bg=BG)
        self._set_icon()
        self._style()
        self._vars()
        self._build()
        self.entry_server_row_update()
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
        root.after(200, self._tick)

    # ------------------------------------------------------------------ look
    def px(self, n: float) -> int:
        return int(n * self.k)

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
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 9))
        st.configure(".", background=SURFACE, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER,
                     lightcolor=SURFACE, darkcolor=SURFACE, troughcolor=FIELD, font=(FONT, 9))
        st.configure("TFrame", background=SURFACE)
        st.configure("Bg.TFrame", background=BG)
        st.configure("TLabel", background=SURFACE, foreground=TEXT)
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Warn.TLabel", foreground=WARN)
        st.configure("Ok.TLabel", foreground=OK)
        st.configure("Name.TLabel", background=BG, foreground=TEXT, font=(FONT, 15, "bold"))
        st.configure("Info.TLabel", background=BG, foreground=MUTED)
        st.configure("Card.TLabel", foreground=MUTED, font=(FONT, 8, "bold"))
        st.configure("Link.TLabel", foreground=ACCENT)
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, arrowcolor=MUTED,
                     borderwidth=0, padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
               background=[("active", FIELD)])
        st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, borderwidth=0, padding=5)
        st.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
        st.map("TCheckbutton", background=[("active", SURFACE)])
        st.configure("TButton", background=FIELD, foreground=TEXT, padding=(10, 4), borderwidth=0)
        st.map("TButton", background=[("active", "#272f3f")])

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
        self.v_mic = tk.StringVar(value=s.mic or NONE)
        self.v_mic_on = tk.BooleanVar(value=s.mic_on)
        self.v_desktop = tk.StringVar(value=SYSTEM_SOUND if s.desktop == "loopback" else (s.desktop or NONE))
        self.v_desktop_on = tk.BooleanVar(value=s.desktop_on)
        self.v_layout_num = tk.IntVar(value=s.layout)
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
        self.info = tk.StringVar(value="")
        self.key_hint = tk.StringVar(value="")
        self.layout_caption = tk.StringVar(value="")

    # ---------------------------------------------------------------- layout
    def _card(self, parent, title: str, pady=(0, 10)):
        outer = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        outer.pack(fill="x", pady=pady)
        ttk.Label(outer, text=title.upper(), style="Card.TLabel").pack(anchor="w", padx=14, pady=(10, 4))
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        body.columnconfigure(1, weight=1)
        return outer, body

    def _row(self, body, row, label, widget, sticky="ew"):
        ttk.Label(body, text=label, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        widget.grid(row=row, column=1, sticky=sticky)

    def _combo(self, parent, var, values, command=None, width=24, readonly=True, **kw):
        box = ttk.Combobox(parent, textvariable=var, values=values, width=width,
                           state="readonly" if readonly else "normal", **kw)
        if command:
            box.bind("<<ComboboxSelected>>", lambda _e: command())
        return box

    def _bar(self, parent, source_id: str, width=110) -> LevelBar:
        bar = LevelBar(parent, surface=SURFACE, track=TRACK, width=width, scale=self.k)
        self.meters.setdefault(source_id, []).append(bar)
        return bar

    def _slider(self, parent, lo, hi, value, command, width=120) -> Slider:
        return Slider(parent, lo, hi, value, command, surface=SURFACE, track=TRACK, accent=ACCENT,
                      width=width, scale=self.k)

    def _switch(self, parent, var, command) -> Switch:
        return Switch(parent, var, command, surface=SURFACE, on=ACCENT, off=TRACK, scale=self.k)

    def _segmented(self, parent, options, var, command, seg_width=44) -> Segmented:
        return Segmented(parent, options, var, command, surface=SURFACE, field=FIELD, accent=ACCENT,
                         seg_width=seg_width, scale=self.k)

    def _button(self, parent, text, command, *, bg, surface=BG, outline=None, width=140, height=40, fg="#ffffff"):
        return RoundButton(parent, text, command, surface=surface, bg=bg, fg=fg, outline=outline,
                           width=width, height=height, scale=self.k)

    def _build(self) -> None:
        root = self.root
        head = ttk.Frame(root, style="Bg.TFrame")
        head.pack(fill="x", padx=18, pady=(14, 8))
        if self.icon is not None:
            self.small_icon = self.icon.subsample(max(1, int(8 / self.k)), max(1, int(8 / self.k)))
            tk.Label(head, image=self.small_icon, bg=BG).pack(side="left", padx=(0, 10))
        titles = ttk.Frame(head, style="Bg.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text="JernCast", style="Name.TLabel").pack(anchor="w")
        ttk.Label(titles, textvariable=self.info, style="Info.TLabel").pack(anchor="w")
        self.compact_btn = self._button(head, "Compact view", self._toggle_compact, bg=FIELD, width=110, height=32)
        self.compact_btn.pack(side="right")
        self.pill = Pill(head, "Ready", OK, surface=BG, scale=self.k)
        self.pill.pack(side="right", padx=14)

        bottom = ttk.Frame(root, style="Bg.TFrame")
        bottom.pack(fill="x", padx=18, pady=(8, 16), side="bottom")
        self.rec_btn = self._button(bottom, "●  Start Recording", self._toggle_record, bg=REC, width=200, height=46)
        self.rec_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.live_btn = self._button(bottom, "Go live", self._toggle_live, bg=FIELD, outline=BORDER, width=160,
                                     height=46)
        self.live_btn.pack(side="left", fill="x", expand=True, padx=8)
        self.manage_btn = self._button(bottom, "Manage Broadcast", self._manage_broadcast, bg=BG, outline=BORDER,
                                       width=150, height=46, fg=MUTED)
        self.manage_btn.pack(side="left", padx=(8, 0))

        self.main = ttk.Frame(root, style="Bg.TFrame")
        self.main.pack(fill="both", expand=True, padx=18)

        # ---- compact view
        self.compact_view = ttk.Frame(self.main, style="Bg.TFrame")
        self.preview = tk.Canvas(self.compact_view, bg="#050608", highlightthickness=1, highlightbackground=BORDER,
                                 width=self.px(480), height=self.px(270))
        self.preview.pack(pady=(6, 10))
        strip = tk.Frame(self.compact_view, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        strip.pack(fill="x")
        inner = ttk.Frame(strip)
        inner.pack(fill="x", padx=14, pady=10)
        for text, sid in (("Mic", "mic"), ("Desktop", "desktop")):
            ttk.Label(inner, text=text, style="Muted.TLabel").pack(side="left", padx=(0, 6))
            self._bar(inner, sid, 90).pack(side="left", padx=(0, 16))
        self.compact_layout = self._segmented(inner, [1], self.v_layout_num, self._layout_picked, 36)
        self.compact_layout.pack(side="right")
        ttk.Label(inner, text="Layout", style="Muted.TLabel").pack(side="right", padx=(0, 8))

        # ---- full view
        self.cards = ttk.Frame(self.main, style="Bg.TFrame")
        left = ttk.Frame(self.cards, style="Bg.TFrame")
        right = ttk.Frame(self.cards, style="Bg.TFrame")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_capture(left)
        self._build_stream(left)
        self._build_recent(left)
        self._build_audio(right)
        self._build_webcam(right)
        self._build_advanced(right)

    def _build_capture(self, parent) -> None:
        _, body = self._card(parent, "Capture")
        self.cb_screen = self._combo(body, self.v_screen, [m.name for m in self.monitors], self._choices_changed)
        self._row(body, 0, "Screen", self.cb_screen)
        self.cb_window = self._combo(body, self.v_window, [ENTIRE_SCREEN], self._choices_changed,
                                     postcommand=self._refresh_windows)
        self._row(body, 1, "Window", self.cb_window)
        self._row(body, 2, "Frame rate", self._segmented(body, [30, 60], self.v_fps, self._choices_changed),
                  sticky="w")
        dur = ttk.Frame(body)
        ttk.Entry(dur, textvariable=self.v_duration, width=6).pack(side="left")
        ttk.Label(dur, text="minutes  (empty: until you stop)", style="Muted.TLabel").pack(side="left", padx=8)
        self._row(body, 3, "Stop after", dur, sticky="w")
        folder = ttk.Frame(body)
        ttk.Entry(folder, textvariable=self.v_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(folder, text="Browse", command=self._browse).pack(side="left", padx=(6, 0))
        self._row(body, 4, "Save to", folder)
        self._row(body, 5, "File name", ttk.Entry(body, textvariable=self.v_prefix))

    def _build_stream(self, parent) -> None:
        _, body = self._card(parent, "Stream")
        self._row(body, 0, "Service", self._combo(body, self.v_service, list(SERVICES), self._service_changed))
        self.server_label = ttk.Label(body, text="Server", style="Muted.TLabel")
        self.entry_server = ttk.Entry(body, textvariable=self.v_server)
        self.server_label.grid(row=1, column=0, sticky="w", pady=4, padx=(0, 12))
        self.entry_server.grid(row=1, column=1, sticky="ew")
        key = ttk.Frame(body)
        self.entry_key = ttk.Entry(key, textvariable=self.v_key, show="*")
        self.entry_key.pack(side="left", fill="x", expand=True)
        self.show_key = False
        self.key_toggle = ttk.Button(key, text="Show", width=6, command=self._show_key)
        self.key_toggle.pack(side="left", padx=(6, 0))
        self._row(body, 2, "Stream key", key)
        self.key_label = ttk.Label(body, textvariable=self.key_hint, style="Warn.TLabel")
        self.key_label.grid(row=3, column=1, sticky="w", pady=(2, 0))
        self.v_key.trace_add("write", lambda *_: self._update_key_hint())
        self._update_key_hint()

    def _build_recent(self, parent) -> None:
        outer, body = self._card(parent, "Recent recordings", pady=(0, 0))
        self.recent_list = tk.Listbox(body, height=4, bg=FIELD, fg=TEXT, selectbackground=ACCENT, highlightthickness=0,
                                      bd=0, activestyle="none", font=(FONT, 9))
        self.recent_list.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.recent_list.bind("<Double-Button-1>", lambda _e: self._open_recent())
        row = ttk.Frame(body)
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(row, text="Open", command=self._open_recent).pack(side="left")
        ttk.Button(row, text="Open folder", command=self._open_folder).pack(side="left", padx=6)

    def _build_audio(self, parent) -> None:
        _, body = self._card(parent, "Audio")
        self.cb_mic = self._combo(body, self.v_mic, [NONE], self._choices_changed)
        self._row(body, 0, "Microphone", self.cb_mic)
        mic = ttk.Frame(body)
        self._switch(mic, self.v_mic_on, lambda: self._audio_on("mic")).pack(side="left")
        self.mic_slider = self._slider(mic, 0, 200, self.settings.mic_volume, lambda v: self._volume("mic", v))
        self.mic_slider.pack(side="left", padx=10)
        self._bar(mic, "mic").pack(side="left")
        self._row(body, 1, "", mic, sticky="w")
        self.cb_desktop = self._combo(body, self.v_desktop, [SYSTEM_SOUND, NONE], self._choices_changed)
        self._row(body, 2, "Desktop sound", self.cb_desktop)
        desk = ttk.Frame(body)
        self._switch(desk, self.v_desktop_on, lambda: self._audio_on("desktop")).pack(side="left")
        self.desk_slider = self._slider(desk, 0, 200, self.settings.desktop_volume,
                                        lambda v: self._volume("desktop", v))
        self.desk_slider.pack(side="left", padx=10)
        self._bar(desk, "desktop").pack(side="left")
        self._row(body, 3, "", desk, sticky="w")

    def _build_webcam(self, parent) -> None:
        _, body = self._card(parent, "Webcam and layout")
        self.cb_webcam = self._combo(body, self.v_webcam, [NONE], self._choices_changed)
        self._row(body, 0, "Webcam", self.cb_webcam)
        lay = ttk.Frame(body)
        self.layout_bar = self._segmented(lay, [1], self.v_layout_num, self._layout_picked, 36)
        self.layout_bar.pack(side="left")
        ttk.Label(lay, textvariable=self.layout_caption, style="Muted.TLabel").pack(side="left", padx=10)
        self._row(body, 1, "Layout", lay, sticky="w")
        self._row(body, 2, "Webcam corner", self._combo(body, self.v_corner, CORNERS, self._choices_changed, width=14),
                  sticky="w")
        self.cam_slider = self._slider(body, 10, 50, self.settings.cam_size, self._cam_size, width=150)
        self._row(body, 3, "Webcam size", self.cam_slider, sticky="w")
        ttk.Label(body, text="Switch layouts any time with Ctrl+Alt+1 to 4.", style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_advanced(self, parent) -> None:
        outer = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        outer.pack(fill="x")
        self.adv_toggle = ttk.Label(outer, text="ADVANCED   ▸", style="Card.TLabel", cursor="hand2")
        self.adv_toggle.pack(anchor="w", padx=14, pady=10)
        self.adv_toggle.bind("<Button-1>", lambda _e: self._toggle_advanced())
        self.adv_body = ttk.Frame(outer)
        body = self.adv_body
        body.columnconfigure(1, weight=1)
        self.cb_encoder = self._combo(body, self.v_encoder, ["auto"], self._choices_changed, width=14)
        self._row(body, 0, "Encoder", self.cb_encoder, sticky="w")
        self._row(body, 1, "Output size", self._combo(body, self.v_output, list(OUTPUT_SIZES), self._choices_changed,
                                                      width=8), sticky="w")
        self._row(body, 2, "Quality", self._combo(body, self.v_quality, ["high", "medium", "low"],
                                                  self._choices_changed, width=8), sticky="w")
        self._row(body, 3, "Top bitrate (kbps)", self._combo(
            body, self.v_kbps, ["1500", "2500", "3500", "4500", "6000", "8000"], self._choices_changed, width=8,
            readonly=False), sticky="w")
        hk = ttk.Frame(body)
        ttk.Label(hk, text="Record", style="Muted.TLabel").pack(side="left")
        self._combo(hk, self.v_hk_rec, HOTKEYS, self._hotkeys_changed, width=5).pack(side="left", padx=(6, 14))
        ttk.Label(hk, text="Live", style="Muted.TLabel").pack(side="left")
        self._combo(hk, self.v_hk_live, HOTKEYS, self._hotkeys_changed, width=5).pack(side="left", padx=6)
        self._row(body, 4, "Hotkeys", hk, sticky="w")
        for r, (text, var) in enumerate((("Show the mouse cursor", self.v_cursor),
                                         ("Convert recordings to MP4 when finished", self.v_mp4),
                                         ("Remember the stream key on this PC", self.v_remember)), start=5):
            ttk.Checkbutton(body, text=text, variable=var, command=self._choices_changed).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Button(body, text="Open logs folder", command=self._open_logs).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _toggle_advanced(self) -> None:
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.adv_body.pack(fill="x", padx=14, pady=(0, 12))
        else:
            self.adv_body.pack_forget()
        self.adv_toggle.configure(text="ADVANCED   " + ("▾" if self.advanced_open else "▸"))

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
            self.compact_btn.style(text="Full view")
            self.root.minsize(self.px(560), self.px(500))
            self.root.geometry(f"{self.px(600)}x{self.px(600)}")
            self._set_preview_wanted(True)
        else:
            self.cards.pack(fill="both", expand=True)
            self.compact_btn.style(text="Compact view")
            self.root.minsize(self.px(900), self.px(640))
            self.root.geometry(f"{self.px(980)}x{self.px(760)}")
            self._set_preview_wanted(False)

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

    def _draw_preview(self) -> None:
        s = self.session
        if s is None or not self.settings.compact:
            return
        path = s.preview_path()
        canvas = self.preview
        try:
            if path is not None and path.exists() and path.stat().st_mtime != self.preview_mtime:
                self.preview_mtime = path.stat().st_mtime
                if self.preview_photo is None:
                    self.preview_photo = tk.PhotoImage()
                    canvas.delete("all")
                    canvas.create_image(self.px(240), self.px(135), image=self.preview_photo)
                self.preview_photo.configure(file=str(path))       # reuse one image: no memory churn
            elif path is None and self.preview_photo is not None:
                self.preview_photo = None
                self.preview_mtime = 0.0
                canvas.delete("all")
                canvas.create_text(self.px(240), self.px(135), text="Preview is starting...", fill=MUTED)
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
        number = int(self.v_layout_num.get())
        s.layout = number if number in available_layouts(s) else 1
        return s

    def _choices_changed(self) -> None:
        """Something changed: save it, and rebuild the sources if nothing is capturing."""
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
        for bar in (self.layout_bar, self.compact_layout):
            bar.set_options(numbers)
        if self.settings.layout not in numbers:
            self.settings.layout = 1
        self.v_layout_num.set(self.settings.layout)
        self.layout_caption.set(LAYOUT_NAMES[self.settings.layout])

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
            self.key_hint.set("Add your stream key to go live")
            self.key_label.configure(style="Warn.TLabel")

    def _show_key(self) -> None:
        self.show_key = not self.show_key
        self.entry_key.configure(show="" if self.show_key else "*")
        self.key_toggle.configure(text="Hide" if self.show_key else "Show")

    def entry_server_row_update(self) -> None:
        """The server box is only shown for a custom service."""
        if self.settings.stream_service == "Custom":
            self.server_label.grid()
            self.entry_server.grid()
        else:
            self.server_label.grid_remove()
            self.entry_server.grid_remove()

    def _service_changed(self) -> None:
        service = self.v_service.get()
        if SERVICES.get(service):
            self.v_server.set(SERVICES[service])
        self.settings.stream_service = service
        self.entry_server_row_update()
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
    def _layout_picked(self) -> None:
        self._set_layout(int(self.v_layout_num.get()))

    def _set_layout(self, number: int) -> None:
        if number not in available_layouts(self.settings):
            self.note = "Choose a webcam first to use the webcam layouts."
            return
        self.settings.layout = number
        self.v_layout_num.set(number)
        self.layout_caption.set(LAYOUT_NAMES[number])
        name = layout_scene_name(number)
        if self.session is not None:
            try:
                self.session.switch_scene(name)
            except KeyError:
                self.project.current = name
        self.settings.save()

    def _toggle_webcam(self) -> None:
        if not self.settings.webcam:
            self.note = "Choose a webcam first (Webcam and layout card)."
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
        if not (self.session and (self.session.recording or self.session.streaming)):
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
        encoders = cached_working_encoders(self.ffmpeg)
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
            self.v_mic.set(pick_microphone(mics))
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
        active = False
        if self.session is not None:
            self._ticks += 1
            active = self.session.running or self.busy
            if self.session.running:
                self._draw_meters(self.session.meters())
                if self._ticks % 3 == 0:
                    self._draw_preview()
            if self._ticks % (10 if active else 2) == 0:
                for message in self.session.poll():
                    messagebox.showwarning("JernCast", message[:1500])
                self._update_buttons()
                self._check_duration()
            self._update_status()
        # wake up often only while something is running: an idle JernCast uses almost no CPU
        self.root.after(100 if active else 500, self._tick)

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
        if s.recording:
            self.rec_btn.style(text="■  Stop Recording", bg="#7a2a30")
        else:
            self.rec_btn.style(text="●  Start Recording", bg=REC)
        if s.streaming:
            self.live_btn.style(text="Stop Live", bg=ACCENT, outline=None)
        else:
            self.live_btn.style(text="Go live", bg=FIELD, outline=BORDER)

    def _update_status(self) -> None:
        s = self.session
        if s.streaming and s.recording:
            text, color = f"LIVE + REC  {format_time(s.stream_seconds())}", REC
        elif s.streaming:
            text, color = f"LIVE  {format_time(s.stream_seconds())}", REC
        elif s.recording:
            text, color = f"REC  {format_time(s.record_seconds())}", REC
        else:
            text, color = "Ready", OK
        self.info.set(self.note or self.base_info)
        self.pill.set(text, color)

    def _draw_meters(self, levels: dict) -> None:
        for sid, bars in self.meters.items():
            frac = max(0.0, min(1.0, (levels.get(sid, -100.0) + 60.0) / 60.0))
            frac = max(frac, self.peaks.get(sid, 0.0) - 0.05)       # falls back smoothly
            self.peaks[sid] = frac
            for bar in bars:
                bar.set(frac)

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
            self.recent_list.insert("end", "Nothing recorded yet. Press Start Recording.")
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
        folder = data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

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
            self.note = "Saving..."
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
