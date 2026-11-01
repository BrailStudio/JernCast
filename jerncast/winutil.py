"""Windows-only helpers (monitors, windows, global hotkey). Safe to import anywhere."""
from __future__ import annotations

import ctypes
import dataclasses
import logging
import threading
from ctypes import wintypes

from .core import IS_WINDOWS

log = logging.getLogger("jerncast")

@dataclasses.dataclass(frozen=True)
class Monitor:
    name: str
    x: int
    y: int
    w: int
    h: int


HOTKEYS = ["F6", "F7", "F8", "F9", "F10", "F11", "F12"]
_MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(spec: str):
    """"Ctrl+Alt+1" -> (modifier flags, virtual-key code), or None if not understood."""
    mods, key = 0, None
    for part in [p.strip() for p in spec.split("+") if p.strip()]:
        low = part.lower()
        if low in _MODS:
            mods |= _MODS[low]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 12:
            key = 0x70 + int(low[1:]) - 1
        else:
            return None
    return None if key is None else (mods, key)


def set_dpi_aware() -> None:
    """Report real pixel sizes (otherwise a 150% scaled screen is captured too small)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def list_monitors() -> list[Monitor]:
    if not IS_WINDOWS:
        return []
    found: list[tuple[int, int, int, int]] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def callback(_monitor, _dc, rect, _data):
        r = rect.contents
        found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    except Exception:
        log.exception("monitor listing failed")
        return []
    found.sort(key=lambda m: (m[0], m[1]) != (0, 0))      # primary (at 0,0) first
    result = []
    for i, (x, y, w, h) in enumerate(found, 1):
        tag = " (main)" if (x, y) == (0, 0) else ""
        result.append(Monitor(f"Display {i} - {w}x{h}{tag}", x, y, w, h))
    return result


def list_windows(skip: tuple[str, ...] = ("JernCast",)) -> list[str]:
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    titles: list[str] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _data):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title and title not in skip and title != "Program Manager" and title not in titles:
                    titles.append(title)
        return True

    try:
        user32.EnumWindows(proc_type(callback), 0)
    except Exception:
        log.exception("window listing failed")
    return sorted(titles, key=str.lower)


class Hotkey(threading.Thread):
    """Global hotkey. Calls `callback()` from its own thread each time it is pressed."""

    def __init__(self, key: str, callback):
        super().__init__(daemon=True)
        self.key = key
        self.callback = callback
        self.ok = False
        self._halt = threading.Event()
        self._ready = threading.Event()

    def run(self) -> None:
        parsed = parse_hotkey(self.key)
        if not IS_WINDOWS or parsed is None:
            self._ready.set()
            return
        user32 = ctypes.windll.user32
        mods, vk = parsed
        self.ok = bool(user32.RegisterHotKey(None, 1, mods | 0x4000, vk))  # 0x4000: no auto-repeat
        self._ready.set()
        if not self.ok:
            log.warning("hotkey %s is already used by another program", self.key)
            return
        msg = wintypes.MSG()
        while not self._halt.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == 0x0312:                       # WM_HOTKEY
                    try:
                        self.callback()
                    except Exception:
                        log.exception("hotkey callback failed")
            self._halt.wait(0.05)
        user32.UnregisterHotKey(None, 1)

    def start_and_wait(self) -> bool:
        self.start()
        self._ready.wait(2)
        return self.ok

    def stop(self) -> None:
        self._halt.set()
