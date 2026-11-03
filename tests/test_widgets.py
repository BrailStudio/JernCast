"""The hand-drawn widgets, run against a fake tkinter (checks their logic, not how they look)."""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import fake_tk  # noqa: E402


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.mods = fake_tk.install()
        self.patch = mock.patch.dict(sys.modules, self.mods)
        self.patch.start()
        sys.modules.pop("jerncast.widgets", None)
        self.w = importlib.import_module("jerncast.widgets")
        self.Var = fake_tk.Var

    def tearDown(self):
        sys.modules.pop("jerncast.widgets", None)
        self.patch.stop()

    def event(self, x):
        return mock.Mock(x=x, y=5)

    def test_helpers(self):
        self.assertEqual(len(self.w.rounded(0, 0, 100, 40, 10)), 24)
        self.assertEqual(self.w.mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(self.w.mix("#102030", "#102030", 0.7), "#102030")

    def test_round_button_click_disable_and_restyle(self):
        clicks = []
        b = self.w.RoundButton(None, "Go", lambda: clicks.append(1), surface="#000000", bg="#112233")
        b.invoke()
        b.state(["disabled"])
        b.invoke()
        self.assertEqual(clicks, [1])
        self.assertEqual(b.state(), ["disabled"])
        b.state(["!disabled"])
        b.style(text="Stop", bg="#445566")
        b.invoke()
        self.assertEqual((clicks, b.text, b.bg), ([1, 1], "Stop", "#445566"))

    def test_switch_toggles_variable_and_calls_back(self):
        var, calls = self.Var(value=False), []
        sw = self.w.Switch(None, var, lambda: calls.append(var.get()), surface="#000000")
        sw.toggle()
        sw.toggle()
        self.assertEqual(calls, [True, False])

    def test_segmented_click_picks_the_right_option(self):
        var, calls = self.Var(value=30), []
        seg = self.w.Segmented(None, [30, 60], var, lambda: calls.append(var.get()), surface="#000", field="#111",
                               accent="#22f", seg_width=50, scale=1.0)
        seg._click(self.event(70))                  # second segment
        seg._click(self.event(10))
        seg._click(self.event(500))                 # outside: ignored
        self.assertEqual(calls, [60, 30])
        seg.set_options([1])
        self.assertEqual(seg.choices, [1])

    def test_slider_maps_position_to_value(self):
        seen = []
        sl = self.w.Slider(None, 0, 200, 100, seen.append, surface="#000", track="#111", accent="#22f",
                           width=216, scale=1.0)
        sl._drag(self.event(8))                     # far left (pad = 8)
        sl._drag(self.event(208))                   # far right
        sl._drag(self.event(108))                   # middle
        sl._drag(self.event(-50))                   # clamped
        self.assertEqual(seen, [0, 200, 100, 0])
        sl.set(500)
        self.assertEqual(sl.get(), 200)

    def test_level_bar_only_redraws_when_the_level_changes(self):
        bar = self.w.LevelBar(None, surface="#000", track="#111", width=100, scale=1.0)
        with mock.patch.object(bar, "coords") as coords:
            bar.set(0.5)
            bar.set(0.5)
            bar.set(0.5001)
            self.assertEqual(coords.call_count, 3)          # one redraw (3 bars), not three
            bar.set(2.0)                                    # clamped to full
            self.assertEqual(coords.call_count, 6)

    def test_pill_skips_identical_updates(self):
        pill = self.w.Pill(None, "Ready", "#0a0", surface="#000")
        with mock.patch.object(pill, "delete") as deleted:
            pill.set("Ready", "#0a0")
            pill.set("REC 00:01", "#a00")
            self.assertEqual(deleted.call_count, 1)


if __name__ == "__main__":
    unittest.main()
