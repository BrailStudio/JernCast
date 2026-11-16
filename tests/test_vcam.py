import os
import shutil
import tempfile
import time
import unittest

from jerncast import core, vcam
from jerncast.core import Settings
from jerncast.project import Project
from jerncast.session import Session, SessionError

FFMPEG = core.find_ffmpeg()


def average(frame, width, height):
    n = width * height
    sums = [0, 0, 0]
    for i in range(0, n * 3, 3 * 50):                       # sample every 50th pixel
        for c in range(3):
            sums[c] += frame[i + c]
    count = len(range(0, n * 3, 3 * 50))
    return tuple(v // count for v in sums)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class VirtualCameraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.project = Project()
        self.project.add_scene("Red")
        self.project.add_scene("Blue")
        red = self.project.add_source("test_color", "Red", "red")
        blue = self.project.add_source("test_color", "Blue", "blue")
        self.project.add_item("Red", red.id)
        self.project.add_item("Blue", blue.id)
        self.settings = Settings(folder=self.tmp + "/out", canvas="320x180", fps=15)
        self.session = Session(FFMPEG, self.settings, self.project)
        self.cameras = []

        def factory(w, h, fps):
            cam = vcam.MemoryCamera(w, h, fps)
            self.cameras.append(cam)
            return cam
        self.session.vcam_factory = factory

    def tearDown(self):
        self.session.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_frames_reach_the_camera_and_follow_scene_changes(self):
        s = self.session
        s.start_vcam()
        self.assertTrue(s.vcam_running and s.running)
        time.sleep(2.5)
        cam = self.cameras[0]
        self.assertGreater(cam.count, 10)
        self.assertEqual(len(cam.last), 320 * 180 * 3)                 # raw RGB frames of the picture size
        r, g, b = average(cam.last, 320, 180)
        self.assertGreater(r, 200)
        self.assertLess(b, 60)
        s.switch_scene("Blue")
        time.sleep(3)
        r, g, b = average(cam.last, 320, 180)
        self.assertGreater(b, 200)
        self.assertLess(r, 60)
        self.assertIn(cam.device, ("memory camera",))
        s.stop_vcam()
        self.assertTrue(cam.closed)
        self.assertFalse(s.running)                                    # nothing else needed the engine

    def test_virtual_camera_and_recording_are_independent(self):
        s = self.session
        s.start_recording()
        s.start_vcam()
        time.sleep(2)
        s.stop_vcam()
        self.assertTrue(s.recording and s.running)                     # the recording carries on
        time.sleep(1)
        path = s.stop_recording()
        self.assertTrue(path.exists())
        self.assertFalse(s.running)

    def test_missing_driver_is_explained_and_leaves_nothing_running(self):
        def broken(w, h, fps):
            raise vcam.VCamError("no driver\n\n" + vcam.SETUP_HELP)
        self.session.vcam_factory = broken
        with self.assertRaises(SessionError) as ctx:
            self.session.start_vcam()
        self.assertIn("pyvirtualcam", str(ctx.exception))
        self.assertFalse(self.session.running)

    def test_needs_a_picture(self):
        p = Project()
        p.add_scene("Scene")
        p.add_source("test_audio", "Tone", "440")
        s = Session(FFMPEG, self.settings, p)
        with self.assertRaises(SessionError):
            s.start_vcam()

    def test_real_backend_without_the_package_gives_setup_help(self):
        # pyvirtualcam is not installed in the test environment
        try:
            import pyvirtualcam  # noqa: F401
            self.skipTest("pyvirtualcam is installed here")
        except ImportError:
            pass
        with self.assertRaises(vcam.VCamError) as ctx:
            vcam.PyVirtualCam(320, 180, 15)
        self.assertIn("OBS", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
