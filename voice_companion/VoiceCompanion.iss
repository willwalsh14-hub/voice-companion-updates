#define AppName "Voice Companion"
#define AppVersion "0.2.80-test"
[Setup]
AppId={{9DFB48ED-1D99-4230-A78D-FD727536AC40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Miles Access Skills Training
DefaultDirName={localappdata}\Programs\Voice Companion
UsePreviousAppDir=no
DefaultGroupName=Voice Companion
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=VoiceCompanion-Setup
Compression=lzma2
SolidCompression=yes
SetupLogging=yes
CloseApplications=yes
ArchitecturesAllowed=x64compatible
WizardStyle=modern
DisableWelcomePage=yes
DisableStartupPrompt=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=no
UninstallDisplayIcon={app}\VoiceCompanion.exe

[Files]
Source: "dist\VoiceCompanion\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "dist\VoiceCompanion\START HERE - Veteran.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.html"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.docx"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.epub"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran - DAISY 3.zip"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall

[Icons]
Name: "{group}\Voice Companion"; Filename: "{app}\VoiceCompanion.exe"
Name: "{group}\Start Here"; Filename: "{userdocs}\Voice Companion\User Guides\START HERE - Veteran.html"
Name: "{group}\Start Here - Text"; Filename: "{userdocs}\Voice Companion\User Guides\START HERE - Veteran.txt"
Name: "{group}\Voice Companion User Guides"; Filename: "{userdocs}\Voice Companion\User Guides"
Name: "{group}\Email Setup - Helper"; Filename: "{app}\EMAIL SETUP - Helper.txt"
Name: "{userdesktop}\Voice Companion"; Filename: "{app}\VoiceCompanion.exe"
Name: "{userdesktop}\Voice Companion User Guides"; Filename: "{userdocs}\Voice Companion\User Guides"

[Run]
Filename: "{app}\VoiceCompanion.exe"; Description: "Open Voice Companion now"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  ProgressPath: String;
  Percent: Integer;
begin
  ProgressPath := ExpandConstant('{param:UPDATESTATUSFILE|}');
  if (ProgressPath <> '') and (MaxProgress > 0) then
  begin
    Percent := Round((CurProgress * 1.0 / MaxProgress) * 99);
    SaveStringToFile(ProgressPath, IntToStr(Percent), False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Diagnostic: String;
begin
  if CurStep = ssPostInstall then
  begin
    Diagnostic := ExpandConstant('{app}\VoiceCompanion-Diagnostics.exe');
    if not FileExists(Diagnostic) then
      RaiseException('Voice Companion was not installed completely. Run Setup again.');
    if not Exec(Diagnostic, '--check-runtime', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException('The installed Voice Companion could not verify its included components. Run Setup again or contact your trainer.');
  end;
end;
