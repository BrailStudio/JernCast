<p align="center"><img src="assets/jerncast-logo.png" alt="JernCast" width="240"></p>

# JernCast 0.0.1

A screen recorder and live streamer for Windows 10/11, laid out like OBS.

| Dock | What it does |
| --- | --- |
| **Scenes** | Make several scenes (for example "Game", "Just me", "Be right back") and click one to switch to it, even while you are live. Add, remove, rename. |
| **Sources** | What is in the current scene: screen, a single window, webcam, picture. Show/hide each one, move it forward or back, make it fill the picture or sit small in a corner. |
| **Audio Mixer** | Every microphone or Stereo Mix you add gets a volume slider, a Mute box and a live level meter. All work while recording or streaming. |
| **Controls** | **Start Streaming**, **Start Recording**, **Manage Broadcast**, **Settings**, Open recordings folder, Exit. |

- Streaming and recording are separate buttons. Start either one first, stop either one without stopping the other.
- If your internet drops, the stream ends and a message tells you, but the recording on your PC keeps going.
- Hotkeys (change them in Settings): **F9** start/stop recording, **F10** start/stop streaming.
- Records to MKV (safe if the PC crashes) and converts to MP4 when you stop.
- Uses your graphics card's encoder when it works (NVIDIA, Intel, AMD), otherwise the CPU.
- Light enough for a PC with 4 GB of RAM (use a smaller picture size and `low` quality in Settings).

JernCast is a window around [FFmpeg](https://ffmpeg.org), which does the actual capturing and encoding.

## Get it

### Easiest: build the installer on GitHub (nothing to install on your PC)

1. Make a GitHub repository (private is fine) and upload everything in this folder.
2. Open the **Actions** tab, choose **JernCast Windows installer**, press **Run workflow**.
3. After about 10 minutes, open the run and download **Jerncast-0.0.1-Setup**. Unzip it and run the setup.

### Or run it right now without building

1. Install Python 3.10 or newer from https://www.python.org/downloads/ and tick **Add python.exe to PATH**.
2. Double-click **run.bat**. The first time, it downloads FFmpeg (about 150 MB).

### Or build the installer on your own PC

```powershell
scripts\build-windows.ps1 -Installer     # needs Python, and Inno Setup 6 for the installer
```

## Going live on YouTube

1. In YouTube Studio press **Create > Go live > Stream** and copy the **Stream key**.
2. In JernCast open **Settings > Stream**, keep the YouTube server, paste the key, **Save**.
   In **Settings > Output** start with a bitrate of **4500** and picture size **1280x720**
   (use **854x480** and **2500** on a slow PC or connection).
3. Press **Start Streaming**. JernCast checks the connection and shows an error if YouTube can't be reached.
4. Press **Manage Broadcast** to open YouTube's live control page. When the preview looks good there,
   press its own **Go live** button. Press **Stop Streaming** here to end.

The stream key is saved only on your PC, in `%LOCALAPPDATA%\JernCast\settings.json`, and JernCast hides it
in logs and messages. Treat it like a password. Your upload speed must be higher than the bitrate.
Twitch works too (server `rtmp://live.twitch.tv/app`).

## First steps

1. Open JernCast. There is one scene with a **Display capture**.
2. **+ Audio input** in the mixer: add your microphone (and **Stereo Mix** if your sound card has it,
   which is how you record what the PC plays).
3. **+ Add > Webcam** in Sources, choose **Small, in a corner**. Make a second scene without it.
4. Press **Start Recording** (or F9). Click between scenes. Press it again to stop; the MP4 is in `Videos\JernCast`.

## Notes and limits (version 0.0.1)

- **No live picture preview yet.** The big area shows a layout view of the current scene (boxes), not the
  real picture. A real preview is planned.
- **Add, remove and move sources and scenes while stopped.** While recording or streaming you can switch
  scenes, show/hide sources, change volume and mute, but not add or remove. Stop first.
- **Every source in every scene is captured at the same time** (so switching is instant). Keep the number
  of sources small on a slow PC.
- **PC sound** needs "Stereo Mix" (Sound settings > Recording devices > right-click > Show disabled devices)
  or a virtual audio cable. Windows does not allow FFmpeg to record "what you hear" directly.
- Window capture records the window's size at the moment you start. Some protected windows (video apps with
  DRM) record as black. If a webcam will not start, close other apps using it (Teams, Zoom, browser).
- The "separate file per source" mode from the first preview was replaced by scenes.
- Logs and settings are in `%LOCALAPPDATA%\JernCast`. If something fails, the newest file in `logs` shows FFmpeg's message.

## For developers

`python -m unittest discover -s tests` runs 42 tests with real FFmpeg and generated test sources (they work on
any OS; the window's tests use a stand-in for tkinter). The code is in `jerncast/`:

- `core.py` settings, devices, encoders · `project.py` scenes, sources and the FFmpeg graph
- `engine.py` the running FFmpeg and its outputs · `session.py` what the buttons call
- `gui.py` the window · `winutil.py` monitors, windows, hotkeys

How it works: one FFmpeg process captures every source, draws every scene, mixes the audio and encodes once.
Scene switches, hiding, volume and mute are commands sent to that running FFmpeg. Its output is relayed to two
small `ffmpeg -c copy` processes, one saving the file and one sending the stream, which is why each can start
and stop on its own.

MIT licensed, see `LICENSE`. FFmpeg notice in `THIRD_PARTY.md`.
