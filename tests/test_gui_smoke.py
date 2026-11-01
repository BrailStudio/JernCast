"""Presses the window's buttons through a fake tkinter and checks what happens."""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import fake_tk  # noqa: E402

from jerncast import core, loopback, project as project_module  # noqa: E402
from jerncast.layouts import layout_scene_name  # noqa: E402
from jerncast.project import Capture  # noqa: E402

REAL_RESOLVE = project_module.resolve


def fake_resolve(src, monitors, cursor=True, loopback=None):
    """On this test machine: the 'screen' is a red picture, the 'webcam' a blue one, the mic a beep."""
    if src.kind in ("display", "window"):
        return Capture("test_color", "red")
    if src.kind == "webcam":
        return Capture("test_color", "blue")
    if src.kind == "audio":
        return Capture("test_audio", "440")
    return REAL_RESOLVE(src, monitors, cursor, loopback)


def wait_idle(app, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        app._tick()
        if not app.busy:
            return
        time.sleep(0.1)
    raise AssertionError("background action did not finish")


@unittest.skipUnless(core.find_ffmpeg(), "needs ffmpeg")
class WindowSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.mods = fake_tk.install()
        self.patches = [mock.patch.dict(sys.modules, self.mods),
                        mock.patch.object(project_module, "resolve", fake_resolve)]
        for p in self.patches:
            p.start()
        sys.modules.pop("jerncast.gui", None)
        self.gui = importlib.import_module("jerncast.gui")
        core.Settings(folder=self.tmp + "/videos", canvas="640x360", webcam="Cam A", mic="Mic A").save()
        self.app = self.gui.App(self.mods["tkinter"].Tk())
        self.app.session.loopback_factory = lambda: loopback.Feeder(loopback.ToneSource()).start()
        time.sleep(1.5)                       # the device scan finishes in the background
        self.app._tick()
        self.shown = self.mods["tkinter.messagebox"].shown

    def tearDown(self):
        try:
            self.app.session.shutdown()
        finally:
            for hk in self.app.hotkeys:
                hk.stop()
            for p in reversed(self.patches):
                p.stop()
            sys.modules.pop("jerncast.gui", None)
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_window_shows_the_settings(self):
        a = self.app
        self.assertEqual(a.cb_layout["values"], ["Screen only", "Screen + webcam", "Webcam only",
                                                 "Webcam + small screen"])
        self.assertIn("1280×720", a.info.get() or a.base_info)
        self.assertIn("required", a.key_hint.get())
        self.assertEqual(sorted(a.project.sources), ["desktop", "mic", "screen", "webcam"])
        self.assertEqual(len(a.hotkeys), 6)                      # record, live, Ctrl+Alt+1..4
        self.assertEqual(a.recent_list.items, ["No recordings yet. Press Start Recording."])

    def test_record_change_layout_live_mute_and_stop(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        self.assertTrue(a.session.recording, self.shown)
        a._update_buttons()
        self.assertEqual(a.rec_btn["text"], "Stop Recording")
        time.sleep(2)
        a._set_layout(2)                                         # screen + webcam, live
        self.assertEqual(a.session.project.current, layout_scene_name(2))
        a._toggle_webcam()                                       # back to screen only
        self.assertEqual(a.session.project.current, layout_scene_name(1))
        a.v_mic_on.set(False)
        a._audio_on("mic")                                       # mute the mic live
        self.assertTrue(a.session.project.sources["mic"].muted)
        a._update_status()
        self.assertTrue(a.status.get().startswith("REC"))
        a._volume("desktop", 50)
        self.assertEqual(a.session.project.sources["desktop"].volume, 0.5)
        time.sleep(1.5)
        a._draw_meters(a.session.meters())
        a._toggle_record()
        wait_idle(a)
        self.assertFalse(a.session.recording)
        self.assertEqual(len(a.recent_list.items), 1)
        self.assertTrue(a.recent_list.items[0].startswith("JernCast "))
        self.assertTrue(a.recent_list.items[0].endswith(".mp4"))
        self.assertFalse(any(k == "showerror" for k, _ in self.shown), self.shown)
        self.assertEqual(json.loads((core.data_dir() / "settings.json").read_text())["recent"][0],
                         a.settings.recent[0])

    def test_compact_mode_shows_the_live_picture(self):
        a = self.app
        a._toggle_compact()
        wait_idle(a)
        self.assertTrue(a.session.running and not a.session.recording)   # the preview keeps capturing
        time.sleep(2)
        a._draw_preview()
        self.assertTrue(any(kind == "image" for kind, _ in a.preview.drawn))
        a._toggle_compact()                                              # back to the full view
        wait_idle(a)
        self.assertFalse(a.session.running)                              # preview stopped, nothing recording

    def test_go_live_without_key_explains(self):
        a = self.app
        a._toggle_live()
        wait_idle(a)
        kind, args = self.shown[-1]
        self.assertEqual(kind, "showerror")
        self.assertIn("stream key", args[1])

    def test_services_fill_the_server(self):
        a = self.app
        a.v_service.set("Twitch")
        a._service_changed()
        self.assertEqual(a.v_server.get(), "rtmp://live.twitch.tv/app")
        self.assertEqual(a.settings.stream_server, "rtmp://live.twitch.tv/app")
        with mock.patch.object(self.gui.webbrowser, "open") as opened:
            a._manage_broadcast()
        self.assertIn("twitch", opened.call_args[0][0])

    def test_duration_stops_the_recording(self):
        a = self.app
        a.v_duration.set("1")
        a._toggle_record()
        wait_idle(a)
        self.assertTrue(a.session.recording)
        time.sleep(1.5)
        with mock.patch.object(type(a.session), "record_seconds", lambda self: 61.0):
            a._collect()
            a._check_duration()
        wait_idle(a)
        self.assertFalse(a.session.recording)
        self.assertEqual(len(a.recent_list.items), 1)

    def test_webcam_layouts_need_a_webcam(self):
        a = self.app
        a.v_webcam.set("None")
        a._choices_changed()
        self.assertEqual(a.cb_layout["values"], ["Screen only"])
        a._toggle_webcam()
        self.assertIn("webcam", a.note)

    def test_stream_key_is_only_saved_when_allowed(self):
        a = self.app
        a.v_key.set("topsecret")
        a.v_remember.set(False)
        a._exit()
        self.assertEqual(core.Settings.load().stream_key, "")
        self.assertNotIn("topsecret", (core.data_dir() / "settings.json").read_text())


if __name__ == "__main__":
    unittest.main()
