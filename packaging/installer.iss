; Inno Setup script: wraps dist\MeetingRecorder into MeetingRecorder-Setup-<version>.exe.
;   iscc /DAppVersion=0.1.0 packaging\installer.iss
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\Meeting Recorder.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C2A4E8B-3F1D-4B7A-9C55-2E8D1F0A7B34}
AppName=Meeting Recorder
AppVersion={#AppVersion}
AppPublisher=Meeting Recorder
DefaultDirName={localappdata}\Programs\Meeting Recorder
DefaultGroupName=Meeting Recorder
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MeetingRecorder-Setup-{#AppVersion}
SetupIconFile=..\meeting_recorder\assets\icon.ico
UninstallDisplayIcon={app}\MeetingRecorder.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options (you can change these later in Settings):"
Name: "startup"; Description: "Start with Windows in the tray, so it can catch meetings as they start"; GroupDescription: "Options (you can change these later in Settings):"

[Files]
Source: "..\dist\MeetingRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"
Name: "{autodesktop}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"; Tasks: desktopicon

[Registry]
; Same value the app's "Start with Windows" setting reads and writes (meeting_recorder/startup.py).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MeetingRecorder"; ValueData: """{app}\MeetingRecorder.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\MeetingRecorder.exe"; Description: "Launch Meeting Recorder"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Close a running copy so its files can be removed.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MeetingRecorder.exe"; Flags: runhidden; RunOnceId: "StopApp"
