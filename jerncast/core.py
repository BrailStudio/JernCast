"""JernCast core: settings, finding FFmpeg, listing devices, encoders.

Nothing in here needs a window, so it can be tested without a screen.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("jerncast")

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


# --------------------------------------------------------------------------
# Folders and settings
# --------------------------------------------------------------------------

def data_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    folder = base / "JernCast"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_output_folder() -> str:
    return str(Path.home() / "Videos" / "JernCast")


def assets_dir() -> Path:
    here = Path(__file__).resolve().parent.parent
    for candidate in (Path(getattr(sys, "_MEIPASS", here)) / "assets", here / "assets"):
        if candidate.is_dir():
            return candidate
    return here / "assets"


@dataclasses.dataclass
class Settings:
    # output
    folder: str = dataclasses.field(default_factory=default_output_folder)
    prefix: str = "JernCast"        # start of recording file names
    canvas: str = "1280x720"        # the size of the picture that is recorded / streamed
    fps: int = 30
    quality: str = "medium"         # high | medium | low
    kbps: int = 4500                # top video bitrate (keeps the stream within your upload speed)
    encoder: str = "auto"
    mp4: bool = True                # convert to MP4 when finished
    keep_mkv: bool = False
    duration_min: int = 0           # stop recording by itself after this many minutes (0 = never)
    show_cursor: bool = True
    # capture
    screen: str = ""                # monitor label ("" = the first one)
    window: str = ""                # "" = the whole screen, otherwise a window title
    # audio
    mic: str = ""
    mic_on: bool = True
    mic_volume: int = 100           # percent, 0-200
    desktop: str = "loopback"       # "loopback" = system sound, "" = off, otherwise a device name
    desktop_on: bool = True
    desktop_volume: int = 100
    # webcam and layout
    webcam: str = ""
    layout: int = 1                 # 1 screen, 2 screen + webcam, 3 webcam, 4 webcam + small screen
    cam_corner: str = "bottom-right"
    cam_size: int = 25              # percent of the picture width
    # stream
    stream_service: str = "YouTube"
    stream_server: str = "rtmp://a.rtmp.youtube.com/live2"
    stream_key: str = ""
    remember_key: bool = True
    # app
    hotkey_record: str = "F9"
    hotkey_stream: str = "F10"
    compact: bool = False
    recent: list = dataclasses.field(default_factory=list)

    @property
    def canvas_size(self) -> tuple[int, int]:
        m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", self.canvas.strip())
        if not m:
            return 1280, 720
        w, h = int(m.group(1)) // 2 * 2, int(m.group(2)) // 2 * 2
        return w, h

    @classmethod
    def load(cls) -> "Settings":
        path = data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            names = {f.name for f in dataclasses.fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in names})
        except Exception:  # missing or damaged file: start fresh
            return cls()

    def save(self) -> None:
        try:
            data = dataclasses.asdict(self)
            if not self.remember_key:
                data["stream_key"] = ""
            (data_dir() / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            log.exception("could not save settings")


# --------------------------------------------------------------------------
# FFmpeg and devices
# --------------------------------------------------------------------------

def find_ffmpeg() -> str | None:
    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    candidates = []
    if os.environ.get("JERNCAST_FFMPEG"):
        candidates.append(Path(os.environ["JERNCAST_FFMPEG"]))
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / exe)
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / exe)
    root = Path(__file__).resolve().parent.parent
    candidates += [root / exe, root / "tools" / exe, Path.cwd() / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("ffmpeg")


def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)


_DSHOW_TYPED = re.compile(r'"([^"]+)"\s+\((video|audio|none)\)')
_DSHOW_PLAIN = re.compile(r'\]\s+"([^"]+)"\s*$')


def parse_dshow(text: str) -> tuple[list[str], list[str]]:
    """Read `ffmpeg -list_devices true -f dshow -i dummy` output (old and new styles)."""
    video: list[str] = []
    audio: list[str] = []
    section = None
    for line in text.splitlines():
        low = line.lower()
        if "directshow video devices" in low:
            section = "video"
            continue
        if "directshow audio devices" in low:
            section = "audio"
            continue
        if "alternative name" in low:
            continue
        m = _DSHOW_TYPED.search(line)
        if m:
            name, kind = m.groups()
            target = {"video": video, "audio": audio}.get(kind)
        else:
            m = _DSHOW_PLAIN.search(line)
            if not m or section is None:
                continue
            name = m.group(1)
            target = video if section == "video" else audio
        if target is not None and name not in target:
            target.append(name)
    return video, audio


def list_devices(ffmpeg: str) -> tuple[list[str], list[str]]:
    """(webcams, audio inputs) as DirectShow names. Empty lists off Windows."""
    if not IS_WINDOWS:
        return [], []
    try:
        res = run([ffmpeg, "-hide_banner", "-list_devices", "true",
                   "-f", "dshow", "-i", "dummy"], 20)
        return parse_dshow((res.stderr or "") + "\n" + (res.stdout or ""))
    except Exception:
        log.exception("device listing failed")
        return [], []


# Quality presets: (x264 crf, x264 preset, NVENC cq)
_QUALITY = {
    "high":   (19, "veryfast", 20),
    "medium": (23, "superfast", 24),
    "low":    (28, "ultrafast", 29),
}
HARDWARE_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")


def pix_fmt_for(encoder: str) -> str:
    return "nv12" if encoder in ("h264_qsv", "h264_amf") else "yuv420p"


def encoder_args(encoder: str, quality: str, kbps: int, fps: int) -> list[str]:
    """Video encoder settings: good quality, but never above `kbps` (so it can be streamed)."""
    crf, preset, cq = _QUALITY.get(quality, _QUALITY["medium"])
    cap = ["-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
    gop = str(max(1, fps))                       # a keyframe every second
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(cq),
                "-b:v", "0", *cap, "-g", gop]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate",
                f"{kbps}k", "-g", gop]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "vbr_peak",
                "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-g", gop]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), *cap,
            "-g", gop, "-sc_threshold", "0"]


def probe_encoder(ffmpeg: str, encoder: str) -> bool:
    """True if a real 3-frame test encode with this encoder works on this PC."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "color=c=black:s=640x360:r=30", "-frames:v", "3",
           "-vf", f"format={pix_fmt_for(encoder)}",
           *encoder_args(encoder, "medium", 4500, 30), "-f", "null", "-"]
    try:
        return run(cmd, 25).returncode == 0
    except Exception:
        return False


def working_encoders(ffmpeg: str) -> list[str]:
    """Encoders that really work here, best first. libx264 is the fallback."""
    found = ["libx264"]
    try:
        listing = run([ffmpeg, "-hide_banner", "-encoders"], 15).stdout or ""
    except Exception:
        return found
    hardware = [e for e in HARDWARE_ENCODERS if e in listing and probe_encoder(ffmpeg, e)]
    return hardware + found


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def redact(text: str, secrets) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "****")
    return text


def tail(path: Path, lines: int = 8) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def safe_name(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return cleaned[:60] or "source"


def format_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


SERVICES = {
    "YouTube": "rtmp://a.rtmp.youtube.com/live2",
    "Twitch": "rtmp://live.twitch.tv/app",
    "Custom": "",
}
OUTPUT_SIZES = {"480p": "854x480", "720p": "1280x720", "1080p": "1920x1080"}


def manage_broadcast_url(server: str) -> str:
    """Where 'Manage Broadcast' opens, depending on the streaming service."""
    low = (server or "").lower()
    if "youtube" in low or "youtu.be" in low:
        return "https://www.youtube.com/live_dashboard"
    if "twitch" in low:
        return "https://dashboard.twitch.tv/stream-manager"
    return ""
