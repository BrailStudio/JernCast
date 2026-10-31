import os
import tempfile
import unittest

from jerncast import core
from jerncast.core import Settings

NEW_STYLE = '''[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Camera" (video)
[dshow @ 000001]   Alternative name "@device_pnp_\\\\?\\usb#vid_04f2"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone (Realtek(R) Audio)" (audio)
[dshow @ 000001]   Alternative name "@device_cm_{33D9A762}"
[dshow @ 000001]  "Stereo Mix (Realtek(R) Audio)" (audio)
[dshow @ 000001]  "OBS Virtual Camera" (none)
'''
OLD_STYLE = '''[dshow @ 0] DirectShow video devices
[dshow @ 0]  "USB Webcam"
[dshow @ 0]     Alternative name "@device_pnp_x"
[dshow @ 0] DirectShow audio devices
[dshow @ 0]  "Line In (USB)"
'''


class CoreTests(unittest.TestCase):
    def test_dshow_new_style(self):
        video, audio = core.parse_dshow(NEW_STYLE)
        self.assertEqual(video, ["Integrated Camera"])
        self.assertEqual(audio, ["Microphone (Realtek(R) Audio)", "Stereo Mix (Realtek(R) Audio)"])

    def test_dshow_old_style(self):
        video, audio = core.parse_dshow(OLD_STYLE)
        self.assertEqual((video, audio), (["USB Webcam"], ["Line In (USB)"]))

    def test_safe_name_and_time(self):
        self.assertEqual(core.safe_name('Mic: "Main" / 1?'), 'Mic_ _Main_ _ 1_')
        self.assertEqual(core.safe_name("..."), "source")
        self.assertEqual(core.format_time(3725), "01:02:05")

    def test_redact(self):
        self.assertEqual(core.redact("a rtmp://x/live/SECRET b", ["rtmp://x/live/SECRET", "SECRET"]), "a **** b")

    def test_canvas_size(self):
        self.assertEqual(Settings(canvas="1920x1080").canvas_size, (1920, 1080))
        self.assertEqual(Settings(canvas="garbage").canvas_size, (1280, 720))
        self.assertEqual(Settings(canvas="1281x721").canvas_size, (1280, 720))

    def test_encoder_args_cap_the_bitrate(self):
        for enc in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf"):
            text = " ".join(core.encoder_args(enc, "medium", 3000, 30))
            self.assertIn("3000k", text)
            self.assertIn("-g 30", text)

    def test_manage_broadcast_url(self):
        self.assertIn("youtube.com", core.manage_broadcast_url("rtmp://a.rtmp.youtube.com/live2"))
        self.assertIn("twitch", core.manage_broadcast_url("rtmp://live.twitch.tv/app"))
        self.assertEqual(core.manage_broadcast_url("rtmp://example.org/x"), "")

    def test_settings_roundtrip_and_damage(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            Settings(fps=60, stream_key="k").save()
            self.assertEqual(Settings.load().fps, 60)
            (core.data_dir() / "settings.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(Settings.load().fps, 30)


if __name__ == "__main__":
    unittest.main()
