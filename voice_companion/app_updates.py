"""Opt-in HTTPS installer updates; network work never blocks the speech loop."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

MAX_INSTALLER = 2 * 1024**3


def https_url(value):
    if not isinstance(value, str): raise ValueError('Invalid update address')
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Updates require HTTPS')
    return value


def version_key(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-(test|rc|beta)(?:\.(\d+))?)?', str(value))
    if not match: raise ValueError('Invalid update version')
    return (*map(int, match.groups()[:3]), {'test':0,'beta':1,'rc':2,None:3}[match[4]], int(match[5] or 0))


def validate_release(data, current):
    if data.get('schema') != 1: raise ValueError('Invalid update manifest')
    if version_key(data['version']) <= version_key(current): return None
    https_url(data['installer_url'])
    if not re.fullmatch(r'[a-fA-F0-9]{64}', data.get('sha256', '')): raise ValueError('Invalid checksum')
    if type(data.get('size')) is not int or not 1 <= data['size'] <= MAX_INSTALLER: raise ValueError('Invalid installer size')
    return {key:data[key] for key in ('version','installer_url','sha256','size')}


def fetch_release(url, current):
    request = urllib.request.Request(https_url(url), headers={'User-Agent':'VoiceCompanion-Updater/1','Cache-Control':'no-cache'})
    with urllib.request.urlopen(request, timeout=8) as response:
        https_url(response.geturl())
        raw = response.read(65537)
    if len(raw) > 65536: raise ValueError('Update manifest too large')
    data = json.loads(raw)
    if not isinstance(data, dict): raise ValueError('Invalid update manifest')
    return validate_release(data, current)


def download_release(release, folder, canceled, progress=None):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'VoiceCompanion-Setup.exe'
    temporary = target.with_suffix('.part')
    digest = hashlib.sha256(); size = 0
    try:
        request = urllib.request.Request(https_url(release['installer_url']),headers={'User-Agent':'VoiceCompanion-Updater/1'})
        with urllib.request.urlopen(request, timeout=15) as response, temporary.open('wb') as output:
            https_url(response.geturl())
            while True:
                if canceled.is_set(): raise InterruptedError('Canceled')
                block = response.read(1024*1024)
                if not block: break
                size += len(block)
                if size > release['size']: raise ValueError('Installer size mismatch')
                digest.update(block); output.write(block)
                if progress: progress(min(99, size * 100 // release['size']))
        if canceled.is_set(): raise InterruptedError('Canceled')
        if size != release['size'] or digest.hexdigest() != release['sha256'].lower(): raise ValueError('Installer checksum mismatch')
        with temporary.open('rb') as source:
            if source.read(2) != b'MZ': raise ValueError('Not a Windows installer')
        os.replace(temporary, target)
        if progress: progress(100)
        return target
    finally:
        temporary.unlink(missing_ok=True)


class AppUpdates:
    def __init__(self, data_folder, current, resource_folder=None):
        self.folder = Path(data_folder); self.current = current
        self.resources = Path(resource_folder or Path(__file__).parent)
        self.events = queue.Queue(); self.busy = False; self.release = None
        self.canceled = threading.Event()
        self.percent = 0
        self.phase = 'idle'
        self.manifest_url = ''
        for config in (self.folder/'update-settings.json', self.resources/'update-settings.json'):
            if config.exists():
                try:
                    value = json.loads(config.read_text())['manifest_url']
                    if value: self.manifest_url = https_url(value)
                except (ValueError,KeyError,OSError): pass
                break

    def check(self, manual=False):
        if self.busy: return False
        if not self.manifest_url:
            if manual: self.events.put(('unconfigured',None))
            return False
        self.busy = True
        def work():
            try:
                release = fetch_release(self.manifest_url,self.current)
                self.events.put(('available',release) if release else ('current',manual))
            except Exception: self.events.put(('check_failed',manual))
            finally: self.busy = False
        threading.Thread(target=work,daemon=True).start()
        return True

    def download(self):
        if self.busy or not self.release: return False
        self.busy = True; self.canceled.clear(); self.percent = 0; self.phase = 'downloading'
        release = dict(self.release)
        def work():
            try:
                folder = self.folder/'Updates'/str(uuid.uuid4())
                def report(percent):
                    if percent != self.percent:
                        self.percent = percent
                        self.events.put(('progress', percent))
                path = download_release(release,folder,self.canceled, report)
                self.events.put(('downloaded',(path,release)))
            except InterruptedError: self.events.put(('canceled',None))
            except Exception: self.events.put(('download_failed',None))
            finally: self.busy = False
        threading.Thread(target=work,daemon=True).start()
        return True

    def can_install(self):
        installed = Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Voice Companion'/'VoiceCompanion.exe'
        return os.name == 'nt' and getattr(sys,'frozen',False) and Path(sys.executable).resolve() == installed.resolve()

    def launch(self, path, release):
        installed = Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Voice Companion'/'VoiceCompanion.exe'
        if os.name != 'nt' or not getattr(sys,'frozen',False) or Path(sys.executable).resolve() != installed.resolve():
            raise ValueError('Update installation requires the installed app')
        path = Path(path)
        with path.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest() != release['sha256'].lower(): raise ValueError('Installer changed')
        helper = path.parent/'apply-update.ps1'
        shutil.copyfile(self.resources/'apply-update.ps1',helper)
        ready = path.parent/'update-ready.json'
        ready.unlink(missing_ok=True)
        process = subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper),
                          '-InstallerPath',str(path),'-AppPath',str(installed),'-PreviousProcessId',str(os.getpid()),
                          '-ExpectedHash',release['sha256'],'-DataFolder',str(self.folder),'-ReadyFile',str(ready)],
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready.is_file():
                state = json.loads(ready.read_text(encoding='utf-8-sig'))
                if not state.get('ready'): raise RuntimeError('Update helper could not initialize')
                self.phase = 'installing'
                return
            if process.poll() is not None: raise RuntimeError('Update helper exited before handoff')
            time.sleep(0.1)
        process.terminate()
        raise RuntimeError('Update helper did not acknowledge the handoff')
