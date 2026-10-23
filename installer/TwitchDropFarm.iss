; Версія надходить із core.config через tools/build_installer.py.
#ifndef AppVersion
  #error AppVersion must be passed by the build tool
#endif
#ifndef AppExe
  #error AppExe must point to the tested portable executable
#endif

[Setup]
AppId={{B56C565A-4324-46D9-9D56-D515166C4435}
AppName=Twitch Drop Farm
AppVersion={#AppVersion}
AppPublisher=Viktor Hartunh
AppPublisherURL=https://github.com/RiasJ1Dar/TwitchDropFarm
AppSupportURL=https://github.com/RiasJ1Dar/TwitchDropFarm/issues
AppUpdatesURL=https://github.com/RiasJ1Dar/TwitchDropFarm/releases
DefaultDirName={localappdata}\Programs\TwitchDropFarm
DefaultGroupName=Twitch Drop Farm
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir=..
OutputDir=dist
OutputBaseFilename=TwitchDropFarm-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\TwitchDropFarm.exe
LicenseFile=LICENSE
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Twitch Drop Farm
VersionInfoVersion={#AppVersion}

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Лише програма й ліцензія: auth/settings/cookies ніколи не пакуються.
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "TwitchDropFarm.exe"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\Twitch Drop Farm"; Filename: "{app}\TwitchDropFarm.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Twitch Drop Farm"; Filename: "{app}\TwitchDropFarm.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; ESET на робочому ПК блокує нащадків оболонок. Explorer передає запуск
; оболонці робочого столу, як і звичайний подвійний клік користувача.
Filename: "{win}\explorer.exe"; Parameters: """{app}\TwitchDropFarm.exe"""; Description: "{cm:LaunchProgram,Twitch Drop Farm}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\TwitchDropFarm.exe.old"
Type: files; Name: "{app}\started.flag"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StartupCommand, InstalledExe: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    InstalledExe := '"' + ExpandConstant('{app}\TwitchDropFarm.exe') + '"';
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run',
       'TwitchDropFarm', StartupCommand) then
    begin
      { Не чіпаємо автозапуск іншої portable-копії. Дані користувача
        в LocalAppData\TwitchDropFarm і Documents лишаються на місці. }
      if CompareText(Copy(StartupCommand, 1, Length(InstalledExe)), InstalledExe) = 0 then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'TwitchDropFarm');
    end;
  end;
end;
