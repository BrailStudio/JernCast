"""Scenes, sources and the FFmpeg graph that draws them.

A *source* is a thing you capture (screen, window, webcam, picture, audio input).
A *scene* is a list of video sources placed on the picture. Audio sources are
global: they are in the mixer whichever scene is showing, as in OBS.

JernCast starts ONE FFmpeg "engine" that captures every source and draws every
scene; changing scene, hiding a source, changing volume or muting is then done by
sending that FFmpeg a command, so it works while you are live.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from .core import (Settings, data_dir, encoder_args, pix_fmt_for)

log = logging.getLogger("jerncast")

VIDEO_KINDS = ("display", "window", "webcam", "image", "test_color")
AUDIO_KINDS = ("audio", "test_audio")
KIND_LABELS = {
    "display": "Display capture", "window": "Window capture", "webcam": "Webcam",
    "image": "Image", "audio": "Audio input",
    "test_color": "Test colour", "test_audio": "Test tone",
}
CORNER_FRACTION = {"small": 0.18, "medium": 0.25, "large": 0.33}
CORNER_POS = {
    "bottom-right": "W-w-24:H-h-24",
    "bottom-left": "24:H-h-24",
    "top-right": "W-w-24:24",
    "top-left": "24:24",
}
AUDIO_FILTER = "aresample=async=1:first_pts=0"
METER_SAMPLES = 4800          # level meters update 10 times a second at 48 kHz


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

@dataclasses.dataclass
class SourceDef:
    id: str
    kind: str
    name: str
    device: str = ""            # monitor label / window title / device name / image path
    volume: float = 1.0         # audio only: 0.0 - 2.0
    muted: bool = False

    @property
    def is_audio(self) -> bool:
        return self.kind in AUDIO_KINDS


@dataclasses.dataclass
class SceneItem:
    source: str
    visible: bool = True
    layout: str = "fill"        # fill | corner
    corner: str = "bottom-right"
    size: str = "medium"


@dataclasses.dataclass
class Scene:
    name: str
    items: list[SceneItem] = dataclasses.field(default_factory=list)   # back to front


@dataclasses.dataclass
class Project:
    sources: dict[str, SourceDef] = dataclasses.field(default_factory=dict)
    scenes: list[Scene] = dataclasses.field(default_factory=list)
    current: str = ""

    # -- creation ---------------------------------------------------------
    @classmethod
    def default(cls) -> "Project":
        p = cls()
        p.add_scene("Scene")
        src = p.add_source("display", "Display capture")
        p.add_item("Scene", src.id)
        return p

    # -- lookups ----------------------------------------------------------
    def scene(self, name: str) -> Scene:
        for s in self.scenes:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def current_scene(self) -> Scene:
        return self.scene(self.current)

    @property
    def current_index(self) -> int:
        return [s.name for s in self.scenes].index(self.current)

    def audio_sources(self) -> list[SourceDef]:
        return [s for s in self.sources.values() if s.is_audio]

    def video_source_ids(self) -> list[str]:
        """Video sources used by at least one scene item, in first-use order."""
        seen: list[str] = []
        for scene in self.scenes:
            for item in scene.items:
                if item.source not in seen and not self.sources[item.source].is_audio:
                    seen.append(item.source)
        return seen

    def has_video(self) -> bool:
        return bool(self.video_source_ids())

    # -- scenes -----------------------------------------------------------
    def _unique(self, base: str, taken: list[str]) -> str:
        name, n = base, 2
        while name in taken:
            name, n = f"{base} {n}", n + 1
        return name

    def add_scene(self, name: str = "Scene") -> Scene:
        scene = Scene(self._unique(name.strip() or "Scene", [s.name for s in self.scenes]))
        self.scenes.append(scene)
        if not self.current:
            self.current = scene.name
        return scene

    def rename_scene(self, old: str, new: str) -> str:
        scene = self.scene(old)
        new = new.strip()
        if not new or new == old:
            return old
        scene.name = self._unique(new, [s.name for s in self.scenes if s is not scene])
        if self.current == old:
            self.current = scene.name
        return scene.name

    def remove_scene(self, name: str) -> None:
        if len(self.scenes) <= 1:
            raise ValueError("A project needs at least one scene.")
        scene = self.scene(name)
        self.scenes.remove(scene)
        if self.current == name:
            self.current = self.scenes[0].name
        self._drop_unused_video_sources()

    # -- sources ----------------------------------------------------------
    def add_source(self, kind: str, name: str = "", device: str = "") -> SourceDef:
        n = 1
        while f"s{n}" in self.sources:
            n += 1
        label = self._unique(name.strip() or KIND_LABELS.get(kind, kind),
                             [s.name for s in self.sources.values()])
        src = SourceDef(f"s{n}", kind, label, device)
        self.sources[src.id] = src
        return src

    def add_item(self, scene_name: str, source_id: str, **kw) -> SceneItem:
        item = SceneItem(source_id, **kw)
        self.scene(scene_name).items.append(item)
        return item

    def remove_item(self, scene_name: str, index: int) -> None:
        del self.scene(scene_name).items[index]
        self._drop_unused_video_sources()

    def remove_audio(self, source_id: str) -> None:
        if self.sources.get(source_id) and self.sources[source_id].is_audio:
            del self.sources[source_id]

    def move_item(self, scene_name: str, index: int, delta: int) -> int:
        items = self.scene(scene_name).items
        new = index + delta
        if 0 <= new < len(items):
            items[index], items[new] = items[new], items[index]
            return new
        return index

    def _drop_unused_video_sources(self) -> None:
        used = {i.source for s in self.scenes for i in s.items}
        for sid in [k for k, v in self.sources.items() if not v.is_audio and k not in used]:
            del self.sources[sid]

    # -- saving -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {"current": self.current,
                "sources": [dataclasses.asdict(s) for s in self.sources.values()],
                "scenes": [{"name": s.name, "items": [dataclasses.asdict(i) for i in s.items]}
                           for s in self.scenes]}

    @classmethod
    def from_dict(cls, raw: dict) -> "Project":
        p = cls()
        for s in raw.get("sources", []):
            src = SourceDef(**{k: v for k, v in s.items() if k in SourceDef.__dataclass_fields__})
            p.sources[src.id] = src
        for sc in raw.get("scenes", []):
            items = [SceneItem(**{k: v for k, v in i.items() if k in SceneItem.__dataclass_fields__})
                     for i in sc.get("items", []) if i.get("source") in p.sources]
            p.scenes.append(Scene(sc.get("name", "Scene"), items))
        if not p.scenes:
            return cls.default()
        p.current = raw.get("current") if raw.get("current") in [s.name for s in p.scenes] \
            else p.scenes[0].name
        return p

    @classmethod
    def load(cls) -> "Project":
        try:
            return cls.from_dict(json.loads((data_dir() / "project.json").read_text(encoding="utf-8")))
        except Exception:
            return cls.default()

    def save(self) -> None:
        try:
            (data_dir() / "project.json").write_text(json.dumps(self.to_dict(), indent=2),
                                                     encoding="utf-8")
        except OSError:
            log.exception("could not save project")


# --------------------------------------------------------------------------
# Capture inputs
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Capture:
    """A source with everything FFmpeg needs (monitor position, etc.) filled in."""
    kind: str                    # screen | window | webcam | image | audio | test_color | test_audio
    name: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


def input_args(cap: Capture, fps: int) -> list[str]:
    if cap.kind == "screen":
        args = ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1"]
        if cap.w and cap.h:
            args += ["-offset_x", str(cap.x), "-offset_y", str(cap.y),
                     "-video_size", f"{cap.w}x{cap.h}"]
        return args + ["-i", "desktop"]
    if cap.kind == "window":
        return ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
                "-i", f"title={cap.name}"]
    if cap.kind == "webcam":
        return ["-f", "dshow", "-rtbufsize", "256M", "-use_wallclock_as_timestamps", "1",
                "-i", f"video={cap.name}"]
    if cap.kind == "image":
        return ["-loop", "1", "-framerate", str(fps), "-i", cap.name]
    if cap.kind == "audio":
        return ["-f", "dshow", "-thread_queue_size", "1024",
                "-use_wallclock_as_timestamps", "1", "-i", f"audio={cap.name}"]
    if cap.kind == "test_color":                       # used by the tests
        return ["-re", "-f", "lavfi", "-i", f"color=c={cap.name or 'red'}:s=640x360:r={fps}"]
    if cap.kind == "test_audio":
        return ["-re", "-f", "lavfi", "-i", f"sine=frequency={cap.name or 440}:sample_rate=48000"]
    raise ValueError(f"unknown source kind {cap.kind!r}")


def resolve(src: SourceDef, monitors: list) -> Capture:
    """Turn a saved source into a capture (look up the monitor by its label)."""
    if src.kind == "display":
        mon = next((m for m in monitors if m.name == src.device), None) or (monitors[0] if monitors else None)
        if mon is None:
            return Capture("screen")
        return Capture("screen", mon.name, mon.x, mon.y, mon.w, mon.h)
    kind = {"window": "window", "webcam": "webcam", "image": "image", "audio": "audio",
            "test_color": "test_color", "test_audio": "test_audio"}[src.kind]
    return Capture(kind, src.device)


# --------------------------------------------------------------------------
# The engine command
# --------------------------------------------------------------------------

def item_filter_name(scene_index: int, item_index: int) -> str:
    return f"overlay@ov{scene_index}_{item_index}"


def volume_filter_name(source_id: str) -> str:
    return f"volume@vol_{source_id}"


def effective_volume(src: SourceDef) -> float:
    return 0.0 if src.muted else max(0.0, min(2.0, src.volume))


def item_enabled(project: Project, scene_index: int, item: SceneItem) -> bool:
    return scene_index == project.current_index and item.visible


@dataclasses.dataclass
class Plan:
    cmd: list[str]
    has_video: bool
    has_audio: bool
    meter_files: dict[str, str]          # audio source id -> file name (inside the meters folder)


def build_plan(ffmpeg: str, project: Project, settings: Settings, encoder: str,
               monitors: list) -> Plan:
    fps = settings.fps
    cw, ch = settings.canvas_size
    pix = pix_fmt_for(encoder)

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
    graph: list[str] = []
    maps: list[str] = []
    count = 0

    def add(args: list[str]) -> int:
        nonlocal count
        cmd.extend(args)
        count += 1
        return count - 1

    # ---- picture
    video_ids = project.video_source_ids()
    has_video = bool(video_ids)
    if has_video:
        base_idx = add(["-re", "-f", "lavfi", "-i", f"color=c=black:s={cw}x{ch}:r={fps}"])
        src_index = {sid: add(input_args(resolve(project.sources[sid], monitors), fps))
                     for sid in video_ids}
        uses: dict[str, list[tuple[int, int]]] = {sid: [] for sid in video_ids}
        for si, scene in enumerate(project.scenes):
            for ii, item in enumerate(scene.items):
                uses[item.source].append((si, ii))
        branch: dict[tuple[int, int], str] = {}
        for sid in video_ids:
            places = uses[sid]
            if len(places) == 1:
                graph.append(f"[{src_index[sid]}:v]fps={fps}[v_{sid}_0]")
                branch[places[0]] = f"v_{sid}_0"
            else:
                outs = "".join(f"[v_{sid}_{k}]" for k in range(len(places)))
                graph.append(f"[{src_index[sid]}:v]fps={fps},split={len(places)}{outs}")
                for k, place in enumerate(places):
                    branch[place] = f"v_{sid}_{k}"

        last = f"{base_idx}:v"
        for si, scene in enumerate(project.scenes):
            for ii, item in enumerate(scene.items):
                if (si, ii) not in branch:
                    continue
                if item.layout == "corner":
                    width = int(cw * CORNER_FRACTION.get(item.size, 0.25)) // 2 * 2
                    scale = f"scale={width}:-2,setsar=1"
                    pos = CORNER_POS.get(item.corner, CORNER_POS["bottom-right"])
                    x, y = pos.split(":")
                else:
                    scale = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,setsar=1")
                    x, y = "(W-w)/2", "(H-h)/2"
                enable = 1 if item_enabled(project, si, item) else 0
                graph.append(f"[{branch[(si, ii)]}]{scale}[i{si}_{ii}]")
                out = f"o{si}_{ii}"
                graph.append(f"[{last}][i{si}_{ii}]{item_filter_name(si, ii)}="
                             f"x={x}:y={y}:enable={enable}:eof_action=repeat[{out}]")
                last = out
        graph.append(f"[{last}]format={pix}[v]")
        maps += ["-map", "[v]"]

    # ---- sound
    audios = project.audio_sources()
    meter_files: dict[str, str] = {}
    mix_labels: list[str] = []
    for src in audios:
        idx = add(input_args(resolve(src, monitors), fps))
        meter = f"meter_{src.id}.txt"
        meter_files[src.id] = meter
        graph.append(
            f"[{idx}:a]{AUDIO_FILTER},{volume_filter_name(src.id)}=volume={effective_volume(src):.3f}"
            f":eval=once,asplit=2[m_{src.id}][t_{src.id}]")
        graph.append(
            f"[t_{src.id}]asetnsamples=n={METER_SAMPLES}:p=0,astats=metadata=1:reset=1,"
            f"ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file={meter}:direct=1,anullsink")
        mix_labels.append(f"[m_{src.id}]")
    has_audio = bool(audios)
    if has_audio:
        if len(mix_labels) == 1:
            graph.append(f"{mix_labels[0]}alimiter=limit=0.95[a]")
        else:
            n = len(mix_labels)
            graph.append(f"{''.join(mix_labels)}amix=inputs={n}:duration=longest:"
                         f"dropout_transition=0,volume={n},alimiter=limit=0.95[a]")
        maps += ["-map", "[a]"]

    if not has_video and not has_audio:
        raise ValueError("Add at least one source first.")

    cmd += ["-filter_complex", ";".join(graph), *maps]
    if has_video:
        cmd += encoder_args(encoder, settings.quality, settings.kbps, fps)
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += ["-f", "mpegts", "-muxdelay", "0", "-flush_packets", "1", "pipe:1"]
    return Plan(cmd, has_video, has_audio, meter_files)


# --------------------------------------------------------------------------
# Drawing the scene (a simple layout view, no capture)
# --------------------------------------------------------------------------

def layout_rects(project: Project, scene: Scene, width: int, height: int) -> list[tuple]:
    """(label, x0, y0, x1, y1) for each visible item, back to front, in a width x height box."""
    rects = []
    for item in scene.items:
        src = project.sources.get(item.source)
        if src is None or not item.visible:
            continue
        if item.layout == "corner":
            w = int(width * CORNER_FRACTION.get(item.size, 0.25))
            h = int(w * 9 / 16)
            m = int(width * 0.02)
            x0 = m if "left" in item.corner else width - w - m
            y0 = m if "top" in item.corner else height - h - m
            rects.append((src.name, x0, y0, x0 + w, y0 + h))
        else:
            rects.append((src.name, 0, 0, width, height))
    return rects
