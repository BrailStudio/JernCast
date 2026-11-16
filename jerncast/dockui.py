"""Docks you can drag to another place, hide, show and lock (the window part of the dock layout)."""
from __future__ import annotations

import tkinter as tk

from .docklayout import AREAS, DOCKS, DockLayout, choose_drop

FONT = "Segoe UI"


class DockFrame(tk.Frame):
    """One dock: a header you can drag, and a body for the dock's contents."""

    def __init__(self, host: "DockHost", key: str):
        c = host.colors
        super().__init__(host.root, bg=c["surface"], highlightbackground=c["border"], highlightthickness=1)
        self.host, self.key = host, key
        self.header = tk.Frame(self, bg=c["header"])
        self.header.pack(fill="x")
        self.grip = tk.Label(self.header, text="::", bg=c["header"], fg=c["muted"], font=(FONT, 9, "bold"))
        self.grip.pack(side="left", padx=(8, 2))
        self.title = tk.Label(self.header, text=DOCKS[key], bg=c["header"], fg=c["text"], font=(FONT, 9, "bold"))
        self.title.pack(side="left", pady=5)
        self.close = tk.Label(self.header, text="✕", bg=c["header"], fg=c["muted"], font=(FONT, 9), cursor="hand2")
        self.close.pack(side="right", padx=8)
        self.close.bind("<Button-1>", lambda _e: host.hide(key))
        for widget in (self.header, self.grip, self.title):
            widget.bind("<ButtonPress-1>", lambda e: host.start_drag(self, e))
            widget.bind("<B1-Motion>", lambda e: host.drag(self, e))
            widget.bind("<ButtonRelease-1>", lambda e: host.drop(self, e))
        self.body = tk.Frame(self, bg=c["surface"])
        self.body.pack(fill="both", expand=True, padx=6, pady=6)
        self.set_locked(host.layout.locked)

    def set_locked(self, locked: bool) -> None:
        self.grip.configure(text="" if locked else "::")
        for widget in (self.header, self.grip, self.title):
            widget.configure(cursor="arrow" if locked else "fleur")


class DockHost:
    def __init__(self, root, layout: DockLayout, on_change, colors: dict, scale: float = 1.0):
        self.root, self.layout, self.on_change, self.colors, self.k = root, layout, on_change, colors, scale
        bg = colors["bg"]
        pane = dict(bg=bg, bd=0, sashwidth=int(5 * scale), sashrelief="flat", opaqueresize=True)
        self.outer = tk.PanedWindow(root, orient="vertical", **pane)
        self.top = tk.PanedWindow(self.outer, orient="horizontal", **pane)
        self.areas = {
            "left": tk.Frame(self.top, bg=bg),
            "right": tk.Frame(self.top, bg=bg),
            "bottom": tk.Frame(self.outer, bg=bg),
        }
        self.center = tk.Frame(self.top, bg=bg)
        self.placeholders = {name: tk.Label(frame, text="Drop a dock here", bg=colors["field"], fg=colors["muted"],
                                            font=(FONT, 9)) for name, frame in self.areas.items()}
        self.docks: dict[str, DockFrame] = {}
        self._dragging = None
        self._moved = False
        self._ghost = None

    def add_dock(self, key: str) -> DockFrame:
        dock = DockFrame(self, key)
        self.docks[key] = dock
        return dock

    # -- arranging -------------------------------------------------------------
    def relayout(self, dragging: bool = False) -> None:
        for pane, children in ((self.top, (self.areas["left"], self.center, self.areas["right"])),
                               (self.outer, (self.top, self.areas["bottom"]))):
            for child in children:
                try:
                    pane.forget(child)
                except tk.TclError:
                    pass
        for dock in self.docks.values():
            dock.pack_forget()
        for area in AREAS:
            frame = self.areas[area]
            self.placeholders[area].pack_forget()
            shown = self.layout.visible(area)
            for key in shown:
                dock = self.docks[key]
                if area == "bottom":
                    expand = key != "controls"                       # Controls stays narrow
                    dock.pack(in_=frame, side="left", fill="both", expand=expand, padx=(0, int(6 * self.k)))
                else:
                    dock.pack(in_=frame, side="top", fill="both", expand=True, pady=(0, int(6 * self.k)))
                dock.set_locked(self.layout.locked)
            if not shown and dragging:
                self.placeholders[area].pack(in_=frame, fill="both", expand=True)
        k = self.k
        want = {a: bool(self.layout.visible(a)) or dragging for a in AREAS}
        if want["left"]:
            self.top.add(self.areas["left"], minsize=int(200 * k), width=int(250 * k), stretch="never")
        self.top.add(self.center, minsize=int(260 * k), stretch="always")
        if want["right"]:
            self.top.add(self.areas["right"], minsize=int(200 * k), width=int(250 * k), stretch="never")
        self.outer.add(self.top, minsize=int(180 * k), stretch="always")
        if want["bottom"]:
            self.outer.add(self.areas["bottom"], minsize=int(120 * k), height=int(250 * k), stretch="never")

    # -- hide / show / lock ----------------------------------------------------
    def hide(self, key: str) -> None:
        self.layout.hide(key)
        self.relayout()
        self.on_change()

    def toggle(self, key: str) -> bool:
        shown = self.layout.toggle(key)
        self.relayout()
        self.on_change()
        return shown

    def set_locked(self, locked: bool) -> None:
        self.layout.locked = locked
        for dock in self.docks.values():
            dock.set_locked(locked)
        self.on_change()

    def reset(self) -> None:
        self.layout.reset()
        self.relayout()
        self.on_change()

    # -- dragging ---------------------------------------------------------------
    def start_drag(self, dock: DockFrame, event) -> None:
        if self.layout.locked:
            return
        self._dragging, self._moved = dock, False

    def drag(self, dock: DockFrame, event) -> None:
        if self._dragging is not dock:
            return
        if not self._moved:
            self._moved = True
            self._make_ghost(dock)
            self.relayout(dragging=True)
        if self._ghost is not None:
            self._ghost.geometry(f"+{int(event.x_root) + 14}+{int(event.y_root) + 14}")
        target = self.drop_target(event.x_root, event.y_root)
        for area, frame in self.areas.items():
            frame.configure(bg=self.colors["accent_dim"] if target and target[0] == area else self.colors["bg"])

    def drop(self, dock: DockFrame, event) -> None:
        if self._dragging is not dock:
            return
        moved = self._moved
        target = self.drop_target(event.x_root, event.y_root) if moved else None
        self._dragging, self._moved = None, False
        if self._ghost is not None:
            self._ghost.destroy()
            self._ghost = None
        for frame in self.areas.values():
            frame.configure(bg=self.colors["bg"])
        if moved:
            if target is not None:
                self.layout.move(dock.key, target[0], target[1])
            self.relayout()
            self.on_change()

    def _make_ghost(self, dock: DockFrame) -> None:
        ghost = tk.Toplevel(self.root)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-alpha", 0.85)
            ghost.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(ghost, text=f"  {DOCKS[dock.key]}  ", bg=self.colors["accent"], fg="#ffffff",
                 font=(FONT, 10, "bold"), padx=10, pady=6).pack()
        self._ghost = ghost

    def boxes(self):
        """(area boxes, boxes of the docks in each area) in screen coordinates, the dragged dock left out."""
        def box(widget):
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            return (x, y, x + widget.winfo_width(), y + widget.winfo_height())
        areas = {a: box(f) for a, f in self.areas.items()}
        kids = {a: [box(self.docks[k]) for k in self.layout.visible(a)
                    if self._dragging is None or k != self._dragging.key] for a in AREAS}
        return areas, kids

    def drop_target(self, x, y):
        areas, kids = self.boxes()
        return choose_drop(areas, kids, x, y)
