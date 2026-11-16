"""A folder of named JSON files with one of them 'current': used for Profiles and Scene Collections."""
from __future__ import annotations

import shutil
from pathlib import Path

from .core import safe_name


class NamedStore:
    def __init__(self, folder: Path, default_name: str = "Default", legacy: Path | None = None):
        self.folder = folder
        self.default_name = default_name
        folder.mkdir(parents=True, exist_ok=True)
        self._pointer = folder / "current.txt"
        if not self.names():                                   # first run: adopt the old single file
            if legacy is not None and legacy.exists():
                shutil.copyfile(legacy, self.path(default_name))
            else:
                self.path(default_name).write_text("{}", encoding="utf-8")

    def path(self, name: str) -> Path:
        return self.folder / f"{safe_name(name)}.json"

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.json"))

    @property
    def current(self) -> str:
        try:
            name = self._pointer.read_text(encoding="utf-8").strip()
        except OSError:
            name = ""
        names = self.names()
        if name in names:
            return name
        return self.default_name if self.default_name in names else names[0]

    def set_current(self, name: str) -> None:
        if name not in self.names():
            raise KeyError(name)
        self._pointer.write_text(name, encoding="utf-8")

    def _unique(self, name: str) -> str:
        base = safe_name(name.strip() or self.default_name)
        taken, candidate, n = set(self.names()), base, 2
        while candidate in taken:
            candidate, n = f"{base} {n}", n + 1
        return candidate

    def create(self, name: str, copy_from: str | None = None) -> str:
        name = self._unique(name)
        if copy_from is not None and self.path(copy_from).exists():
            shutil.copyfile(self.path(copy_from), self.path(name))
        else:
            self.path(name).write_text("{}", encoding="utf-8")
        return name

    def rename(self, old: str, new: str) -> str:
        if old not in self.names():
            raise KeyError(old)
        new = self._unique(new) if safe_name(new) != old else old
        if new != old:
            was_current = self.current == old          # ask before the file moves
            self.path(old).rename(self.path(new))
            if was_current:
                self.set_current(new)
        return new

    def delete(self, name: str) -> None:
        if len(self.names()) <= 1:
            raise ValueError("You need at least one.")
        if name not in self.names():
            raise KeyError(name)
        was_current = self.current == name
        self.path(name).unlink()
        if was_current:
            self.set_current(self.names()[0])
