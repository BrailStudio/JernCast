"""Virtual camera: lets other programs (Zoom, Discord, browsers) use JernCast's picture as a webcam.

Windows has no built-in way to make a camera from a program, so this needs a virtual camera DRIVER installed on
the PC. JernCast sends its frames to it with the optional `pyvirtualcam` package, which works with the OBS
virtual camera (install OBS) or Unity Capture. JernCast cannot install a camera driver itself.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from .engine import Consumer

log = logging.getLogger("jerncast")

SETUP_HELP = ("The virtual camera needs two things on this PC:\n"
              "  1. the Python package 'pyvirtualcam'  (pip install pyvirtualcam)\n"
              "  2. a virtual camera driver: install OBS Studio (it includes one), or Unity Capture.")


class VCamError(Exception):
    pass


class PyVirtualCam:
    """Sends RGB frames to the installed virtual camera driver through pyvirtualcam."""

    def __init__(self, width: int, height: int, fps: int):
        try:
            import numpy as np
            import pyvirtualcam
        except Exception as exc:
            raise VCamError("The virtual camera package is not installed.\n\n" + SETUP_HELP) from exc
        try:
            self._cam = pyvirtualcam.Camera(width=width, height=height, fps=fps)
        except Exception as exc:
            raise VCamError(f"No virtual camera driver could be opened ({exc}).\n\n" + SETUP_HELP) from exc
        self._np = np
        self.width, self.height = width, height
        self.device = getattr(self._cam, "device", "virtual camera")

    def send(self, frame: bytes) -> None:
        self._cam.send(self._np.frombuffer(frame, dtype=self._np.uint8).reshape(self.height, self.width, 3))

    def close(self) -> None:
        try:
            self._cam.close()
        except Exception:
            log.exception("closing the virtual camera failed")


class MemoryCamera:
    """A stand-in camera for tests: keeps the last frame and counts frames."""

    def __init__(self, width: int, height: int, fps: int):
        self.width, self.height, self.fps = width, height, fps
        self.count = 0
        self.last = b""
        self.closed = False
        self.device = "memory camera"

    def send(self, frame: bytes) -> None:
        self.count += 1
        self.last = frame

    def close(self) -> None:
        self.closed = True


class VCamConsumer(Consumer):
    """Decodes the engine's stream to raw frames and sends each one to the virtual camera."""

    def __init__(self, ffmpeg: str, camera, width: int, height: int, log_path: Path):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-probesize", "500000", "-analyzeduration", "500000",
               "-fflags", "nobuffer", "-flags", "low_delay", "-f", "mpegts", "-i", "pipe:0",
               "-map", "0:v:0", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        super().__init__("vcam", cmd, log_path, droppable=True, stdout=subprocess.PIPE)
        self.camera = camera
        self.frames = 0
        self._frame_size = width * height * 3
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        stream = self.proc.stdout
        while True:
            try:
                frame = stream.read(self._frame_size)
            except (OSError, ValueError):
                break
            if len(frame) < self._frame_size:                    # the decoder ended
                break
            try:
                self.camera.send(frame)
            except Exception:
                log.exception("sending a frame to the virtual camera failed")
                self.broken = True
                break
            self.frames += 1

    def close(self, timeout: float = 60) -> None:
        super().close(timeout)
        self._reader.join(5)
        self.camera.close()
