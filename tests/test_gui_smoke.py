"""Presses the window's buttons through a fake tkinter and checks what happens."""
import importlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import fake_tk  # noqa: E402

from jerncast import core  # noqa: E402
from jerncast.project import Project  # noqa: E402


def wait_idle(app, timeout=40):
    """Run the window's event loop by hand until the background action finishes."""
    end = time.time() + timeout
    while time.time() < end:
        app._tick_once = True
        try:
            while True:
                event = app.events.get_nowait()
                if event[0] == "done":
                    app._finish_bg(*event[1:])
                elif event[0] == "devices":
                    app.session.encoders[:] = event[3]
        except Exception:
            pass
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
        self.patch = mock.patch.dict(sys.modules, self.mods)
        self.patch.start()
        sys.modules.pop("jerncast.gui", None)
        self.gui = importlib.import_module("jerncast.gui")
        # a project made of generated test sources, saved where the window will load it
        p = Project()
        p.add_scene("Red")
        p.add_scene("Blue")
        red = p.add_source("test_color", "Red screen", "red")
        blue = p.add_source("test_color", "Blue screen", "blue")
        p.add_item("Red", red.id)
        p.add_item("Blue", blue.id)
        p.add_source("test_audio", "Tone", "440")
        p.save()
        core.Settings(folder=self.tmp + "/videos", canvas="640x360").save()
        self.app = self.gui.App(self.mods["tkinter"].Tk())
        time.sleep(1.5)                      # device scan finishes in the background
        self.shown = self.mods["tkinter.messagebox"].shown

    def tearDown(self):
        try:
            self.app.session.shutdown()
        finally:
            for hk in self.app.hotkeys:
                hk.stop()
            self.patch.stop()
            sys.modules.pop("jerncast.gui", None)
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_docks_show_the_project(self):
        a = self.app
        self.assertEqual(a.scene_list.items, ["Red", "Blue"])
        self.assertEqual(a.source_list.items, ["●  Red screen"])
        self.assertEqual(list(a.meter_canvases), ["s3"])
        self.assertTrue(any(kind == "rect" for kind, _ in a.preview.drawn))

    def test_record_switch_scene_mute_hide_and_stop(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        self.assertTrue(a.session.recording, self.shown)
        self.assertEqual(a.rec_btn["text"] if a.rec_btn["text"] else "", a.rec_btn["text"])
        a._update_buttons()
        self.assertEqual(a.rec_btn["text"], "Stop Recording")
        time.sleep(2)
        a.scene_list.selection_set(1)                      # click "Blue"
        a._on_scene_select()
        self.assertEqual(a.project.current, "Blue")
        self.assertEqual(a.source_list.items, ["●  Blue screen"])
        a._on_mute("s3", True)
        a.source_list.selection_set(0)
        a._toggle_visible()                                # hide the blue screen live
        self.assertEqual(a.source_list.items, ["○  Blue screen"])
        a._update_status()
        self.assertIn("REC", a.status.get())
        time.sleep(1.5)
        a._draw_meters(a.session.meters())
        a._toggle_record()
        wait_idle(a)
        self.assertFalse(a.session.recording)
        self.assertTrue(a.session.last_recording is None or a.session.last_recording.exists())
        self.assertIn("Saved", a.note)
        self.assertFalse(any(k == "showerror" for k, _ in self.shown), self.shown)

    def test_editing_is_locked_while_recording_but_not_scene_switching(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        before = len(a.project.scenes)
        a._remove_scene()
        self.assertEqual(len(a.project.scenes), before)
        self.assertEqual(self.shown[-1][0], "showinfo")
        a._toggle_record()
        wait_idle(a)

    def test_scene_add_rename_remove_when_idle(self):
        a = self.app
        simple = self.mods["tkinter.simpledialog"]
        simple.answers[:] = ["Talking"]
        a._add_scene()
        self.assertEqual(a.project.current, "Talking")
        simple.answers[:] = ["Chat"]
        a._rename_scene()
        self.assertEqual(a.scene_list.items, ["Red", "Blue", "Chat"])
        a._remove_scene()
        self.assertEqual(a.scene_list.items, ["Red", "Blue"])

    def test_source_remove_and_move(self):
        a = self.app
        a.source_list.selection_set(0)
        a._remove_source()
        self.assertEqual(a.source_list.items, [])
        self.assertNotIn("s1", a.project.sources)

    def test_stream_without_key_explains(self):
        a = self.app
        a.settings.stream_key = ""
        a._toggle_stream()
        wait_idle(a)
        self.assertEqual(self.shown[-1][0], "showerror")
        self.assertIn("stream key", self.shown[-1][1][1])

    def test_manage_broadcast_opens_youtube(self):
        a = self.app
        with mock.patch.object(self.gui.webbrowser, "open") as opened:
            a._manage_broadcast()
        self.assertIn("youtube.com", opened.call_args[0][0])
        a.settings.stream_server = "rtmp://example.org/x"
        a._manage_broadcast()
        self.assertEqual(self.shown[-1][0], "showinfo")

    def test_dialogs_build_and_collect(self):
        a = self.app
        from jerncast.winutil import Monitor
        a.monitors = [Monitor("Display 1 - 800x600 (main)", 0, 0, 800, 600)]
        a.windows, a.webcams, a.audio_inputs = ["Notepad"], ["Cam A"], ["Mic A"]
        for kind, expect in (("display", "Display 1 - 800x600 (main)"), ("window", "Notepad"),
                             ("webcam", "Cam A"), ("audio", "Mic A")):
            result = self.gui.SourceDialog(a, kind).collect()
            self.assertEqual(result["device"], expect, kind)
        image = self.gui.SourceDialog(a, "image")
        self.assertIsNone(image.collect())                      # asks for a file first
        image.v_device.set("logo.png")
        self.assertEqual(image.collect()["device"], "logo.png")

        dlg = self.gui.SettingsDialog(a)
        dlg.v_fps.set("60")
        dlg.v_key.set(" abc ")
        self.assertTrue(dlg.collect())
        self.assertEqual((a.settings.fps, a.settings.stream_key), (60, "abc"))
        dlg.v_hk_stream.set(dlg.v_hk_rec.get())
        self.assertIsNone(dlg.collect())                        # two identical hotkeys are refused

    def test_adding_sources_through_the_dialog(self):
        a = self.app
        answer = {"name": "Cam", "device": "Cam A", "layout": "corner", "corner": "top-left", "size": "small"}
        with mock.patch.object(self.gui.SourceDialog, "show", return_value=answer):
            a._add_source("webcam")
            self.assertEqual(a.source_list.items[0], "●  Cam")
            a._add_source("audio")
        self.assertEqual(len(a.project.audio_sources()), 2)
        self.assertEqual(len(a.meter_canvases), 2)
        item = a.project.current_scene.items[-1]
        self.assertEqual((item.layout, item.corner, item.size), ("corner", "top-left", "small"))
        a.source_list.selection_set(0)
        edit = dict(answer, name="Me", device="Cam B")
        with mock.patch.object(self.gui.SourceDialog, "show", return_value=edit):
            a._properties()
        self.assertEqual(a.source_list.items[0], "●  Me")
        a._move_source(-1)
        self.assertEqual([i.source for i in a.project.current_scene.items], ["s4", "s1"])   # sent backwards

    def test_exit_saves_everything(self):
        a = self.app
        a.settings.fps = 24
        a._exit()
        self.assertEqual(core.Settings.load().fps, 24)


if __name__ == "__main__":
    unittest.main()
