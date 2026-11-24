; Inno Setup 6 script for LocalAssistant.
; Build after build_exe.bat:
;   ISCC /DAppVersion=<contents of VERSION> installer\LocalAssistant.iss
; The installer is per-user by default (no administrator rights needed).
; User data in %LOCALAPPDATA%\LocalAssistant is deliberately kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E1D4B7A-2C58-4F0B-9A3E-5D7C81B0F2A9}
AppName=LocalAssistant
AppVersion={#AppVersion}
AppPublisher=LocalAssistant
DefaultDirName={autopf}\LocalAssistant
DefaultGroupName=LocalAssistant
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=LocalAssistant-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\LocalAssistant.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Запускать вместе с Windows (в трее)"; Flags: unchecked
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked

[Files]
Source: "..\dist\LocalAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LocalAssistant"; Filename: "{app}\LocalAssistant.exe"
Name: "{autodesktop}\LocalAssistant"; Filename: "{app}\LocalAssistant.exe"; Tasks: desktopicon

[Registry]
; Autostart chosen in the installer (same value the in-app setting manages).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LocalAssistant"; ValueData: """{app}\LocalAssistant.exe"" --tray"; Flags: uninsdeletevalue; Tasks: autostart
; The in-app setting may create this value after installation. The [Code] handler
; below removes the actual HKCU value on uninstall, regardless of how it was enabled.

[Run]
Filename: "{app}\LocalAssistant.exe"; Description: "Запустить LocalAssistant"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if RegValueExists(
      HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      'LocalAssistant') then
    begin
      if not RegDeleteValue(
        HKEY_CURRENT_USER,
        'Software\Microsoft\Windows\CurrentVersion\Run',
        'LocalAssistant') then
        Log('Could not remove the LocalAssistant autostart registry value.');
    end;
  end;
end;
