"""The controller behind the window: scenes, recording, streaming, live changes.

The window only calls this class, so everything here can be tested without a screen.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .core import Settings, data_dir, redact, run, safe_name
from . import loopback as loopback_module
from . import vcam as vcam_module
from .engine import Consumer, Engine, EngineError, FileConsumer
from .project import (Project, box_pixels, build_plan, effective_volume, item_box, item_enabled,
                      item_filter_name, overlay_position, scale_filter_name, volume_filter_name)

log = logging.getLogger("jerncast")


class SessionError(Exception):
    pass


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H-%M-%S")


class Session:
    def __init__(self, ffmpeg: str, settings: Settings, project: Project,
                 monitors=(), encoders=("libx264",)):
        self.ffmpeg = ffmpeg
        self.settings = settings
        self.project = project
        self.monitors = list(monitors)
        self.encoders = list(encoders)
        self.engine: Engine | None = None
        self.record: Consumer | None = None
        self.stream: Consumer | None = None
        self.record_path: Path | None = None
        self.last_recording: Path | None = None
        self.preview_wanted = False
        self.vcam = None
        self.vcam_factory = vcam_module.PyVirtualCam                    # tests replace this
        self._record_has_video = True
        self.loopback_factory = loopback_module.start_system_sound     # tests replace this
        self._sent: dict[tuple[int, int], bool] = {}
        self._sent_boxes: dict[tuple[int, int], tuple] = {}     # where each source is in the running engine
        self._ops = threading.RLock()       # long actions: start/stop recording and streaming
        self._state = threading.RLock()     # quick live changes: scene, volume, mute

    # -- state ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.engine is not None

    @property
    def recording(self) -> bool:
        return self.record is not None

    @property
    def streaming(self) -> bool:
        return self.stream is not None

    @property
    def vcam_running(self) -> bool:
        return self.vcam is not None

    def record_seconds(self) -> float:
        return time.monotonic() - self.record.started_at if self.record else 0.0

    def stream_seconds(self) -> float:
        return time.monotonic() - self.stream.started_at if self.stream else 0.0

    def _encoder(self) -> str:
        want = self.settings.encoder
        if want == "auto":
            return self.encoders[0]
        return want if want in self.encoders else "libx264"

    def _logs(self) -> Path:
        folder = data_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # -- the engine ----------------------------------------------------------
    def _ensure_engine(self) -> bool:
        """Start the capture engine if it is not running. True if this call started it."""
        if self.engine is not None and self.engine.alive:
            return False
        if self.engine is not None:
            self._drop_dead_engine()
        feeder = None
        if any(src.kind == "loopback" for src in self.project.audio_sources()):
            try:
                feeder = self.loopback_factory()
            except loopback_module.LoopbackError as exc:
                raise SessionError(str(exc)) from exc
        try:
            plan = build_plan(self.ffmpeg, self.project, self.settings, self._encoder(), self.monitors,
                              loopback=feeder.params if feeder else None)
        except ValueError as exc:
            if feeder:
                feeder.stop()
            raise SessionError(str(exc)) from exc
        engine = Engine(self.ffmpeg, plan, data_dir(), self._logs() / f"{_stamp()} engine.log")
        if feeder:
            engine.cleanups.append(feeder.stop)
        try:
            engine.start()
        except EngineError as exc:
            if feeder:
                feeder.stop()
            raise SessionError("Could not start capturing:\n\n" + str(exc)) from exc
        with self._state:
            self.engine = engine
            self._sent = {}
            self._sent_boxes = {}
            cw, ch = self.settings.canvas_size
            for si, scene in enumerate(self.project.scenes):
                for ii, item in enumerate(scene.items):
                    self._sent[(si, ii)] = item_enabled(self.project, si, item)
                    self._sent_boxes[(si, ii)] = box_pixels(item_box(item, cw, ch), cw, ch)
        return True

    def _drop_dead_engine(self) -> None:
        engine, self.engine = self.engine, None
        self.record = self.stream = self.vcam = None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                log.exception("closing a dead engine failed")

    def _stop_engine_if_idle(self) -> None:
        if (self.engine is not None and not self.record and not self.stream and not self.vcam
                and not self.preview_wanted):
            engine, self.engine = self.engine, None
            engine.stop()

    # -- preview -------------------------------------------------------------
    def set_preview(self, wanted: bool) -> None:
        """Show the live picture even when not recording (this keeps capturing, which uses CPU)."""
        with self._ops:
            self.preview_wanted = wanted
            if wanted:
                self._ensure_engine()
            else:
                self._stop_engine_if_idle()

    def preview_path(self) -> Path | None:
        engine = self.engine
        return engine.preview_path if engine is not None and engine.alive else None

    # -- recording -----------------------------------------------------------
    def start_recording(self) -> Path:
        with self._ops:
            if self.record:
                return self.record_path
            started = self._ensure_engine()
            folder = Path(os.path.expanduser(self.settings.folder))
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._rollback(started)
                raise SessionError(f"Cannot use the folder {folder}: {exc}") from exc
            out = folder / f"{safe_name(self.settings.prefix or 'JernCast')} {_stamp()}.ts"
            try:
                consumer = FileConsumer("record", out, self._logs() / f"{_stamp()} record.log")
            except OSError as exc:
                self._rollback(started)
                raise SessionError(f"Could not create the recording file:\n{exc}") from exc
            self.engine.add_consumer(consumer)
            self.record, self.record_path = consumer, out
            self._record_has_video = self.engine.plan.has_video
            return out

    def stop_recording(self) -> Path | None:
        with self._ops:
            consumer, path = self.record, self.record_path
            if consumer is None:
                return None
            self.engine.remove_consumer("record") if self.engine else None
            self.record = self.record_path = None
            consumer.close()
            final = self._finish(path)
            self.last_recording = final
            self._stop_engine_if_idle()
            return final

    def _finish(self, path: Path) -> Path | None:
        """Turn the .ts file into the final MP4 (or MKV) with a quick copy, no re-encoding."""
        if not path.exists() or path.stat().st_size == 0:
            return None
        has_video = self._record_has_video
        if self.settings.mp4:
            target = path.with_suffix(".mp4" if has_video else ".m4a")
        else:
            target = path.with_suffix(".mkv" if has_video else ".mka")
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
               "-map", "0:v?", "-map", "0:a?", "-c", "copy"]
        if target.suffix in (".mp4", ".m4a"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(target))
        try:
            ok = run(cmd, 3600).returncode == 0 and target.exists() and target.stat().st_size > 0
        except Exception:
            log.exception("convert failed")
            ok = False
        if not ok:
            return path                       # keep the .ts, it still plays
        if not self.settings.keep_mkv:
            try:
                path.unlink()
            except OSError:
                pass
        return target

    # -- streaming -----------------------------------------------------------
    def start_stream(self) -> None:
        with self._ops:
            if self.stream:
                return
            server, key = self.settings.stream_server.strip(), self.settings.stream_key.strip()
            if not server or not key:
                raise SessionError("Enter the stream server and your stream key in the Stream card first.")
            if not self.project.has_video():
                raise SessionError("Live streaming needs a picture: add a display, window or webcam source.")
            started = self._ensure_engine()
            target = server.rstrip("/") + "/" + key
            cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "warning",
                   "-probesize", "500000", "-analyzeduration", "1000000",
                   "-f", "mpegts", "-i", "pipe:0", "-map", "0:v?", "-map", "0:a?",
                   "-c", "copy", "-flvflags", "no_duration_filesize", "-f", "flv", target]
            consumer = Consumer("stream", cmd, self._logs() / f"{_stamp()} stream.log",
                                droppable=True, secrets=[target, key])
            self.engine.add_consumer(consumer)
            deadline = time.monotonic() + 6.0            # time for the connection to fail
            while time.monotonic() < deadline and consumer.alive:
                time.sleep(0.1)
            if not consumer.alive:
                message = consumer.problem()
                self.engine.remove_consumer("stream")
                consumer.close()
                self._rollback(started)
                raise SessionError("Could not connect to the live stream.\n"
                                   "Check the server address, the stream key and your internet.\n\n"
                                   + message)
            self.stream = consumer

    def stop_stream(self) -> None:
        with self._ops:
            consumer = self.stream
            if consumer is None:
                return
            if self.engine:
                self.engine.remove_consumer("stream")
            self.stream = None
            consumer.close()
            self._stop_engine_if_idle()

    # -- virtual camera ------------------------------------------------------
    def start_vcam(self) -> None:
        with self._ops:
            if self.vcam:
                return
            if not self.project.has_video():
                raise SessionError("The virtual camera needs a picture: add a display, window or webcam source.")
            started = self._ensure_engine()
            width, height = self.settings.canvas_size
            try:
                camera = self.vcam_factory(width, height, self.settings.fps)
            except vcam_module.VCamError as exc:
                self._rollback(started)
                raise SessionError(str(exc)) from exc
            consumer = vcam_module.VCamConsumer(self.ffmpeg, camera, width, height,
                                                self._logs() / f"{_stamp()} vcam.log")
            self.engine.add_consumer(consumer)
            deadline = time.monotonic() + 6.0                  # wait for the first picture to arrive
            while time.monotonic() < deadline and consumer.alive and consumer.frames == 0:
                time.sleep(0.1)
            if not consumer.alive or consumer.frames == 0:
                message = consumer.problem()
                self.engine.remove_consumer("vcam")
                consumer.close()
                self._rollback(started)
                raise SessionError("The virtual camera did not start." + (f"\n\n{message}" if message else ""))
            self.vcam = consumer

    def stop_vcam(self) -> None:
        with self._ops:
            consumer = self.vcam
            if consumer is None:
                return
            if self.engine:
                self.engine.remove_consumer("vcam")
            self.vcam = None
            consumer.close()
            self._stop_engine_if_idle()

    def _rollback(self, started_engine: bool) -> None:
        if started_engine:
            self._stop_engine_if_idle()

    # -- live changes --------------------------------------------------------
    def switch_scene(self, name: str) -> None:
        with self._state:
            self.project.scene(name)                     # KeyError if unknown
            self.project.current = name
            self._sync_enables()

    def set_visible(self, scene_name: str, index: int, visible: bool) -> None:
        with self._state:
            self.project.scene(scene_name).items[index].visible = visible
            self._sync_enables()

    def _sync_enables(self) -> None:
        if self.engine is None or not self.engine.alive:
            return
        for si, scene in enumerate(self.project.scenes):
            for ii, item in enumerate(scene.items):
                want = item_enabled(self.project, si, item)
                if self._sent.get((si, ii)) != want:
                    if self.engine.send(item_filter_name(si, ii), "enable", "1" if want else "0"):
                        self._sent[(si, ii)] = want

    def set_item_box(self, scene_name: str, index: int, box) -> None:
        """Move / resize a source (box = x, y, w, h as fractions of the picture). Works live."""
        with self._state:
            scene = self.project.scene(scene_name)
            item = scene.items[index]
            item.layout, item.box = "free", [float(v) for v in box]
            si = self.project.scenes.index(scene)
            if self.engine is None or not self.engine.alive or (si, index) not in self._sent_boxes:
                return
            cw, ch = self.settings.canvas_size
            new = box_pixels(item_box(item, cw, ch), cw, ch)
            old = self._sent_boxes[(si, index)]
            if new == old:
                return
            if new[2:] != old[2:]:                                  # the size changed too
                self.engine.send(scale_filter_name(si, index), "width", str(new[2]))
                self.engine.send(scale_filter_name(si, index), "height", str(new[3]))
            x, y = overlay_position(*new)
            self.engine.send(item_filter_name(si, index), "x", x)
            self.engine.send(item_filter_name(si, index), "y", y)
            self._sent_boxes[(si, index)] = new

    def set_volume(self, source_id: str, volume: float) -> None:
        with self._state:
            src = self.project.sources[source_id]
            src.volume = max(0.0, min(2.0, volume))
            self._push_volume(src)

    def set_muted(self, source_id: str, muted: bool) -> None:
        with self._state:
            src = self.project.sources[source_id]
            src.muted = muted
            self._push_volume(src)

    def _push_volume(self, src) -> None:
        if self.engine is not None and self.engine.alive:
            self.engine.send(volume_filter_name(src.id), "volume", f"{effective_volume(src):.3f}")

    def pids(self) -> list[int]:
        """Process ids JernCast started (for the memory readout)."""
        found = []
        engine = self.engine
        if engine is not None and engine.proc is not None:
            found.append(engine.proc.pid)
        for consumer in (self.stream, self.vcam):
            if consumer is not None and getattr(consumer, "proc", None) is not None:
                found.append(consumer.proc.pid)
        return found

    def meters(self) -> dict[str, float]:
        engine = self.engine
        return engine.meters() if engine is not None and engine.alive else {}

    # -- watching ------------------------------------------------------------
    def poll(self) -> list[str]:
        """Call regularly. Returns messages for the user if something stopped by itself."""
        messages: list[str] = []
        if not self._ops.acquire(blocking=False):      # a start/stop is in progress: ask again later
            return messages
        try:
            engine = self.engine
            if engine is None:
                return messages
            if not engine.alive:
                note = "Capturing stopped unexpectedly."
                if self.stream:
                    note += " The live stream has ended."
                if self.record:
                    note += " Your recording so far was saved."
                messages.append(note)
                record = self.record_path
                self.record = self.stream = None
                self.engine = None
                try:
                    engine.stop()
                finally:
                    if record is not None:
                        self.last_recording = self._finish(record)
                return messages
            if self.stream is not None and not self.stream.alive:
                problem = self.stream.problem()
                engine.remove_consumer("stream")
                self.stream.close()
                self.stream = None
                messages.append("The live stream was disconnected (internet or server problem).\n"
                                "Your recording, if one is running, is still going. Press Start "
                                "Streaming to go live again.\n\n" + problem)
            if self.vcam is not None and not self.vcam.alive:
                engine.remove_consumer("vcam")
                self.vcam.close()
                self.vcam = None
                messages.append("The virtual camera stopped. Press Start Virtual Camera to turn it on again.")
            if self.record is not None and not self.record.alive:
                engine.remove_consumer("record")
                path = self.record_path
                self.record.close()
                self.record = self.record_path = None
                self.last_recording = self._finish(path)
                messages.append("The recording stopped unexpectedly. What was recorded is saved.")
            self._stop_engine_if_idle()
        finally:
            self._ops.release()
        return messages

    def shutdown(self) -> None:
        with self._ops:
            try:
                self.stop_vcam()
            except Exception:
                log.exception("stop virtual camera failed")
            try:
                self.stop_stream()
            except Exception:
                log.exception("stop stream failed")
            try:
                self.stop_recording()
            except Exception:
                log.exception("stop recording failed")
            if self.engine is not None:
                engine, self.engine = self.engine, None
                engine.stop()
