import os
import shutil
import struct
import tempfile
import time
import unittest

from jerncast import core, loopback
from jerncast.core import Settings
from jerncast.layouts import available_layouts, layout_scene_name, project_from_settings
from jerncast.project import Project
from jerncast.session import Session
from jerncast.winutil import parse_hotkey

FFMPEG = core.find_ffmpeg()


class LayoutTests(unittest.TestCase):
    def test_without_a_webcam_only_screen_layout(self):
        p = project_from_settings(Settings(mic="Mic A"))
        self.assertEqual([s.name for s in p.scenes], [layout_scene_name(1)])
        self.assertEqual(sorted(p.sources), ["desktop", "mic", "screen"])
        self.assertEqual(p.sources["desktop"].kind, "loopback")

    def test_with_a_webcam_four_layouts_and_current_layout(self):
        s = Settings(webcam="Cam A", layout=3, cam_corner="top-left", cam_size=30, desktop="Stereo Mix",
                     mic_on=False, mic="Mic A")
        self.assertEqual(available_layouts(s), [1, 2, 3, 4])
        p = project_from_settings(s)
        self.assertEqual(len(p.scenes), 4)
        self.assertEqual(p.current, layout_scene_name(3))
        two = p.scene(layout_scene_name(2)).items
        self.assertEqual((two[1].source, two[1].layout, two[1].corner, two[1].size), ("webcam", "corner", "top-left", "30"))
        four = p.scene(layout_scene_name(4)).items
        self.assertEqual([i.source for i in four], ["webcam", "screen"])       # screen drawn on top
        self.assertNotIn("mic", p.sources)                                      # mic switched off
        self.assertEqual(p.sources["desktop"].kind, "audio")                    # a real device name

    def test_window_and_volumes_and_unknown_layout(self):
        p = project_from_settings(Settings(window="Notepad", mic="M", mic_volume=50, layout=4))
        self.assertEqual((p.sources["screen"].kind, p.sources["screen"].device), ("window", "Notepad"))
        self.assertEqual(p.sources["mic"].volume, 0.5)
        self.assertEqual(p.current, layout_scene_name(1))                       # no webcam: falls back

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
        self.assertEqual((width, height), (480, 270))
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
