"""Per-user startup and confirmed computer power requests."""
from pathlib import Path
import os
import subprocess
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'VoiceCompanion'

def startup_enabled():
    if os.name != 'nt':return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key,RUN_NAME)[0])
    except OSError:return False

def set_startup(enabled):
    if os.name != 'nt':
        if enabled:raise ValueError('Start with Windows requires an installed Windows app.')
        return
    import winreg
    if enabled and (not getattr(sys,'frozen',False) or Path(sys.executable).name.casefold()!='voicecompanion.exe'):
        raise ValueError('Install Voice Companion before enabling Start with Windows.')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        if enabled:winreg.SetValueEx(key,RUN_NAME,0,winreg.REG_SZ,'"'+str(Path(sys.executable).resolve())+'"')
        else:
            try:winreg.DeleteValue(key,RUN_NAME)
            except FileNotFoundError:pass

def request_power(action):
    if action not in ('restart','shutdown'):raise ValueError('Unknown computer action.')
    if os.name!='nt':raise ValueError('Computer restart and shutdown require Windows.')
    # Do not force applications closed: Windows retains its unsaved-work protection.
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'shutdown.exe'
    subprocess.run([str(executable),'/r' if action=='restart' else '/s','/t','0'],check=True,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
