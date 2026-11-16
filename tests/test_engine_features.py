import os
import shutil
import struct
import tempfile
import time
import unittest

from jerncast import core, loopback
from jerncast.core import Settings
from jerncast.project import Project
from jerncast.session import Session
from jerncast.winutil import parse_hotkey

FFMPEG = core.find_ffmpeg()


class HotkeyAndAudioTests(unittest.TestCase):
    def test_hotkey_specs(self):
        self.assertEqual(parse_hotkey("F9"), (0, 0x78))
        self.assertEqual(parse_hotkey("Ctrl+Alt+1"), (3, 0x31))
        self.assertIsNone(parse_hotkey("Ctrl+"))
        self.assertIsNone(parse_hotkey("Ctrl+Banana"))

    def test_downmix(self):
        six = struct.pack("<12h", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)       # two 6-channel frames
        self.assertEqual(struct.unpack("<4h", loopback.downmix_to_stereo(six, 6)), (1, 2, 7, 8))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class EngineFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.settings = Settings(folder=self.tmp + "/out", canvas="640x360")
        self.project = Project()
        self.project.add_scene("Scene")
        red = self.project.add_source("test_color", "Red", "red")
        self.project.add_item("Scene", red.id)
        self.session = Session(FFMPEG, self.settings, self.project)

    def tearDown(self):
        self.session.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_preview_picture_while_recording_and_while_idle(self):
        s = self.session
        self.assertIsNone(s.preview_path())
        s.set_preview(True)                              # capture without recording
        self.assertTrue(s.running and not s.recording)
        time.sleep(2)
        png = s.preview_path()
        data = png.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (640, 360))
        s.start_recording()
        s.set_preview(False)
        self.assertTrue(s.running)                       # still recording, so the engine stays
        time.sleep(1.5)
        self.assertIsNotNone(s.preview_path())
        s.stop_recording()
        self.assertFalse(s.running)

    def test_system_sound_through_the_feeder(self):
        from jerncast.project import SourceDef
        self.project.sources["desktop"] = SourceDef(
            "desktop", "loopback", "Desktop sound")
        made = []

        def factory():
            feeder = loopback.Feeder(loopback.ToneSource()).start()
            made.append(feeder)
            return feeder
        s = self.session
        s.loopback_factory = factory
        s.start_recording()
        time.sleep(2.5)
        self.assertGreater(s.meters()["desktop"], -40)           # the beep reaches the mixer meter
        s.set_muted("desktop", True)
        time.sleep(1.5)
        self.assertLess(s.meters()["desktop"], -80)
        path = s.stop_recording()
        self.assertTrue(path.exists())
        self.assertTrue(made[0]._stop.is_set())                   # feeder stopped with the engine

    def test_silent_system_sound_does_not_stall_recording(self):
        from jerncast.project import SourceDef
        self.project.sources["desktop"] = SourceDef("desktop", "loopback", "Desktop sound")
        self.session.loopback_factory = lambda: loopback.Feeder(loopback.ToneSource(silent=True)).start()
        self.session.start_recording()
        time.sleep(3)
        self.assertEqual(self.session.poll(), [])
        path = self.session.stop_recording()
        import json, subprocess
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json",
             str(path)], capture_output=True, text=True).stdout)
        self.assertEqual(sorted(x["codec_type"] for x in info["streams"]), ["audio", "video"])
        self.assertGreater(float(info["format"]["duration"]), 2.0)           # not stuck waiting for sound

    def test_missing_system_sound_support_is_explained(self):
        from jerncast.project import SourceDef
        from jerncast.session import SessionError
        self.project.sources["desktop"] = SourceDef("desktop", "loopback", "Desktop sound")

        def broken():
            raise loopback.LoopbackError("needs pyaudiowpatch")
        self.session.loopback_factory = broken
        with self.assertRaises(SessionError) as ctx:
            self.session.start_recording()
        self.assertIn("pyaudiowpatch", str(ctx.exception))
        self.assertFalse(self.session.running)


if __name__ == "__main__":
    unittest.main()


def red_box(path, seconds):
    """Where red is in the frame at `seconds`, as (x0, y0, x1, y1) in a 640x360 picture (None if no red)."""
    import subprocess
    raw = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(seconds), "-i", str(path),
                          "-frames:v", "1", "-vf", "scale=64:36,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    xs, ys = [], []
    for y in range(36):
        for x in range(64):
            r, g, b = raw[(y * 64 + x) * 3:(y * 64 + x) * 3 + 3]
            if r > 150 and g < 90 and b < 90:
                xs.append(x)
                ys.append(y)
    return (min(xs) * 10, min(ys) * 10, (max(xs) + 1) * 10, (max(ys) + 1) * 10) if xs else None


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class LiveGeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.project = Project()
        self.project.add_scene("S")
        red = self.project.add_source("test_color", "Red", "red")
        item = self.project.add_item("S", red.id)
        item.layout, item.box = "free", [0.0, 0.0, 0.25, 0.25]
        self.session = Session(FFMPEG, Settings(folder=self.tmp + "/out", canvas="640x360"), self.project)

    def tearDown(self):
        self.session.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_move_and_resize_a_source_while_recording(self):
        s = self.session
        s.start_recording()
        time.sleep(2.5)
        s.set_item_box("S", 0, [0.5, 0.5, 0.25, 0.25])            # drag it
        time.sleep(2.5)
        s.set_item_box("S", 0, [0.25, 0.25, 0.5, 0.5])            # make it bigger
        time.sleep(2.5)
        path = s.stop_recording()
        first, moved, grown = red_box(path, 1.0), red_box(path, 3.8), red_box(path, 6.5)
        self.assertEqual(first, (0, 0, 160, 90))
        self.assertEqual(moved, (320, 180, 480, 270))
        self.assertEqual(grown, (160, 90, 480, 270))
        saved = Project.from_dict(self.project.to_dict()).scenes[0].items[0]
        self.assertEqual((saved.layout, saved.box), ("free", [0.25, 0.25, 0.5, 0.5]))

    def test_set_item_box_while_idle_only_changes_the_project(self):
        self.session.set_item_box("S", 0, [0.1, 0.1, 0.3, 0.3])
        self.assertEqual(self.project.scenes[0].items[0].box, [0.1, 0.1, 0.3, 0.3])
        self.assertFalse(self.session.running)
