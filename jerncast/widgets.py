"""Small hand-drawn widgets (rounded buttons, switches, sliders, level bars).

Each is one lightweight Canvas, so they cost almost no memory. Sizes are multiplied
by `scale` so they stay the right size on high-resolution screens.
"""
from __future__ import annotations

import tkinter as tk


def rounded(x1, y1, x2, y2, r):
    """Corner points for a rounded rectangle polygon (draw it with smooth=True)."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
            x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def mix(color_a: str, color_b: str, amount: float) -> str:
    """Blend two #rrggbb colours (amount 0 = a, 1 = b)."""
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def ui_scale(root) -> float:
    try:
        return max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:
        return 1.0


class RoundButton(tk.Canvas):
    def __init__(self, parent, text, command=None, *, surface, bg, fg="#ffffff", hover=None,
                 outline=None, height=38, width=120, radius=10, font=("Segoe UI", 10, "bold"), scale=1.0):
        self._s = scale
        super().__init__(parent, width=int(width * scale), height=int(height * scale), bg=surface,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.text, self.bg, self.fg = text, bg, fg
        self.hover_bg = hover or mix(bg, "#ffffff", 0.12)
        self.outline = outline
        self.radius = int(radius * scale)
        self.font = font
        self.enabled = True
        self._over = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self.invoke())
        self._draw()

    def _draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            w, h = int(self["width"]), int(self["height"])
        fill = self.bg if self.enabled else mix(self.bg, "#101114", 0.6)
        if self._over and self.enabled:
            fill = self.hover_bg
        text_color = self.fg if self.enabled else mix(self.fg, "#101114", 0.55)
        self.delete("all")
        self.create_polygon(rounded(1, 1, w - 1, h - 1, self.radius), smooth=True, fill=fill,
                            outline=self.outline or fill, width=1)
        self.create_text(w / 2, h / 2, text=self.text, fill=text_color, font=self.font)

    def _hover(self, over: bool) -> None:
        self._over = over
        self._draw()

    def invoke(self) -> None:
        if self.enabled and self.command:
            self.command()

    def style(self, *, text=None, bg=None, fg=None, outline="keep", hover=None) -> None:
        if text is not None:
            self.text = text
        if bg is not None:
            self.bg = bg
            self.hover_bg = hover or mix(bg, "#ffffff", 0.12)
        if fg is not None:
            self.fg = fg
        if outline != "keep":
            self.outline = outline
        self._draw()

    def state(self, spec=None):
        """Compatible with ttk: state(["disabled"]) and state(["!disabled"])."""
        if spec is not None:
            for item in spec:
                if item == "disabled":
                    self.enabled = False
                elif item == "!disabled":
                    self.enabled = True
            self._draw()
        return [] if self.enabled else ["disabled"]


class Switch(tk.Canvas):
    def __init__(self, parent, variable, command=None, *, surface, on="#4c8dff", off="#2c3342", scale=1.0):
        self._s = scale
        super().__init__(parent, width=int(38 * scale), height=int(20 * scale), bg=surface,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command, self.on, self.off = variable, command, on, off
        self.bind("<Button-1>", lambda _e: self.toggle())
        self.bind("<Configure>", lambda _e: self._draw())
        try:
            variable.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass
        self._draw()

    def toggle(self) -> None:
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self) -> None:
        w, h = int(38 * self._s), int(20 * self._s)
        on = bool(self.var.get())
        self.delete("all")
        self.create_polygon(rounded(0, 0, w, h, h / 2), smooth=True, fill=self.on if on else self.off, outline="")
        pad = 3 * self._s
        d = h - 2 * pad
        x = w - pad - d if on else pad
        self.create_oval(x, pad, x + d, pad + d, fill="#ffffff", outline="")


class Segmented(tk.Canvas):
    """A row of choices, one selected (like 30 | 60 fps)."""

    def __init__(self, parent, options, variable, command=None, *, surface, field, accent,
                 text="#e8ecf4", muted="#8a93a6", seg_width=44, height=28, scale=1.0):
        self._s = scale
        self.choices = list(options)
        super().__init__(parent, width=int(seg_width * scale * max(1, len(self.choices))),
                         height=int(height * scale), bg=surface, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command = variable, command
        self.field, self.accent, self.text, self.muted = field, accent, text, muted
        self.seg = int(seg_width * scale)
        self.bind("<Button-1>", self._click)
        try:
            variable.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass
        self._draw()

    def set_options(self, options) -> None:
        self.choices = list(options)
        self.configure(width=self.seg * max(1, len(self.choices)))
        self._draw()

    def _click(self, event) -> None:
        index = int(event.x // self.seg)
        if 0 <= index < len(self.choices):
            self.select(self.choices[index])

    def select(self, value) -> None:
        self.var.set(value)
        self._draw()
        if self.command:
            self.command()

    def _draw(self) -> None:
        h = int(self["height"])
        w = self.seg * len(self.choices)
        self.delete("all")
        self.create_polygon(rounded(0, 0, w, h, 8 * self._s), smooth=True, fill=self.field, outline="")
        for i, option in enumerate(self.choices):
            chosen = option == self.var.get()
            x0 = i * self.seg
            if chosen:
                self.create_polygon(rounded(x0 + 2, 2, x0 + self.seg - 2, h - 2, 7 * self._s), smooth=True,
                                    fill=self.accent, outline="")
            self.create_text(x0 + self.seg / 2, h / 2, text=str(option), font=("Segoe UI", 9, "bold"),
                             fill="#ffffff" if chosen else self.muted)


class Slider(tk.Canvas):
    def __init__(self, parent, from_, to, value, command=None, *, surface, track, accent, width=120, scale=1.0):
        self._s = scale
        super().__init__(parent, width=int(width * scale), height=int(20 * scale), bg=surface,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.lo, self.hi, self.command = from_, to, command
        self.track, self.accent = track, accent
        self.value = max(from_, min(to, value))
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._draw()

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = max(self.lo, min(self.hi, value))
        self._draw()

    def _drag(self, event) -> None:
        w = int(self["width"])
        pad = 8 * self._s
        frac = max(0.0, min(1.0, (event.x - pad) / max(1, w - 2 * pad)))
        self.value = round(self.lo + frac * (self.hi - self.lo))
        self._draw()
        if self.command:
            self.command(self.value)

    def _draw(self) -> None:
        w, h = int(self["width"]), int(self["height"])
        pad = 8 * self._s
        frac = (self.value - self.lo) / max(1, self.hi - self.lo)
        x = pad + frac * (w - 2 * pad)
        y, t = h / 2, 3 * self._s
        self.delete("all")
        self.create_polygon(rounded(pad, y - t / 2, w - pad, y + t / 2, t / 2), smooth=True, fill=self.track, outline="")
        self.create_polygon(rounded(pad, y - t / 2, max(pad + t, x), y + t / 2, t / 2), smooth=True,
                            fill=self.accent, outline="")
        r = 6 * self._s
        self.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="")


class LevelBar(tk.Canvas):
    """A slim audio level meter: green, then amber, then red."""

    def __init__(self, parent, *, surface, track, width=110, height=6, scale=1.0, colors=("#2fbf71", "#f0a93b", "#f0434f")):
        super().__init__(parent, width=int(width * scale), height=int(height * scale), bg=surface,
                         highlightthickness=0, bd=0)
        self.w, self.h = int(width * scale), int(height * scale)
        self.create_rectangle(0, 0, self.w, self.h, fill=track, outline="")
        self._bars = [self.create_rectangle(0, 0, 0, self.h, fill=c, outline="") for c in colors]
        self._last = -1

    def set(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        step = round(frac * 40)
        if step == self._last:                        # nothing visible changed: do no work
            return
        self._last = step
        x = self.w * step / 40
        green, amber = self.w * 0.7, self.w * 0.9
        self.coords(self._bars[0], 0, 0, min(x, green), self.h)
        self.coords(self._bars[1], green, 0, max(green, min(x, amber)), self.h)
        self.coords(self._bars[2], amber, 0, max(amber, x), self.h)


class Pill(tk.Canvas):
    """A rounded status label."""

    def __init__(self, parent, text, color, *, surface, scale=1.0, font=("Segoe UI", 9, "bold")):
        self._s = scale
        super().__init__(parent, width=int(150 * scale), height=int(24 * scale), bg=surface,
                         highlightthickness=0, bd=0)
        self.font = font
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        if (text, color) == getattr(self, "_shown", None):
            return
        self._shown = (text, color)
        w, h = int(150 * self._s), int(24 * self._s)
        self.delete("all")
        self.create_polygon(rounded(0, 0, w, h, h / 2), smooth=True, fill=color, outline="")
        self.create_text(w / 2, h / 2, text=text, fill="#ffffff", font=self.font)


class VLevel(tk.Canvas):
    """A vertical audio level meter with a dB scale, like a mixer channel."""

    def __init__(self, parent, *, surface, track, height=120, width=14, scale=1.0,
                 colors=("#2fbf71", "#f0a93b", "#f0434f"), tick="#566074"):
        super().__init__(parent, width=int(width * scale), height=int(height * scale), bg=surface,
                         highlightthickness=0, bd=0)
        self.w, self.h = int(width * scale), int(height * scale)
        self.create_rectangle(0, 0, self.w, self.h, fill=track, outline="")
        self._bars = [self.create_rectangle(0, self.h, self.w, self.h, fill=c, outline="") for c in colors]
        for db in (0, -10, -20, -30, -40, -50):              # tick marks
            y = self.h * (-db) / 60
            self.create_line(0, y, self.w * 0.35, y, fill=tick)
        self._last = -1

    def set(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        step = round(frac * 60)
        if step == self._last:
            return
        self._last = step
        top = self.h * (1 - step / 60)
        green_top, amber_top = self.h * 0.3, self.h * 0.1        # above -18 dB turns amber, above -6 dB red
        self.coords(self._bars[0], 0, max(top, green_top), self.w, self.h)
        if top < green_top:
            self.coords(self._bars[1], 0, max(top, amber_top), self.w, green_top)
        else:
            self.coords(self._bars[1], 0, 0, 0, 0)
        if top < amber_top:
            self.coords(self._bars[2], 0, top, self.w, amber_top)
        else:
            self.coords(self._bars[2], 0, 0, 0, 0)


class VFader(tk.Canvas):
    """A vertical volume fader, 0 to 200 percent."""

    def __init__(self, parent, value, command=None, *, surface, track, accent, height=120, width=22, scale=1.0,
                 lo=0, hi=200):
        super().__init__(parent, width=int(width * scale), height=int(height * scale), bg=surface,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._s = scale
        self.lo, self.hi, self.command = lo, hi, command
        self.track, self.accent = track, accent
        self.value = max(lo, min(hi, value))
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._draw()

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = max(self.lo, min(self.hi, value))
        self._draw()

    def _drag(self, event) -> None:
        h = int(self["height"])
        pad = 8 * self._s
        frac = 1 - max(0.0, min(1.0, (event.y - pad) / max(1, h - 2 * pad)))
        self.value = round(self.lo + frac * (self.hi - self.lo))
        self._draw()
        if self.command:
            self.command(self.value)

    def _draw(self) -> None:
        w, h = int(self["width"]), int(self["height"])
        pad = 8 * self._s
        frac = (self.value - self.lo) / max(1, self.hi - self.lo)
        y = h - pad - frac * (h - 2 * pad)
        x = w / 2
        self.delete("all")
        self.create_line(x, pad, x, h - pad, fill=self.track, width=int(3 * self._s))
        self.create_line(x, y, x, h - pad, fill=self.accent, width=int(3 * self._s))
        kw, kh = 9 * self._s, 5 * self._s
        self.create_polygon(rounded(x - kw, y - kh, x + kw, y + kh, 3 * self._s), smooth=True, fill="#e8ecf4", outline="")


class MuteButton(tk.Canvas):
    """A small speaker icon; red with a cross while muted."""

    def __init__(self, parent, variable, command=None, *, surface, scale=1.0, size=22):
        super().__init__(parent, width=int(size * scale), height=int(size * scale), bg=surface,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._s, self.size = scale, int(size * scale)
        self.var, self.command = variable, command
        self.bind("<Button-1>", lambda _e: self.toggle())
        try:
            variable.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass
        self._draw()

    def toggle(self) -> None:
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self) -> None:
        z = self.size
        muted = bool(self.var.get())
        colour = "#f0434f" if muted else "#aab3c5"
        self.delete("all")
        self.create_polygon(z * .18, z * .38, z * .36, z * .38, z * .56, z * .2, z * .56, z * .8,
                            z * .36, z * .62, z * .18, z * .62, fill=colour, outline="")
        if muted:
            self.create_line(z * .64, z * .36, z * .88, z * .64, fill=colour, width=max(1, int(2 * self._s)))
            self.create_line(z * .88, z * .36, z * .64, z * .64, fill=colour, width=max(1, int(2 * self._s)))
        else:
            self.create_arc(z * .5, z * .3, z * .86, z * .7, start=-50, extent=100, style="arc",
                            outline=colour, width=max(1, int(2 * self._s)))
