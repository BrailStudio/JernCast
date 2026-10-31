"""The JernCast window: scenes, sources, audio mixer and controls, like OBS."""
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
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__
from .core import (IS_WINDOWS, Settings, assets_dir, find_ffmpeg, format_time, list_devices,
                   manage_broadcast_url, working_encoders)
from .project import KIND_LABELS, Project, layout_rects
from .session import Session, SessionError
from .winutil import HOTKEYS, Hotkey, list_monitors, list_windows

log = logging.getLogger("jerncast")

SERVERS = ["rtmp://a.rtmp.youtube.com/live2", "rtmps://a.rtmps.youtube.com:443/live2",
           "rtmp://live.twitch.tv/app"]
CANVASES = ["1920x1080", "1280x720", "854x480"]

BG = "#0b0e14"
PANEL = "#141923"
FIELD = "#0e121a"
TEXT = "#e8edf5"
MUTED = "#8b97ab"
BLUE = "#1e90ff"
RED = "#e5484d"
GREEN = "#2fbf71"
KIND_COLORS = {"display": "#16314f", "window": "#1c3a3a", "webcam": "#3a2a4f",
               "image": "#4f3a1c", "test_color": "#2a2a2a"}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.project = Project.load()
        self.ffmpeg = find_ffmpeg()
        self.events: queue.Queue = queue.Queue()
        self.monitors = list_monitors()
        self.windows = list_windows()
        self.webcams: list[str] = []
        self.audio_inputs: list[str] = []
        self.encoders = ["libx264"]
        self.session = (Session(self.ffmpeg, self.settings, self.project, self.monitors, self.encoders)
                        if self.ffmpeg else None)
        self.busy = False
        self.note = ""
        self.hotkeys: list[Hotkey] = []
        self.peaks: dict[str, float] = {}
        self.meter_canvases: dict = {}
        self._ticks = 0

        root.title(f"JernCast {__version__}")
        root.configure(bg=BG)
        root.minsize(1000, 680)
        self._set_icon()
        self._style()
        self._build()
        self._refresh_all()
        self._set_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self._exit)

        if self.session is None:
            self.note = "FFmpeg was not found. Put ffmpeg.exe next to JernCast.exe."
            for button in (self.rec_btn, self.stream_btn):
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
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("Dock.TLabelframe", background=PANEL, foreground=TEXT, bordercolor="#232b3a")
        st.configure("Dock.TLabelframe.Label", background=PANEL, foreground=BLUE,
                     font=("Segoe UI", 10, "bold"))
        for name in ("TLabel", "TCheckbutton", "TRadiobutton"):
            st.configure(name, background=PANEL, foreground=TEXT)
        st.configure("Head.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED)
        st.configure("Bar.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        st.configure("Hint.TLabel", background=PANEL, foreground=MUTED)
        st.configure("TButton", padding=5)
        st.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        st.configure("On.TButton", font=("Segoe UI", 10, "bold"), padding=8,
                     background=RED, foreground="white")
        st.map("On.TButton", background=[("active", "#f06065"), ("disabled", "#2a3446")])
        st.configure("TMenubutton", padding=5)

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        root = self.root
        head = ttk.Frame(root)
        head.pack(fill="x", padx=14, pady=(10, 4))
        if self.icon is not None:
            self.small_icon = self.icon.subsample(5, 5)
            tk.Label(head, image=self.small_icon, bg=BG).pack(side="left", padx=(0, 10))
        ttk.Label(head, text="JernCast", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="  scenes, sources and live streaming", style="Sub.TLabel").pack(
            side="left", pady=(8, 0))

        self.preview = tk.Canvas(root, bg="#05070b", highlightthickness=0, height=260)
        self.preview.pack(fill="both", expand=True, padx=14, pady=4)
        self.preview.bind("<Configure>", lambda _e: self._draw_preview())

        docks = ttk.Frame(root)
        docks.pack(fill="x", padx=14, pady=4)
        for col, weight in enumerate((2, 3, 4, 2)):
            docks.columnconfigure(col, weight=weight)
        self._build_scenes(docks)
        self._build_sources(docks)
        self._build_mixer(docks)
        self._build_controls(docks)

        self.status = tk.StringVar(value="Ready")
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=14, pady=(2, 10))
        ttk.Label(bar, textvariable=self.status, style="Bar.TLabel").pack(side="left")

    def _listbox(self, parent) -> tk.Listbox:
        return tk.Listbox(parent, height=7, bg=FIELD, fg=TEXT, selectbackground=BLUE,
                          selectforeground="white", highlightthickness=0, bd=0,
                          exportselection=False, activestyle="none", font=("Segoe UI", 10))

    def _build_scenes(self, docks) -> None:
        box = ttk.LabelFrame(docks, text=" Scenes ", style="Dock.TLabelframe", padding=8)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.scene_list = self._listbox(box)
        self.scene_list.pack(fill="both", expand=True)
        self.scene_list.bind("<<ListboxSelect>>", lambda _e: self._on_scene_select())
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="+", width=3, command=self._add_scene).pack(side="left")
        ttk.Button(row, text="-", width=3, command=self._remove_scene).pack(side="left", padx=4)
        ttk.Button(row, text="Rename", command=self._rename_scene).pack(side="left")

    def _build_sources(self, docks) -> None:
        box = ttk.LabelFrame(docks, text=" Sources ", style="Dock.TLabelframe", padding=8)
        box.grid(row=0, column=1, sticky="nsew", padx=6)
        self.source_list = self._listbox(box)
        self.source_list.pack(fill="both", expand=True)
        self.source_list.bind("<Double-Button-1>", lambda _e: self._properties())
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        add = ttk.Menubutton(row, text="+ Add")
        menu = tk.Menu(add, tearoff=0)
        for kind in ("display", "window", "webcam", "image"):
            menu.add_command(label=KIND_LABELS[kind], command=lambda k=kind: self._add_source(k))
        add["menu"] = menu
        add.pack(side="left")
        ttk.Button(row, text="-", width=3, command=self._remove_source).pack(side="left", padx=4)
        ttk.Button(row, text="Properties", command=self._properties).pack(side="left")
        ttk.Button(row, text="Show/Hide", command=self._toggle_visible).pack(side="left", padx=4)
        ttk.Button(row, text="Up", width=4, command=lambda: self._move_source(1)).pack(side="left")
        ttk.Button(row, text="Down", width=5, command=lambda: self._move_source(-1)).pack(side="left", padx=4)

    def _build_mixer(self, docks) -> None:
        self.mixer_box = ttk.LabelFrame(docks, text=" Audio Mixer ", style="Dock.TLabelframe", padding=8)
        self.mixer_box.grid(row=0, column=2, sticky="nsew", padx=6)
        self.mixer_rows = ttk.Frame(self.mixer_box, style="Panel.TFrame")
        self.mixer_rows.pack(fill="both", expand=True)
        ttk.Button(self.mixer_box, text="+ Audio input", command=lambda: self._add_source("audio")).pack(
            anchor="w", pady=(6, 0))

    def _build_controls(self, docks) -> None:
        box = ttk.LabelFrame(docks, text=" Controls ", style="Dock.TLabelframe", padding=8)
        box.grid(row=0, column=3, sticky="nsew", padx=(6, 0))
        self.stream_btn = ttk.Button(box, text="Start Streaming", style="Big.TButton", command=self._toggle_stream)
        self.stream_btn.pack(fill="x", pady=2)
        self.rec_btn = ttk.Button(box, text="Start Recording", style="Big.TButton", command=self._toggle_record)
        self.rec_btn.pack(fill="x", pady=2)
        ttk.Button(box, text="Manage Broadcast", command=self._manage_broadcast).pack(fill="x", pady=2)
        ttk.Button(box, text="Settings", command=self._open_settings).pack(fill="x", pady=2)
        ttk.Button(box, text="Open recordings folder", command=self._open_folder).pack(fill="x", pady=2)
        ttk.Button(box, text="Exit", command=self._exit).pack(fill="x", pady=2)

    # --------------------------------------------------------- refreshing
    def _refresh_all(self) -> None:
        self._refresh_scenes()
        self._refresh_sources()
        self._rebuild_mixer()
        self._draw_preview()

    def _refresh_scenes(self) -> None:
        self.scene_list.delete(0, "end")
        for scene in self.project.scenes:
            self.scene_list.insert("end", scene.name)
        self.scene_list.selection_clear(0, "end")
        self.scene_list.selection_set(self.project.current_index)

    def _items_front_first(self):
        scene = self.project.current_scene
        return list(reversed(list(enumerate(scene.items))))     # (item index, item)

    def _refresh_sources(self, keep: int | None = None) -> None:
        self.source_list.delete(0, "end")
        for _ii, item in self._items_front_first():
            src = self.project.sources[item.source]
            self.source_list.insert("end", f"{'●' if item.visible else '○'}  {src.name}")
        if keep is not None and self.source_list.size() > 0:
            self.source_list.selection_set(min(keep, self.source_list.size() - 1))

    def _selected_item(self) -> int | None:
        """Index (in the scene's item list) of the selected source, or None."""
        sel = self.source_list.curselection()
        if not sel:
            return None
        order = self._items_front_first()
        return order[sel[0]][0] if sel[0] < len(order) else None

    def _draw_preview(self) -> None:
        canvas = self.preview
        canvas.delete("all")
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw < 50 or ch < 50:
            return
        aspect = 16 / 9
        w = min(cw - 20, int((ch - 28) * aspect))
        h = int(w / aspect)
        x0, y0 = (cw - w) // 2, 24 + (ch - 28 - h) // 2
        canvas.create_rectangle(x0, y0, x0 + w, y0 + h, fill="#000000", outline="#2a3446")
        scene = self.project.current_scene
        for name, ax, ay, bx, by in layout_rects(self.project, scene, w, h):
            src = next((s for s in self.project.sources.values() if s.name == name), None)
            color = KIND_COLORS.get(src.kind if src else "", "#222a38")
            canvas.create_rectangle(x0 + ax, y0 + ay, x0 + bx, y0 + by, fill=color, outline="#3b4a63")
            canvas.create_text(x0 + (ax + bx) // 2, y0 + (ay + by) // 2, text=name, fill=TEXT,
                               font=("Segoe UI", 11), width=max(40, bx - ax - 8))
        live = self.session is not None and (self.session.recording or self.session.streaming)
        canvas.create_text(x0, 4, anchor="nw", fill=MUTED, font=("Segoe UI", 9),
                           text=f"Scene: {scene.name}   (layout view; the real picture is what gets "
                                f"{'sent' if live else 'recorded or streamed'})")

    def _rebuild_mixer(self) -> None:
        for child in self.mixer_rows.winfo_children():
            child.destroy()
        self.meter_canvases = {}
        audios = self.project.audio_sources()
        if not audios:
            ttk.Label(self.mixer_rows, text="No audio yet. Press + Audio input to add a "
                      "microphone or Stereo Mix.", style="Hint.TLabel", wraplength=300).pack(anchor="w")
        for src in audios:
            row = ttk.Frame(self.mixer_rows, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=src.name, width=16).grid(row=0, column=0, sticky="w")
            scale = tk.Scale(row, from_=0, to=200, orient="horizontal", showvalue=False, length=110,
                             bg=PANEL, fg=TEXT, troughcolor=FIELD, highlightthickness=0, bd=0,
                             sliderlength=14)
            scale.set(int(src.volume * 100))
            scale.configure(command=lambda v, sid=src.id: self._on_volume(sid, v))
            scale.grid(row=0, column=1, padx=4)
            var = tk.BooleanVar(value=src.muted)
            ttk.Checkbutton(row, text="Mute", variable=var,
                            command=lambda sid=src.id, v=var: self._on_mute(sid, v.get())).grid(row=0, column=2)
            ttk.Button(row, text="x", width=2, command=lambda sid=src.id: self._remove_audio(sid)).grid(
                row=0, column=3, padx=(4, 0))
            meter = tk.Canvas(row, width=240, height=8, bg=FIELD, highlightthickness=0)
            meter.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 2))
            self.meter_canvases[src.id] = meter

    def _draw_meters(self, levels: dict) -> None:
        for sid, canvas in self.meter_canvases.items():
            frac = max(0.0, min(1.0, (levels.get(sid, -100.0) + 60.0) / 60.0))
            frac = max(frac, self.peaks.get(sid, 0.0) - 0.04)           # falls back smoothly
            self.peaks[sid] = frac
            canvas.delete("all")
            width = int(240 * frac)
            green, yellow = int(240 * 0.7), int(240 * 0.9)
            canvas.create_rectangle(0, 0, min(width, green), 8, fill=GREEN, width=0)
            if width > green:
                canvas.create_rectangle(green, 0, min(width, yellow), 8, fill="#e5c13a", width=0)
            if width > yellow:
                canvas.create_rectangle(yellow, 0, width, 8, fill=RED, width=0)

    # --------------------------------------------------------- background work
    def _probe(self) -> None:
        cams, mics = list_devices(self.ffmpeg)
        encoders = working_encoders(self.ffmpeg)
        self.events.put(("devices", cams, mics, encoders))

    def _bg(self, label: str, work, done=None) -> None:
        """Run `work()` off the screen thread; show `label`; call done(result, error) back on it."""
        if self.busy:
            return
        self.busy = True
        self.note = label
        self._set_buttons_enabled(False)

        def run():
            try:
                self.events.put(("done", done, work(), None))
            except Exception as exc:      # shown to the person by `done` or the default handler
                log.exception("background action failed")
                self.events.put(("done", done, None, exc))

        threading.Thread(target=run, daemon=True).start()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in (self.rec_btn, self.stream_btn):
            button.state(["!disabled"] if enabled else ["disabled"])

    def _tick(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "devices":
                    self.webcams, self.audio_inputs = event[1], event[2]
                    self.encoders = event[3]
                    self.session.encoders[:] = self.encoders
                elif event[0] == "record":
                    self._toggle_record()
                elif event[0] == "stream":
                    self._toggle_stream()
                elif event[0] == "done":
                    self._finish_bg(*event[1:])
        except queue.Empty:
            pass
        if self.session is not None:
            self._ticks += 1
            self._draw_meters(self.session.meters())
            if self._ticks % 10 == 0:
                for message in self.session.poll():
                    messagebox.showwarning("JernCast", message[:1500])
                self._update_buttons()
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
        self._draw_preview()

    def _update_buttons(self) -> None:
        s = self.session
        self.rec_btn.configure(text="Stop Recording" if s.recording else "Start Recording",
                               style="On.TButton" if s.recording else "Big.TButton")
        self.stream_btn.configure(text="Stop Streaming" if s.streaming else "Start Streaming",
                                  style="On.TButton" if s.streaming else "Big.TButton")

    def _update_status(self) -> None:
        s = self.session
        parts = []
        if s.streaming:
            parts.append(f"LIVE {format_time(s.stream_seconds())}")
        if s.recording:
            parts.append(f"REC {format_time(s.record_seconds())}")
        parts.append(f"Scene: {self.project.current}")
        if self.note:
            parts.append(self.note)
        self.status.set("    |    ".join(parts))

    # ---------------------------------------------------------------- actions
    def _toggle_record(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.recording:
            def done(path, error):
                self.note = f"Saved: {path.name}" if path else "Nothing was saved."
                if error:
                    messagebox.showerror("JernCast", str(error)[:1500])
            self._bg("Saving...", s.stop_recording, done)
        else:
            self._save_state()
            self._bg("Starting...", s.start_recording, self._started)

    def _toggle_stream(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.streaming:
            self._bg("Stopping the stream...", s.stop_stream, self._started)
        else:
            self._save_state()
            self._bg("Connecting to the stream...", s.start_stream, self._started)

    def _started(self, _result, error) -> None:
        if error is not None:
            messagebox.showerror("JernCast", str(error)[:1500])

    def _manage_broadcast(self) -> None:
        url = manage_broadcast_url(self.settings.stream_server)
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("JernCast", "Manage Broadcast opens your streaming service's control "
                                "page. It knows YouTube and Twitch; set the server in Settings > Stream.")

    def _open_folder(self) -> None:
        target = Path(os.path.expanduser(self.settings.folder))
        target.mkdir(parents=True, exist_ok=True)
        try:
            if IS_WINDOWS:
                os.startfile(str(target))                       # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            log.exception("could not open folder")

    def _save_state(self) -> None:
        self.settings.save()
        self.project.save()

    def _can_edit(self) -> bool:
        if self.session is not None and self.session.running:
            messagebox.showinfo("JernCast", "Stop recording and streaming first to add, remove or "
                                "move sources and scenes. (Switching scenes, hiding sources, "
                                "volume and mute work live.)")
            return False
        return True

    # ----------------------------------------------------------------- scenes
    def _on_scene_select(self) -> None:
        sel = self.scene_list.curselection()
        if not sel or sel[0] >= len(self.project.scenes):
            return
        name = self.project.scenes[sel[0]].name
        if name != self.project.current:
            if self.session is not None:
                self.session.switch_scene(name)
            else:
                self.project.current = name
            self._refresh_sources()
            self._draw_preview()
            self.project.save()

    def _add_scene(self) -> None:
        if not self._can_edit():
            return
        name = simpledialog.askstring("New scene", "Scene name:", parent=self.root)
        if name:
            scene = self.project.add_scene(name)
            self.project.current = scene.name
            self._refresh_all()
            self.project.save()

    def _rename_scene(self) -> None:
        name = simpledialog.askstring("Rename scene", "New name:", initialvalue=self.project.current,
                                      parent=self.root)
        if name:
            self.project.rename_scene(self.project.current, name)
            self._refresh_scenes()
            self._draw_preview()
            self.project.save()

    def _remove_scene(self) -> None:
        if not self._can_edit():
            return
        if len(self.project.scenes) <= 1:
            messagebox.showinfo("JernCast", "You need at least one scene.")
            return
        if messagebox.askyesno("Remove scene", f"Remove the scene \"{self.project.current}\"?"):
            self.project.remove_scene(self.project.current)
            self._refresh_all()
            self.project.save()

    # ---------------------------------------------------------------- sources
    def _add_source(self, kind: str) -> None:
        if not self._can_edit():
            return
        result = SourceDialog(self, kind).show()
        if result is None:
            return
        src = self.project.add_source(kind, result["name"], result["device"])
        if kind == "audio":
            self._rebuild_mixer()
        else:
            self.project.add_item(self.project.current, src.id, layout=result["layout"],
                                  corner=result["corner"], size=result["size"])
            self._refresh_sources(keep=0)
            self._draw_preview()
        self.project.save()

    def _properties(self) -> None:
        index = self._selected_item()
        if index is None:
            return
        if self.session is not None and self.session.running:
            messagebox.showinfo("JernCast", "Stop recording and streaming first to change a source.")
            return
        item = self.project.current_scene.items[index]
        src = self.project.sources[item.source]
        result = SourceDialog(self, src.kind, src, item).show()
        if result is None:
            return
        src.name, src.device = result["name"].strip() or src.name, result["device"]
        item.layout, item.corner, item.size = result["layout"], result["corner"], result["size"]
        self._refresh_sources(keep=len(self.project.current_scene.items) - 1 - index)
        self._draw_preview()
        self.project.save()

    def _remove_source(self) -> None:
        index = self._selected_item()
        if index is None or not self._can_edit():
            return
        self.project.remove_item(self.project.current, index)
        self._refresh_sources(keep=0)
        self._draw_preview()
        self.project.save()

    def _toggle_visible(self) -> None:
        index = self._selected_item()
        if index is None:
            return
        item = self.project.current_scene.items[index]
        position = self.source_list.curselection()[0]
        if self.session is not None:
            self.session.set_visible(self.project.current, index, not item.visible)
        else:
            item.visible = not item.visible
        self._refresh_sources(keep=position)
        self._draw_preview()
        self.project.save()

    def _move_source(self, delta: int) -> None:
        index = self._selected_item()
        if index is None or not self._can_edit():
            return
        new = self.project.move_item(self.project.current, index, delta)
        self._refresh_sources(keep=len(self.project.current_scene.items) - 1 - new)
        self._draw_preview()
        self.project.save()

    # ------------------------------------------------------------------ audio
    def _on_volume(self, source_id: str, value) -> None:
        if self.session is not None:
            self.session.set_volume(source_id, float(value) / 100.0)

    def _on_mute(self, source_id: str, muted: bool) -> None:
        if self.session is not None:
            self.session.set_muted(source_id, muted)
            self.project.save()

    def _remove_audio(self, source_id: str) -> None:
        if not self._can_edit():
            return
        self.project.remove_audio(source_id)
        self._rebuild_mixer()
        self.project.save()

    # --------------------------------------------------------------- settings
    def _open_settings(self) -> None:
        if SettingsDialog(self).show():
            self.settings.save()
            self._set_hotkeys()
            self.note = ("Saved. Picture size, frame rate and quality apply the next time "
                         "you start." if self.session and self.session.running else "")

    def _set_hotkeys(self) -> None:
        for hotkey in self.hotkeys:
            hotkey.stop()
        self.hotkeys = []
        problems = []
        for key, action in ((self.settings.hotkey_record, "record"), (self.settings.hotkey_stream, "stream")):
            hotkey = Hotkey(key, lambda a=action: self.events.put((a,)))
            if not hotkey.start_and_wait() and IS_WINDOWS:
                problems.append(key)
            self.hotkeys.append(hotkey)
        if problems:
            self.note = f"{', '.join(problems)} is used by another program; pick another key in Settings."

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
        self._save_state()
        self.root.destroy()


# ---------------------------------------------------------------------- dialogs

class Dialog:
    """A small modal window."""

    def __init__(self, app: App, title: str):
        self.app = app
        self.result = None
        self.win = tk.Toplevel(app.root)
        self.win.title(title)
        self.win.configure(bg=PANEL)
        self.win.transient(app.root)
        self.body = ttk.Frame(self.win, style="Panel.TFrame", padding=14)
        self.body.pack(fill="both", expand=True)

    def buttons(self, ok_text: str = "OK") -> None:
        row = ttk.Frame(self.win, style="Panel.TFrame", padding=(14, 0, 14, 12))
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
    def __init__(self, app: App, kind: str, src=None, item=None):
        super().__init__(app, ("Properties - " if src else "Add - ") + KIND_LABELS[kind])
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
            "window": ("Window", app.windows),
            "webcam": ("Camera", app.webcams),
            "audio": ("Device", app.audio_inputs),
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
                ttk.Button(b, text="Refresh", command=lambda: combo.configure(values=self._windows())).grid(
                    row=1, column=2)
            if not values and kind != "window":
                ttk.Label(b, text="No devices found yet (the list is filled in a few seconds after "
                          "JernCast starts).", style="Hint.TLabel", wraplength=380).grid(
                    row=2, column=1, columnspan=2, sticky="w", padx=6)

        if kind != "audio":
            ttk.Label(b, text="Layout").grid(row=3, column=0, sticky="w", pady=(10, 4))
            lay = ttk.Frame(b, style="Panel.TFrame")
            lay.grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=(10, 4))
            ttk.Radiobutton(lay, text="Fill the picture", value="fill", variable=self.v_layout).pack(side="left")
            ttk.Radiobutton(lay, text="Small, in a corner", value="corner", variable=self.v_layout).pack(
                side="left", padx=10)
            ttk.Label(b, text="Corner / size").grid(row=4, column=0, sticky="w")
            cs = ttk.Frame(b, style="Panel.TFrame")
            cs.grid(row=4, column=1, columnspan=2, sticky="w", padx=6)
            ttk.Combobox(cs, textvariable=self.v_corner, width=14, state="readonly",
                         values=["bottom-right", "bottom-left", "top-right", "top-left"]).pack(side="left")
            ttk.Combobox(cs, textvariable=self.v_size, width=9, state="readonly",
                         values=["small", "medium", "large"]).pack(side="left", padx=8)
        self.buttons()

    def _windows(self):
        self.app.windows = list_windows()
        return self.app.windows

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
    def __init__(self, app: App):
        super().__init__(app, "Settings")
        s = app.settings
        self.v_folder = tk.StringVar(value=s.folder)
        self.v_canvas = tk.StringVar(value=s.canvas)
        self.v_fps = tk.StringVar(value=str(s.fps))
        self.v_quality = tk.StringVar(value=s.quality)
        self.v_kbps = tk.StringVar(value=str(s.kbps))
        self.v_encoder = tk.StringVar(value=s.encoder)
        self.v_mp4 = tk.BooleanVar(value=s.mp4)
        self.v_server = tk.StringVar(value=s.stream_server)
        self.v_key = tk.StringVar(value=s.stream_key)
        self.v_hk_rec = tk.StringVar(value=s.hotkey_record)
        self.v_hk_stream = tk.StringVar(value=s.hotkey_stream)

        tabs = ttk.Notebook(self.body)
        tabs.pack(fill="both", expand=True)
        out = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        live = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        keys = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        tabs.add(out, text="Output")
        tabs.add(live, text="Stream")
        tabs.add(keys, text="Hotkeys")

        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save recordings in").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(out, textvariable=self.v_folder, width=36).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(out, text="Browse", command=self._browse).grid(row=0, column=2)
        self._row(out, 1, "Picture size", ttk.Combobox(out, textvariable=self.v_canvas, width=12, values=CANVASES))
        self._row(out, 2, "Frame rate", ttk.Combobox(out, textvariable=self.v_fps, width=6, state="readonly",
                                                    values=["24", "30", "60"]))
        self._row(out, 3, "Quality", ttk.Combobox(out, textvariable=self.v_quality, width=10, state="readonly",
                                                 values=["high", "medium", "low"]))
        self._row(out, 4, "Top bitrate (kbps)", ttk.Combobox(out, textvariable=self.v_kbps, width=8,
                                                            values=["2500", "3500", "4500", "6000", "8000"]))
        self._row(out, 5, "Encoder", ttk.Combobox(out, textvariable=self.v_encoder, width=14, state="readonly",
                                                 values=["auto"] + app.encoders))
        ttk.Checkbutton(out, text="Convert recordings to MP4 when finished", variable=self.v_mp4).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(out, text="Smaller picture size, lower frame rate and quality use less CPU "
                  "(good for slow PCs). Your upload speed must be higher than the top bitrate to stream.",
                  style="Hint.TLabel", wraplength=420).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        live.columnconfigure(1, weight=1)
        ttk.Label(live, text="Server").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(live, textvariable=self.v_server, values=SERVERS, width=44).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Label(live, text="Stream key").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(live, textvariable=self.v_key, show="*", width=44).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(live, text="YouTube: Studio > Create > Go live > Stream, copy the Stream key. Treat it like a "
                  "password. Then press Start Streaming, and Manage Broadcast to open YouTube's control page.",
                  style="Hint.TLabel", wraplength=420).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self._row(keys, 0, "Start/stop recording", ttk.Combobox(keys, textvariable=self.v_hk_rec, width=6,
                                                               state="readonly", values=list(HOTKEYS)))
        self._row(keys, 1, "Start/stop streaming", ttk.Combobox(keys, textvariable=self.v_hk_stream, width=6,
                                                               state="readonly", values=list(HOTKEYS)))
        self.buttons("Save")

    @staticmethod
    def _row(parent, row, label, widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        widget.grid(row=row, column=1, sticky="w", padx=6)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self.win, initialdir=self.v_folder.get() or str(Path.home()))
        if chosen:
            self.v_folder.set(chosen)

    def collect(self):
        if self.v_hk_rec.get() == self.v_hk_stream.get():
            messagebox.showinfo("JernCast", "Pick two different hotkeys.", parent=self.win)
            return None
        s = self.app.settings
        s.folder = self.v_folder.get().strip() or s.folder
        s.canvas = self.v_canvas.get().strip()
        s.fps = int(self.v_fps.get() or 30)
        s.quality = self.v_quality.get()
        s.kbps = int(self.v_kbps.get() or 4500)
        s.encoder = self.v_encoder.get()
        s.mp4 = self.v_mp4.get()
        s.stream_server = self.v_server.get().strip()
        s.stream_key = self.v_key.get().strip()
        s.hotkey_record, s.hotkey_stream = self.v_hk_rec.get(), self.v_hk_stream.get()
        return True
