<#
  Builds dist\JernCast\JernCast.exe (and, with -Installer, dist\Jerncast-0.0.2-Setup.exe).
  Needs Python 3.10+ from python.org (tick "Add python.exe to PATH" and keep "tcl/tk" ticked).
  Works fine with 4 GB of RAM.

      scripts\build-windows.ps1
      scripts\build-windows.ps1 -Installer
#>
param([switch]$Installer)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH").'
}
python -c "import tkinter" 2>$null
if ($LASTEXITCODE) { throw 'This Python has no tkinter. Reinstall Python and keep "tcl/tk and IDLE" ticked.' }

& "$PSScriptRoot\get-ffmpeg.ps1"

python -m pip install --upgrade pip pyinstaller
if ($LASTEXITCODE) { throw 'pip failed' }

# System sound capture (what the PC plays). Optional: without it JernCast still works,
# and desktop sound then needs Stereo Mix or a virtual audio cable.
python -m pip install pyaudiowpatch
if ($LASTEXITCODE) { Write-Warning 'pyaudiowpatch could not be installed: no system sound capture in this build.' }

python -m PyInstaller --noconfirm --clean --windowed --name JernCast `
    --icon assets\jerncast.ico --add-data "assets;assets" --hidden-import pyaudiowpatch jerncast_app.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }

Copy-Item tools\ffmpeg.exe dist\JernCast\ffmpeg.exe -Force
Copy-Item LICENSE, THIRD_PARTY.md dist\JernCast\ -Force
Write-Host 'Built: dist\JernCast\JernCast.exe'

if ($Installer) {
    # ${env:ProgramFiles(x86)} needs the braces: the "(x86)" is part of the variable's name.
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
              "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) { throw 'Inno Setup 6 not found (https://jrsoftware.org/isdl.php or: choco install innosetup).' }
    & $iscc installer\jerncast.iss
    if ($LASTEXITCODE) { throw 'installer build failed' }
    Write-Host 'Built: dist\Jerncast-0.0.2-Setup.exe'
}
