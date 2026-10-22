"""Set up Google Chrome and the Voice Companion extension with yes-or-no answers.

Every step that changes the computer asks first. Companion never presses
buttons in Chrome's own prompts: turning on an extension stays the user's
choice in Chrome. While setup waits, Companion reads aloud the Chrome control
that has keyboard focus, so a user without a screen reader can find
"Enable extension" or "Add extension" with Tab.

The extension is added the way Chrome documents for programs on Windows
("external extensions"): a registry value that points Chrome at the Chrome Web
Store. Chrome downloads the store copy and asks the user to turn it on. If that
does not link within two minutes, setup offers the Chrome Web Store page.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

from browser_bridge_host import CHROME_STORE_EXTENSION_ID as STORE_ID

# Unpacked test copies use the ID made from the manifest "key".
DEVELOPER_ID = 'lmpkdmlehooibijenhkhpbjcgjehjbig'
EXTENSION_IDS = (STORE_ID, DEVELOPER_ID)
EXTENSION_NAME = 'Voice Companion Browser Connection'
STORE_PAGE = 'https://chromewebstore.google.com/detail/' + STORE_ID
WEB_STORE_UPDATE_URL = 'https://clients2.google.com/service/update2/crx'
CHROME_INSTALLER_URL = 'https://dl.google.com/chrome/install/latest/chrome_installer.exe'
EXTENSION_KEY = 'Software\\Google\\Chrome\\Extensions\\' + STORE_ID
APP_PATHS_KEY = 'Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe'
UNINSTALL_KEYS = (('HKCU', 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome'),
                  ('HKLM', 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome'),
                  ('HKLM', 'Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome'))
WINGET_ARGUMENTS = ('install', '--id', 'Google.Chrome', '-e', '--silent',
                    '--accept-package-agreements', '--accept-source-agreements')
REQUEST_FILE = 'setup-chrome.request'
MAX_INSTALLER = 300 * 1024 * 1024
MAX_PREFERENCES = 50 * 1024 * 1024

# Spoken words. Plain, short, and each says what to do next.
YES_NO = ' Say yes or no, or press Y or N.'
MESSAGES = {
    'windows_only': 'Chrome setup works only on Windows.',
    'linked': 'Chrome is linked to Voice Companion. You are all set. Open Web browser to use Chrome with your usual commands.',
    'ask_chrome': 'Google Chrome is not installed. Install Google Chrome now? It takes a few minutes.' + YES_NO,
    'no_chrome': 'Okay. Chrome was not installed. Say set up Chrome when you are ready.',
    'installing': 'Installing Google Chrome. This can take a few minutes. I will tell you how it is going. Say cancel to stop.',
    'still_installing': 'Still installing Chrome. Please wait.',
    'second_way': 'Trying another way to install Chrome. If Windows asks for permission, press Alt+Y to allow it.',
    'install_failed': 'Chrome could not be installed. Check that the internet works, then say set up Chrome to try again. Or ask your helper.',
    'installed': 'Google Chrome is installed. Next, add the Voice Companion extension to Chrome. Add it now?' + YES_NO,
    'ask_extension': 'Chrome is ready. Add the Voice Companion extension to Chrome now?' + YES_NO,
    'no_extension': 'Okay. The extension was not added. Say set up Chrome when you are ready.',
    'added': ('Done. Chrome is opening. Chrome will ask to turn on the Voice Companion extension. '
              'Press Tab until you hear Enable extension, then press Enter. I will read the Chrome buttons for you.'),
    'menu_hint': ('If Chrome did not ask, press Alt+F to open the Chrome menu. Press Down Arrow until you hear '
                  'Voice Companion Browser Connection, then press Enter. Then choose Enable extension. '
                  'If you are not in Chrome, press Alt+Tab.'),
    'already_added': 'The Voice Companion extension is already in Chrome. Opening Chrome so it can link. Please wait.',
    'not_linking': ('The extension is on, but it did not link. Close Chrome and open it again. '
                    'If it still does not link, ask your helper to run the Voice Companion installer again.'),
    'waiting': 'Still waiting for the extension. Say cancel to stop.',
    'ask_store': 'The extension has not linked yet. Try the Chrome Web Store page instead?' + YES_NO,
    'no_store': 'Okay. Say set up Chrome to try again later.',
    'store': ('Chrome is opening the Voice Companion page in the Chrome Web Store. Press Tab until you hear '
              'Add to Chrome, then press Enter. Chrome asks again. Press Tab until you hear Add extension, '
              'then press Enter. I will read the Chrome buttons for you.'),
    'store_failed': ('The extension has not linked yet. Ask your helper to finish setup with section 23 of the guide. '
                     'Everything else in Voice Companion still works.'),
    'no_open': 'Chrome could not be opened. Open Chrome yourself. I will keep waiting.',
    'canceled': 'Chrome setup stopped.',
    'canceled_install': 'Chrome setup stopped. If Chrome was already installing, it may still finish on its own.',
    'answer': 'Say yes or no.',
    'busy': 'Chrome setup is still running. Say status to hear the steps again, or say cancel to stop.',
}


class Registry:
    """Small winreg wrapper so tests can use a dictionary instead."""
    def __init__(self, winreg=None):
        if winreg is None:
            import winreg
        self.winreg = winreg

    def _root(self, root):
        return {'HKCU': self.winreg.HKEY_CURRENT_USER, 'HKLM': self.winreg.HKEY_LOCAL_MACHINE}[root]

    def read(self, root, key, name=''):
        try:
            with self.winreg.OpenKey(self._root(root), key) as handle:
                value = self.winreg.QueryValueEx(handle, name)[0]
        except OSError:
            return None
        return value if isinstance(value, str) else None

    def write(self, root, key, name, value):
        with self.winreg.CreateKeyEx(self._root(root), key, 0, self.winreg.KEY_SET_VALUE) as handle:
            self.winreg.SetValueEx(handle, name, 0, self.winreg.REG_SZ, value)

    def delete(self, root, key):
        try:
            self.winreg.DeleteKey(self._root(root), key)
        except FileNotFoundError:
            pass


FOCUS_SCRIPT = r'''
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$end = (Get-Date).AddSeconds([int]$env:VC_FOCUS_SECONDS)
$last = ''
while ((Get-Date) -lt $end) {
  try {
    $element = [System.Windows.Automation.AutomationElement]::FocusedElement
    if ($element) {
      $current = $element.Current
      $process = Get-Process -Id $current.ProcessId -ErrorAction SilentlyContinue
      if ($process -and $process.ProcessName -eq 'chrome') {
        $line = $current.ControlType.ProgrammaticName + "`t" + ($current.Name -replace '\s+', ' ')
        if ($line -ne $last) { [Console]::Out.WriteLine($line); [Console]::Out.Flush(); $last = $line }
      }
    }
  } catch {}
  Start-Sleep -Milliseconds 250
}
'''

CONTROL_WORDS = {'Button': 'button', 'MenuItem': 'menu item', 'Hyperlink': 'link', 'CheckBox': 'check box',
                 'RadioButton': 'radio button', 'Edit': 'edit field', 'TabItem': 'tab', 'ListItem': 'list item',
                 'ComboBox': 'combo box', 'SplitButton': 'button'}


def focus_words(line):
    """Turn one focus line from the reader into short spoken words."""
    kind, _, name = line.rstrip('\r\n').partition('\t')
    name = ' '.join(name.split())[:200]
    kind = kind.rpartition('.')[2]
    if not name:
        return ''
    word = CONTROL_WORDS.get(kind)
    return name + (', ' + word if word else '') + '.'


class FocusReader:
    """Reads, never presses: reports the focused Chrome control while setup waits."""
    def __init__(self, speak, popen=subprocess.Popen, seconds=600):
        self.speak, self.popen, self.seconds = speak, popen, seconds
        self.process = None
        self.last_spoken = 0.0

    def start(self):
        if self.process is not None:
            return
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        environment = dict(os.environ, VC_FOCUS_SECONDS=str(self.seconds))
        try:
            self.process = self.popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                       '-Command', FOCUS_SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      stdin=subprocess.DEVNULL, env=environment, text=True, encoding='utf-8', errors='replace',
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            self.process = None
            return
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()

    def _read(self, process):
        try:
            for line in process.stdout:
                words = focus_words(line)
                if words and self.process is process:
                    self.last_spoken = time.monotonic()
                    self.speak(words)
        except (OSError, ValueError):
            pass

    def stop(self):
        process, self.process = self.process, None
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass


class WindowsChrome:
    """Everything that looks at or changes the computer. Tests replace the parts."""
    def __init__(self, registry=None, environ=None, run=subprocess.run, popen=subprocess.Popen,
                 urlopen=urllib.request.urlopen, which=shutil.which, sleep=time.sleep):
        self._registry = registry
        self.environ = os.environ if environ is None else environ
        self.run, self.popen, self.urlopen, self.which, self.sleep = run, popen, urlopen, which, sleep

    @property
    def registry(self):
        if self._registry is None:
            self._registry = Registry()
        return self._registry

    def supported(self):
        return os.name == 'nt'

    def find_chrome(self):
        candidates = []
        for root in ('HKCU', 'HKLM'):
            value = self.registry.read(root, APP_PATHS_KEY)
            if value:
                candidates.append(Path(os.path.expandvars(value.strip().strip('"'))))
        for root, key in UNINSTALL_KEYS:
            value = self.registry.read(root, key, 'InstallLocation')
            if value:
                candidates.append(Path(value.strip().strip('"')) / 'chrome.exe')
        for variable in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            folder = self.environ.get(variable)
            if folder:
                candidates.append(Path(folder) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
        return next((path for path in candidates if path.is_file()), None)

    def profiles(self):
        user_data = Path(self.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'User Data'
        try:
            return sorted(folder for folder in user_data.iterdir()
                          if folder.is_dir() and ((folder / 'Preferences').is_file() or (folder / 'Secure Preferences').is_file()))
        except OSError:
            return []

    def extension_state(self):
        """'enabled', 'disabled', 'removed' (the user removed the added copy), or None."""
        found = set()
        for profile in self.profiles():
            for name in ('Secure Preferences', 'Preferences'):
                try:
                    path = profile / name
                    if path.stat().st_size > MAX_PREFERENCES:
                        continue
                    data = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                extensions = data.get('extensions') if isinstance(data, dict) else None
                if not isinstance(extensions, dict):
                    continue
                settings = extensions.get('settings')
                for identity in EXTENSION_IDS:
                    entry = settings.get(identity) if isinstance(settings, dict) else None
                    if isinstance(entry, dict):
                        reasons = entry.get('disable_reasons')
                        found.add('disabled' if reasons or entry.get('state') == 0 else 'enabled')
                removed = extensions.get('external_uninstalls')
                if isinstance(removed, list) and STORE_ID in removed:
                    found.add('removed')
        return next((state for state in ('enabled', 'disabled', 'removed') if state in found), None)

    def add_extension(self):
        self.registry.write('HKCU', EXTENSION_KEY, 'update_url', WEB_STORE_UPDATE_URL)

    def remove_extension(self):
        if self.registry.read('HKCU', EXTENSION_KEY, 'update_url') == WEB_STORE_UPDATE_URL:
            self.registry.delete('HKCU', EXTENSION_KEY)

    def open_chrome(self, address=None):
        chrome = self.find_chrome()
        if chrome is None:
            return False
        if os.name == 'nt':
            try:
                # Companion has the foreground; let Chrome take it so keys reach Chrome.
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except (OSError, AttributeError):
                pass
        try:
            self.popen([str(chrome)] + ([address] if address else []), close_fds=True)
        except OSError:
            return False
        return True

    def powershell(self):
        return str(Path(self.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')

    def signed_by_google(self, path):
        """Same check the build uses for the WebView2 installer: a valid Authenticode signature."""
        script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:VC_SIGNED_FILE; "
                  "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Google LLC') { exit 0 } else { exit 1 }")
        try:
            result = self.run([self.powershell(), '-NoProfile', '-NonInteractive', '-Command', script],
                              env=dict(self.environ, VC_SIGNED_FILE=str(path)), capture_output=True, timeout=60,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def download_installer(self, folder, canceled):
        target = Path(folder) / 'chrome_installer.exe'
        request = urllib.request.Request(CHROME_INSTALLER_URL, headers={'User-Agent': 'VoiceCompanion'})
        total = 0
        with self.urlopen(request, timeout=30) as response, target.open('wb') as output:
            while not canceled.is_set():
                chunk = response.read(256 * 1024)
                if not chunk:
                    return target
                total += len(chunk)
                if total > MAX_INSTALLER:
                    raise OSError('The Chrome installer is too large.')
                output.write(chunk)
        return None

    def wait_for_chrome(self, seconds=30):
        for _ in range(seconds):
            if self.find_chrome():
                return True
            self.sleep(1)
        return bool(self.find_chrome())

    def install_chrome(self, progress, canceled):
        """Google's signed installer for this Windows user first; winget second."""
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            with tempfile.TemporaryDirectory(prefix='vc-chrome-') as folder:
                installer = self.download_installer(folder, canceled)
                if installer is not None and not canceled.is_set() and self.signed_by_google(installer):
                    self.run([str(installer), '/silent', '/install'], timeout=900, capture_output=True, creationflags=flags)
                    if self.wait_for_chrome():
                        return True
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        if canceled.is_set():
            return False
        winget = self.which('winget')
        if not winget:
            return False
        progress(MESSAGES['second_way'])
        try:
            self.run([winget, *WINGET_ARGUMENTS], timeout=1200, capture_output=True, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return False
        # winget exit codes vary by version; whether Chrome is now there is what counts.
        return self.wait_for_chrome()


class ChromeSetup:
    """One setup conversation. Long work runs on a thread; poll() reports it."""
    WAIT_SECONDS = 120
    LINK_SECONDS = 60
    HINT_SECONDS = 40
    PROGRESS_SECONDS = 15

    def __init__(self, system, connected, clock=time.monotonic, focus_reader=None):
        self.system, self.connected, self.clock = system, connected, clock
        self.notices = queue.Queue()
        self.focus = focus_reader
        self.state = None
        self.question = None
        self.finished = False
        self.last_instruction = ''
        self.started = self.last_progress = 0.0
        self.hinted = False
        self.canceled = threading.Event()
        self.worker = None
        self.result = None

    # Answers and commands ------------------------------------------------
    def start(self, quiet=False):
        if not self.system.supported():
            return self._finish(None if quiet else MESSAGES['windows_only'])
        if self.connected():
            return self._finish(None if quiet else MESSAGES['linked'])
        if self.system.find_chrome() is None:
            return self._ask('install_chrome', MESSAGES['ask_chrome'])
        if quiet and self.system.extension_state() == 'enabled':
            return self._finish(None)
        return self._extension_step(MESSAGES['ask_extension'])

    def respond(self, command):
        if self.finished:
            return None
        if command in ('cancel', 'stop', 'cancel setup', 'stop setup', 'escape') or command == 'no' and self.question is None:
            return self.cancel()
        if command in ('status', 'repeat', 'say that again', 'what now', 'help'):
            return self.last_instruction
        yes = command in ('yes', 'yes please', 'okay', 'ok', 'sure', 'go ahead')
        no = command in ('no', 'no thanks', 'not now', 'later')
        if self.question is None:
            return MESSAGES['busy']
        if not (yes or no):
            return MESSAGES['answer']
        question, self.question = self.question, None
        if question == 'install_chrome':
            if no:
                return self._finish(MESSAGES['no_chrome'])
            self.state = 'installing'
            self._begin_wait()
            self.worker = threading.Thread(target=self._install, daemon=True)
            self.worker.start()
            return self._say(MESSAGES['installing'])
        if question == 'add_extension':
            if no:
                return self._finish(MESSAGES['no_extension'])
            if self.system.extension_state() == 'removed':
                return self._open_store()
            try:
                self.system.add_extension()
            except OSError:
                return self._open_store()
            return self._wait('extension', MESSAGES['added'])
        if no:
            return self._finish(MESSAGES['no_store'])
        return self._open_store()

    def cancel(self):
        if self.finished:
            return None
        self.canceled.set()
        return self._finish(MESSAGES['canceled_install'] if self.state == 'installing' else MESSAGES['canceled'])

    def close(self):
        self.canceled.set()
        if self.focus is not None:
            self.focus.stop()

    # Background progress -------------------------------------------------
    def poll(self):
        """Spoken notices that are due now. Never blocks."""
        if self.finished:
            return self._drain()
        now = self.clock()
        if self.state == 'installing':
            if self.worker is not None and not self.worker.is_alive():
                if self.result:
                    self.state = None
                    self._ask('add_extension', MESSAGES['installed'])
                    self.notices.put(MESSAGES['installed'])
                else:
                    self.notices.put(self._finish(MESSAGES['install_failed']))
            elif now - self.last_progress >= self.PROGRESS_SECONDS:
                self.last_progress = now
                self.notices.put(MESSAGES['still_installing'])
        elif self.state in ('extension', 'store', 'linking'):
            if self.connected():
                self.notices.put(self._finish(MESSAGES['linked']))
            elif now - self.started >= (self.LINK_SECONDS if self.state == 'linking' else self.WAIT_SECONDS):
                if self.state == 'extension':
                    self._stop_waiting()
                    self.notices.put(self._ask('open_store', MESSAGES['ask_store']))
                else:
                    self.notices.put(self._finish(MESSAGES['not_linking' if self.state == 'linking' else 'store_failed']))
            elif self.state == 'extension' and not self.hinted and now - self.started >= self.HINT_SECONDS:
                self.hinted = True
                self.last_progress = now
                self.last_instruction = MESSAGES['added'] + ' ' + MESSAGES['menu_hint']
                self.notices.put(MESSAGES['menu_hint'])
            elif now - self.last_progress >= self.PROGRESS_SECONDS:
                self.last_progress = now
                recent_focus = self.focus is not None and time.monotonic() - self.focus.last_spoken < 10
                if not recent_focus:
                    self.notices.put(MESSAGES['waiting'])
        return self._drain()

    # Steps ---------------------------------------------------------------
    def _install(self):
        try:
            self.result = self.system.install_chrome(self.notices.put, self.canceled)
        except Exception:
            # A failed install must never stop the app; poll() reports it.
            self.result = False

    def _extension_step(self, prompt):
        state = self.system.extension_state()
        if state == 'enabled':
            return self._wait('linking', MESSAGES['already_added'])
        return self._ask('add_extension', prompt)

    def _open_store(self):
        if self.system.extension_state() != 'enabled':
            # A turned-off copy added by Companion would hide the store's Add button.
            try:
                self.system.remove_extension()
            except OSError:
                pass
        return self._wait('store', MESSAGES['store'], STORE_PAGE)

    def _wait(self, state, message, address=None):
        self.state = state
        self.hinted = False
        self._begin_wait()
        if not self.system.open_chrome(address):
            message = message + ' ' + MESSAGES['no_open']
        if self.focus is not None:
            self.focus.start()
        return self._say(message)

    def _begin_wait(self):
        self.started = self.last_progress = self.clock()

    def _stop_waiting(self):
        if self.focus is not None:
            self.focus.stop()

    def _ask(self, question, message):
        self.question = question
        self.state = None
        return self._say(message)

    def _say(self, message):
        self.last_instruction = message
        return message

    def _finish(self, message):
        self.finished = True
        self.question = None
        self.state = None
        self.close()
        return message

    def _drain(self):
        notices = []
        while True:
            try:
                notice = self.notices.get_nowait()
            except queue.Empty:
                return notices
            if notice:
                notices.append(notice)


# Companion hooks -------------------------------------------------------------
def relay_connected(app):
    try:
        import browser_cdp_relay
        relay = browser_cdp_relay.prepare(app.APP) if app.sys.platform == 'win32' else browser_cdp_relay._RELAY
    except Exception:
        return False
    return bool(relay is not None and relay.connected.is_set())


def begin(app, mode, quiet=False):
    """Start setup from a command, the Options button or the installer request."""
    if app.CHROME_SETUP is not None and not app.CHROME_SETUP.finished:
        app.speak(app.CHROME_SETUP.last_instruction)
        return 'chrome_setup'
    setup = ChromeSetup(WindowsChrome(), lambda: relay_connected(app), focus_reader=FocusReader(app.speak))
    message = setup.start(quiet)
    if message:
        app.speak(message)
    if setup.finished:
        return mode
    app.CHROME_SETUP, app.CHROME_SETUP_RETURN = setup, mode
    app.CONFIRM_CHOICE = 'yes'
    return 'chrome_setup'


def handle(app, text, mode):
    """Spoken or typed input while setup is on screen."""
    command = app.normalized_command(text)
    setup = app.CHROME_SETUP
    if setup is None or setup.finished:
        return begin(app, mode)
    control = app.speech_control(text)
    if control:
        app.control_speech(control)
        return mode
    if app.spoken_control(text) in ('exit', 'sleep'):
        # Exit and sleep keep working; setup stops first so nothing is left waiting.
        setup.cancel()
        target = finish(app, app.CHROME_SETUP_RETURN)
        return app.handle(text, 'awake' if target == 'sleep' else target, typed=True)
    if command in ('main menu', 'go to main menu', 'back to main menu'):
        setup.cancel()
        app.speak(MESSAGES['canceled'])
        return finish(app, 'awake')
    asked = setup.question
    message = setup.respond(command)
    if message:
        app.speak(message)
    if setup.question and setup.question != asked:
        app.CONFIRM_CHOICE = 'yes'
    return finish(app, app.CHROME_SETUP_RETURN) if setup.finished else 'chrome_setup'


def finish(app, mode):
    app.CHROME_SETUP = None
    app.sync_app_context(mode, True)
    return mode


def poll(app, mode):
    """Called from the main loop. Speaks progress and leaves setup when it ends."""
    setup = app.CHROME_SETUP
    if setup is None:
        return mode
    asked = setup.question
    for notice in setup.poll():
        app.speak_status(notice)
    if setup.question and setup.question != asked:
        app.CONFIRM_CHOICE = 'yes'
        app.sync_app_context(mode, True)
    if not setup.finished:
        return mode
    target = app.CHROME_SETUP_RETURN
    app.CHROME_SETUP = None
    if mode == 'sleep':
        app.SLEEP_RETURN_MODE = target
        return mode
    if mode == 'chrome_setup':
        app.sync_app_context(target, True)
        return target
    return mode


def startup_request(app, mode):
    """The installer leaves a request file when its Chrome checkbox was ticked."""
    request = Path(app.APP) / REQUEST_FILE
    if mode != 'sleep' or not request.is_file():
        return mode
    try:
        request.unlink()
    except OSError:
        return mode
    result = begin(app, 'sleep', quiet=True)
    if result == 'chrome_setup':
        app.CHROME_SETUP_RETURN = 'awake'
    return result
