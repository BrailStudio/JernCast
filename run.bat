@echo off
rem Starts JernCast straight from the source, no building needed.
rem Needs Python 3.10+ (python.org, "Add python.exe to PATH" ticked).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python was not found. Install it from https://www.python.org/downloads/ & pause & exit /b 1)
if not exist tools\ffmpeg.exe powershell -NoProfile -ExecutionPolicy Bypass -File scripts\get-ffmpeg.ps1
if not exist tools\ffmpeg.exe (echo FFmpeg download failed. & pause & exit /b 1)
start "" pythonw jerncast_app.py
