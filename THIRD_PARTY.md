# Third-party software

JernCast records by running **FFmpeg** (https://ffmpeg.org) as a separate
program, `ffmpeg.exe`, shipped next to `JernCast.exe`. The build used is the
GPL build from https://github.com/BtbN/FFmpeg-Builds (FFmpeg is licensed under
the GPL for this build, and includes x264). Its source code and licence are
available from FFmpeg and from the BtbN project above.

JernCast's own code (the `jerncast` folder) is MIT licensed, see `LICENSE`.
The installer's Python runtime and Tk are under the Python Software
Foundation licence; PyInstaller is used to package the app.

System sound capture uses **PyAudioWPatch** (MIT licence, based on PyAudio and PortAudio), a Windows
WASAPI loopback fork of PyAudio: https://github.com/s0d3s/PyAudioWPatch
