"""Real FFmpeg, generated test sources (coloured screens and a tone), no screen needed."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from jerncast import core
from jerncast.core import Settings
from jerncast.project import Project
from jerncast.session import Session, SessionError

FFMPEG = core.find_ffmpeg()
FFPROBE = shutil.which("ffprobe")
needs = unittest.skipUnless(FFMPEG and FFPROBE, "needs ffmpeg and ffprobe")


def make_project() -> Project:
    p = Project()
    p.add_scene("Red")
    p.add_scene("Blue")
    red = p.add_source("test_color", "Red screen", "red")
    blue = p.add_source("test_color", "Blue screen", "blue")
    p.add_item("Red", red.id)
    p.add_item("Blue", blue.id)
    p.add_item("Blue", red.id, layout="corner", corner="top-left", size="small")   # red inside blue
    p.add_source("test_audio", "Tone", "440")
    return p


def colour_at(path, seconds, x=None):
    """Rough (r, g, b) of the middle of the frame at `seconds`."""
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(seconds), "-i", str(path),
         "-frames:v", "1", "-vf", "crop=iw/4:ih/4:iw*3/8:ih*3/8,scale=1:1,format=rgb24",
         "-f", "rawvideo", "-"], capture_output=True).stdout
    return tuple(out[:3])


def kinds_and_duration(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type",
                          "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    data = json.loads(out)
    return sorted(s["codec_type"] for s in data["streams"]), float(data["format"]["duration"])


class Receiver:
    """A throw-away local RTMP server (another ffmpeg) that saves what it is sent."""

    def __init__(self, tmp, key):
        self.port = 19400 + (os.getpid() + hash(key)) % 500
        self.file = Path(tmp) / f"received-{key}.flv"
        self.proc = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-listen", "1",
             "-i", f"rtmp://127.0.0.1:{self.port}/live/{key}", "-c", "copy", str(self.file)],
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    @property
    def server(self):
        return f"rtmp://127.0.0.1:{self.port}/live"

    def finish(self):
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def kill(self):
        self.proc.kill()


@needs
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_DATA_HOME"] = self.tmp
        self.settings = Settings(folder=self.tmp + "/out", canvas="640x360", kbps=1500)
        self.project = make_project()
        self.session = Session(FFMPEG, self.settings, self.project)

    def tearDown(self):
        self.session.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_with_live_scene_switch(self):
        s = self.session
        s.start_recording()
        time.sleep(3)
        s.switch_scene("Blue")
        time.sleep(3)
        path = s.stop_recording()
        self.assertEqual(path.suffix, ".mp4")
        kinds, duration = kinds_and_duration(path)
        self.assertEqual(kinds, ["audio", "video"])
        self.assertGreater(duration, 4)
        r, g, b = colour_at(path, 1.0)
        self.assertGreater(r, 200)
        self.assertLess(b, 60)                                  # red scene first
        r, g, b = colour_at(path, duration - 1.2)
        self.assertGreater(b, 200)
        self.assertLess(r, 60)                                  # then blue
        self.assertFalse(s.running)                             # engine stopped with nothing left to do

    def test_hide_and_show_a_source_live(self):
        s = self.session
        s.start_recording()
        time.sleep(2)
        s.set_visible("Red", 0, False)
        time.sleep(2.5)
        path = s.stop_recording()
        _, duration = kinds_and_duration(path)
        r, g, b = colour_at(path, duration - 1.0)
        self.assertLess(max(r, g, b), 60)                       # nothing shown: black canvas

    def test_volume_meter_and_mute_live(self):
        s = self.session
        s.start_recording()
        time.sleep(2.5)
        before = s.meters()["s3"]
        self.assertGreater(before, -40)
        s.set_muted("s3", True)
        time.sleep(1.5)
        self.assertLess(s.meters()["s3"], -80)
        s.set_muted("s3", False)
        s.set_volume("s3", 0.5)
        time.sleep(1.5)
        half = s.meters()["s3"]
        self.assertGreater(half, -60)
        self.assertLess(half, before - 3)                       # about 6 dB quieter
        s.stop_recording()

    def test_audio_only_recording(self):
        p = Project()
        p.add_scene("Scene")
        p.add_source("test_audio", "Tone", "440")
        s = Session(FFMPEG, self.settings, p)
        s.start_recording()
        time.sleep(2)
        path = s.stop_recording()
        self.assertEqual(path.suffix, ".m4a")
        self.assertEqual(kinds_and_duration(path)[0], ["audio"])
        with self.assertRaises(SessionError):                   # no picture, so no live stream
            self.settings.stream_key = "k"
            s.start_stream()

    def test_empty_project_gives_a_clear_error(self):
        p = Project()
        p.add_scene("Scene")
        with self.assertRaises(SessionError) as ctx:
            Session(FFMPEG, self.settings, p).start_recording()
        self.assertIn("Add at least one source", str(ctx.exception))

    def test_capture_failure_shows_ffmpeg_message(self):
        p = Project()
        p.add_scene("Scene")
        src = p.add_source("image", "Picture", "/no/such/picture.png")
        p.add_item("Scene", src.id)
        s = Session(FFMPEG, self.settings, p)
        with self.assertRaises(SessionError) as ctx:
            s.start_recording()
        self.assertIn("picture.png", str(ctx.exception))
        self.assertFalse(s.running)

    def test_stream_and_record_together_then_stop_each_independently(self):
        rx = Receiver(self.tmp, "KEY1")
        self.settings.stream_server, self.settings.stream_key = rx.server, "KEY1"
        s = self.session
        s.start_recording()
        time.sleep(2)
        s.start_stream()                                        # go live while already recording
        time.sleep(3)
        self.assertTrue(s.streaming and s.recording)
        s.stop_stream()                                         # stop the stream, recording goes on
        rx.finish()
        self.assertFalse(s.streaming)
        self.assertTrue(s.recording)
        self.assertTrue(s.running)
        time.sleep(1.5)
        self.assertEqual(s.poll(), [])
        path = s.stop_recording()
        self.assertFalse(s.running)
        kinds, _ = kinds_and_duration(rx.file)
        self.assertEqual(kinds, ["audio", "video"])
        kinds, duration = kinds_and_duration(path)
        self.assertEqual(kinds, ["audio", "video"])
        self.assertGreater(duration, 5)
        logs = "".join(p.read_text(errors="replace") for p in (core.data_dir() / "logs").glob("*.log"))
        self.assertNotIn("KEY1", logs)

    def test_stream_only_then_start_recording_during_it(self):
        rx = Receiver(self.tmp, "KEY2")
        self.settings.stream_server, self.settings.stream_key = rx.server, "KEY2"
        s = self.session
        s.start_stream()
        time.sleep(1.5)
        s.start_recording()
        time.sleep(2.5)
        path = s.stop_recording()
        self.assertTrue(s.running)                              # the stream keeps the engine alive
        s.stop_stream()
        rx.finish()
        self.assertFalse(s.running)
        self.assertGreater(kinds_and_duration(path)[1], 1.5)
        self.assertEqual(kinds_and_duration(rx.file)[0], ["audio", "video"])

    def test_scene_switch_reaches_the_live_stream(self):
        rx = Receiver(self.tmp, "KEY3")
        self.settings.stream_server, self.settings.stream_key = rx.server, "KEY3"
        s = self.session
        s.start_stream()
        time.sleep(3)
        s.switch_scene("Blue")
        time.sleep(3.5)
        s.stop_stream()
        rx.finish()
        _, duration = kinds_and_duration(rx.file)
        r, g, b = colour_at(rx.file, duration - 1.2)
        self.assertGreater(b, 200)

    def test_bad_server_is_reported_and_leaves_recording_alone(self):
        self.settings.stream_server, self.settings.stream_key = "rtmp://127.0.0.1:1/live", "SECRETKEY"
        s = self.session
        s.start_recording()
        with self.assertRaises(SessionError) as ctx:
            s.start_stream()
        self.assertIn("Could not connect", str(ctx.exception))
        self.assertNotIn("SECRETKEY", str(ctx.exception))
        self.assertTrue(s.recording)
        self.assertFalse(s.streaming)
        time.sleep(1)
        self.assertTrue(s.running)
        s.stop_recording()

    def test_bad_server_alone_does_not_leave_an_engine_running(self):
        self.settings.stream_server, self.settings.stream_key = "rtmp://127.0.0.1:1/live", "SECRETKEY"
        with self.assertRaises(SessionError):
            self.session.start_stream()
        self.assertFalse(self.session.running)

    def test_stream_dropping_midway_is_reported_and_recording_continues(self):
        rx = Receiver(self.tmp, "KEY4")
        self.settings.stream_server, self.settings.stream_key = rx.server, "KEY4"
        s = self.session
        s.start_recording()
        s.start_stream()
        time.sleep(2)
        rx.kill()
        messages = []
        for _ in range(40):
            messages = s.poll()
            if messages:
                break
            time.sleep(0.5)
        self.assertTrue(messages, "the dropped stream was not noticed")
        self.assertIn("disconnected", messages[0])
        self.assertNotIn("KEY4", messages[0])
        self.assertFalse(s.streaming)
        self.assertTrue(s.recording)
        self.assertEqual(s.poll(), [])
        path = s.stop_recording()
        self.assertGreater(kinds_and_duration(path)[1], 2)

    def test_engine_dying_saves_the_recording(self):
        s = self.session
        s.start_recording()
        time.sleep(2.5)
        s.engine.proc.kill()
        messages = []
        for _ in range(20):
            messages = s.poll()
            if messages:
                break
            time.sleep(0.3)
        self.assertTrue(messages)
        self.assertFalse(s.running)
        self.assertIsNotNone(s.last_recording)
        self.assertTrue(s.last_recording.exists())


if __name__ == "__main__":
    unittest.main()
