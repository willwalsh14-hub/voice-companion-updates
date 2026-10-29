param([string]$InstallFolder, [string[]]$GuideFolder = @(), [string]$ReasonFile, [string]$LogFile, [switch]$Speak,
      [int]$GentleSeconds = 10, [int]$ForceSeconds = 10, [int]$BlockedSeconds = 120)
# Closes every program that runs from the Voice Companion install folder, and the
# helpers those programs started, so Setup can replace the files. Used by
# apply-update.ps1 before Setup starts, and by Setup itself (PrepareToInstall in
# VoiceCompanion.iss), because an update is installed by the old version's helper.
#
# Rules (Will, October 2026):
# - Close only Voice Companion's own programs: anything whose program file is in
#   the install folder, plus the hidden helpers they started (the WebView2 browser
#   engine, console windows, hidden PowerShell). Never close a program that belongs
#   to the user (Word, Thunderbird, the user's own Chrome, NVDA), even when Voice
#   Companion opened it: closing it could lose their work.
# - Never close the update itself: this script, the programs that started it
#   (the update helper and Setup) and what the helper started (the update voice).
# - Ask gently first (the window's close message), then force after a short wait.
# - When a program that is not Voice Companion still holds a file, name it to the
#   user and wait for them to close it, up to a time limit.
# Windows PowerShell 5.1 runs this script: no PowerShell 7 syntax.

# Hidden helper programs Voice Companion starts from outside its folder. A child
# with any other program file is the user's own program and is never closed.
$script:CompanionHelperNames = @('msedgewebview2.exe', 'conhost.exe', 'powershell.exe')
# Native messaging manifests: Chrome, Edge and Thunderbird start the browser host
# again within 30 seconds through these files. They are paused during the update.
$script:NativeHostManifests = @('browser-native-host.json', 'mail-native-host.json')
$script:PausedSuffix = '.update-paused'
$script:CloseLogFile = $LogFile
$script:CloseVoice = $null

function Write-CloseLog([string]$message) {
  $line = (Get-Date).ToString('HH:mm:ss') + ' Close: ' + $message
  if ($script:CloseLogFile) { try { $line | Add-Content -LiteralPath $script:CloseLogFile -Encoding UTF8 } catch {} }
}

function Say-CloseMessage([string]$message) {
  Write-CloseLog ('Said: ' + $message)
  if ($script:CloseVoice) { try { $script:CloseVoice.Speak($message, 0) | Out-Null } catch { Write-CloseLog ('Speech failed: ' + $_) } }
}

function ConvertTo-CompanionPath([string]$path) {
  if (-not $path) { return '' }
  $path = $path.Trim().Trim('"').Replace('/', '\')
  if ($path.StartsWith('\\?\')) { $path = $path.Substring(4) }
  return $path
}

$script:LongFolders = @{}
function Get-LongFolderPath([string]$folder) {
  # Windows can name a folder by its short 8.3 name (C:\Users\RUNNER~1\...), while
  # running programs are listed by the long one. Directory listings give long names.
  if ($script:LongFolders.ContainsKey($folder)) { return $script:LongFolders[$folder] }
  $long = $folder
  try {
    $item = New-Object IO.DirectoryInfo $folder
    if ($item.Exists -and $item.Parent) {
      $parent = Get-LongFolderPath $item.Parent.FullName.TrimEnd('\')
      $match = @((New-Object IO.DirectoryInfo $item.Parent.FullName).GetDirectories($item.Name))
      if ($match.Count -eq 1) { $long = $parent.TrimEnd('\') + '\' + $match[0].Name }
    }
  } catch {}
  $script:LongFolders[$folder] = $long
  return $long
}

function Test-InsideFolder([string]$path, [string]$folder) {
  $path = ConvertTo-CompanionPath $path; $folder = (ConvertTo-CompanionPath $folder).TrimEnd('\')
  if (-not $path -or -not $folder) { return $false }
  if ($path.StartsWith($folder + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
  $long = Get-LongFolderPath $folder
  return ($long -ne $folder -and $path.StartsWith($long.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase))
}

function Get-CompanionProcessList {
  # One record per running process: id, parent id, program file, name, start time.
  foreach ($item in @(Get-CimInstance -ClassName Win32_Process -Property ProcessId, ParentProcessId, ExecutablePath, Name, CreationDate -ErrorAction Stop)) {
    [pscustomobject]@{ ProcessId = [int]$item.ProcessId; ParentProcessId = [int]$item.ParentProcessId; Path = [string]$item.ExecutablePath
                       Name = [string]$item.Name; Created = $item.CreationDate }
  }
}

function Test-StartedAfter($child, $parent) {
  # A parent id can belong to a newer process that reused the number.
  if ($null -eq $child.Created -or $null -eq $parent.Created) { return $true }
  return $child.Created -ge $parent.Created
}

function Select-CompanionProcesses($Processes, [string]$InstallFolder, [int]$SelfId) {
  # Returns the processes to close, children before parents (deepest first).
  # Ids as [int] everywhere: a hashtable keyed by Int64 or UInt32 never finds an Int32 key.
  $Processes = @($Processes | Where-Object { $_ -and [int64]$_.ProcessId -gt 0 } | ForEach-Object {
    [pscustomobject]@{ ProcessId = [int]$_.ProcessId; ParentProcessId = [int]$_.ParentProcessId; Path = [string]$_.Path; Name = [string]$_.Name; Created = $_.Created } })
  $SelfId = [int]$SelfId
  $byId = @{}; foreach ($item in $Processes) { $byId[$item.ProcessId] = $item }
  $children = @{}
  foreach ($item in $Processes) {
    $parent = $byId[$item.ParentProcessId]
    if ($parent -and $item.ProcessId -ne $item.ParentProcessId -and (Test-StartedAfter $item $parent)) {
      if (-not $children.ContainsKey($parent.ProcessId)) { $children[$parent.ProcessId] = New-Object System.Collections.ArrayList }
      [void]$children[$parent.ProcessId].Add($item)
    }
  }
  # Protected: this script, everything that started it, and everything it started.
  # One exception: the old app itself started the update helper, and it must
  # close. Closing a parent never closes its children on Windows.
  $protected = @{}; $visited = @{}
  $current = $byId[$SelfId]
  while ($current -and -not $visited.ContainsKey($current.ProcessId)) {
    $visited[$current.ProcessId] = $true
    if ($current.ProcessId -eq $SelfId -or -not (Test-InsideFolder $current.Path $InstallFolder)) { $protected[$current.ProcessId] = $true }
    $parent = $byId[$current.ParentProcessId]
    if (-not $parent -or -not (Test-StartedAfter $current $parent)) { break }
    $current = $parent
  }
  $pending = New-Object System.Collections.Queue
  if ($byId[$SelfId]) { $pending.Enqueue($byId[$SelfId]) }
  while ($pending.Count) {
    $item = $pending.Dequeue()
    foreach ($child in @($children[$item.ProcessId])) {
      # What the update started (its voice, Setup) stays; a program from the install folder does not.
      if ($child -and -not $protected.ContainsKey($child.ProcessId) -and -not (Test-InsideFolder $child.Path $InstallFolder)) { $protected[$child.ProcessId] = $true; $pending.Enqueue($child) }
    }
  }
  $selected = @{}
  foreach ($item in $Processes) {
    if (-not $protected.ContainsKey($item.ProcessId) -and (Test-InsideFolder $item.Path $InstallFolder)) {
      $selected[$item.ProcessId] = [pscustomobject]@{ ProcessId = $item.ProcessId; Name = $item.Name; Path = $item.Path; Created = $item.Created; Depth = 0; Why = 'program in the install folder' }
    }
  }
  # Children of Voice Companion programs: only its own programs and hidden helpers.
  $pending = New-Object System.Collections.Queue
  foreach ($entry in @($selected.Values)) { $pending.Enqueue($entry) }
  while ($pending.Count) {
    $entry = $pending.Dequeue()
    foreach ($child in @($children[$entry.ProcessId])) {
      if (-not $child -or $protected.ContainsKey($child.ProcessId)) { continue }
      $ours = (Test-InsideFolder $child.Path $InstallFolder) -or ($script:CompanionHelperNames -contains ([string]$child.Name).ToLowerInvariant())
      if (-not $ours) { continue }
      if ($selected.ContainsKey($child.ProcessId)) {
        if ($selected[$child.ProcessId].Depth -le $entry.Depth) { $selected[$child.ProcessId].Depth = $entry.Depth + 1; $pending.Enqueue($selected[$child.ProcessId]) }
        continue
      }
      $selected[$child.ProcessId] = [pscustomobject]@{ ProcessId = $child.ProcessId; Name = $child.Name; Path = $child.Path; Created = $child.Created; Depth = $entry.Depth + 1; Why = 'started by ' + $entry.Name }
      $pending.Enqueue($selected[$child.ProcessId])
    }
  }
  return @($selected.Values | Sort-Object -Property @{ Expression = 'Depth'; Descending = $true }, @{ Expression = 'ProcessId'; Descending = $false })
}

function Open-CompanionProcess($entry) {
  # The live process, only if it is still the same one (same start time).
  try { $process = [Diagnostics.Process]::GetProcessById($entry.ProcessId) }
  catch { Write-CloseLog ($entry.Name + ' (process ' + $entry.ProcessId + ') has already ended.'); return $null }
  try {
    if ($null -ne $entry.Created -and [Math]::Abs(($process.StartTime.ToUniversalTime() - ([datetime]$entry.Created).ToUniversalTime()).TotalSeconds) -gt 2) {
      Write-CloseLog ('Process ' + $entry.ProcessId + ' is now another program (started ' + $process.StartTime + ', not ' + $entry.Created + '). Left alone.')
      $process.Dispose(); return $null
    }
  } catch {}
  return $process
}

function Wait-CompanionExit($handles, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  foreach ($handle in $handles) {
    $left = [int][Math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds)
    try { [void]$handle.WaitForExit($left) } catch {}
  }
  return @($handles | Where-Object { try { -not $_.HasExited } catch { $false } })
}

function Close-CompanionProcesses([string]$InstallFolder, [int]$SelfId = $PID, [int]$GentleSeconds = 10, [int]$ForceSeconds = 10) {
  # Returns the names of Voice Companion programs that would not close. Programs
  # that start meanwhile (a browser restarts the browser host) are found by a new
  # look, up to three rounds.
  $left = @()
  foreach ($round in 1, 2, 3) {
    $listed = Get-Date
    $all = @(Get-CompanionProcessList)
    $selected = @(Select-CompanionProcesses $all $InstallFolder $SelfId)
    $seconds = ((Get-Date) - $listed).TotalSeconds
    if ($seconds -gt 3) { Write-CloseLog ('Listing ' + $all.Count + ' running programs took ' + [Math]::Round($seconds, 1) + ' seconds.') }
    if (-not $selected.Count) {
      if ($round -eq 1) { Write-CloseLog 'No Voice Companion programs are running.' }
      return @()
    }
    $left = @(Close-SelectedProcesses $selected $GentleSeconds $ForceSeconds)
    if ($left.Count) { return $left }
  }
  return $left
}

function Close-SelectedProcesses($selected, [int]$GentleSeconds, [int]$ForceSeconds) {
  $handles = @(); $names = @{}
  try {
    foreach ($entry in $selected) {
      $process = Open-CompanionProcess $entry
      if (-not $process) { continue }
      Write-CloseLog ('Closing ' + $entry.Name + ' (process ' + $entry.ProcessId + ', ' + $entry.Path + ', ' + $entry.Why + ').')
      $names[$process.Id] = $entry.Name
      $handles += $process
    }
    # Gently: the window's close message, as the close button does. Up to
    # $GentleSeconds of waiting follow, so the user hears why it is quiet.
    if (@($handles | Where-Object { try { -not $_.HasExited -and $_.MainWindowHandle -ne [IntPtr]::Zero } catch { $false } }).Count) {
      Say-CloseMessage 'Closing Voice Companion. Please wait.'
    }
    foreach ($process in $handles) {
      try { if (-not $process.HasExited -and $process.MainWindowHandle -ne [IntPtr]::Zero) { [void]$process.CloseMainWindow() } } catch { Write-CloseLog ('Close request failed: ' + $_) }
    }
    [void](Wait-CompanionExit @($handles | Where-Object { try { $_.MainWindowHandle -ne [IntPtr]::Zero } catch { $false } }) $GentleSeconds)
    $running = @($handles | Where-Object { try { -not $_.HasExited } catch { $false } })
    # Then forced, children first, twice if needed.
    foreach ($attempt in 1, 2) {
      if (-not $running.Count) { break }
      foreach ($process in $running) {
        Write-CloseLog ('Stopping ' + $names[$process.Id] + ' (process ' + $process.Id + ').')
        try { $process.Kill() } catch { Write-CloseLog ('Stop failed: ' + $_) }
      }
      $running = @(Wait-CompanionExit $running $ForceSeconds)
    }
    $left = @($running | ForEach-Object { $names[$_.Id] } | Sort-Object -Unique)
    if ($left.Count) { Write-CloseLog ('Still running: ' + ($left -join ', ') + '.') } else { Write-CloseLog 'All Voice Companion programs are closed.' }
    return $left
  } finally {
    foreach ($process in $handles) { try { $process.Dispose() } catch {} }
  }
}

# Files a program can hold open: program files, and documents people open (the
# guides, the release notes). Opening every file instead (speech models, browser
# data, thousands of them) took 91 seconds on Will's computer: the antivirus
# scanned each one as it was opened.
$script:LockCheckExtensions = @('.exe', '.dll', '.pyd', '.zip', '.docx', '.doc', '.rtf', '.pdf', '.txt', '.htm', '.html', '.epub', '.brf', '.xlsx', '.odt', '.json')

function Get-CompanionFiles([string]$InstallFolder, [string[]]$GuideFolder) {
  if ($InstallFolder -and (Test-Path -LiteralPath $InstallFolder -PathType Container)) {
    Get-ChildItem -LiteralPath $InstallFolder -Recurse -File -Force -ErrorAction SilentlyContinue |
      Where-Object { $script:LockCheckExtensions -contains $_.Extension.ToLowerInvariant() } | ForEach-Object { $_.FullName }
  }
  foreach ($folder in @($GuideFolder)) {
    if ($folder -and (Test-Path -LiteralPath $folder -PathType Container)) {
      Get-ChildItem -LiteralPath $folder -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    }
  }
}

function Test-FileLocked([string]$path) {
  # Setup must open each file to replace it. A sharing or lock violation means a
  # program holds it. A read-only or missing file is not a lock.
  try { $stream = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None'); $stream.Dispose(); return $false }
  catch [IO.IOException] { $code = $_.Exception.HResult -band 0xFFFF; return ($code -eq 32 -or $code -eq 33) }
  catch { return $false }
}

function Find-LockedFiles([string]$InstallFolder, [string[]]$GuideFolder, [int]$Seconds = 20) {
  # At most $Seconds: a slow disk or scan never holds the update for long.
  $deadline = (Get-Date).AddSeconds($Seconds)
  $locked = New-Object System.Collections.ArrayList
  foreach ($file in @(Get-CompanionFiles $InstallFolder $GuideFolder)) {
    if (Test-FileLocked $file) { [void]$locked.Add($file); if ($locked.Count -ge 50) { break } }
    if ((Get-Date) -gt $deadline) { Write-CloseLog ('The file check stopped after ' + $Seconds + ' seconds.'); break }
  }
  return @($locked)
}

function Get-FileUsers([string[]]$Files) {
  # Windows Restart Manager names the programs that hold the files.
  if (-not ('VoiceCompanionRestartManager' -as [type])) {
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class VoiceCompanionRestartManager {
  [StructLayout(LayoutKind.Sequential)]
  struct UniqueProcess { public int ProcessId; public System.Runtime.InteropServices.ComTypes.FILETIME StartTime; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct ProcessInfo {
    public UniqueProcess Process;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string AppName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string ServiceName;
    public int ApplicationType; public uint AppStatus; public uint SessionId;
    [MarshalAs(UnmanagedType.Bool)] public bool Restartable;
  }
  [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] static extern int RmStartSession(out uint session, int flags, System.Text.StringBuilder key);
  [DllImport("rstrtmgr.dll")] static extern int RmEndSession(uint session);
  [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] static extern int RmRegisterResources(uint session, uint fileCount, string[] files, uint appCount, UniqueProcess[] apps, uint serviceCount, string[] services);
  [DllImport("rstrtmgr.dll")] static extern int RmGetList(uint session, out uint needed, ref uint count, [In, Out] ProcessInfo[] list, ref uint reasons);
  public static string[] Users(string[] files) {
    uint session; var key = new System.Text.StringBuilder(64);
    int error = RmStartSession(out session, 0, key);
    if (error != 0) throw new System.ComponentModel.Win32Exception(error);
    try {
      error = RmRegisterResources(session, (uint)files.Length, files, 0, null, 0, null);
      if (error != 0) throw new System.ComponentModel.Win32Exception(error);
      uint needed = 0, count = 0, reasons = 0; ProcessInfo[] list = null;
      for (int attempt = 0; attempt < 5; attempt++) {
        error = RmGetList(session, out needed, ref count, list, ref reasons);
        if (error == 0) break;
        if (error != 234) throw new System.ComponentModel.Win32Exception(error);
        count = needed; list = new ProcessInfo[needed];
      }
      if (error != 0) throw new System.ComponentModel.Win32Exception(error);
      var result = new List<string>();
      for (int index = 0; index < count; index++) result.Add(list[index].Process.ProcessId + "|" + list[index].AppName + "|" + list[index].ServiceName);
      return result.ToArray();
    } finally { RmEndSession(session); }
  }
}
'@
  }
  foreach ($line in [VoiceCompanionRestartManager]::Users($Files)) {
    $parts = $line.Split('|')
    [pscustomobject]@{ ProcessId = [int]$parts[0]; AppName = $parts[1]; ServiceName = $parts[2] }
  }
}

function Get-FriendlyName($user, $process) {
  $name = [string]$user.AppName
  if (-not $name.Trim() -and $process) { $name = [IO.Path]::GetFileNameWithoutExtension([string]$process.Name) }
  $name = ($name -replace '[^\w .,()&+-]', '').Trim()
  if ($name.Length -gt 60) { $name = $name.Substring(0, 60).Trim() }
  if (-not $name) { $name = 'Another program' }
  return $name
}

function Join-Names([string[]]$names) {
  $names = @($names | Where-Object { $_ } | Sort-Object -Unique)
  if ($names.Count -le 1) { return ($names -join '') }
  return (($names[0..($names.Count - 2)]) -join ', ') + ' and ' + $names[-1]
}

function Get-BlockedMessage([string[]]$names) {
  # Said while waiting: what is happening and what to do. 8th-grade words.
  $names = @($names | Where-Object { $_ } | Sort-Object -Unique)
  if (-not $names.Count -or ($names.Count -eq 1 -and $names[0] -eq 'Another program')) {
    return 'Another program is using a Voice Companion file. Save your work and close your other programs. The update will go on after that.'
  }
  $joined = Join-Names $names
  $verb = $(if ($names.Count -gt 1) { 'are' } else { 'is' })
  return $joined + ' ' + $verb + ' using a Voice Companion file. Save your work in ' + $joined + ', then close it. The update will go on after that.'
}

function Resolve-FileUser($user, $everything, [string]$InstallFolder, [int]$SelfId) {
  # Who holds a file, from what Restart Manager reported: the update itself,
  # Voice Companion (its program file is in the install folder), a Windows service,
  # a process that has already ended, or a program that is not Voice Companion.
  $process = $everything | Where-Object { $_.ProcessId -eq $user.ProcessId } | Select-Object -First 1
  $path = $(if ($process) { [string]$process.Path } else { '' })
  if (-not $path) { try { $path = [string](Get-Process -Id $user.ProcessId -ErrorAction Stop).Path } catch {} }
  $alive = [bool]$process -or [bool](Get-Process -Id $user.ProcessId -ErrorAction SilentlyContinue)
  $kind = $(if ($user.ProcessId -eq $SelfId) { 'the update itself' }
            elseif ($path -and (Test-InsideFolder $path $InstallFolder)) { 'Voice Companion' }
            elseif ([string]$user.ServiceName) { 'a Windows service' }
            elseif (-not $alive) { 'a program that has ended' }
            elseif (-not $path) { 'a program Windows does not name' }
            else { 'not Voice Companion' })
  return [pscustomobject]@{ ProcessId = [int]$user.ProcessId; Name = (Get-FriendlyName $user $process); Path = $path; Kind = $kind }
}

function Stop-CompanionProcessById([int]$id, [string]$name) {
  try {
    $process = [Diagnostics.Process]::GetProcessById($id)
    try { Write-CloseLog ('Stopping ' + $name + ' (process ' + $id + '), which holds Voice Companion files.'); $process.Kill(); [void]$process.WaitForExit(5000) }
    finally { $process.Dispose() }
  } catch { Write-CloseLog ('Could not stop process ' + $id + ': ' + $_.Exception.Message) }
}

function Wait-CompanionFilesFree([string]$InstallFolder, [string[]]$GuideFolder, [int]$SelfId = $PID, [int]$BlockedSeconds = 120,
                                 [int]$GentleSeconds = 10, [int]$ForceSeconds = 10, [int]$UnknownSeconds = 20) {
  # Returns $null when the update can go on, or Code 2 and the names of the
  # user's programs that still hold files after $BlockedSeconds.
  # Only a program that is not Voice Companion, still running and named by
  # Windows is told to the user and waited for. Voice Companion's own programs are
  # stopped. A lock nobody can be found for (a program that just ended, a Windows
  # service such as an antivirus scan) is waited for quietly, at most
  # $UnknownSeconds; then the update goes on, and Setup replaces those files when
  # they are free. Will's 0.2.173 update was told "Another program is using a
  # Voice Companion file" about Voice Companion's own runtime files.
  $deadline = (Get-Date).AddSeconds($BlockedSeconds)
  $quietDeadline = (Get-Date).AddSeconds($UnknownSeconds)
  $lastSaid = $null; $lastMessage = ''
  while ($true) {
    $started = Get-Date
    $locked = @(Find-LockedFiles $InstallFolder $GuideFolder)
    $seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    if (-not $locked.Count) { Write-CloseLog ('All Voice Companion files are free (checked in ' + $seconds + ' seconds).'); return $null }
    Write-CloseLog ([string]$locked.Count + ' files still in use (checked in ' + $seconds + ' seconds): ' + (($locked | Select-Object -First 5) -join '; ') + '.')
    $users = @()
    try { $users = @(Get-FileUsers $locked) } catch { Write-CloseLog ('Could not find which program uses the files: ' + $_.Exception.Message) }
    if (-not $users.Count) { Write-CloseLog 'Windows names no program that holds them. A program that just ended can hold files for a moment.' }
    $everything = @(); try { $everything = @(Get-CompanionProcessList) } catch { Write-CloseLog ('Could not list programs: ' + $_.Exception.Message) }
    $outside = @()
    foreach ($user in $users) {
      $holder = Resolve-FileUser $user $everything $InstallFolder $SelfId
      Write-CloseLog ('Held by ' + $holder.Name + ' (process ' + $holder.ProcessId + ', ' + $(if ($holder.Path) { $holder.Path } else { 'no program file' }) + '): ' + $holder.Kind + '.')
      if ($holder.Kind -eq 'Voice Companion') { Stop-CompanionProcessById $holder.ProcessId $holder.Name }
      elseif ($holder.Kind -eq 'not Voice Companion') { $outside += $holder.Name }
    }
    if ($outside.Count) {
      # Said at once, again when the programs change, and every 30 seconds.
      $message = Get-BlockedMessage $outside
      if ($message -ne $lastMessage -or -not $lastSaid -or ((Get-Date) - $lastSaid).TotalSeconds -ge 30) {
        Say-CloseMessage $message; $lastSaid = Get-Date; $lastMessage = $message
      }
      if ((Get-Date) -ge $deadline) { return [pscustomobject]@{ Code = 2; Names = @($outside | Sort-Object -Unique) } }
    } elseif ((Get-Date) -ge $quietDeadline) {
      Write-CloseLog 'Going on with the update. Setup replaces these files once they are free.'
      return $null
    }
    Start-Sleep -Seconds 2
  }
}

function Suspend-NativeHostManifests([string]$InstallFolder) {
  # Chrome, Edge and Thunderbird start the browser host again from these files.
  # Setup's last step writes new ones; Resume-NativeHostManifests puts these back
  # if the update stops first.
  foreach ($name in $script:NativeHostManifests) {
    $live = Join-Path $InstallFolder $name; $paused = $live + $script:PausedSuffix
    if (Test-Path -LiteralPath $live) {
      try { Move-Item -LiteralPath $live -Destination $paused -Force; Write-CloseLog ('Paused ' + $name + ' so browsers cannot start the browser host during the update.') }
      catch { Write-CloseLog ('Could not pause ' + $name + ': ' + $_) }
    }
  }
}

function Resume-NativeHostManifests([string]$InstallFolder) {
  foreach ($name in $script:NativeHostManifests) {
    $live = Join-Path $InstallFolder $name; $paused = $live + $script:PausedSuffix
    if (-not (Test-Path -LiteralPath $paused)) { continue }
    try {
      if (Test-Path -LiteralPath $live) { Remove-Item -LiteralPath $paused -Force }
      else { Move-Item -LiteralPath $paused -Destination $live -Force; Write-CloseLog ('Restored ' + $name + '.') }
    } catch { Write-CloseLog ('Could not restore ' + $name + ': ' + $_) }
  }
}

function Invoke-CompanionClose([string]$InstallFolder, [string[]]$GuideFolder, [int]$SelfId = $PID, [int]$GentleSeconds = 10,
                               [int]$ForceSeconds = 10, [int]$BlockedSeconds = 120) {
  # The whole close step. Returns an object: Code 0 (go on), 1 (go on, but a Voice
  # Companion program did not close; Setup closes it) or 2 (a program of the
  # user's holds a file: the only reason to stop), and Names.
  Write-CloseLog ('Preparing ' + $InstallFolder + ' for the update.')
  Suspend-NativeHostManifests $InstallFolder
  $left = @(Close-CompanionProcesses $InstallFolder $SelfId $GentleSeconds $ForceSeconds)
  if ($left.Count) {
    # One more try after a short pause: a stuck program can need a moment.
    Start-Sleep -Seconds 2
    $left = @(Close-CompanionProcesses $InstallFolder $SelfId $GentleSeconds $ForceSeconds)
  }
  # A Voice Companion program that would not close never stops the update: the
  # file check below stops what holds files, and Setup closes the rest.
  if ($left.Count) { Write-CloseLog ('Going on although ' + (Join-Names $left) + ' did not close.') }
  $blocked = Wait-CompanionFilesFree $InstallFolder $GuideFolder $SelfId $BlockedSeconds $GentleSeconds $ForceSeconds
  if ($blocked) { return $blocked }
  if ($left.Count) { return [pscustomobject]@{ Code = 1; Names = $left } }
  return [pscustomobject]@{ Code = 0; Names = @() }
}

# Run as a script (from Setup): close, write the reason, and set the exit code.
# Dot-sourced (from apply-update.ps1) it only defines the functions above.
if ($InstallFolder -and $MyInvocation.InvocationName -ne '.') {
  $code = 1
  try {
    if ($Speak) { try { $script:CloseVoice = New-Object -ComObject SAPI.SpVoice } catch { Write-CloseLog ('Speech is not available: ' + $_) } }
    $outcome = Invoke-CompanionClose $InstallFolder $GuideFolder $PID $GentleSeconds $ForceSeconds $BlockedSeconds
    $code = $outcome.Code
    if ($ReasonFile -and $code -ne 0) { ([string]$code + "`r`n" + ($outcome.Names -join "`r`n")) | Set-Content -LiteralPath $ReasonFile -Encoding ASCII }
    # Only code 2 stops Setup; then browsers get their browser host back at once.
    # Otherwise Setup goes on, and puts the manifests back when it ends.
    if ($code -eq 2) { Resume-NativeHostManifests $InstallFolder }
  } catch {
    # The close step itself failed: Setup goes on, and Restart Manager closes
    # Voice Companion's programs.
    Write-CloseLog ('The close step failed: ' + $_)
    if ($ReasonFile) { "1`r`n" | Set-Content -LiteralPath $ReasonFile -Encoding ASCII }
    $code = 1
  } finally {
    if ($script:CloseVoice) { try { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($script:CloseVoice) | Out-Null } catch {} }
  }
  exit $code
}
