"""Moving and resizing sources by dragging in the preview (pure maths, no window).

A box is (x, y, w, h) as fractions of the picture. A view is (x, y, w, h) in screen pixels: where the picture
is drawn. Handles are the four corners: "nw", "ne", "sw", "se".
"""
from __future__ import annotations

HANDLES = ("nw", "ne", "sw", "se")
MIN_SIZE = 0.03
KEEP_VISIBLE = 0.1            # at least this much of a source stays inside the picture


def box_to_rect(box, view):
    x, y, w, h = box
    vx, vy, vw, vh = view
    return (vx + x * vw, vy + y * vh, vx + (x + w) * vw, vy + (y + h) * vh)


def hit_test(items, px: float, py: float, view):
    """items: [(index, box)] back to front. Returns the index of the front-most box under the point, or None."""
    for index, box in reversed(items):
        x0, y0, x1, y1 = box_to_rect(box, view)
        if x0 <= px <= x1 and y0 <= py <= y1:
            return index
    return None


def handle_at(rect, px: float, py: float, radius: float = 8):
    """Which corner handle of `rect` (x0, y0, x1, y1) is under the point, or None."""
    x0, y0, x1, y1 = rect
    for name, (cx, cy) in (("nw", (x0, y0)), ("ne", (x1, y0)), ("sw", (x0, y1)), ("se", (x1, y1))):
        if abs(px - cx) <= radius and abs(py - cy) <= radius:
            return name
    return None


def move_box(box, dfx: float, dfy: float):
    x, y, w, h = box
    x = max(-w + KEEP_VISIBLE, min(1 - KEEP_VISIBLE, x + dfx))
    y = max(-h + KEEP_VISIBLE, min(1 - KEEP_VISIBLE, y + dfy))
    return (x, y, w, h)


def resize_box(box, handle: str, dfx: float, dfy: float, min_size: float = MIN_SIZE):
    x, y, w, h = box
    right, bottom = x + w, y + h
    if "w" in handle:                                   # a left handle moves the left edge
        x = min(x + dfx, right - min_size)
    else:
        right = max(right + dfx, x + min_size)
    if "n" in handle:                                   # a top handle moves the top edge
        y = min(y + dfy, bottom - min_size)
    else:
        bottom = max(bottom + dfy, y + min_size)
    return (x, y, right - x, bottom - y)


def snap_box(box, threshold: float = 0.012):
    """Pull the box's edges and centre onto the picture's edges and centre when they are close."""
    x, y, w, h = box

    def snap_axis(start: float, size: float) -> float:
        best, best_distance = start, threshold
        for point, offset in ((0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (0.0, size), (0.5, size / 2), (1.0, size)):
            distance = abs(start + offset - point)
            if distance < best_distance:
                best, best_distance = point - offset, distance
        return best

    return (snap_axis(x, w), snap_axis(y, h), w, h)


def fractions_from_pixels(dx: float, dy: float, view):
    """A mouse movement in pixels as a movement in picture fractions."""
    return dx / max(1.0, view[2]), dy / max(1.0, view[3])
