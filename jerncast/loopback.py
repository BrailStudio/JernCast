"""System sound ("what you hear") on Windows, with no Stereo Mix needed.

FFmpeg cannot record Windows' sound output by itself. This module captures it with
WASAPI loopback (the `pyaudiowpatch` package) and hands it to FFmpeg over a local
socket as raw audio. If the package is missing, JernCast tells the person to use
Stereo Mix instead.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from array import array

log = logging.getLogger("jerncast")

CHUNK_SECONDS = 0.02            # audio is sent to FFmpeg in 20 ms pieces, silence if nothing plays


class LoopbackError(Exception):
    pass


class WasapiLoopback:
    """Captures the default speakers' output. Windows only."""

    def __init__(self):
        try:
            import pyaudiowpatch as pyaudio
        except Exception as exc:
            raise LoopbackError("System sound capture needs the 'pyaudiowpatch' package, which is "
                                "not installed. Pick a Stereo Mix device for desktop sound instead.") from exc
        self._pyaudio = pyaudio
        self._pa = pyaudio.PyAudio()
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            device = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not device.get("isLoopbackDevice"):
                for candidate in self._pa.get_loopback_device_info_generator():
                    if device["name"] in candidate["name"]:
                        device = candidate
                        break
                else:
                    raise LoopbackError("Could not find a loopback version of your speakers.")
        except LoopbackError:
            raise
        except Exception as exc:
            raise LoopbackError(f"Windows sound is not available for capture: {exc}") from exc
        self.device = device
        self.rate = int(device["defaultSampleRate"])
        self.device_channels = int(device["maxInputChannels"])
        self.channels = 2                         # always sent as stereo
        self._stream = None

    def start(self, callback) -> None:
        pyaudio, ch = self._pyaudio, self.device_channels

        def on_audio(in_data, _frames, _time_info, _status):
            callback(in_data if ch == 2 else downmix_to_stereo(in_data, ch))
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(format=pyaudio.paInt16, channels=ch, rate=self.rate,
                                     frames_per_buffer=1024, input=True,
                                     input_device_index=self.device["index"], stream_callback=on_audio)

    def stop(self) -> None:
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
            self._pa.terminate()
        except Exception:
            log.exception("closing system sound capture failed")


def downmix_to_stereo(data: bytes, channels: int) -> bytes:
    """Keep the front left and right channels of interleaved 16-bit audio."""
    samples = array("h")
    samples.frombytes(data[: len(data) // (2 * channels) * (2 * channels)])
    out = array("h", bytes(2 * 2 * (len(samples) // channels)))
    out[0::2] = samples[0::channels]
    out[1::2] = samples[1::channels]
    return out.tobytes()


class ToneSource:
    """A stand-in sound source for tests: a steady beep, or silence."""

    rate, channels = 48000, 2

    def __init__(self, silent: bool = False):
        self.silent = silent
        self._stop = threading.Event()

    def start(self, callback) -> None:
        def run():
            n = 0
            while not self._stop.is_set():
                frames = 480                                      # 10 ms
                if not self.silent:
                    out = array("h")
                    for i in range(frames):
                        v = 8000 if ((n + i) // 55) % 2 else -8000    # about 436 Hz square wave
                        out.extend((v, v))
                    n += frames
                    callback(out.tobytes())
                time.sleep(0.01)
        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


class Feeder:
    """Serves a sound source to FFmpeg as a steady stream of raw audio on a local port."""

    def __init__(self, source):
        self.source = source
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self.params = (self.port, source.rate, source.channels)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "Feeder":
        self.source.start(self._on_audio)
        self._thread.start()
        return self

    def _on_audio(self, data: bytes) -> None:
        with self._lock:
            self._buffer += data

    def _serve(self) -> None:
        chunk = int(self.source.rate * CHUNK_SECONDS) * self.source.channels * 2
        try:
            self._server.settimeout(60)
            conn, _ = self._server.accept()
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        next_time = time.monotonic()
        try:
            while not self._stop.is_set():
                with self._lock:
                    data = bytes(self._buffer[:chunk])
                    del self._buffer[:chunk]
                    if len(self._buffer) > chunk * 15:            # fell behind: skip ahead, stay live
                        del self._buffer[: len(self._buffer) - chunk * 3]
                if len(data) < chunk:
                    data += bytes(chunk - len(data))              # silence keeps FFmpeg's clock running
                conn.sendall(data)
                next_time += CHUNK_SECONDS
                delay = next_time - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1:
                    next_time = time.monotonic()
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.source.stop()
        finally:
            self._server.close()


def start_system_sound() -> Feeder:
    """Start capturing the PC's sound output. Raises LoopbackError if it cannot."""
    return Feeder(WasapiLoopback()).start()
