import unittest

from jerncast.docklayout import AREAS, DOCKS, DockLayout, choose_drop


class LayoutTests(unittest.TestCase):
    def test_default_like_obs(self):
        d = DockLayout()
        self.assertEqual(d.visible("left"), ["scenes", "sources"])
        self.assertEqual(d.visible("bottom"), ["mixer", "controls"])
        self.assertEqual(d.visible("right"), [])
        self.assertFalse(d.is_visible("recent"))
        self.assertFalse(d.locked)

    def test_move_between_areas_and_order(self):
        d = DockLayout()
        self.assertTrue(d.move("controls", "right"))
        self.assertEqual(d.visible("right"), ["controls"])
        self.assertEqual(d.visible("bottom"), ["mixer"])
        self.assertTrue(d.move("sources", "left", 0))            # to the top of the left stack
        self.assertEqual(d.visible("left"), ["sources", "scenes"])
        self.assertTrue(d.move("scenes", "left", 99))            # past the end = last
        self.assertEqual(d.visible("left"), ["sources", "scenes"])
        self.assertTrue(d.move("mixer", "bottom", 1))
        self.assertEqual(d.locate("mixer"), ("bottom", 0))

    def test_locked_layout_does_not_move(self):
        d = DockLayout()
        d.locked = True
        self.assertFalse(d.move("scenes", "right"))
        self.assertEqual(d.locate("scenes"), ("left", 0))
        self.assertFalse(DockLayout().move("nonsense", "left"))
        self.assertFalse(DockLayout().move("scenes", "middle"))

    def test_hide_show_toggle_keep_position(self):
        d = DockLayout()
        d.hide("scenes")
        self.assertEqual(d.visible("left"), ["sources"])
        self.assertTrue(d.toggle("scenes"))
        self.assertEqual(d.visible("left"), ["scenes", "sources"])
        self.assertFalse(d.toggle("scenes"))

    def test_reset(self):
        d = DockLayout()
        d.move("scenes", "bottom")
        d.hide("mixer")
        d.reset()
        self.assertEqual(d.to_dict()["areas"], DockLayout().to_dict()["areas"])
        self.assertEqual(d.hidden, {"recent"})

    def test_save_and_load_roundtrip_and_repair(self):
        d = DockLayout()
        d.move("sources", "right")
        d.hide("controls")
        d.locked = True
        e = DockLayout.from_dict(d.to_dict())
        self.assertEqual(e.to_dict(), d.to_dict())
        broken = DockLayout.from_dict({"areas": {"left": ["scenes", "scenes", "ghost"], "right": "x"},
                                       "hidden": ["ghost", "mixer"], "locked": 1})
        placed = [x for a in AREAS for x in broken.areas[a]]
        self.assertEqual(sorted(placed), sorted(DOCKS))          # every dock exactly once
        self.assertEqual(broken.hidden, {"mixer"})
        self.assertTrue(broken.locked)
        self.assertEqual(DockLayout.from_dict(None).to_dict(), DockLayout().to_dict())

    def test_choose_drop(self):
        areas = {"left": (0, 0, 200, 600), "right": (800, 0, 1000, 600), "bottom": (0, 600, 1000, 800)}
        kids = {"left": [(0, 0, 200, 300), (0, 300, 200, 600)], "right": [],
                "bottom": [(0, 600, 500, 800), (500, 600, 1000, 800)]}
        self.assertEqual(choose_drop(areas, kids, 50, 100), ("left", 0))
        self.assertEqual(choose_drop(areas, kids, 50, 400), ("left", 1))
        self.assertEqual(choose_drop(areas, kids, 50, 590), ("left", 2))
        self.assertEqual(choose_drop(areas, kids, 900, 20), ("right", 0))      # an empty area
        self.assertEqual(choose_drop(areas, kids, 100, 700), ("bottom", 0))    # left to right here
        self.assertEqual(choose_drop(areas, kids, 900, 700), ("bottom", 2))
        self.assertIsNone(choose_drop(areas, kids, 400, 300))                  # the preview: no dock zone


if __name__ == "__main__":
    unittest.main()
