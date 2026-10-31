# Downloads ffmpeg.exe (FFmpeg is the engine JernCast records with) into tools\ffmpeg.exe
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path 'tools\ffmpeg.exe') { Write-Host 'tools\ffmpeg.exe already there.'; return }
New-Item -ItemType Directory -Force tools | Out-Null
$zip = Join-Path $env:TEMP 'jerncast-ffmpeg.zip'
$dir = Join-Path $env:TEMP 'jerncast-ffmpeg'
Write-Host 'Downloading FFmpeg (about 150 MB)...'
Invoke-WebRequest 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile $zip
if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
Expand-Archive $zip -DestinationPath $dir
$exe = Get-ChildItem $dir -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if (-not $exe) { throw 'ffmpeg.exe not found in the download' }
Copy-Item $exe.FullName 'tools\ffmpeg.exe'
Remove-Item $zip, $dir -Recurse -Force
Write-Host 'Done: tools\ffmpeg.exe'
