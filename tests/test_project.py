import os
import tempfile
import unittest

from jerncast import core
from jerncast.core import Settings
from jerncast.project import Project, build_plan, layout_rects
from jerncast.winutil import Monitor

MON = [Monitor("Display 1 - 1920x1080 (main)", 0, 0, 1920, 1080)]


def sample():
    p = Project.default()                       # "Scene" with a display capture
    cam = p.add_source("webcam", "Webcam", "Cam A")
    p.add_item("Scene", cam.id, layout="corner", corner="top-left", size="large")
    p.add_scene("Just me")
    p.add_item("Just me", cam.id)               # the same webcam in a second scene
    p.add_source("audio", "Mic", "Mic A")
    p.add_source("audio", "Desktop", "Stereo Mix")
    return p


class ModelTests(unittest.TestCase):
    def test_default_project(self):
        p = Project.default()
        self.assertEqual([s.name for s in p.scenes], ["Scene"])
        self.assertEqual(p.current, "Scene")
        self.assertTrue(p.has_video())

    def test_scene_names_are_unique_and_rename_follows_current(self):
        p = Project.default()
        self.assertEqual(p.add_scene("Scene").name, "Scene 2")
        p.current = "Scene 2"
        self.assertEqual(p.rename_scene("Scene 2", "Scene"), "Scene 2")    # "Scene" is taken
        self.assertEqual(p.rename_scene("Scene 2", "Talking"), "Talking")
        self.assertEqual(p.current, "Talking")

    def test_cannot_remove_last_scene_and_unused_sources_go(self):
        p = sample()
        with self.assertRaises(ValueError):
            Project.default().remove_scene("Scene")
        p.remove_scene("Just me")
        self.assertIn("s2", p.sources)                 # still used by "Scene"
        p.remove_item("Scene", 1)
        self.assertNotIn("s2", p.sources)              # the webcam is no longer used anywhere
        self.assertEqual(len(p.audio_sources()), 2)    # audio is global, it stays

    def test_move_item(self):
        p = sample()
        self.assertEqual(p.move_item("Scene", 0, 1), 1)
        self.assertEqual(p.move_item("Scene", 1, 1), 1)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            p = sample()
            p.current = "Just me"
            p.sources["s4"].volume, p.sources["s4"].muted = 0.5, True
            p.save()
            q = Project.load()
            self.assertEqual(q.current, "Just me")
            self.assertEqual(q.to_dict(), p.to_dict())
            (core.data_dir() / "project.json").write_text("nonsense", encoding="utf-8")
            self.assertEqual(Project.load().scenes[0].name, "Scene")


class PlanTests(unittest.TestCase):
    def plan(self, project=None, **settings):
        return build_plan("ffmpeg", project or sample(), Settings(**settings), "libx264", MON)

    def test_graph_contents(self):
        plan = self.plan()
        text = " ".join(plan.cmd)
        self.assertIn("-i desktop", text)
        self.assertIn("-offset_x 0 -offset_y 0 -video_size 1920x1080", text)
        self.assertIn("-i video=Cam A", text)
        self.assertIn("split=2", text)                                  # webcam used in two scenes
        self.assertIn("overlay@ov0_0=x=(W-w)/2:y=(H-h)/2:enable=1", text)
        self.assertIn("overlay@ov0_1=x=24:y=24:enable=1", text)         # corner, top-left
        self.assertIn("overlay@ov1_0=x=(W-w)/2:y=(H-h)/2:enable=0", text)   # other scene is off
        self.assertIn("scale=422:-2", text)                             # 33 percent of 1280
        self.assertIn("amix=inputs=2", text)
        self.assertIn("volume@vol_s4=volume=1.000", text)
        self.assertIn("direct=1", text)
        self.assertEqual(sorted(plan.meter_files), ["s3", "s4"])
        self.assertEqual(plan.cmd[-1], "pipe:1")

    def test_muted_source_starts_silent_and_hidden_item_starts_off(self):
        p = sample()
        p.sources["s4"].muted = True
        p.scene("Scene").items[1].visible = False
        text = " ".join(self.plan(p).cmd)
        self.assertIn("volume@vol_s4=volume=0.000", text)
        self.assertIn("overlay@ov0_1=x=24:y=24:enable=0", text)

    def test_current_scene_decides_what_is_on(self):
        p = sample()
        p.current = "Just me"
        text = " ".join(self.plan(p).cmd)
        self.assertIn("overlay@ov0_0=x=(W-w)/2:y=(H-h)/2:enable=0", text)
        self.assertIn("overlay@ov1_0=x=(W-w)/2:y=(H-h)/2:enable=1", text)

    def test_audio_only_and_empty(self):
        p = Project()
        p.add_scene("Scene")
        with self.assertRaises(ValueError):
            self.plan(p)
        p.add_source("audio", "Mic", "Mic A")
        plan = self.plan(p)
        self.assertFalse(plan.has_video)
        self.assertTrue(plan.has_audio)
        self.assertNotIn("libx264", " ".join(plan.cmd))

    def test_hardware_encoder_uses_nv12(self):
        plan = build_plan("ffmpeg", sample(), Settings(), "h264_qsv", MON)
        self.assertIn("format=nv12", " ".join(plan.cmd))

    def test_layout_rects(self):
        p = sample()
        rects = layout_rects(p, p.scene("Scene"), 640, 360)
        self.assertEqual(rects[0][1:], (0, 0, 640, 360))
        name, x0, y0, x1, y1 = rects[1]
        self.assertEqual(name, "Webcam")
        self.assertEqual((x0, y0), (12, 12))                 # top-left corner with a small margin
        p.scene("Scene").items[1].visible = False
        self.assertEqual(len(layout_rects(p, p.scene("Scene"), 640, 360)), 1)


if __name__ == "__main__":
    unittest.main()
