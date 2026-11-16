"""The Settings window and the source properties window."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import OUTPUT_SIZES, SERVICES, Settings
from .project import KIND_LABELS
from .winutil import HOTKEYS, list_windows

DISABLED = "Disabled"
SYSTEM_SOUND = "System sound"
SERVERS = list(SERVICES)


class Dialog:
    """A small modal window."""

    def __init__(self, app, title: str, bg: str):
        self.app = app
        self.result = None
        self.win = tk.Toplevel(app.root)
        self.win.title(title)
        self.win.configure(bg=bg)
        self.win.transient(app.root)
        self.body = ttk.Frame(self.win, padding=14)
        self.body.pack(fill="both", expand=True)

    def buttons(self, ok_text: str = "OK") -> None:
        row = ttk.Frame(self.win, padding=(14, 0, 14, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Cancel", command=self.win.destroy).pack(side="right")
        ttk.Button(row, text=ok_text, command=self._ok).pack(side="right", padx=6)

    def _ok(self) -> None:
        self.result = self.collect()
        if self.result is not None:
            self.win.destroy()

    def collect(self):
        return True

    def show(self):
        self.win.grab_set()
        self.app.root.wait_window(self.win)
        return self.result


class SourceDialog(Dialog):
    """Add or change a source (screen, window, webcam, picture or audio input)."""

    def __init__(self, app, kind: str, src=None, item=None):
        super().__init__(app, ("Properties - " if src else "Add - ") + KIND_LABELS[kind], app.colors["surface"])
        self.kind = kind
        b = self.body
        b.columnconfigure(1, weight=1)
        self.v_name = tk.StringVar(value=src.name if src else KIND_LABELS[kind])
        self.v_device = tk.StringVar(value=src.device if src else "")
        self.v_layout = tk.StringVar(value=item.layout if item else ("corner" if kind == "webcam" else "fill"))
        self.v_corner = tk.StringVar(value=item.corner if item else "bottom-right")
        self.v_size = tk.StringVar(value=item.size if item else "medium")

        ttk.Label(b, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(b, textvariable=self.v_name, width=40).grid(row=0, column=1, columnspan=2, sticky="ew", padx=6)

        label, values = {
            "display": ("Screen", [m.name for m in app.monitors]),
            "window": ("Window", app.windows or list_windows()),
            "webcam": ("Camera", app.webcams),
            "audio": ("Device", app.mics),
        }.get(kind, ("File", []))
        ttk.Label(b, text=label).grid(row=1, column=0, sticky="w", pady=4)
        if kind == "image":
            ttk.Entry(b, textvariable=self.v_device, width=40).grid(row=1, column=1, sticky="ew", padx=6)
            ttk.Button(b, text="Browse", command=self._browse).grid(row=1, column=2)
        else:
            combo = ttk.Combobox(b, textvariable=self.v_device, values=values, width=38,
                                 state="readonly" if kind in ("display", "webcam", "audio") else "normal")
            combo.grid(row=1, column=1, sticky="ew", padx=6)
            if not self.v_device.get() and values:
                self.v_device.set(values[0])
            if kind == "window":
                ttk.Button(b, text="Refresh", command=lambda: combo.configure(values=list_windows())).grid(
                    row=1, column=2)
            if not values and kind != "window":
                ttk.Label(b, text="No devices found yet. The list fills in a few seconds after JernCast starts.",
                          wraplength=380).grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        if kind != "audio":
            ttk.Label(b, text="Layout").grid(row=3, column=0, sticky="w", pady=(10, 4))
            lay = ttk.Frame(b)
            lay.grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=(10, 4))
            ttk.Radiobutton(lay, text="Fill the picture", value="fill", variable=self.v_layout).pack(side="left")
            ttk.Radiobutton(lay, text="Small, in a corner", value="corner", variable=self.v_layout).pack(
                side="left", padx=10)
            ttk.Label(b, text="Corner / size").grid(row=4, column=0, sticky="w")
            cs = ttk.Frame(b)
            cs.grid(row=4, column=1, columnspan=2, sticky="w", padx=6)
            ttk.Combobox(cs, textvariable=self.v_corner, width=14, state="readonly",
                         values=["bottom-right", "bottom-left", "top-right", "top-left"]).pack(side="left")
            ttk.Combobox(cs, textvariable=self.v_size, width=9, values=["small", "medium", "large", "15", "20", "30",
                                                                      "40"]).pack(side="left", padx=8)
        self.buttons()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self.win, filetypes=[
            ("Pictures", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")])
        if path:
            self.v_device.set(path)

    def collect(self):
        device = self.v_device.get().strip()
        if not device:
            messagebox.showinfo("JernCast", "Choose or enter the source first.", parent=self.win)
            return None
        return {"name": self.v_name.get().strip(), "device": device, "layout": self.v_layout.get(),
                "corner": self.v_corner.get(), "size": self.v_size.get()}


class SettingsDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Settings", app.colors["surface"])
        s: Settings = app.settings
        self.v_service = tk.StringVar(value=s.stream_service)
        self.v_server = tk.StringVar(value=s.stream_server)
        self.v_key = tk.StringVar(value=s.stream_key)
        self.v_remember = tk.BooleanVar(value=s.remember_key)
        self.v_folder = tk.StringVar(value=s.folder)
        self.v_prefix = tk.StringVar(value=s.prefix)
        self.v_mp4 = tk.BooleanVar(value=s.mp4)
        self.v_encoder = tk.StringVar(value=s.encoder)
        self.v_quality = tk.StringVar(value=s.quality)
        self.v_kbps = tk.StringVar(value=str(s.kbps))
        self.v_desktop = tk.StringVar(value=SYSTEM_SOUND if s.desktop == "loopback" else (s.desktop or DISABLED))
        self.v_mic = tk.StringVar(value=s.mic or DISABLED)
        self.v_output = tk.StringVar(value=next((k for k, v in OUTPUT_SIZES.items() if v == s.canvas), "720p"))
        self.v_fps = tk.StringVar(value=str(s.fps))
        self.v_hk_rec = tk.StringVar(value=s.hotkey_record)
        self.v_hk_live = tk.StringVar(value=s.hotkey_stream)
        self.v_cursor = tk.BooleanVar(value=s.show_cursor)
        self.v_duration = tk.StringVar(value=str(s.duration_min or ""))

        tabs = ttk.Notebook(self.body)
        tabs.pack(fill="both", expand=True)
        pages = {}
        for name in ("General", "Stream", "Output", "Audio", "Video", "Hotkeys"):
            page = ttk.Frame(tabs, padding=14)
            page.columnconfigure(1, weight=1)
            tabs.add(page, text=name)
            pages[name] = page

        def row(page, r, label, widget, sticky="ew"):
            ttk.Label(page, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))
            widget.grid(row=r, column=1, sticky=sticky)

        g = pages["General"]
        dur = ttk.Frame(g)
        ttk.Entry(dur, textvariable=self.v_duration, width=6).pack(side="left")
        ttk.Label(dur, text="minutes  (empty: until you stop)").pack(side="left", padx=8)
        row(g, 0, "Stop recording after", dur, "w")
        ttk.Checkbutton(g, text="Show the mouse cursor in the picture", variable=self.v_cursor).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=4)

        st = pages["Stream"]
        cb = ttk.Combobox(st, textvariable=self.v_service, values=SERVERS, state="readonly", width=16)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._service_picked())
        row(st, 0, "Service", cb, "w")
        row(st, 1, "Server", ttk.Entry(st, textvariable=self.v_server, width=44))
        key = ttk.Frame(st)
        self.entry_key = ttk.Entry(key, textvariable=self.v_key, show="*", width=36)
        self.entry_key.pack(side="left", fill="x", expand=True)
        self.show_key = False
        self.key_button = ttk.Button(key, text="Show", width=6, command=self._toggle_key)
        self.key_button.pack(side="left", padx=(6, 0))
        row(st, 2, "Stream key", key)
        ttk.Checkbutton(st, text="Remember the stream key on this PC", variable=self.v_remember).grid(
            row=3, column=1, sticky="w", pady=4)
        ttk.Label(st, text="YouTube: Studio > Create > Go live > Stream, copy the Stream key. Treat it like a "
                  "password.", wraplength=420).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        o = pages["Output"]
        folder = ttk.Frame(o)
        ttk.Entry(folder, textvariable=self.v_folder, width=34).pack(side="left", fill="x", expand=True)
        ttk.Button(folder, text="Browse", command=self._browse).pack(side="left", padx=(6, 0))
        row(o, 0, "Recording folder", folder)
        row(o, 1, "File name starts with", ttk.Entry(o, textvariable=self.v_prefix, width=20), "w")
        row(o, 2, "Encoder", ttk.Combobox(o, textvariable=self.v_encoder, values=["auto"] + app.encoders,
                                          state="readonly", width=14), "w")
        row(o, 3, "Quality", ttk.Combobox(o, textvariable=self.v_quality, values=["high", "medium", "low"],
                                          state="readonly", width=10), "w")
        row(o, 4, "Top bitrate (kbps)", ttk.Combobox(o, textvariable=self.v_kbps, width=8,
                                                     values=["1500", "2500", "3500", "4500", "6000", "8000"]), "w")
        ttk.Checkbutton(o, text="Convert recordings to MP4 when finished", variable=self.v_mp4).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=4)

        a = pages["Audio"]
        row(a, 0, "Desktop Audio", ttk.Combobox(a, textvariable=self.v_desktop, width=40,
                                                values=[SYSTEM_SOUND, DISABLED] + app.mics, state="readonly"))
        row(a, 1, "Mic/Aux", ttk.Combobox(a, textvariable=self.v_mic, width=40, values=[DISABLED] + app.mics,
                                          state="readonly"))
        ttk.Label(a, text="\"System sound\" records what your PC plays (needs the pyaudiowpatch package that the "
                  "installer includes). Otherwise pick Stereo Mix or a virtual cable.",
                  wraplength=420).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        v = pages["Video"]
        row(v, 0, "Output resolution", ttk.Combobox(v, textvariable=self.v_output, values=list(OUTPUT_SIZES),
                                                    state="readonly", width=10), "w")
        row(v, 1, "Frame rate", ttk.Combobox(v, textvariable=self.v_fps, values=["24", "30", "60"],
                                             state="readonly", width=6), "w")
        ttk.Label(v, text="Smaller resolution and lower frame rate use less memory and CPU.",
                  wraplength=420).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        h = pages["Hotkeys"]
        row(h, 0, "Start/stop recording", ttk.Combobox(h, textvariable=self.v_hk_rec, values=HOTKEYS,
                                                       state="readonly", width=6), "w")
        row(h, 1, "Start/stop streaming", ttk.Combobox(h, textvariable=self.v_hk_live, values=HOTKEYS,
                                                       state="readonly", width=6), "w")
        ttk.Label(h, text="Switch scenes with Ctrl+Alt+1 to 9.").grid(row=2, column=0, columnspan=2, sticky="w",
                                                                       pady=(10, 0))
        self.buttons("OK")

    def _service_picked(self) -> None:
        server = SERVICES.get(self.v_service.get(), "")
        if server:
            self.v_server.set(server)

    def _toggle_key(self) -> None:
        self.show_key = not self.show_key
        self.entry_key.configure(show="" if self.show_key else "*")
        self.key_button.configure(text="Hide" if self.show_key else "Show")

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self.win, initialdir=self.v_folder.get() or str(Path.home()))
        if chosen:
            self.v_folder.set(chosen)

    def collect(self):
        if self.v_hk_rec.get() == self.v_hk_live.get():
            messagebox.showinfo("JernCast", "Pick two different hotkeys.", parent=self.win)
            return None
        s: Settings = self.app.settings
        s.stream_service, s.stream_server = self.v_service.get(), self.v_server.get().strip()
        s.stream_key, s.remember_key = self.v_key.get().strip(), self.v_remember.get()
        s.folder = self.v_folder.get().strip() or s.folder
        s.prefix = self.v_prefix.get().strip() or "JernCast"
        s.mp4, s.encoder, s.quality = self.v_mp4.get(), self.v_encoder.get(), self.v_quality.get()
        s.kbps = int(self.v_kbps.get()) if self.v_kbps.get().strip().isdigit() else 4500
        desktop, mic = self.v_desktop.get(), self.v_mic.get()
        s.desktop = "" if desktop == DISABLED else ("loopback" if desktop == SYSTEM_SOUND else desktop)
        s.mic = "" if mic == DISABLED else mic
        s.canvas = OUTPUT_SIZES.get(self.v_output.get(), "1280x720")
        s.fps = int(self.v_fps.get()) if self.v_fps.get().isdigit() else 30
        s.hotkey_record, s.hotkey_stream = self.v_hk_rec.get(), self.v_hk_live.get()
        s.show_cursor = self.v_cursor.get()
        s.duration_min = int(self.v_duration.get()) if self.v_duration.get().strip().isdigit() else 0
        return True
