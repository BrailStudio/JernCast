; Inno Setup script. Build with: scripts\build-windows.ps1 -Installer
#define AppName "JernCast"
#define AppVersion "0.0.2"

[Setup]
AppId={{6E0C3F52-4B1D-4C8A-9D27-5A1F0E7B9C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=JernCast
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\JernCast.exe
SetupIconFile=..\assets\jerncast.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Jerncast-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\JernCast\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\JernCast.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\JernCast.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JernCast.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
