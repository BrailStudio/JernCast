import os
import tempfile
import time
import unittest
from pathlib import Path

from jerncast import sysinfo
from jerncast.core import Settings
from jerncast.namedstore import NamedStore
from jerncast.project import Project, sync_global_audio


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_adopts_the_old_file_and_manages_names(self):
        legacy = self.tmp / "old.json"
        legacy.write_text('{"fps": 60}', encoding="utf-8")
        store = NamedStore(self.tmp / "profiles", legacy=legacy)
        self.assertEqual(store.names(), ["Default"])
        self.assertEqual(Settings.load(store.path("Default")).fps, 60)
        made = store.create("Gaming", copy_from="Default")
        self.assertEqual(made, "Gaming")
        self.assertEqual(store.create("Gaming"), "Gaming 2")
        store.set_current("Gaming")
        self.assertEqual(store.current, "Gaming")
        self.assertEqual(store.rename("Gaming", "Streaming"), "Streaming")
        self.assertEqual(store.current, "Streaming")               # the pointer followed the rename
        store.delete("Streaming")
        self.assertEqual(store.current, store.names()[0])          # fell back to an existing one
        with self.assertRaises(KeyError):
            store.set_current("nope")

    def test_cannot_delete_the_last_one(self):
        store = NamedStore(self.tmp / "c")
        with self.assertRaises(ValueError):
            store.delete("Default")

    def test_settings_and_project_use_the_given_file(self):
        store = NamedStore(self.tmp / "p")
        Settings(fps=24).save(store.path("Default"))
        self.assertEqual(Settings.load(store.path("Default")).fps, 24)
        project = Project.default()
        project.add_scene("Second")
        project.save(store.path("Default"))
        self.assertEqual([s.name for s in Project.load(store.path("Default")).scenes], ["Scene", "Second"])
        self.assertEqual(Project.load(self.tmp / "missing.json").scenes[0].name, "Scene")


class ProjectHelpers(unittest.TestCase):
    def test_move_scene(self):
        p = Project.default()
        p.add_scene("B")
        p.add_scene("C")
        self.assertEqual(p.move_scene("C", -1), 1)
        self.assertEqual([s.name for s in p.scenes], ["Scene", "C", "B"])
        self.assertEqual(p.move_scene("Scene", -1), 0)

    def test_global_audio_follows_settings_and_keeps_levels(self):
        p = Project.default()
        sync_global_audio(p, Settings(mic="Mic A", desktop="loopback"))
        self.assertEqual((p.sources["desktop"].kind, p.sources["mic"].device), ("loopback", "Mic A"))
        p.sources["mic"].volume, p.sources["mic"].muted = 0.4, True
        sync_global_audio(p, Settings(mic="Mic B", desktop="Stereo Mix"))
        self.assertEqual((p.sources["mic"].device, p.sources["mic"].volume, p.sources["mic"].muted),
                         ("Mic B", 0.4, True))
        self.assertEqual((p.sources["desktop"].kind, p.sources["desktop"].device), ("audio", "Stereo Mix"))
        sync_global_audio(p, Settings(mic="", desktop=""))
        self.assertNotIn("mic", p.sources)
        self.assertNotIn("desktop", p.sources)


@unittest.skipIf(os.name == "nt", "reads /proc")
class SysinfoTests(unittest.TestCase):
    def test_cpu_percent_is_a_sane_number(self):
        meter = sysinfo.CpuMeter()
        end = time.time() + 0.3
        while time.time() < end:
            pass                                    # keep one core busy for a moment
        value = meter.percent()
        self.assertTrue(0.0 <= value <= 100.0)

    def test_process_memory(self):
        self.assertGreater(sysinfo.process_memory_mb([os.getpid()]), 5)
        self.assertEqual(sysinfo.process_memory_mb([999999999]), 0.0)


if __name__ == "__main__":
    unittest.main()
