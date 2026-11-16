"""The JernCast window (0.0.3): dockable Scenes / Sources / Audio Mixer / Controls, dark, light on memory."""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from . import __version__
from .core import (IS_WINDOWS, Settings, assets_dir, cached_working_encoders, data_dir, find_ffmpeg, format_time,
                   list_devices, manage_broadcast_url)
from .dialogs import SettingsDialog, SourceDialog
from .dockui import DockHost
from .docklayout import DOCKS, DockLayout
from .namedstore import NamedStore
from .preview import PreviewPane
from .project import KIND_LABELS, Project, sync_global_audio
from .session import Session
from .sysinfo import CpuMeter, process_memory_mb
from .widgets import MuteButton, Pill, RoundButton, VFader, VLevel, ui_scale
from .winutil import Hotkey, list_monitors

log = logging.getLogger("jerncast")

COLORS = {
    "bg": "#07080b", "surface": "#0f1116", "header": "#161922", "field": "#1a1e29", "border": "#232836",
    "text": "#e4e8f0", "muted": "#7d8699", "accent": "#4c8dff", "accent_dim": "#1c2c4d",
    "rec": "#e5484d", "ok": "#2fbf71", "warn": "#f0a93b", "track": "#2a3040",
}
C = COLORS
FONT = "Segoe UI"
GLOBAL_AUDIO = ("desktop", "mic")
KIND_FILL = {"display": "#16314f", "window": "#1c3a3a", "webcam": "#3a2a4f", "image": "#4f3a1c",
             "test_color": "#2a2a2a"}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.k = ui_scale(root)
        self.colors = COLORS
        self.ffmpeg = find_ffmpeg()
        self.events: queue.Queue = queue.Queue()
        self.monitors = list_monitors()
        self.windows: list[str] = []
        self.webcams: list[str] = []
        self.mics: list[str] = []
        self.encoders = ["libx264"]
        self.profiles = NamedStore(data_dir() / "profiles", legacy=data_dir() / "settings.json")
        self.collections = NamedStore(data_dir() / "collections", legacy=data_dir() / "project.json")
        self.settings = Settings.load(self.profiles.path(self.profiles.current))
        self.project = Project.load(self.collections.path(self.collections.current))
        sync_global_audio(self.project, self.settings)
        self.dock_file = data_dir() / "docks.json"
        self.layout = self._load_docks()
        self.session = (Session(self.ffmpeg, self.settings, self.project, self.monitors, self.encoders)
                        if self.ffmpeg else None)
        self.busy = False
        self.note = ""
        self.dirty = False
        self.hotkeys: list[Hotkey] = []
        self.strips: dict[str, dict] = {}
        self.peaks: dict[str, float] = {}
        self.studio = bool(self.settings.studio_mode)
        self.preview_name = self.project.current
        self._last_box_push = 0.0
        self.cpu = CpuMeter()
        self._ticks = 0
        self._recent_files: list[str] = []

        root.title(f"JernCast {__version__}")
        root.configure(bg=C["bg"])
        root.geometry(f"{int(1120 * self.k)}x{int(780 * self.k)}")
        root.minsize(int(860 * self.k), int(600 * self.k))
        self._set_icon()
        self._style()
        self._build()
        root.attributes("-topmost", bool(self.settings.always_on_top))
        self._refresh_all()
        self._set_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self._exit)

        if self.session is None:
            self.note = "FFmpeg was not found. Put ffmpeg.exe next to JernCast.exe."
            for button in (self.rec_btn, self.live_btn, self.vcam_btn):
                button.state(["disabled"])
        else:
            threading.Thread(target=self._probe, daemon=True).start()
            if self.settings.live_preview:
                self._bg("Starting the preview...", lambda: self.session.set_preview(True), self._started)
        root.after(300, self._tick)

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
        for option, value in (("background", C["field"]), ("foreground", C["text"]), ("selectBackground", C["accent"]),
                              ("font", (FONT, 9))):
            self.root.option_add(f"*TCombobox*Listbox.{option}", value)
        st.configure(".", background=C["surface"], foreground=C["text"], fieldbackground=C["field"],
                     bordercolor=C["border"], lightcolor=C["surface"], darkcolor=C["surface"],
                     troughcolor=C["field"], font=(FONT, 9))
        st.configure("TFrame", background=C["surface"])
        st.configure("TLabel", background=C["surface"], foreground=C["text"])
        st.configure("TCombobox", fieldbackground=C["field"], background=C["field"], foreground=C["text"],
                     arrowcolor=C["muted"], borderwidth=0, padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", C["field"])], foreground=[("readonly", C["text"])])
        st.configure("TEntry", fieldbackground=C["field"], foreground=C["text"], insertcolor=C["text"],
                     borderwidth=0, padding=5)
        st.configure("TCheckbutton", background=C["surface"], foreground=C["text"])
        st.configure("TRadiobutton", background=C["surface"], foreground=C["text"])
        st.configure("TButton", background=C["field"], foreground=C["text"], padding=(10, 4), borderwidth=0)
        st.map("TButton", background=[("active", "#262d3d")])
        st.configure("TNotebook", background=C["surface"], borderwidth=0)
        st.configure("TNotebook.Tab", background=C["header"], foreground=C["muted"], padding=(12, 5))
        st.map("TNotebook.Tab", background=[("selected", C["field"])], foreground=[("selected", C["text"])])

    # ---------------------------------------------------------------- layout
    def _label(self, parent, text="", *, fg=None, bg=None, font=None, **kw):
        return tk.Label(parent, text=text, bg=bg or C["surface"], fg=fg or C["text"], font=font or (FONT, 9), **kw)

    def _tool(self, parent, text, command, *, width=30, bg=None):
        button = RoundButton(parent, text, command, surface=C["surface"], bg=bg or C["field"], fg=C["text"],
                             height=26, width=width, radius=6, font=(FONT, 10, "bold"), scale=self.k)
        button.pack(side="left", padx=(0, 4))
        return button

    def _listbox(self, parent, height=6):
        box = tk.Listbox(parent, height=height, bg=C["field"], fg=C["text"], selectbackground=C["accent"],
                         selectforeground="#ffffff", highlightthickness=0, bd=0, activestyle="none",
                         exportselection=False, font=(FONT, 10))
        box.pack(fill="both", expand=True)
        return box

    def _build(self) -> None:
        root = self.root
        self._build_menubar()
        self._build_statusbar()
        self.host = DockHost(root, self.layout, self._docks_changed, COLORS, self.k)
        self.host.outer.pack(fill="both", expand=True, padx=self.px(6), pady=(self.px(6), 0))
        self._build_preview(self.host.center)
        for key in DOCKS:
            self.host.add_dock(key)
        self._build_scenes(self.host.docks["scenes"].body)
        self._build_sources(self.host.docks["sources"].body)
        self._build_mixer(self.host.docks["mixer"].body)
        self._build_controls(self.host.docks["controls"].body)
        self._build_recent(self.host.docks["recent"].body)
        self.host.relayout()

    # ---- menu bar
    def _build_menubar(self) -> None:
        bar = tk.Frame(self.root, bg=C["header"])
        bar.pack(fill="x")
        if self.icon is not None:
            self.small_icon = self.icon.subsample(max(1, int(11 / self.k)), max(1, int(11 / self.k)))
            tk.Label(bar, image=self.small_icon, bg=C["header"]).pack(side="left", padx=(8, 4))
        for title, builder in (("File", self._menu_file), ("View", self._menu_view), ("Docks", self._menu_docks),
                               ("Profile", self._menu_profile), ("Scene Collection", self._menu_collection),
                               ("Tools", self._menu_tools), ("Help", self._menu_help)):
            label = tk.Label(bar, text=title, bg=C["header"], fg=C["text"], font=(FONT, 9), padx=10, pady=5,
                             cursor="hand2")
            label.pack(side="left")
            label.bind("<Button-1>", lambda _e, b=builder, w=label: self._popup(w, b))
            label.bind("<Enter>", lambda _e, w=label: w.configure(bg=C["field"]))
            label.bind("<Leave>", lambda _e, w=label: w.configure(bg=C["header"]))

    def _new_menu(self):
        return tk.Menu(self.root, tearoff=0, bg=C["header"], fg=C["text"], activebackground=C["accent"],
                       activeforeground="#ffffff", bd=0, relief="flat", font=(FONT, 9))

    def _popup(self, widget, builder) -> None:
        menu = self._new_menu()
        builder(menu)
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    @staticmethod
    def _tick_label(text: str, on: bool) -> str:
        return ("✓  " if on else "      ") + text

    def _menu_file(self, menu) -> None:
        menu.add_command(label="Show Recordings", command=self._open_folder)
        menu.add_command(label="Settings", command=self._open_settings)
        menu.add_separator()
        menu.add_command(label="Exit", command=self._exit)

    def _menu_view(self, menu) -> None:
        menu.add_command(label=self._tick_label("Lock Docks", self.layout.locked), command=self._toggle_lock)
        menu.add_command(label="      Reset Docks", command=self.host.reset)
        menu.add_separator()
        menu.add_command(label=self._tick_label("Live Preview", self.settings.live_preview),
                         command=self._toggle_live_preview)
        menu.add_command(label=self._tick_label("Studio Mode", self.studio), command=self._toggle_studio)
        menu.add_command(label=self._tick_label("Always on Top", self.settings.always_on_top),
                         command=self._toggle_on_top)

    def _menu_docks(self, menu) -> None:
        for key, title in DOCKS.items():
            menu.add_command(label=self._tick_label(title, self.layout.is_visible(key)),
                             command=lambda k=key: self.host.toggle(k))

    def _named_menu(self, menu, store: NamedStore, switch, new, rename, remove) -> None:
        for name in store.names():
            menu.add_command(label=("●  " if name == store.current else "      ") + name,
                             command=lambda n=name: switch(n))
        menu.add_separator()
        menu.add_command(label="      New", command=new)
        menu.add_command(label="      Rename", command=rename)
        menu.add_command(label="      Remove", command=remove)

    def _menu_profile(self, menu) -> None:
        self._named_menu(menu, self.profiles, self._switch_profile, lambda: self._store_new(self.profiles, True),
                         lambda: self._store_rename(self.profiles), lambda: self._store_remove(self.profiles))

    def _menu_collection(self, menu) -> None:
        self._named_menu(menu, self.collections, self._switch_collection,
                         lambda: self._store_new(self.collections, False),
                         lambda: self._store_rename(self.collections), lambda: self._store_remove(self.collections))

    def _menu_tools(self, menu) -> None:
        menu.add_command(label="Open Logs Folder", command=self._open_logs)
        menu.add_command(label="Show Recordings", command=self._open_folder)

    def _menu_help(self, menu) -> None:
        menu.add_command(label="About JernCast", command=self._about)

    # ---- status bar
    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=C["header"])
        bar.pack(fill="x", side="bottom")
        self.pill = Pill(bar, "Ready", C["ok"], surface=C["header"], scale=self.k)
        self.pill.pack(side="left", padx=8, pady=4)
        self.note_label = tk.Label(bar, text="", bg=C["header"], fg=C["muted"], font=(FONT, 9))
        self.note_label.pack(side="left")
        self.stats = tk.Label(bar, text="", bg=C["header"], fg=C["muted"], font=(FONT, 9))
        self.stats.pack(side="right", padx=10)

    # ---- preview (centre)
    def _build_preview(self, parent) -> None:
        box = tk.Frame(parent, bg=C["bg"])
        box.pack(fill="both", expand=True, padx=(0, self.px(6)), pady=(0, self.px(6)))
        self.panes = tk.Frame(box, bg=C["bg"])
        self.panes.pack(fill="both", expand=True)
        self.edit_pane = PreviewPane(self.panes, self, editable=True, live=not self.studio, title="",
                                     scene_getter=self._edit_scene)
        self.program_pane = PreviewPane(self.panes, self, editable=False, live=True, title="PROGRAM (live)",
                                        scene_getter=lambda: self.project.scene(self.project.current))
        strip = tk.Frame(box, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        strip.pack(fill="x", pady=(6, 0))
        inner = tk.Frame(strip, bg=C["surface"])
        inner.pack(fill="x", padx=8, pady=6)
        self.selected_label = self._label(inner, "No source selected", fg=C["muted"])
        self.selected_label.pack(side="left")
        self.preview_btn = RoundButton(inner, "Live preview: off", self._toggle_live_preview, surface=C["surface"],
                                       bg=C["field"], fg=C["text"], height=26, width=130, radius=6,
                                       font=(FONT, 9, "bold"), scale=self.k)
        self.preview_btn.pack(side="right")
        RoundButton(inner, "Properties", self._properties, surface=C["surface"], bg=C["field"], fg=C["text"],
                    height=26, width=90, radius=6, font=(FONT, 9, "bold"), scale=self.k).pack(side="right", padx=6)
        self.transition_btn = RoundButton(inner, "Transition  ▶", self._transition, surface=C["surface"],
                                          bg=C["accent"], fg="#ffffff", height=26, width=110, radius=6,
                                          font=(FONT, 9, "bold"), scale=self.k)
        self._layout_panes()

    def _layout_panes(self) -> None:
        """One preview normally; in Studio Mode a Preview (edit) next to the Program (live) with a Transition button."""
        for pane in (self.edit_pane, self.program_pane):
            pane.frame.pack_forget()
        self.edit_pane.title_label.pack_forget()
        self.transition_btn.pack_forget()
        if self.studio:
            self.edit_pane.title_label.configure(text="PREVIEW (edit the next scene)")
            self.edit_pane.title_label.pack(anchor="w", padx=8, pady=(6, 0), before=self.edit_pane.canvas)
            self.edit_pane.frame.pack(side="left", fill="both", expand=True, padx=(0, 3))
            self.program_pane.frame.pack(side="left", fill="both", expand=True, padx=(3, 0))
            self.transition_btn.pack(side="right", padx=(0, 6))
        else:
            self.edit_pane.frame.pack(fill="both", expand=True)

    @property
    def live_image_shown(self) -> bool:
        return self.edit_pane.live_shown or (self.studio and self.program_pane.live_shown)

    def _draw_layout_preview(self) -> None:
        self.edit_pane.redraw()
        if self.studio:
            self.program_pane.redraw()

    # ---- which scene is being edited, and which source is selected
    @property
    def edit_name(self) -> str:
        names = [s.name for s in self.project.scenes]
        if self.studio and self.preview_name in names:
            return self.preview_name
        return self.project.current

    def _edit_scene(self):
        return self.project.scene(self.edit_name)

    def selected_index(self):
        return self._selected_item()

    def select_item(self, index) -> None:
        self.source_list.selection_clear(0, "end")
        if index is not None:
            self.source_list.selection_set(len(self._edit_scene().items) - 1 - index)      # the list shows front first
        self._update_selected_label()
        self._draw_layout_preview()

    def item_box_changed(self, index: int, box, final: bool) -> None:
        """A source was dragged or resized in the preview. Live changes are sent to the running engine, a few a second."""
        scene = self._edit_scene()
        item = scene.items[index]
        item.layout, item.box = "free", [float(v) for v in box]
        now = time.monotonic()
        if self.session is not None and (final or now - self._last_box_push > 0.4):
            self._last_box_push = now
            self.session.set_item_box(scene.name, index, box)
        if final:
            self.dirty = True

    # ---- Studio Mode
    def _toggle_studio(self) -> None:
        self.studio = not self.studio
        self.settings.studio_mode = self.studio
        self.preview_name = self.project.current
        self.edit_pane.live = not self.studio
        self.edit_pane.live_shown, self.edit_pane.photo, self.edit_pane.mtime = False, None, 0.0
        self._layout_panes()
        self._refresh_scenes()
        self._refresh_sources()
        self._draw_layout_preview()
        self._save()

    def _transition(self) -> None:
        """Send the Preview scene live (a cut), and keep the old Program scene in Preview so you can go back."""
        if not self.studio:
            return
        old, new = self.project.current, self.preview_name
        if new == old or new not in [s.name for s in self.project.scenes]:
            self.note = "Pick a different scene to transition to."
            return
        self._select_scene(new)
        self.preview_name = old
        self._refresh_scenes()
        self._refresh_sources()
        self._draw_layout_preview()

    # ---- Scenes dock
    def _build_scenes(self, body) -> None:
        bar = tk.Frame(body, bg=C["surface"])
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.scene_list = self._listbox(body)
        self.scene_list.bind("<<ListboxSelect>>", lambda _e: self._on_scene_select())
        self.scene_list.bind("<Double-Button-1>", lambda _e: self._rename_scene())
        self.scene_list.bind("<Button-3>", lambda e: self._context(e, self.scene_list, self._scene_menu))
        self._tool(bar, "+", self._add_scene)
        self._tool(bar, "−", self._remove_scene)
        self._tool(bar, "▲", lambda: self._move_scene(-1))
        self._tool(bar, "▼", lambda: self._move_scene(1))

    def _scene_menu(self, menu) -> None:
        menu.add_command(label="Rename", command=self._rename_scene)
        menu.add_command(label="Move up", command=lambda: self._move_scene(-1))
        menu.add_command(label="Move down", command=lambda: self._move_scene(1))
        menu.add_separator()
        menu.add_command(label="Remove", command=self._remove_scene)

    # ---- Sources dock
    def _build_sources(self, body) -> None:
        bar = tk.Frame(body, bg=C["surface"])
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.source_list = self._listbox(body)
        self.source_list.bind("<ButtonRelease-1>", self._source_clicked)
        self.source_list.bind("<Double-Button-1>", lambda _e: self._properties())
        self.source_list.bind("<Button-3>", lambda e: self._context(e, self.source_list, self._source_menu))
        self.add_btn = self._tool(bar, "+", self._add_menu)
        self._tool(bar, "−", self._remove_source)
        self._tool(bar, "⚙", self._properties)
        self._tool(bar, "▲", lambda: self._move_source(1))
        self._tool(bar, "▼", lambda: self._move_source(-1))

    def _source_menu(self, menu) -> None:
        menu.add_command(label="Properties", command=self._properties)
        menu.add_command(label="Show / Hide", command=self._toggle_visible)
        menu.add_command(label="Move up", command=lambda: self._move_source(1))
        menu.add_command(label="Move down", command=lambda: self._move_source(-1))
        menu.add_separator()
        menu.add_command(label="Remove", command=self._remove_source)

    def _add_menu(self) -> None:
        menu = self._new_menu()
        for kind in ("display", "window", "webcam", "image"):
            menu.add_command(label=KIND_LABELS[kind], command=lambda k=kind: self._add_source(k))
        menu.add_command(label="Audio input (mixer)", command=lambda: self._add_source("audio"))
        try:
            menu.tk_popup(self.add_btn.winfo_rootx(), self.add_btn.winfo_rooty() + self.add_btn.winfo_height())
        finally:
            menu.grab_release()

    def _context(self, event, listbox, builder) -> None:
        index = listbox.nearest(event.y)
        if index is not None and index >= 0:
            listbox.selection_clear(0, "end")
            listbox.selection_set(index)
            listbox.event_generate("<<ListboxSelect>>")
        menu = self._new_menu()
        builder(menu)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---- Audio Mixer dock
    def _build_mixer(self, body) -> None:
        self.mixer_row = tk.Frame(body, bg=C["surface"])
        self.mixer_row.pack(fill="both", expand=True)

    def _rebuild_mixer(self) -> None:
        for child in self.mixer_row.winfo_children():
            child.destroy()
        self.strips = {}
        audios = self.project.audio_sources()
        for src in sorted(audios, key=lambda s: (s.id not in GLOBAL_AUDIO,
                                                 GLOBAL_AUDIO.index(s.id) if s.id in GLOBAL_AUDIO else 0, s.id)):
            strip = tk.Frame(self.mixer_row, bg=C["field"], padx=8, pady=6)
            strip.pack(side="left", fill="y", padx=(0, 6))
            self._label(strip, src.name, bg=C["field"], font=(FONT, 9, "bold")).pack()
            mid = tk.Frame(strip, bg=C["field"])
            mid.pack(pady=4)
            level = VLevel(mid, surface=C["field"], track="#0a0c12", height=110, scale=self.k)
            level.pack(side="left", padx=(0, 6))
            fader = VFader(mid, int(src.volume * 100), lambda v, sid=src.id: self._on_volume(sid, v),
                           surface=C["field"], track=C["track"], accent=C["accent"], height=110, scale=self.k)
            fader.pack(side="left")
            var = tk.BooleanVar(value=src.muted)
            row = tk.Frame(strip, bg=C["field"])
            row.pack()
            MuteButton(row, var, lambda sid=src.id, v=var: self._on_mute(sid, v.get()), surface=C["field"],
                       scale=self.k).pack(side="left")
            if src.id not in GLOBAL_AUDIO:
                x = tk.Label(row, text="✕", bg=C["field"], fg=C["muted"], cursor="hand2", font=(FONT, 9))
                x.pack(side="left", padx=(8, 0))
                x.bind("<Button-1>", lambda _e, sid=src.id: self._remove_audio(sid))
            self.strips[src.id] = {"level": level, "fader": fader}
        if not audios:
            self._label(self.mixer_row, "No audio yet. Choose Desktop Audio and Mic/Aux in Settings > Audio,\n"
                        "or add an Audio input.", fg=C["muted"], justify="left").pack(side="left", anchor="n")
        RoundButton(self.mixer_row, "+ Audio input", lambda: self._add_source("audio"), surface=C["surface"],
                    bg=C["field"], fg=C["text"], height=26, width=100, radius=6, font=(FONT, 9, "bold"),
                    scale=self.k).pack(side="left", anchor="n", padx=(4, 0))

    # ---- Controls dock
    def _build_controls(self, body) -> None:
        def big(text, command):
            button = RoundButton(body, text, command, surface=C["surface"], bg=C["field"], fg=C["text"], height=28,
                                 width=190, radius=7, font=(FONT, 9, "bold"), scale=self.k)
            button.pack(fill="x", pady=1)
            return button
        self.live_btn = big("Start Streaming", self._toggle_live)
        self.rec_btn = big("Start Recording", self._toggle_record)
        self.vcam_btn = big("Start Virtual Camera", self._toggle_vcam)
        self.studio_btn = big("Studio Mode", self._toggle_studio)
        big("Manage Broadcast", self._manage_broadcast)
        big("Settings", self._open_settings)
        big("Exit", self._exit)

    # ---- Recent recordings dock
    def _build_recent(self, body) -> None:
        bar = tk.Frame(body, bg=C["surface"])
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.recent_list = self._listbox(body, height=5)
        self.recent_list.bind("<Double-Button-1>", lambda _e: self._open_recent())
        for text, command in (("Open", self._open_recent), ("Folder", self._open_folder)):
            RoundButton(bar, text, command, surface=C["surface"], bg=C["field"], fg=C["text"], height=26, width=70,
                        radius=6, font=(FONT, 9, "bold"), scale=self.k).pack(side="left", padx=(0, 4))

    # ----------------------------------------------------------- refreshing
    def _refresh_all(self) -> None:
        if self.preview_name not in [s.name for s in self.project.scenes]:
            self.preview_name = self.project.current
        self._refresh_scenes()
        self._refresh_sources()
        self._rebuild_mixer()
        self._refresh_recent()
        self._refresh_preview_button()
        self._draw_layout_preview()

    def _refresh_scenes(self) -> None:
        self.scene_list.delete(0, "end")
        for scene in self.project.scenes:
            live = self.studio and scene.name == self.project.current
            self.scene_list.insert("end", ("▶  " if live else "") + scene.name)
        self.scene_list.selection_clear(0, "end")
        self.scene_list.selection_set([s.name for s in self.project.scenes].index(self.edit_name))

    def _items_front_first(self):
        return list(reversed(list(enumerate(self._edit_scene().items))))

    def _refresh_sources(self, keep=None) -> None:
        self.source_list.delete(0, "end")
        for _ii, item in self._items_front_first():
            src = self.project.sources[item.source]
            self.source_list.insert("end", f"{'◉' if item.visible else '○'}   {src.name}")
        if keep is not None and self.source_list.size() > 0:
            self.source_list.selection_set(min(keep, self.source_list.size() - 1))
        self._update_selected_label()

    def _selected_item(self):
        sel = self.source_list.curselection()
        order = self._items_front_first()
        return order[sel[0]][0] if sel and sel[0] < len(order) else None

    def _update_selected_label(self) -> None:
        index = self._selected_item()
        if index is None:
            self.selected_label.configure(text="No source selected")
        else:
            src = self.project.sources[self._edit_scene().items[index].source]
            self.selected_label.configure(text=src.name)

    def _refresh_recent(self) -> None:
        self.recent_list.delete(0, "end")
        files = [p for p in self.settings.recent if os.path.exists(p)]
        for p in files:
            self.recent_list.insert("end", Path(p).name)
        if not files:
            self.recent_list.insert("end", "Nothing recorded yet.")
        self._recent_files = files

    def _refresh_preview_button(self) -> None:
        self.preview_btn.style(text="Live preview: on" if self.settings.live_preview else "Live preview: off",
                               bg=C["accent"] if self.settings.live_preview else C["field"])

    # ------------------------------------------------------------ docks / view
    def _load_docks(self) -> DockLayout:
        try:
            return DockLayout.from_dict(json.loads(self.dock_file.read_text(encoding="utf-8")))
        except Exception:
            return DockLayout()

    def _docks_changed(self) -> None:
        try:
            self.dock_file.write_text(json.dumps(self.layout.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            log.exception("could not save the dock layout")

    def _toggle_lock(self) -> None:
        self.host.set_locked(not self.layout.locked)
        self.note = "Docks are locked." if self.layout.locked else "Docks can be dragged by their title bar."

    def _toggle_on_top(self) -> None:
        self.settings.always_on_top = not self.settings.always_on_top
        self.root.attributes("-topmost", self.settings.always_on_top)
        self._save()

    def _toggle_live_preview(self) -> None:
        if self.session is None:
            return
        self.settings.live_preview = not self.settings.live_preview
        wanted = self.settings.live_preview
        self._refresh_preview_button()
        self._save()
        if wanted and not self.session.running:
            self._bg("Starting the preview...", lambda: self.session.set_preview(True), self._started)
        elif not wanted and self.session.preview_wanted:
            self._bg("", lambda: self.session.set_preview(False), self._started)
        else:
            self.session.preview_wanted = wanted

    def _draw_preview(self) -> None:
        """Show the real picture where there is one (the engine is running), else the layout sketch."""
        s = self.session
        path = s.preview_path() if s is not None else None
        self.edit_pane.update_live(path)
        if self.studio:
            self.program_pane.update_live(path)

    # --------------------------------------------------------- scenes
    def _on_scene_select(self) -> None:
        sel = self.scene_list.curselection()
        if not sel or sel[0] >= len(self.project.scenes):
            return
        name = self.project.scenes[sel[0]].name
        if self.studio:                                   # in Studio Mode a click chooses what to edit (Preview)
            if name != self.preview_name:
                self.preview_name = name
                self._refresh_sources()
                self._draw_layout_preview()
        elif name != self.project.current:
            self._select_scene(name)

    def _select_scene(self, name: str) -> None:
        if self.session is not None:
            self.session.switch_scene(name)               # live: also changes what is recorded / streamed
        else:
            self.project.current = name
        self._refresh_scenes()
        self._refresh_sources()
        self._draw_layout_preview()
        self.dirty = True

    def _select_scene_number(self, number: int) -> None:
        if 1 <= number <= len(self.project.scenes):
            name = self.project.scenes[number - 1].name
            if self.studio:
                self.preview_name = name
                self._refresh_scenes()
                self._refresh_sources()
                self._draw_layout_preview()
            else:
                self._select_scene(name)

    def _add_scene(self) -> None:
        if not self._can_edit():
            return
        name = simpledialog.askstring("New scene", "Scene name:", parent=self.root)
        if name:
            scene = self.project.add_scene(name)
            self.project.current = scene.name
            self._refresh_all()
            self._save()

    def _rename_scene(self) -> None:
        name = simpledialog.askstring("Rename scene", "New name:", initialvalue=self.edit_name, parent=self.root)
        if name:
            was_preview = self.edit_name == self.preview_name
            new = self.project.rename_scene(self.edit_name, name)
            if was_preview:
                self.preview_name = new
            self._refresh_scenes()
            self._save()

    def _remove_scene(self) -> None:
        if not self._can_edit():
            return
        if len(self.project.scenes) <= 1:
            messagebox.showinfo("JernCast", "You need at least one scene.")
            return
        if messagebox.askyesno("Remove scene", f"Remove the scene \"{self.edit_name}\"?"):
            self.project.remove_scene(self.edit_name)
            self._refresh_all()
            self._save()

    def _move_scene(self, delta: int) -> None:
        if not self._can_edit():
            return
        self.project.move_scene(self.edit_name, delta)
        self._refresh_scenes()
        self._save()

    # --------------------------------------------------------- sources
    def _can_edit(self) -> bool:
        if self.session is not None and (self.session.recording or self.session.streaming):
            messagebox.showinfo("JernCast", "Stop recording and streaming first to add, remove or move scenes "
                                "and sources. (Switching scenes, showing or hiding sources, volume and mute "
                                "work live.)")
            return False
        return True

    def _restart_idle_engine(self) -> None:
        """Only the preview is running: restart it so it sees the changed sources."""
        s = self.session
        if s is not None and s.running and not (s.recording or s.streaming):
            wanted = self.settings.live_preview
            self._bg("", lambda: (s.set_preview(False), wanted and s.set_preview(True)), self._started)

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
            self.project.add_item(self.edit_name, src.id, layout=result["layout"],
                                  corner=result["corner"], size=result["size"])
            self._refresh_sources(keep=0)
            self._draw_layout_preview()
        self._save()
        self._restart_idle_engine()

    def _properties(self) -> None:
        index = self._selected_item()
        if index is None:
            return
        if not self._can_edit():
            return
        item = self._edit_scene().items[index]
        src = self.project.sources[item.source]
        result = SourceDialog(self, src.kind, src, item).show()
        if result is None:
            return
        src.name, src.device = result["name"].strip() or src.name, result["device"]
        item.layout, item.corner, item.size = result["layout"], result["corner"], result["size"]
        self._refresh_sources(keep=len(self._edit_scene().items) - 1 - index)
        self._draw_layout_preview()
        self._save()
        self._restart_idle_engine()

    def _remove_source(self) -> None:
        index = self._selected_item()
        if index is None or not self._can_edit():
            return
        self.project.remove_item(self.edit_name, index)
        self._refresh_sources(keep=0)
        self._draw_layout_preview()
        self._save()
        self._restart_idle_engine()

    def _source_clicked(self, event) -> None:
        """Clicking the dot at the left of a source shows or hides it (works live)."""
        self._update_selected_label()
        if event.x < self.px(30):
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        index = self._selected_item()
        if index is None:
            return
        item = self._edit_scene().items[index]
        position = self.source_list.curselection()[0]
        if self.session is not None:
            self.session.set_visible(self.edit_name, index, not item.visible)
        else:
            item.visible = not item.visible
        self._refresh_sources(keep=position)
        self._draw_layout_preview()
        self.dirty = True

    def _move_source(self, delta: int) -> None:
        index = self._selected_item()
        if index is None or not self._can_edit():
            return
        new = self.project.move_item(self.edit_name, index, delta)
        self._refresh_sources(keep=len(self._edit_scene().items) - 1 - new)
        self._draw_layout_preview()
        self._save()

    # ----------------------------------------------------------- audio
    def _on_volume(self, source_id: str, value) -> None:
        if self.session is not None:
            self.session.set_volume(source_id, float(value) / 100.0)
        self.dirty = True

    def _on_mute(self, source_id: str, muted: bool) -> None:
        if self.session is not None:
            self.session.set_muted(source_id, muted)
        self.dirty = True

    def _remove_audio(self, source_id: str) -> None:
        if not self._can_edit():
            return
        self.project.remove_audio(source_id)
        self._rebuild_mixer()
        self._save()
        self._restart_idle_engine()

    # ---------------------------------------------------- settings, profiles
    def _open_settings(self) -> None:
        capturing = self.session is not None and (self.session.recording or self.session.streaming)
        if SettingsDialog(self).show():
            if not capturing:
                sync_global_audio(self.project, self.settings)
            self._save()
            self._rebuild_mixer()
            self._set_hotkeys()
            self.note = "Saved. Most changes apply the next time you start." if capturing else "Settings saved."
            self._restart_idle_engine()

    def _save(self) -> None:
        self.settings.save(self.profiles.path(self.profiles.current))
        self.project.save(self.collections.path(self.collections.current))
        self.dirty = False

    def _guard_idle(self) -> bool:
        if self.session is not None and self.session.running:
            messagebox.showinfo("JernCast", "Stop recording, streaming and the live preview first.")
            return False
        return True

    def _after_profile_change(self) -> None:
        if self.session is not None:
            self.session.settings = self.settings
        sync_global_audio(self.project, self.settings)
        self._set_hotkeys()
        self.root.attributes("-topmost", bool(self.settings.always_on_top))
        self._refresh_all()

    def _switch_profile(self, name: str) -> None:
        if not self._guard_idle() or name == self.profiles.current:
            return
        self._save()
        self.profiles.set_current(name)
        self.settings = Settings.load(self.profiles.path(name))
        self._after_profile_change()
        self._save()
        self.note = f"Profile: {name}"

    def _switch_collection(self, name: str) -> None:
        if not self._guard_idle() or name == self.collections.current:
            return
        self._save()
        self.collections.set_current(name)
        self.project = Project.load(self.collections.path(name))
        sync_global_audio(self.project, self.settings)
        if self.session is not None:
            self.session.project = self.project
        self._refresh_all()
        self._save()
        self.note = f"Scene collection: {name}"

    def _store_new(self, store: NamedStore, is_profile: bool) -> None:
        if not self._guard_idle():
            return
        name = simpledialog.askstring("New", "Name:", parent=self.root)
        if not name:
            return
        self._save()
        made = store.create(name, copy_from=store.current if is_profile else None)
        if not is_profile:                                   # a new collection starts with one empty scene
            Project.default().save(store.path(made))
        (self._switch_profile if is_profile else self._switch_collection)(made)

    def _store_rename(self, store: NamedStore) -> None:
        name = simpledialog.askstring("Rename", "New name:", initialvalue=store.current, parent=self.root)
        if name:
            self._save()
            store.rename(store.current, name)

    def _store_remove(self, store: NamedStore) -> None:
        if len(store.names()) <= 1:
            messagebox.showinfo("JernCast", "You need at least one.")
            return
        if not self._guard_idle():
            return
        if messagebox.askyesno("Remove", f"Remove \"{store.current}\"?"):
            store.delete(store.current)
            if store is self.profiles:
                self.settings = Settings.load(store.path(store.current))
                self._after_profile_change()
            else:
                self.project = Project.load(store.path(store.current))
                sync_global_audio(self.project, self.settings)
                if self.session is not None:
                    self.session.project = self.project
                self._refresh_all()

    # --------------------------------------------------------- background work
    def _probe(self) -> None:
        cams, mics = list_devices(self.ffmpeg)
        encoders = cached_working_encoders(self.ffmpeg)
        self.events.put(("devices", cams, mics, encoders))

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
        for button in (self.rec_btn, self.live_btn, self.vcam_btn):
            button.state(["!disabled"] if enabled else ["disabled"])

    def _tick(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "devices":
                    self.webcams, self.mics, self.encoders = event[1], event[2], event[3]
                    if self.session is not None:
                        self.session.encoders[:] = self.encoders
                elif kind == "record":
                    self._toggle_record()
                elif kind == "stream":
                    self._toggle_live()
                elif kind == "scene":
                    self._select_scene_number(event[1])
                elif kind == "transition":
                    self._transition()
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
            elif self.live_image_shown:
                self._draw_preview()
            if self._ticks % (10 if active else 2) == 0:
                for message in self.session.poll():
                    messagebox.showwarning("JernCast", message[:1500])
                self._update_buttons()
                self._check_duration()
                self._update_stats()
                if self.dirty:
                    self._save()
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
        s, limit = self.session, self.settings.duration_min * 60
        if s.recording and limit and s.record_seconds() >= limit and not getattr(self, "_auto_stopped", False) \
                and not self.busy:
            self._auto_stopped = True
            self._toggle_record()

    def _update_buttons(self) -> None:
        s = self.session
        self.rec_btn.style(text="Stop Recording" if s.recording else "Start Recording",
                           bg="#7a2a30" if s.recording else C["field"])
        self.live_btn.style(text="Stop Streaming" if s.streaming else "Start Streaming",
                            bg=C["accent"] if s.streaming else C["field"])
        self.vcam_btn.style(text="Stop Virtual Camera" if s.vcam_running else "Start Virtual Camera",
                            bg=C["accent"] if s.vcam_running else C["field"])
        self.studio_btn.style(text="Studio Mode: on" if self.studio else "Studio Mode",
                              bg=C["accent"] if self.studio else C["field"])

    def _update_status(self) -> None:
        s = self.session
        if s.streaming and s.recording:
            text, color = f"LIVE + REC  {format_time(s.stream_seconds())}", C["rec"]
        elif s.streaming:
            text, color = f"LIVE  {format_time(s.stream_seconds())}", C["rec"]
        elif s.recording:
            text, color = f"REC  {format_time(s.record_seconds())}", C["rec"]
        elif s.vcam_running:
            text, color = "CAMERA ON", C["accent"]
        else:
            text, color = "Ready", C["ok"]
        self.pill.set(text, color)
        self.note_label.configure(text=self.note or ("Virtual camera is on." if s.vcam_running else ""))

    def _update_stats(self) -> None:
        s = self.session
        pids = [os.getpid()] + s.pids()
        self.stats.configure(text=f"CPU {self.cpu.percent():.1f}%     RAM {process_memory_mb(pids):.0f} MB     "
                                  f"{self.settings.fps} fps")

    def _draw_meters(self, levels: dict) -> None:
        for sid, parts in self.strips.items():
            frac = max(0.0, min(1.0, (levels.get(sid, -100.0) + 60.0) / 60.0))
            frac = max(frac, self.peaks.get(sid, 0.0) - 0.05)
            self.peaks[sid] = frac
            parts["level"].set(frac)

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
            self._auto_stopped = False
            self._bg("Starting...", s.start_recording, self._started)

    def _toggle_vcam(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.vcam_running:
            self._bg("Stopping the virtual camera...", s.stop_vcam, self._started)
        else:
            self._bg("Starting the virtual camera...", s.start_vcam, self._started)

    def _toggle_live(self) -> None:
        if self.session is None or self.busy:
            return
        s = self.session
        if s.streaming:
            self._bg("Stopping the stream...", s.stop_stream, self._started)
        else:
            self._bg("Connecting to the stream...", s.start_stream, self._started)

    def _started(self, _result, error) -> None:
        if error is not None:
            messagebox.showerror("JernCast", str(error)[:1500])

    def _remember_recording(self, path: Path) -> None:
        recent = [str(path)] + [p for p in self.settings.recent if p != str(path)]
        self.settings.recent = recent[:8]
        self._save()
        self._refresh_recent()

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
                                "It knows YouTube and Twitch; choose one in Settings > Stream.")

    def _about(self) -> None:
        messagebox.showinfo("About JernCast", f"JernCast {__version__}\n\nA simple screen recorder and live "
                            "streamer for Windows.\nMIT licensed. Recording and streaming are done by FFmpeg.\n"
                            "JernCast is its own program and is not affiliated with OBS Studio.")

    def _set_hotkeys(self) -> None:
        for hotkey in self.hotkeys:
            hotkey.stop()
        self.hotkeys = []
        wanted = [(self.settings.hotkey_record, ("record",)), (self.settings.hotkey_stream, ("stream",))]
        wanted += [(f"Ctrl+Alt+{n}", ("scene", n)) for n in range(1, 10)]
        wanted.append(("Ctrl+Alt+0", ("transition",)))             # Studio Mode: send Preview live
        problems = []
        for key, event in wanted:
            hotkey = Hotkey(key, lambda e=event: self.events.put(e))
            if not hotkey.start_and_wait() and IS_WINDOWS:
                problems.append(key)
            self.hotkeys.append(hotkey)
        if problems:
            self.note = f"{', '.join(problems)} is used by another program."

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
            self.settings.studio_mode = self.studio
            self._save()
            self._docks_changed()
        finally:
            self.root.destroy()
