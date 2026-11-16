"""The dock window code against a fake tkinter: arranging, hiding, locking and dragging."""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import fake_tk  # noqa: E402

from jerncast.docklayout import DockLayout  # noqa: E402

COLORS = {"bg": "#000", "surface": "#111", "header": "#222", "field": "#333", "border": "#444", "text": "#fff",
          "muted": "#888", "accent": "#44f", "accent_dim": "#115"}


class DockUiTests(unittest.TestCase):
    def setUp(self):
        self.mods = fake_tk.install()
        self.patch = mock.patch.dict(sys.modules, self.mods)
        self.patch.start()
        sys.modules.pop("jerncast.dockui", None)
        self.dockui = importlib.import_module("jerncast.dockui")
        self.changes = []
        self.layout = DockLayout()
        self.host = self.dockui.DockHost(self.mods["tkinter"].Tk(), self.layout, lambda: self.changes.append(1),
                                         COLORS)
        for key in ("scenes", "sources", "mixer", "controls", "recent"):
            self.host.add_dock(key)
        self.host.relayout()

    def tearDown(self):
        sys.modules.pop("jerncast.dockui", None)
        self.patch.stop()

    def event(self, x, y):
        return mock.Mock(x_root=x, y_root=y)

    def test_hide_toggle_lock_reset_notify(self):
        self.host.hide("scenes")
        self.assertEqual(self.layout.visible("left"), ["sources"])
        self.assertTrue(self.host.toggle("scenes"))
        self.host.set_locked(True)
        self.assertTrue(self.layout.locked)
        self.assertEqual(self.host.docks["scenes"].grip["text"], "")        # no grip while locked
        self.host.set_locked(False)
        self.assertEqual(self.host.docks["scenes"].grip["text"], "::")
        self.host.reset()
        self.assertEqual(len(self.changes), 5)

    def test_dragging_a_dock_to_another_area(self):
        areas = {"left": (0, 0, 200, 600), "right": (800, 0, 1000, 600), "bottom": (0, 600, 1000, 800)}
        kids = {"left": [(0, 0, 200, 300), (0, 300, 200, 600)], "right": [], "bottom": [(0, 600, 500, 800)]}
        dock = self.host.docks["scenes"]
        with mock.patch.object(self.host, "boxes", return_value=(areas, kids)):
            self.host.start_drag(dock, self.event(10, 10))
            self.host.drag(dock, self.event(20, 20))                         # starts the drag
            self.assertIsNotNone(self.host._ghost)
            self.host.drag(dock, self.event(900, 100))
            self.host.drop(dock, self.event(900, 100))                       # onto the empty right area
        self.assertEqual(self.layout.visible("right"), ["scenes"])
        self.assertEqual(self.layout.visible("left"), ["sources"])
        self.assertIsNone(self.host._ghost)
        self.assertEqual(len(self.changes), 1)

    def test_a_click_without_moving_changes_nothing(self):
        dock = self.host.docks["scenes"]
        self.host.start_drag(dock, self.event(10, 10))
        self.host.drop(dock, self.event(10, 10))
        self.assertEqual(self.layout.visible("left"), ["scenes", "sources"])
        self.assertEqual(self.changes, [])

    def test_locked_docks_cannot_be_dragged(self):
        self.layout.locked = True
        dock = self.host.docks["scenes"]
        self.host.start_drag(dock, self.event(10, 10))
        self.host.drag(dock, self.event(900, 100))
        self.host.drop(dock, self.event(900, 100))
        self.assertEqual(self.layout.locate("scenes"), ("left", 0))

    def test_dropping_outside_every_area_keeps_the_dock_where_it_was(self):
        areas = {"left": (0, 0, 200, 600), "right": (800, 0, 1000, 600), "bottom": (0, 600, 1000, 800)}
        dock = self.host.docks["mixer"]
        with mock.patch.object(self.host, "boxes", return_value=(areas, {a: [] for a in areas})):
            self.host.start_drag(dock, self.event(10, 700))
            self.host.drag(dock, self.event(20, 710))
            self.host.drop(dock, self.event(500, 300))                      # over the preview
        self.assertEqual(self.layout.locate("mixer"), ("bottom", 0))


if __name__ == "__main__":
    unittest.main()
