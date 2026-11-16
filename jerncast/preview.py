"""A preview area: shows the live picture (or a layout sketch) and lets you drag and resize sources on it."""
from __future__ import annotations

import tkinter as tk

from .placement import (box_to_rect, fractions_from_pixels, handle_at, hit_test, move_box, resize_box,
                        snap_box)
from .project import item_box

FONT = "Segoe UI"
KIND_FILL = {"display": "#16314f", "window": "#1c3a3a", "webcam": "#3a2a4f", "image": "#4f3a1c",
             "test_color": "#2a2a2a"}


class PreviewPane:
    def __init__(self, parent, app, *, editable: bool, live: bool, title: str, scene_getter):
        c = app.colors
        self.app, self.editable, self.live, self.scene_getter = app, editable, live, scene_getter
        self.frame = tk.Frame(parent, bg=c["surface"], highlightbackground=c["border"], highlightthickness=1)
        self.title_label = tk.Label(self.frame, text=title, bg=c["surface"], fg=c["muted"], font=(FONT, 9, "bold"))
        if title:
            self.title_label.pack(anchor="w", padx=8, pady=(6, 0))
        self.canvas = tk.Canvas(self.frame, bg="#030304", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.photo = None
        self.mtime = 0.0
        self.live_shown = False
        self.view = (0.0, 0.0, 1.0, 1.0)
        self.drag = None
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        if editable:
            self.canvas.bind("<ButtonPress-1>", self._press)
            self.canvas.bind("<B1-Motion>", self._motion)
            self.canvas.bind("<ButtonRelease-1>", self._release)

    # ---- geometry
    def picture_view(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.live_shown and self.photo is not None:
            w, h = self.photo.width(), self.photo.height()
        else:
            canvas_w, canvas_h = self.app.settings.canvas_size
            aspect = canvas_w / canvas_h
            w = min(cw - 24, int((ch - 24) * aspect))
            h = int(w / aspect)
        return ((cw - w) / 2, (ch - h) / 2, float(w), float(h))

    def _box(self, item):
        cw, ch = self.app.settings.canvas_size
        return item_box(item, cw, ch)

    # ---- drawing
    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        if canvas.winfo_width() < 60 or canvas.winfo_height() < 60:
            return
        self.view = self.picture_view()
        vx, vy, vw, vh = self.view
        c = self.app.colors
        if self.live_shown:
            canvas.create_image(vx + vw / 2, vy + vh / 2, image=self.photo)
        else:
            canvas.create_rectangle(vx, vy, vx + vw, vy + vh, fill="#000000", outline=c["border"])
        self._draw_boxes()

    def _draw_boxes(self) -> None:
        canvas, c = self.canvas, self.app.colors
        scene = self.scene_getter()
        selected = self.app.selected_index() if self.editable else None
        for index, item in enumerate(scene.items):
            if not item.visible:
                continue
            src = self.app.project.sources.get(item.source)
            x0, y0, x1, y1 = box_to_rect(self._box(item), self.view)
            on = index == selected
            if self.live_shown:
                canvas.create_rectangle(x0, y0, x1, y1, outline=c["accent"] if on else "#4d5870",
                                        width=2 if on else 1, dash=() if on else (3, 3))
            else:
                canvas.create_rectangle(x0, y0, x1, y1, fill=KIND_FILL.get(src.kind if src else "", "#222a38"),
                                        outline=c["accent"] if on else "#3b4a63", width=2 if on else 1)
                canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=src.name if src else "", fill=c["text"],
                                   font=(FONT, 10), width=max(40, x1 - x0 - 8))
            if on:
                r = 4 * self.app.k
                for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                    canvas.create_rectangle(hx - r, hy - r, hx + r, hy + r, fill=c["accent"], outline="#ffffff")
        if not self.live_shown and self.editable:
            canvas.create_text(self.view[0] + self.view[2] / 2, self.view[1] + self.view[3] + 12, fill=c["muted"],
                               font=(FONT, 8), text="Drag a source to move it, drag its corners to resize.")

    # ---- the live picture
    def update_live(self, path) -> None:
        if not self.live:
            return
        try:
            if path is not None and path.exists():
                modified = path.stat().st_mtime
                if modified != self.mtime:
                    self.mtime = modified
                    if self.photo is None:
                        self.photo = tk.PhotoImage()
                    self.photo.configure(file=str(path))             # one image, reused: no memory churn
                    if not self.live_shown or self.picture_view() != self.view:
                        self.live_shown = True
                        self.redraw()
            elif self.live_shown:
                self.live_shown, self.photo, self.mtime = False, None, 0.0
                self.redraw()
        except (tk.TclError, OSError):
            pass                                         # the picture was being replaced; try again next time

    # ---- dragging
    def _visible_boxes(self):
        return [(i, self._box(item)) for i, item in enumerate(self.scene_getter().items) if item.visible]

    def _press(self, event) -> None:
        self.drag = None
        scene = self.scene_getter()
        selected = self.app.selected_index()
        radius = 9 * self.app.k
        if selected is not None and 0 <= selected < len(scene.items) and scene.items[selected].visible:
            box = self._box(scene.items[selected])
            handle = handle_at(box_to_rect(box, self.view), event.x, event.y, radius)
            if handle:
                self.drag = ("resize", selected, handle, box, (event.x, event.y))
                return
        hit = hit_test(self._visible_boxes(), event.x, event.y, self.view)
        self.app.select_item(hit)
        if hit is not None:
            self.drag = ("move", hit, None, self._box(scene.items[hit]), (event.x, event.y))
        self.redraw()

    def _motion(self, event) -> None:
        if not self.drag:
            return
        mode, index, handle, start, (sx, sy) = self.drag
        dfx, dfy = fractions_from_pixels(event.x - sx, event.y - sy, self.view)
        if mode == "move":
            box = snap_box(move_box(start, dfx, dfy))
        else:
            box = resize_box(start, handle, dfx, dfy)
        self.app.item_box_changed(index, box, final=False)
        self.redraw()

    def _release(self, event) -> None:
        if not self.drag:
            return
        mode, index, handle, start, _ = self.drag
        self.drag = None
        scene = self.scene_getter()
        if 0 <= index < len(scene.items) and scene.items[index].layout == "free":
            self.app.item_box_changed(index, tuple(scene.items[index].box), final=True)
        self.redraw()
