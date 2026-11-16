"""Presses the window's buttons and menus through a fake tkinter and checks what happens."""
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

from jerncast import core, loopback, project as project_module, vcam  # noqa: E402
from jerncast.core import Settings  # noqa: E402
from jerncast.project import Capture, Project  # noqa: E402

REAL_RESOLVE = project_module.resolve
MODULES = ("jerncast.gui", "jerncast.widgets", "jerncast.dockui", "jerncast.dialogs")


def fake_resolve(src, monitors, cursor=True, loopback=None):
    """On the test machine the 'screen' is a red picture, the 'webcam' a blue one, the mic a beep."""
    if src.kind in ("display", "window"):
        return Capture("test_color", "red")
    if src.kind == "webcam":
        return Capture("test_color", "blue")
    if src.kind == "audio":
        return Capture("test_audio", "440")
    return REAL_RESOLVE(src, monitors, cursor, loopback)


class MenuRecorder:
    def __init__(self):
        self.items = []

    def add_command(self, **kw):
        self.items.append(kw)

    def add_separator(self):
        pass

    def labels(self):
        return [i["label"] for i in self.items]

    def press(self, text):
        next(i for i in self.items if text in i["label"])["command"]()


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
        for name in MODULES:
            sys.modules.pop(name, None)
        # files from an older JernCast: the window adopts them as the "Default" profile and collection
        Settings(folder=self.tmp + "/videos", canvas="640x360", mic="Mic A", desktop="loopback").save()
        p = Project()
        p.add_scene("Red")
        p.add_scene("Blue")
        red = p.add_source("test_color", "Red screen", "red")
        blue = p.add_source("test_color", "Blue screen", "blue")
        p.add_item("Red", red.id)
        p.add_item("Blue", blue.id)
        p.save()
        self.gui = importlib.import_module("jerncast.gui")
        self.app = self.gui.App(self.mods["tkinter"].Tk())
        self.app.session.loopback_factory = lambda: loopback.Feeder(loopback.ToneSource()).start()
        time.sleep(1.5)
        self.app._tick()
        self.shown = self.mods["tkinter.messagebox"].shown
        self.answers = self.mods["tkinter.simpledialog"].answers

    def tearDown(self):
        try:
            self.app.session.shutdown()
        finally:
            for hk in self.app.hotkeys:
                hk.stop()
            for p in reversed(self.patches):
                p.stop()
            for name in MODULES:
                sys.modules.pop(name, None)
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_window_shows_scenes_sources_and_mixer(self):
        a = self.app
        self.assertEqual(a.scene_list.items, ["Red", "Blue"])
        self.assertEqual(a.source_list.items, ["◉   Red screen"])
        self.assertEqual(list(a.strips), ["desktop", "mic"])               # Desktop Audio, then Mic/Aux
        self.assertEqual(a.profiles.names(), ["Default"])
        self.assertEqual(a.collections.names(), ["Default"])
        self.assertEqual(len(a.hotkeys), 12)                                # record, stream, Ctrl+Alt+1..9, Ctrl+Alt+0
        self.assertEqual(a.layout.visible("left"), ["scenes", "sources"])
        self.assertEqual(a.layout.visible("bottom"), ["mixer", "controls"])

    def test_record_switch_scene_hide_mute_and_stop(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        self.assertTrue(a.session.recording, self.shown)
        a._update_buttons()
        self.assertEqual(a.rec_btn.text, "Stop Recording")
        time.sleep(2)
        a.scene_list.selection_set(1)
        a._on_scene_select()                                                # click "Blue" while recording
        self.assertEqual(a.session.project.current, "Blue")
        self.assertEqual(a.source_list.items, ["◉   Blue screen"])
        a.source_list.selection_set(0)
        a._toggle_visible()                                                 # hide it, live
        self.assertEqual(a.source_list.items, ["○   Blue screen"])
        a._on_mute("mic", True)
        a._on_volume("desktop", 50)
        self.assertTrue(a.session.project.sources["mic"].muted)
        self.assertEqual(a.session.project.sources["desktop"].volume, 0.5)
        time.sleep(1.5)
        a._draw_meters(a.session.meters())
        a._update_status()
        self.assertTrue(a.pill._shown[0].startswith("REC"))
        a._update_stats()
        self.assertIn("CPU", a.stats["text"])
        self.assertIn("RAM", a.stats["text"])
        a._toggle_record()
        wait_idle(a)
        self.assertFalse(a.session.recording)
        self.assertEqual(len(a.recent_list.items), 1)
        self.assertTrue(a.recent_list.items[0].endswith(".mp4"))
        self.assertFalse(any(k == "showerror" for k, _ in self.shown), self.shown)

    def test_scene_hotkey_event_switches_scene(self):
        a = self.app
        a.events.put(("scene", 2))
        a._tick()
        self.assertEqual(a.project.current, "Blue")
        a.events.put(("scene", 9))                                          # no such scene: ignored
        a._tick()
        self.assertEqual(a.project.current, "Blue")

    def test_scene_management(self):
        a = self.app
        self.answers[:] = ["Talking"]
        a._add_scene()
        self.assertEqual(a.project.current, "Talking")
        self.answers[:] = ["Chat"]
        a._rename_scene()
        self.assertEqual(a.scene_list.items, ["Red", "Blue", "Chat"])
        a._move_scene(-1)
        self.assertEqual(a.scene_list.items, ["Red", "Chat", "Blue"])
        a._remove_scene()
        self.assertEqual(a.scene_list.items, ["Red", "Blue"])
        saved = json.loads(a.collections.path(a.collections.current).read_text())
        self.assertEqual([s["name"] for s in saved["scenes"]], ["Red", "Blue"])

    def test_sources_add_edit_move_remove(self):
        a = self.app
        answer = {"name": "Cam", "device": "Cam A", "layout": "corner", "corner": "top-left", "size": "30"}
        with mock.patch.object(self.gui.SourceDialog, "show", return_value=answer):
            a._add_source("webcam")
            self.assertEqual(a.source_list.items[0], "◉   Cam")
            a._add_source("audio")
        self.assertEqual(len(a.project.audio_sources()), 3)                 # desktop, mic, and the new input
        self.assertEqual(len(a.strips), 3)
        a.source_list.selection_set(0)
        with mock.patch.object(self.gui.SourceDialog, "show", return_value=dict(answer, name="Me")):
            a._properties()
        self.assertEqual(a.source_list.items[0], "◉   Me")
        a._move_source(-1)                                                  # towards the back
        self.assertEqual(a.source_list.items, ["◉   Red screen", "◉   Me"])
        a.source_list.selection_set(1)
        a._remove_source()
        self.assertEqual(a.source_list.items, ["◉   Red screen"])
        a._remove_audio("s4")
        self.assertEqual(len(a.strips), 2)

    def test_editing_is_locked_while_recording_but_switching_is_not(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        before = list(a.scene_list.items)
        a._remove_scene()
        self.assertEqual(a.scene_list.items, before)
        self.assertEqual(self.shown[-1][0], "showinfo")
        a.scene_list.selection_set(1)
        a._on_scene_select()                                                # switching still works
        self.assertEqual(a.project.current, "Blue")
        a._toggle_record()
        wait_idle(a)

    def test_docks_menu_lock_reset_and_saved_layout(self):
        a = self.app
        menu = MenuRecorder()
        a._menu_docks(menu)
        self.assertIn("✓  Scenes", menu.labels()[0])
        self.assertNotIn("✓", menu.labels()[-1])                            # Recent recordings is off
        menu.press("Recent recordings")
        self.assertTrue(a.layout.is_visible("recent"))
        a._toggle_lock()
        self.assertTrue(a.layout.locked)
        view = MenuRecorder()
        a._menu_view(view)
        self.assertIn("✓  Lock Docks", view.labels()[0])
        saved = json.loads(a.dock_file.read_text())
        self.assertTrue(saved["locked"])
        self.assertNotIn("recent", saved["hidden"])
        view.press("Reset Docks")
        self.assertFalse(a.layout.is_visible("recent"))
        self.assertEqual(a.layout.visible("left"), ["scenes", "sources"])

    def test_profiles_and_scene_collections(self):
        a = self.app
        self.answers[:] = ["Gaming"]
        a._store_new(a.profiles, True)
        self.assertEqual((a.profiles.current, a.settings.mic), ("Gaming", "Mic A"))     # copied from Default
        a.settings.fps = 60
        a._save()
        a._switch_profile("Default")
        self.assertEqual(a.settings.fps, 30)
        menu = MenuRecorder()
        a._menu_profile(menu)
        self.assertIn("●  Default", menu.labels()[0] + menu.labels()[1])
        self.answers[:] = ["Podcast"]
        a._store_new(a.collections, False)
        self.assertEqual(a.collections.current, "Podcast")
        self.assertEqual(a.scene_list.items, ["Scene"])                     # a new collection starts fresh
        a._switch_collection("Default")
        self.assertEqual(a.scene_list.items, ["Red", "Blue"])
        self.answers[:] = ["Show"]
        a._store_rename(a.collections)
        self.assertEqual(a.collections.names(), ["Podcast", "Show"])
        a._store_remove(a.collections)
        self.assertEqual(a.collections.names(), ["Podcast"])

    def test_settings_dialog_changes_audio_and_hotkeys(self):
        a = self.app
        dlg = self.gui.SettingsDialog(a)
        dlg.v_desktop.set("Disabled")
        dlg.v_mic.set("Mic B")
        dlg.v_fps.set("60")
        dlg.v_key.set(" abc ")
        dlg.v_duration.set("5")
        self.assertTrue(dlg.collect())
        self.assertEqual((a.settings.desktop, a.settings.mic, a.settings.fps, a.settings.stream_key,
                          a.settings.duration_min), ("", "Mic B", 60, "abc", 5))
        dlg.v_hk_live.set(dlg.v_hk_rec.get())
        self.assertIsNone(dlg.collect())                                    # identical hotkeys are refused
        with mock.patch.object(self.gui.SettingsDialog, "show", return_value=True):
            a._open_settings()
        self.assertEqual(list(a.strips), ["mic"])                           # the mixer followed the settings
        self.assertEqual(a.project.sources["mic"].device, "Mic B")

    def test_live_preview_shows_the_real_picture(self):
        a = self.app
        a._toggle_live_preview()
        wait_idle(a)
        self.assertTrue(a.session.running and not a.session.recording)
        time.sleep(2)
        a._draw_preview()
        self.assertTrue(a.live_image_shown)
        a._toggle_live_preview()
        wait_idle(a)
        self.assertFalse(a.session.running)
        a._draw_preview()
        self.assertFalse(a.live_image_shown)                                # back to the layout sketch

    def test_start_streaming_without_key_explains(self):
        a = self.app
        a._toggle_live()
        wait_idle(a)
        kind, args = self.shown[-1]
        self.assertEqual(kind, "showerror")
        self.assertIn("stream key", args[1])

    def _view(self, pane):
        pane.redraw()
        return pane.view

    def test_dragging_and_resizing_a_source_in_the_preview(self):
        a = self.app
        pane = a.edit_pane
        a.item_box_changed(0, [0.1, 0.1, 0.3, 0.3], True)                   # a small box to play with
        vx, vy, vw, vh = self._view(pane)
        ev = lambda x, y: mock.Mock(x=x, y=y)
        pane._press(ev(vx + 0.25 * vw, vy + 0.25 * vh))                     # click inside the box
        self.assertEqual(a.selected_index(), 0)
        pane._motion(ev(vx + 0.45 * vw, vy + 0.45 * vh))                    # drag it right and down by 0.2
        pane._release(ev(vx + 0.45 * vw, vy + 0.45 * vh))
        item = a.project.scene("Red").items[0]
        self.assertEqual(item.layout, "free")
        for got, want in zip(item.box, (0.3, 0.3, 0.3, 0.3)):
            self.assertAlmostEqual(got, want, places=2)
        pane._press(ev(vx + 0.6 * vw, vy + 0.6 * vh))                       # the bottom right handle
        pane._motion(ev(vx + 0.7 * vw, vy + 0.7 * vh))
        pane._release(ev(vx + 0.7 * vw, vy + 0.7 * vh))
        self.assertAlmostEqual(item.box[2], 0.4, places=2)                  # wider and taller by 0.1
        self.assertAlmostEqual(item.box[3], 0.4, places=2)
        pane._press(ev(vx - 50, vy - 5))                                    # click outside: nothing selected
        self.assertIsNone(a.selected_index())
        self.assertTrue(a.dirty)

    def test_dragging_while_recording_reaches_the_engine(self):
        a = self.app
        a._toggle_record()
        wait_idle(a)
        a.item_box_changed(0, [0.5, 0.5, 0.25, 0.25], True)
        self.assertEqual(a.session._sent_boxes[(0, 0)], (320, 180, 160, 90))
        a._toggle_record()
        wait_idle(a)

    def test_studio_mode_edit_preview_then_transition(self):
        a = self.app
        a._toggle_studio()
        self.assertTrue(a.studio)
        a.scene_list.selection_set(1)
        a._on_scene_select()                                                # chooses what to EDIT, not what is live
        self.assertEqual((a.project.current, a.preview_name), ("Red", "Blue"))
        self.assertEqual(a.source_list.items, ["◉   Blue screen"])
        self.assertEqual(a.scene_list.items, ["▶  Red", "Blue"])
        a._transition()                                                     # Blue goes live
        self.assertEqual((a.project.current, a.preview_name), ("Blue", "Red"))
        self.assertEqual(a.scene_list.items, ["Red", "▶  Blue"])
        a._transition()                                                     # and back
        self.assertEqual(a.project.current, "Red")
        a.preview_name = a.project.current
        a._transition()
        self.assertIn("different scene", a.note)
        self.assertTrue(core.Settings.load(a.profiles.path(a.profiles.current)).studio_mode)
        a._toggle_studio()
        self.assertFalse(a.studio)

    def test_studio_mode_while_recording_switches_the_live_scene(self):
        a = self.app
        a._toggle_studio()
        a._toggle_record()
        wait_idle(a)
        a.preview_name = "Blue"
        a.source_list.selection_set(0)
        a._toggle_visible()                                                 # edit the preview scene: not live yet
        self.assertEqual(a.session.project.current, "Red")
        a._transition()
        self.assertEqual(a.session.project.current, "Blue")
        a._toggle_record()
        wait_idle(a)

    def test_virtual_camera_button(self):
        a = self.app
        cams = []

        def factory(w, h, fps):
            cam = vcam.MemoryCamera(w, h, fps)
            cams.append(cam)
            return cam
        a.session.vcam_factory = factory
        a._toggle_vcam()
        wait_idle(a)
        self.assertTrue(a.session.vcam_running, self.shown)
        a._update_buttons()
        self.assertEqual(a.vcam_btn.text, "Stop Virtual Camera")
        a._update_status()
        self.assertEqual(a.pill._shown[0], "CAMERA ON")
        time.sleep(1.5)
        self.assertGreater(cams[0].count, 3)
        a._toggle_vcam()
        wait_idle(a)
        self.assertFalse(a.session.vcam_running)
        self.assertTrue(cams[0].closed)

    def test_virtual_camera_without_a_driver_explains(self):
        a = self.app

        def broken(w, h, fps):
            raise vcam.VCamError("no driver\n\n" + vcam.SETUP_HELP)
        a.session.vcam_factory = broken
        a._toggle_vcam()
        wait_idle(a)
        kind, args = self.shown[-1]
        self.assertEqual(kind, "showerror")
        self.assertIn("OBS", args[1])

    def test_manage_broadcast_and_exit_save(self):
        a = self.app
        with mock.patch.object(self.gui.webbrowser, "open") as opened:
            a._manage_broadcast()
        self.assertIn("youtube.com", opened.call_args[0][0])
        a.settings.fps = 24
        a._exit()
        self.assertEqual(Settings.load(a.profiles.path(a.profiles.current)).fps, 24)


if __name__ == "__main__":
    unittest.main()
