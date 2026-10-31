#define AppName "Voice Companion"
#define AppVersion "0.2.185-test"
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
; Voice Companion's own programs are closed by PrepareToInstall below, with no
; question to answer. Restart Manager is a safety net for one that starts again
; in the meantime (a browser can restart the browser host): it is forced, since
; nothing but Voice Companion's own programs can be left by then. Setup never
; restarts programs: the update helper starts Voice Companion again itself.
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
WizardStyle=modern
DisableWelcomePage=yes
DisableStartupPrompt=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=no
UninstallDisplayIcon={app}\VoiceCompanion.exe
; Silent updates must not repeat the one-time Chrome setup request.
UsePreviousTasks=no

[Tasks]
Name: "setupchrome"; Description: "Set up Chrome and the Voice Companion extension when Voice Companion opens"; Flags: unchecked

[Files]
; The close step, run by PrepareToInstall before any file is changed.
Source: "dist\VoiceCompanion\close-companion.ps1"; Flags: dontcopy
Source: "dist\VoiceCompanion\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "dist\VoiceCompanion\START HERE - Veteran.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.html"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.docx"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran.epub"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\START HERE - Veteran - DAISY 3.zip"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall

Source: "dist\VoiceCompanion\START HERE - Veteran.brf"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\README.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Quick Start.txt"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\EMAIL SETUP - Helper.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\AI VOICE SETUP - Trainer.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\UPDATES - Trainer.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\TRAINER TEST CHECKLIST.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes.txt"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion}.txt"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes.html"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion}.html"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes.docx"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion}.docx"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes.epub"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion}.epub"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes - DAISY 3.zip"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion} - DAISY 3.zip"; Flags: ignoreversion uninsneveruninstall
Source: "dist\VoiceCompanion\Voice Companion Release Notes.brf"; DestDir: "{userdocs}\Voice Companion\User Guides"; DestName: "Release Notes for Voice Companion {#AppVersion}.brf"; Flags: ignoreversion uninsneveruninstall

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.voicecompanion.browser"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-native-host.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.voicecompanion.browser"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-native-host.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Mozilla\NativeMessagingHosts\com.voicecompanion.mail"; ValueType: string; ValueName: ""; ValueData: "{app}\mail-native-host.json"; Flags: uninsdeletekey

[UninstallDelete]
Type: files; Name: "{app}\*.update-paused"
Type: files; Name: "{app}\browser-native-host.json"
Type: files; Name: "{app}\mail-native-host.json"

[Icons]
Name: "{group}\Voice Companion"; Filename: "{app}\VoiceCompanion.exe"
Name: "{group}\Start Here"; Filename: "{userdocs}\Voice Companion\User Guides\START HERE - Veteran.html"
Name: "{group}\Start Here - Text"; Filename: "{userdocs}\Voice Companion\User Guides\START HERE - Veteran.txt"
Name: "{group}\Voice Companion User Guides"; Filename: "{userdocs}\Voice Companion\User Guides"
Name: "{group}\Browser Connection Setup"; Filename: "{app}\regular_browser_extension\SETUP.txt"
Name: "{group}\Email Setup - Helper"; Filename: "{app}\EMAIL SETUP - Helper.txt"
Name: "{userdesktop}\Voice Companion"; Filename: "{app}\VoiceCompanion.exe"
Name: "{userdesktop}\Voice Companion User Guides"; Filename: "{userdocs}\Voice Companion\User Guides"

[Run]
Filename: "{app}\VoiceCompanion-Diagnostics.exe"; Parameters: "--refresh-guides"; Flags: runhidden
Filename: "{app}\VoiceCompanion.exe"; Description: "Open Voice Companion now"; Flags: postinstall nowait skipifsilent

[Code]
var
  CloseStepFolder: String;

function CloseStepNames(const ReasonFile: String; var Count: Integer): String;
var
  Lines: TArrayOfString;
  Index: Integer;
begin
  Result := '';
  Count := 0;
  // The first line is the code; each line after it names one program.
  if LoadStringsFromFile(ReasonFile, Lines) then
    for Index := 1 to GetArrayLength(Lines) - 1 do
      if Trim(Lines[Index]) <> '' then
      begin
        if Result <> '' then Result := Result + ', ';
        Result := Result + Trim(Lines[Index]);
        Count := Count + 1;
      end;
end;

// Close every Voice Companion program and its hidden helpers before any file is
// changed, and wait until the files are free (close-companion.ps1). Programs that
// belong to the user are never closed: when one holds a file, Setup stops before
// changing anything and names it. The updates of older versions run this too.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode, Index, Count: Integer;
  Script, ReasonFile, LogFile, Params, Names: String;
  Lines: TArrayOfString;
begin
  Result := '';
  CloseStepFolder := ExpandConstant('{app}');
  if not DirExists(CloseStepFolder) then Exit;
  ExtractTemporaryFile('close-companion.ps1');
  Script := ExpandConstant('{tmp}\close-companion.ps1');
  ReasonFile := ExpandConstant('{tmp}\close-reason.txt');
  LogFile := ExpandConstant('{tmp}\close-log.txt');
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Script + '" -InstallFolder "' + CloseStepFolder +
    '" -GuideFolder "' + ExpandConstant('{userdocs}\Voice Companion\User Guides') + '" -ReasonFile "' + ReasonFile +
    '" -LogFile "' + LogFile + '"';
  // A silent update has no window: the close step says what to do out loud.
  if WizardSilent then Params := Params + ' -Speak';
  Log('Closing Voice Companion programs before installing.');
  if not Exec(ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe'), Params, ExpandConstant('{tmp}'), SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
  begin
    // Restart Manager still closes Voice Companion's programs.
    Log('The close step could not start: ' + SysErrorMessage(ResultCode));
    Exit;
  end;
  if LoadStringsFromFile(LogFile, Lines) then
    for Index := 0 to GetArrayLength(Lines) - 1 do Log(Lines[Index]);
  Log('The close step ended with code ' + IntToStr(ResultCode) + '.');
  // Only a program of the user's that holds a file (code 2) stops Setup. Anything
  // else goes on: Restart Manager below closes Voice Companion's own programs.
  if ResultCode <> 2 then Exit;
  Names := CloseStepNames(ReasonFile, Count);
  if Count = 0 then Exit;
  if Count = 1 then
    Result := Names + ' is using a Voice Companion file. Save your work in ' + Names + ' and close it. Then run the update again.'
  else
    Result := 'These programs are using Voice Companion files: ' + Names + '. Save your work and close them. Then run the update again.';
end;

// Browsers find the browser host again. Setup's last step writes new manifests;
// when Setup stops first, the paused ones come back.
procedure RestoreNativeHostManifest(const Name: String);
var
  Live, Paused: String;
begin
  Live := AddBackslash(CloseStepFolder) + Name;
  Paused := Live + '.update-paused';
  if not FileExists(Paused) then Exit;
  if FileExists(Live) then DeleteFile(Paused)
  else if not RenameFile(Paused, Live) then Log('Could not restore ' + Name + '.');
end;

procedure DeinitializeSetup;
begin
  if CloseStepFolder <> '' then
  begin
    RestoreNativeHostManifest('browser-native-host.json');
    RestoreNativeHostManifest('mail-native-host.json');
  end;
end;

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
    Diagnostic := ExpandConstant('{app}\VoiceCompanion-BrowserHost.exe');
    if not Exec(Diagnostic, '--register', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException('The browser connection could not be registered. Run Setup again.');
    Diagnostic := ExpandConstant('{app}\VoiceCompanion-Diagnostics.exe');
    if not FileExists(Diagnostic) then
      RaiseException('Voice Companion was not installed completely. Run Setup again.');
    if not Exec(Diagnostic, '--check-runtime', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException('The installed Voice Companion could not verify its included components. Run Setup again or contact your trainer.');
    // The app reads this once at its next start and asks before each change.
    if WizardIsTaskSelected('setupchrome') then
    begin
      ForceDirectories(ExpandConstant('{localappdata}\VoiceCompanion'));
      SaveStringToFile(ExpandConstant('{localappdata}\VoiceCompanion\setup-chrome.request'), 'yes', False);
    end;
  end;
end;





procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'VoiceCompanion');
    // Chrome removes the extension that Chrome setup added once this entry is gone.
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Google\Chrome\Extensions\ijojiipjhcfaajafdbinnbehcenfchcc');
  end;
end;
