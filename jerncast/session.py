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

from .core import (Settings, data_dir, redact, run)
from .engine import Consumer, Engine, EngineError
from .project import (Project, build_plan, item_enabled, item_filter_name, volume_filter_name,
                      effective_volume)

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
        self._sent: dict[tuple[int, int], bool] = {}
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
        try:
            plan = build_plan(self.ffmpeg, self.project, self.settings, self._encoder(), self.monitors)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        engine = Engine(self.ffmpeg, plan, data_dir(), self._logs() / f"{_stamp()} engine.log")
        try:
            engine.start()
        except EngineError as exc:
            raise SessionError("Could not start capturing:\n\n" + str(exc)) from exc
        with self._state:
            self.engine = engine
            self._sent = {}
            for si, scene in enumerate(self.project.scenes):
                for ii, item in enumerate(scene.items):
                    self._sent[(si, ii)] = item_enabled(self.project, si, item)
        return True

    def _drop_dead_engine(self) -> None:
        engine, self.engine = self.engine, None
        self.record = self.stream = None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                log.exception("closing a dead engine failed")

    def _stop_engine_if_idle(self) -> None:
        if self.engine is not None and not self.record and not self.stream:
            engine, self.engine = self.engine, None
            engine.stop()

    # -- recording -----------------------------------------------------------
    def start_recording(self) -> Path:
        with self._ops:
            if self.record:
                return self.record_path
            started = self._ensure_engine()
            ext = ".mkv" if self.engine.plan.has_video else ".mka"
            folder = Path(os.path.expanduser(self.settings.folder))
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._rollback(started)
                raise SessionError(f"Cannot use the folder {folder}: {exc}") from exc
            out = folder / f"JernCast {_stamp()}{ext}"
            cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
                   "-probesize", "500000", "-analyzeduration", "1000000",
                   "-f", "mpegts", "-i", "pipe:0", "-map", "0:v?", "-map", "0:a?",
                   "-c", "copy", "-f", "matroska", str(out)]
            consumer = Consumer("record", cmd, self._logs() / f"{_stamp()} record.log", droppable=False)
            self.engine.add_consumer(consumer)
            self.record, self.record_path = consumer, out
            time.sleep(0.8)
            if not consumer.alive:
                message = consumer.problem()
                self.engine.remove_consumer("record")
                consumer.close()
                self.record = self.record_path = None
                self._rollback(started)
                raise SessionError("Could not start the recording.\n\n" + message)
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
        if not path.exists() or path.stat().st_size == 0:
            return None
        if not self.settings.mp4:
            return path
        target = path.with_suffix(".mp4" if path.suffix == ".mkv" else ".m4a")
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
               "-map", "0", "-c", "copy", "-movflags", "+faststart", str(target)]
        try:
            ok = run(cmd, 3600).returncode == 0 and target.exists() and target.stat().st_size > 0
        except Exception:
            log.exception("convert failed")
            ok = False
        if not ok:
            return path                       # keep the MKV, it is still fine
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
                raise SessionError("Open Settings > Stream and enter the server and your stream key.")
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
