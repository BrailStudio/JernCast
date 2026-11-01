"""Turns the simple choices in the window (screen, webcam, mic...) into scenes and sources.

The window offers four ready-made layouts instead of a scene editor. Behind them
they are ordinary scenes, so switching layout works live, like switching scenes.
"""
from __future__ import annotations

from .core import Settings
from .project import Project, Scene, SceneItem, SourceDef

LAYOUT_NAMES = {
    1: "Screen only",
    2: "Screen + webcam",
    3: "Webcam only",
    4: "Webcam + small screen",
}


def available_layouts(settings: Settings) -> list[int]:
    return [1, 2, 3, 4] if settings.webcam else [1]


def layout_scene_name(number: int) -> str:
    return f"{number}  {LAYOUT_NAMES[number]}"


def project_from_settings(settings: Settings) -> Project:
    """Build the project (sources + the layout scenes) described by the settings."""
    p = Project()
    if settings.window:
        p.sources["screen"] = SourceDef("screen", "window", "Window", settings.window)
    else:
        p.sources["screen"] = SourceDef("screen", "display", "Screen", settings.screen)
    have_cam = bool(settings.webcam)
    if have_cam:
        p.sources["webcam"] = SourceDef("webcam", "webcam", "Webcam", settings.webcam)

    if settings.mic and settings.mic_on:
        p.sources["mic"] = SourceDef("mic", "audio", "Microphone", settings.mic,
                                     volume=settings.mic_volume / 100)
    if settings.desktop and settings.desktop_on:
        kind = "loopback" if settings.desktop == "loopback" else "audio"
        device = "" if kind == "loopback" else settings.desktop
        p.sources["desktop"] = SourceDef("desktop", kind, "Desktop sound", device,
                                         volume=settings.desktop_volume / 100)

    corner = dict(layout="corner", corner=settings.cam_corner, size=str(settings.cam_size))
    scenes = {
        1: [SceneItem("screen")],
        2: [SceneItem("screen"), SceneItem("webcam", **corner)],
        3: [SceneItem("webcam")],
        # the webcam fills the picture and the screen sits on top of it in a corner
        4: [SceneItem("webcam"), SceneItem("screen", layout="corner", corner="bottom-right", size="25")],
    }
    for number in available_layouts(settings):
        p.scenes.append(Scene(layout_scene_name(number), scenes[number]))
    wanted = layout_scene_name(settings.layout if settings.layout in available_layouts(settings) else 1)
    p.current = wanted
    return p
