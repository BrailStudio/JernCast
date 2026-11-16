import unittest

from jerncast.placement import (box_to_rect, fractions_from_pixels, handle_at, hit_test, move_box, resize_box,
                                snap_box)

VIEW = (100, 50, 640, 360)


class PlacementTests(unittest.TestCase):
    def near(self, a, b):
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y)

    def test_rect_and_pixel_fractions(self):
        self.assertEqual(box_to_rect((0.5, 0.5, 0.25, 0.25), VIEW), (420.0, 230.0, 580.0, 320.0))
        self.assertEqual(fractions_from_pixels(64, 36, VIEW), (0.1, 0.1))

    def test_hit_test_picks_the_front_most(self):
        items = [(0, (0.0, 0.0, 1.0, 1.0)), (1, (0.5, 0.5, 0.25, 0.25))]             # 1 is in front of 0
        self.assertEqual(hit_test(items, 450, 250, VIEW), 1)
        self.assertEqual(hit_test(items, 150, 100, VIEW), 0)
        self.assertIsNone(hit_test(items, 20, 20, VIEW))                             # outside the picture
        self.assertIsNone(hit_test([], 450, 250, VIEW))

    def test_handles(self):
        rect = (420, 230, 580, 320)
        self.assertEqual(handle_at(rect, 422, 232), "nw")
        self.assertEqual(handle_at(rect, 578, 232), "ne")
        self.assertEqual(handle_at(rect, 422, 318), "sw")
        self.assertEqual(handle_at(rect, 579, 321), "se")
        self.assertIsNone(handle_at(rect, 500, 270))

    def test_move_stays_partly_in_the_picture(self):
        self.near(move_box((0.1, 0.1, 0.3, 0.3), 0.2, 0.1), (0.3, 0.2, 0.3, 0.3))
        x, y, w, h = move_box((0.1, 0.1, 0.3, 0.3), 5.0, 5.0)
        self.assertEqual((x, y), (0.9, 0.9))
        x, y, w, h = move_box((0.1, 0.1, 0.3, 0.3), -5.0, -5.0)
        self.assertAlmostEqual(x, -0.2)
        self.assertAlmostEqual(y, -0.2)

    def test_resize_from_each_corner(self):
        box = (0.2, 0.2, 0.4, 0.4)
        self.near(resize_box(box, "se", 0.1, 0.05), (0.2, 0.2, 0.5, 0.45))
        x, y, w, h = resize_box(box, "nw", 0.1, 0.1)
        self.assertAlmostEqual(x, 0.3)
        self.assertAlmostEqual(w, 0.3)
        self.assertAlmostEqual(h, 0.3)
        x, y, w, h = resize_box(box, "ne", 0.1, -0.1)
        self.assertEqual(x, 0.2)
        self.assertAlmostEqual(y, 0.1)
        self.assertAlmostEqual(w, 0.5)
        self.assertAlmostEqual(h, 0.5)

    def test_resize_cannot_turn_inside_out_or_vanish(self):
        x, y, w, h = resize_box((0.2, 0.2, 0.4, 0.4), "se", -5.0, -5.0)
        self.assertAlmostEqual(w, 0.03)
        self.assertAlmostEqual(h, 0.03)
        x, y, w, h = resize_box((0.2, 0.2, 0.4, 0.4), "nw", 5.0, 5.0)
        self.assertAlmostEqual(w, 0.03)
        self.assertAlmostEqual(x, 0.57)

    def test_snapping(self):
        self.assertEqual(snap_box((0.005, 0.3, 0.2, 0.2)), (0.0, 0.3, 0.2, 0.2))                 # left edge
        x, y, w, h = snap_box((0.395, 0.3, 0.2, 0.2))                                            # centred
        self.assertAlmostEqual(x, 0.4)
        x, y, w, h = snap_box((0.795, 0.3, 0.2, 0.2))                                            # right edge
        self.assertAlmostEqual(x, 0.8)
        self.assertEqual(snap_box((0.3, 0.3, 0.2, 0.2)), (0.3, 0.3, 0.2, 0.2))                   # far from all


if __name__ == "__main__":
    unittest.main()
