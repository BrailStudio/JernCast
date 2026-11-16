"""Where each dock is (pure logic, no window): areas, order, hidden docks, locked or not."""
from __future__ import annotations

import copy

AREAS = ("left", "right", "bottom")
DOCKS = {
    "scenes": "Scenes",
    "sources": "Sources",
    "mixer": "Audio Mixer",
    "controls": "Controls",
    "recent": "Recent recordings",
}
DEFAULT_AREAS = {
    "left": ["scenes", "sources"],
    "right": ["recent"],               # hidden until you turn it on in the Docks menu
    "bottom": ["mixer", "controls"],
}
DEFAULT_HIDDEN = ["recent"]


class DockLayout:
    def __init__(self):
        self.areas: dict[str, list[str]] = copy.deepcopy(DEFAULT_AREAS)
        self.hidden: set[str] = set(DEFAULT_HIDDEN)
        self.locked = False

    # -- queries --------------------------------------------------------------
    def locate(self, dock: str) -> tuple[str, int]:
        for area, docks in self.areas.items():
            if dock in docks:
                return area, docks.index(dock)
        raise KeyError(dock)

    def visible(self, area: str) -> list[str]:
        return [d for d in self.areas[area] if d not in self.hidden]

    def is_visible(self, dock: str) -> bool:
        return dock not in self.hidden

    # -- changes --------------------------------------------------------------
    def move(self, dock: str, area: str, index: int | None = None) -> bool:
        """Put `dock` in `area` at position `index` among the visible docks. False if locked or unknown."""
        if self.locked or dock not in DOCKS or area not in self.areas:
            return False
        old_area, _ = self.locate(dock)
        self.areas[old_area].remove(dock)
        shown = [d for d in self.areas[area] if d not in self.hidden]
        if index is None or index >= len(shown):
            self.areas[area].append(dock)
        else:
            before = shown[max(0, index)]
            self.areas[area].insert(self.areas[area].index(before), dock)
        return True

    def hide(self, dock: str) -> None:
        if dock in DOCKS:
            self.hidden.add(dock)

    def show(self, dock: str) -> None:
        self.hidden.discard(dock)

    def toggle(self, dock: str) -> bool:
        """Show or hide; returns whether it is visible now."""
        if self.is_visible(dock):
            self.hide(dock)
        else:
            self.show(dock)
        return self.is_visible(dock)

    def reset(self) -> None:
        self.areas = copy.deepcopy(DEFAULT_AREAS)
        self.hidden = set(DEFAULT_HIDDEN)

    # -- saving ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"areas": copy.deepcopy(self.areas), "hidden": sorted(self.hidden), "locked": self.locked}

    @classmethod
    def from_dict(cls, raw) -> "DockLayout":
        layout = cls()
        if not isinstance(raw, dict):
            return layout
        areas = raw.get("areas")
        if isinstance(areas, dict):
            placed: list[str] = []
            clean = {a: [] for a in AREAS}
            for area in AREAS:
                for dock in areas.get(area, []) if isinstance(areas.get(area), list) else []:
                    if dock in DOCKS and dock not in placed:
                        clean[area].append(dock)
                        placed.append(dock)
            for area, docks in DEFAULT_AREAS.items():              # docks the file did not mention
                for dock in docks:
                    if dock not in placed:
                        clean[area].append(dock)
                        placed.append(dock)
            for dock in DOCKS:
                if dock not in placed:
                    clean["right"].append(dock)
            layout.areas = clean
        hidden = raw.get("hidden")
        if isinstance(hidden, list):
            layout.hidden = {d for d in hidden if d in DOCKS}
        layout.locked = bool(raw.get("locked", False))
        return layout


def choose_drop(area_boxes, child_boxes, x: float, y: float):
    """Where did a dragged dock land?

    area_boxes: {area: (x0, y0, x1, y1)} on screen; child_boxes: {area: [(x0, y0, x1, y1), ...]} of the
    docks already visible there (the dragged one excluded). Returns (area, index) or None.
    Left and right areas stack docks top to bottom; the bottom area lines them up left to right.
    """
    for area, (x0, y0, x1, y1) in area_boxes.items():
        if x0 <= x <= x1 and y0 <= y <= y1:
            kids = child_boxes.get(area, [])
            index = 0
            for kx0, ky0, kx1, ky1 in kids:
                middle = (kx0 + kx1) / 2 if area == "bottom" else (ky0 + ky1) / 2
                if (x if area == "bottom" else y) > middle:
                    index += 1
            return area, index
    return None
