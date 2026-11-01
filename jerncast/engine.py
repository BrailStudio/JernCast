"""The running FFmpeg "engine" and the outputs (recording, live stream) fed from it.

The engine captures everything and encodes the picture ONCE, writing an MPEG-TS
stream to its output pipe. JernCast relays that stream to *consumers*: one that
saves a file and one that sends the live stream. Each is a small FFmpeg doing
`-c copy`, so they can be started and stopped independently, and a dropped
internet connection cannot stop the recording.
"""
from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .core import CREATE_NO_WINDOW, redact, tail
from .project import Plan

log = logging.getLogger("jerncast")

TS_PACKET = 188


class EngineError(Exception):
    pass


class Consumer:
    """A `ffmpeg -c copy` process fed from the engine's stream."""

    def __init__(self, name: str, cmd: list[str], log_path: Path, *, droppable: bool,
                 secrets=()):
        self.name = name
        self.log_path = log_path
        self.secrets = [s for s in secrets if s]
        self.droppable = droppable          # a stalled live stream drops data instead of stalling the rest
        self.broken = False
        self.dropped = 0
        self.started_at = time.monotonic()
        log.info("starting %s: %s", name, redact(subprocess.list2cmdline(cmd), self.secrets))
        self._logfile = open(log_path, "wb")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=self._logfile, creationflags=CREATE_NO_WINDOW)
        self._queue: queue.Queue = queue.Queue(maxsize=300)     # about 13 MB of video
        self._thread = threading.Thread(target=self._write_loop, daemon=True)
        self._thread.start()

    def feed(self, data: bytes) -> None:
        if self.broken:
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            if self.droppable:
                self.dropped += 1
            else:
                try:
                    self._queue.put(data, timeout=10)
                except queue.Full:
                    self.broken = True

    def _write_loop(self) -> None:
        while True:
            data = self._queue.get()
            if data is None:
                break
            if self.broken:
                continue
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.broken = True
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None and not self.broken

    def problem(self) -> str:
        """FFmpeg's last words (key hidden), for error messages."""
        try:
            self._logfile.flush()
        except (OSError, ValueError):
            pass
        return redact(tail(self.log_path), self.secrets)

    def close(self, timeout: float = 60) -> None:
        try:
            self._queue.put(None, timeout=5)
        except queue.Full:
            self.broken = True                    # the writer now throws data away and reaches None
            self._queue.put(None)
        self._thread.join(timeout)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._logfile.close()
        if self.secrets and self.log_path.exists():          # never leave a stream key in a log
            try:
                self.log_path.write_text(redact(self.log_path.read_text(
                    encoding="utf-8", errors="replace"), self.secrets), encoding="utf-8")
            except OSError:
                pass


class Engine:
    def __init__(self, ffmpeg: str, plan: Plan, work_dir: Path, log_path: Path,
                 startup_wait: float = 2.0):
        self.ffmpeg = ffmpeg
        self.plan = plan
        self.log_path = log_path
        self.meters_dir = work_dir / "meters"
        self.startup_wait = startup_wait
        self.consumers: dict[str, Consumer] = {}
        self.cleanups: list = []           # things to stop with the engine (e.g. the system-sound feeder)
        self.started_at: float | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._logfile = None
        self.proc: subprocess.Popen | None = None
        self._relay: threading.Thread | None = None

    # -- life cycle ---------------------------------------------------------
    def start(self) -> None:
        shutil.rmtree(self.meters_dir, ignore_errors=True)
        self.meters_dir.mkdir(parents=True, exist_ok=True)
        self._logfile = open(self.log_path, "wb")
        log.info("starting engine: %s", subprocess.list2cmdline(self.plan.cmd))
        try:
            self.proc = subprocess.Popen(
                self.plan.cmd, cwd=str(self.meters_dir), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self._logfile, creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            raise EngineError(f"Could not start FFmpeg: {exc}") from exc
        self._relay = threading.Thread(target=self._relay_loop, daemon=True)
        self._relay.start()
        deadline = time.monotonic() + self.startup_wait
        while time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.1)
        if self.proc.poll() is not None:
            self._relay.join(2)
            message = tail(self.log_path, 10)
            self._logfile.close()
            raise EngineError(message or "FFmpeg stopped right after starting.")
        self.started_at = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _relay_loop(self) -> None:
        buf = b""
        while True:
            try:
                chunk = self.proc.stdout.read1(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk
            whole = len(buf) // TS_PACKET * TS_PACKET      # always hand out whole TS packets
            if not whole:
                continue
            out, buf = buf[:whole], buf[whole:]
            with self._lock:
                targets = list(self.consumers.values())
            for consumer in targets:
                consumer.feed(out)

    def stop(self) -> None:
        """Quit the engine, let the data it still holds reach the consumers, close them."""
        if self.proc is not None:
            if self.alive:
                try:
                    with self._send_lock:
                        self.proc.stdin.write(b"q")
                        self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            if self._relay is not None:
                self._relay.join(10)
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass
        with self._lock:
            consumers = list(self.consumers.values())
            self.consumers.clear()
        for consumer in consumers:
            consumer.close()
        if self._logfile is not None:
            self._logfile.close()
        for cleanup in self.cleanups:
            try:
                cleanup()
            except Exception:
                log.exception("engine cleanup failed")
        shutil.rmtree(self.meters_dir, ignore_errors=True)

    # -- consumers ----------------------------------------------------------
    def add_consumer(self, consumer: Consumer) -> None:
        with self._lock:
            self.consumers[consumer.name] = consumer

    def remove_consumer(self, name: str) -> Consumer | None:
        with self._lock:
            return self.consumers.pop(name, None)

    @property
    def preview_path(self) -> Path | None:
        return self.meters_dir / self.plan.preview_file if self.plan.preview_file else None

    # -- live control -------------------------------------------------------
    def send(self, target: str, command: str, arg: str) -> bool:
        """Tell a filter in the running engine to change something (scene, volume...)."""
        if not self.alive:
            return False
        line = f"c{target} -1 {command} {arg}\n".encode()
        try:
            with self._send_lock:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def meters(self) -> dict[str, float]:
        """Latest level of every audio source in dB (0 is the loudest, -100 is silence)."""
        levels: dict[str, float] = {}
        now = time.time()
        for sid, name in self.plan.meter_files.items():
            level = -100.0
            path = self.meters_dir / name
            try:
                if now - path.stat().st_mtime < 1.5:
                    with open(path, "rb") as fh:
                        fh.seek(0, 2)
                        fh.seek(max(0, fh.tell() - 600))
                        text = fh.read().decode("utf-8", errors="replace")
                    found = re.findall(r"Peak_level=(-?inf|-?[\d.]+)", text)
                    if found and "inf" not in found[-1]:
                        level = max(-100.0, min(0.0, float(found[-1])))
            except (OSError, ValueError):
                pass
            levels[sid] = level
        return levels
