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
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start with Windows (so it can catch meetings automatically)"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\MeetingRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"
Name: "{autodesktop}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"; Tasks: desktopicon
Name: "{userstartup}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"; Parameters: "--minimized"; Tasks: startup

[Run]
Filename: "{app}\MeetingRecorder.exe"; Description: "Launch Meeting Recorder"; Flags: nowait postinstall skipifsilent
