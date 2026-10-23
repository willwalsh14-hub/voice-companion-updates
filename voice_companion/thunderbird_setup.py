"""Thunderbird account setup: find what is missing, say the next step, fix what can be fixed.

Checks, in order:
  a. Is Thunderbird installed?            (registry, then the usual program folders)
  b. Does it have an email account?       (profiles.ini, then the profile's prefs.js)
  c. Is the Voice Companion add-on there? (the profile's extensions folder and extensions.json)
  d. Is Thunderbird running?              (offer to start it, then wait up to a minute)
  e. Does the connected add-on report email accounts?

The add-on is installed by placing its .xpi in the profile's extensions folder
and, in the profile's user.js, letting Thunderbird enable add-ons placed there
without a prompt (extensions.autoDisableScopes without the profile scope) and
look for them at each start (extensions.startupScanScopes with the profile
scope). Thunderbird does not require Mozilla-signed add-ons. No administrator
rights are needed and nothing outside the user's own Thunderbird profile changes.

The same steps get Thunderbird ready for "add Thunderbird account"
(thunderbird_accounts.py). Then a profile without an email account is fine:
adding the first account is the point. An older copy of the add-on is replaced
while Thunderbird is closed; Thunderbird loads the new copy when it starts.

When a Thunderbird account is used and the add-on is not connected,
start_in_background() starts Thunderbird minimized, without taking the
keyboard from Voice Companion, and waits for it. Only a step that needs the
user's yes (install Thunderbird, install or turn on the add-on, restart
Thunderbird) goes back to the spoken flow above.

Installing Thunderbird takes one yes: winget (Windows Package Manager) first;
without it, Mozilla's own installer, run only when Windows confirms it is
signed by Mozilla Corporation. Then Thunderbird is started once so it makes
its profile, closed, given the add-on, and started again.

Everything that touches Windows goes through WindowsSystem, so tests use a fake.
"""
import configparser
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from collections import namedtuple
from pathlib import Path, PureWindowsPath

from thunderbird_bridge import ADDON_ID

SITE_URL = 'https://www.thunderbird.net/'
MIN_VERSION = 128
WAIT_SECONDS = 60
PROGRESS_SECONDS = 15
SCOPE_PROFILE = 1               # AddonManager.SCOPE_PROFILE
DEFAULT_AUTO_DISABLE = 15       # Thunderbird's default: add-ons placed by other programs start turned off
AGAIN = 'Then say add account and choose Thunderbird.'

NOT_INSTALLED = ('Thunderbird is not on this computer. Thunderbird is a free email program. '
                 'Voice Companion can install it for you now. It takes a few minutes. Install Thunderbird? Say yes or no.')
NOT_INSTALLING = 'Okay. Nothing was installed. When you want Thunderbird, say add Thunderbird account.'
INSTALLING = ('Installing Thunderbird. This takes a few minutes. If Windows asks whether to allow changes, press Alt plus Y.')
STILL_INSTALLING = 'Still installing Thunderbird.'
INSTALLED = 'Thunderbird is installed. '
INSTALL_FAILED = ('Thunderbird could not be installed. Nothing else was changed. Check the internet connection, then say '
                  'add Thunderbird account to try again. If it still fails, a helper can install it from thunderbird dot net.')
FIRST_START = 'Starting Thunderbird for the first time.'
FIRST_START_FAILED = ('Thunderbird is installed, but it is not ready yet. Restart the computer, then say '
                      'add Thunderbird account.')
FIRST_CLOSE_FAILED = ('Thunderbird is installed and open. Close Thunderbird, then say add Thunderbird account to finish.')
INSTALL_SECONDS = 15 * 60
PROFILE_SECONDS = 90
CLOSE_SECONDS = 30
WINGET_ID = 'Mozilla.Thunderbird'
INSTALLER_URL = 'https://download.mozilla.org/?product=thunderbird-latest-ssl&os=win64&lang=en-US'
SIGNER = 'Mozilla Corporation'
# Started in the background for a Thunderbird account (start_in_background).
STARTING_QUIETLY = 'Starting Thunderbird.'
WAITING_QUIETLY = 'Waiting for Thunderbird.'
STILL_STARTING = 'Still starting Thunderbird.'
NOTICE_SECONDS = 2
AUTO_NO_CONNECTION = ('Thunderbird started, but it did not connect to Voice Companion within one minute. '
                      'Close Thunderbird, then try again. If it still does not connect, say add account and choose Thunderbird to check it.')
OPENING_SITE = ('Opening thunderbird dot net in your web browser. Ask a helper to install Thunderbird '
                'and add your email account there. ' + AGAIN)
NOT_OPENING_SITE = 'Okay. Nothing was opened. When Thunderbird is installed, say add account and choose Thunderbird.'
SITE_FAILED = 'The website did not open. Ask a helper to go to thunderbird dot net and install Thunderbird. ' + AGAIN
TOO_OLD = ('Thunderbird on this computer is too old for Voice Companion. Ask a helper to update it to version 128 or newer. '
           'In Thunderbird, open Help, then About Thunderbird. ' + AGAIN)
NO_ACCOUNT = ('Thunderbird is installed, but no email account is set up in it yet. A helper needs to add your email account '
              'in Thunderbird. Start Thunderbird now so they can do that? Say yes or no.')
NO_ACCOUNT_OPEN = ('Thunderbird is open, but no email account is set up in it yet. Ask a helper to add your email account '
                   'in Thunderbird. ' + AGAIN)
NO_ACCOUNT_ADD = ('Thunderbird is installed, but no email account is set up in it yet. '
                  'Voice Companion can help you add one now. Add an email account to Thunderbird? Say yes or no.')
STARTING_FOR_ACCOUNT = 'Starting Thunderbird. When your helper has added your email account, say add account and choose Thunderbird.'
NOT_STARTING_FOR_ACCOUNT = 'Okay. When an email account is set up in Thunderbird, say add account and choose Thunderbird.'
ADDON_MISSING = ('Thunderbird needs the Voice Companion connection. It is a small add-on that lets Voice Companion '
                 'use your Thunderbird email. Install the Voice Companion connection in Thunderbird now? Say yes or no.')
ADDON_INSTALLED = 'The Voice Companion connection is installed. '
ADDON_RESTART = ('Thunderbird must restart to use the Voice Companion connection. '
                 'Close Thunderbird, then say add account and choose Thunderbird.')
ADDON_NOT_INSTALLED = ('Okay. Nothing was installed. A helper can install the connection by hand. '
                       'The steps are in Email Setup Helper, section 4.')
ADDON_FAILED = ('The Voice Companion connection could not be installed. Your Thunderbird email was not changed. '
                'Ask a helper to install it by hand. The steps are in Email Setup Helper, section 4.')
ADDON_OFF = ('The Voice Companion connection is turned off in Thunderbird. Ask a helper to open Add-ons and Themes '
             'in Thunderbird and turn on Voice Companion Mail Connection. ' + AGAIN)
ADDON_BLOCKED = ('Thunderbird cannot use the Voice Companion connection. Ask a helper to update Thunderbird '
                 'to version 128 or newer. ' + AGAIN)
NOT_RUNNING = 'Thunderbird is not open. Start Thunderbird now? Say yes or no.'
NOT_STARTING = 'Okay. Thunderbird was not started. ' + AGAIN
STARTING = 'Starting Thunderbird.'
START_FAILED = 'Thunderbird could not start. Try opening it from the Start menu. ' + AGAIN
WAITING = 'Thunderbird is open. Waiting for it to connect.'
STILL_WAITING = 'Still waiting for Thunderbird to connect.'
NO_CONNECTION = ('Thunderbird did not connect after one minute. Close Thunderbird, then say add account and choose '
                 'Thunderbird to try again. If it still does not connect, ask a helper to check Email Setup Helper, section 4.')
NO_RELAY = 'Voice Companion could not get ready for Thunderbird. Close Voice Companion and open it again. ' + AGAIN

Step = namedtuple('Step', 'message question')


class WindowsSystem:
    """The only part that reads the registry or starts programs."""

    def __init__(self):
        self.environ = os.environ

    def registry(self, hive, key, name=''):
        try:
            import winreg
        except ImportError:
            return None
        root = {'HKCU': winreg.HKEY_CURRENT_USER, 'HKLM': winreg.HKEY_LOCAL_MACHINE}[hive]
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                    value, _ = winreg.QueryValueEx(handle, name)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            except OSError:
                continue
        return None

    def exists(self, path):
        # lexists: the Microsoft Store's thunderbird.exe is a zero-size app link.
        return os.path.lexists(path) and not os.path.isdir(path)

    def running(self):
        try:
            found = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq thunderbird.exe', '/NH', '/FO', 'CSV'],
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return '"thunderbird.exe"' in found.lower()

    def launch(self, path):
        os.startfile(str(path))

    def launch_background(self, path):
        """Start minimized and inactive (SW_SHOWMINNOACTIVE): Voice Companion keeps the keyboard."""
        info = subprocess.STARTUPINFO() if hasattr(subprocess, 'STARTUPINFO') else None
        if info is not None:
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 7
        subprocess.Popen([str(path)], startupinfo=info, close_fds=True,
                         creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))

    def close_program(self):
        """Ask Thunderbird to close, as the window's close button does (no force)."""
        try:
            subprocess.run(['taskkill', '/IM', 'thunderbird.exe'], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            pass

    def start_process(self, command):
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def has_winget(self):
        import shutil
        return shutil.which('winget') is not None

    def download(self, url, path, timeout=300):
        """Mozilla's installer, over HTTPS only (download.mozilla.org redirects to its own servers)."""
        from urllib import request, parse

        class HttpsOnly(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if parse.urlsplit(newurl).scheme != 'https':
                    raise OSError('The download left HTTPS.')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        with request.build_opener(HttpsOnly()).open(url, timeout=timeout) as response, open(path, 'wb') as handle:
            while True:
                block = response.read(1 << 16)
                if not block: break
                handle.write(block)

    def signer(self, path):
        """(signature status, signer subject) from Windows' own Authenticode check."""
        script = ('$s = Get-AuthenticodeSignature -LiteralPath $env:VC_FILE; '
                  '"$($s.Status)|$($s.SignerCertificate.Subject)"')
        try:
            found = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                                   text=True, timeout=60, env={**os.environ, 'VC_FILE': str(path)},
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return '', ''
        status, _, subject = found.partition('|')
        return status.strip(), subject.strip()

    def sleep(self, seconds):
        time.sleep(seconds)

    def clock(self):
        return time.monotonic()

    def open_site(self, url):
        import webbrowser
        if not webbrowser.open(url):
            raise OSError('No web browser opened.')


# ---- a. Finding the program -------------------------------------------------

def _command_path(command):
    """'"C:\\...\\thunderbird.exe" -mail' -> the program path."""
    command = (command or '').strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    match = re.match(r'(.+?\.exe)\b', command, re.I)
    return match[1] if match else command


def find_program(system):
    """(path to thunderbird.exe, version text) or (None, '')."""
    candidates = []
    for hive in ('HKCU', 'HKLM'):
        key = r'Software\Mozilla\Mozilla Thunderbird'
        version = system.registry(hive, key, 'CurrentVersion')
        if version:
            path = system.registry(hive, key + '\\' + version + r'\Main', 'PathToExe')
            if path: candidates.append((path, version))
    for hive in ('HKCU', 'HKLM'):
        path = system.registry(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe')
        if path: candidates.append((path, ''))
        command = system.registry(hive, r'Software\Clients\Mail\Mozilla Thunderbird\shell\open\command')
        if command: candidates.append((_command_path(command), ''))
    env = system.environ
    for name, parts in (('ProgramFiles', ('Mozilla Thunderbird',)), ('ProgramW6432', ('Mozilla Thunderbird',)),
                        ('ProgramFiles(x86)', ('Mozilla Thunderbird',)), ('LOCALAPPDATA', ('Mozilla Thunderbird',)),
                        ('LOCALAPPDATA', ('Programs', 'Mozilla Thunderbird')),
                        ('LOCALAPPDATA', ('Microsoft', 'WindowsApps'))):
        if env.get(name):
            candidates.append((str(Path(env[name], *parts, 'thunderbird.exe')), ''))
    for path, version in candidates:
        if system.exists(path):
            return path, version
    return None, ''


def major_version(version):
    match = re.match(r'\s*(\d+)', version or '')
    return int(match[1]) if match else None


# ---- b. Profile and email account -------------------------------------------

def _profile_roots(system):
    env = system.environ
    roots = []
    if env.get('APPDATA'):
        roots.append(Path(env['APPDATA']) / 'Thunderbird')
    if env.get('LOCALAPPDATA'):
        # The Microsoft Store version keeps its profiles in its own package folder.
        roots.extend(sorted(Path(env['LOCALAPPDATA'], 'Packages').glob('Mozilla.Thunderbird*/LocalCache/Roaming/Thunderbird')))
    return roots


def _resolve(root, value, relative=True):
    if not value: return None
    if PureWindowsPath(value).is_absolute() or Path(value).is_absolute() or not relative:
        return Path(value)
    return root / Path(*value.replace('\\', '/').split('/'))


def profile_candidates(system):
    """Profile folders, the one Thunderbird opens by default first."""
    preferred, defaults, others = [], [], []
    for root in _profile_roots(system):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string((root / 'profiles.ini').read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, configparser.Error):
            continue
        for section in parser.sections():
            items = parser[section]
            if section.lower().startswith('install'):
                preferred.append(_resolve(root, items.get('Default')))
            elif section.lower().startswith('profile'):
                path = _resolve(root, items.get('Path'), items.get('IsRelative', '1').strip() != '0')
                (defaults if items.get('Default', '0').strip() == '1' else others).append(path)
    found = []
    for path in preferred + defaults + others:
        if path is not None and path not in found and path.is_dir():
            found.append(path)
    return found


PREF = re.compile(r'user_pref\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*("(?:[^"\\]|\\.)*"|[^)]*?)\s*\)\s*;')


def read_prefs(path):
    """name -> value from a prefs.js or user.js. Damaged lines are skipped."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {}
    prefs = {}
    for name, raw in PREF.findall(text):
        if raw.startswith('"'):
            value = raw[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        elif raw in ('true', 'false'):
            value = raw == 'true'
        else:
            try: value = int(raw)
            except ValueError: continue
        prefs[name] = value
    return prefs


def has_email_account(profile):
    """True when an account has a sending address. "Local Folders" alone has none."""
    prefs = read_prefs(Path(profile) / 'prefs.js')
    accounts = str(prefs.get('mail.accountmanager.accounts', '')).split(',')
    return any(str(prefs.get('mail.account.' + account.strip() + '.identities', '')).strip()
               for account in accounts if account.strip())


def choose_profile(system):
    """(profile folder or None, whether it has an email account)."""
    candidates = profile_candidates(system)
    for profile in candidates:
        if has_email_account(profile):
            return profile, True
    return (candidates[0] if candidates else None), False


# ---- c. The add-on ----------------------------------------------------------

def addon_file(profile):
    return Path(profile) / 'extensions' / (ADDON_ID + '.xpi')


def addon_state(profile):
    """'missing', 'waiting' (placed, Thunderbird has not loaded it yet), 'active', 'off' or 'blocked'."""
    if not addon_file(profile).is_file():
        return 'missing'
    try:
        data = json.loads((Path(profile) / 'extensions.json').read_text(encoding='utf-8'))
        entries = [a for a in data.get('addons') or [] if isinstance(a, dict) and a.get('id') == ADDON_ID]
    except (OSError, ValueError, AttributeError):
        entries = []
    if not entries:
        return 'waiting'
    entry = entries[0]
    if entry.get('active'): return 'active'
    if entry.get('userDisabled'): return 'off'
    if entry.get('appDisabled'): return 'blocked'
    return 'waiting'


def write_atomic(path, data, temporary_folder=None):
    """Write a temporary file, then replace: a failure never leaves half a file."""
    path = Path(path)
    temporary = Path(temporary_folder or path.parent) / ('voice-companion-' + path.name.replace('@', '-') + '.tmp')
    try:
        with open(temporary, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: temporary.unlink()
        except OSError: pass
        raise


def _set_pref(text, name, value):
    line = 'user_pref("' + name + '", ' + str(value) + ');'
    pattern = re.compile(r'user_pref\(\s*"' + re.escape(name) + r'"\s*,[^)]*\)\s*;')
    if pattern.search(text):
        return pattern.sub(lambda m: line, text)
    if text and not text.endswith('\n'): text += '\n'
    return text + line + '\n'


def install_addon(profile, package):
    """Place the add-on in the profile and let Thunderbird turn it on at its next start."""
    profile = Path(profile)
    data = Path(package).read_bytes()
    user_js = profile / 'user.js'
    try:
        text = user_js.read_bytes().decode('utf-8', 'surrogateescape')
    except FileNotFoundError:
        text = ''
    current = read_prefs(profile / 'prefs.js'); current.update(read_prefs(user_js))
    auto = current.get('extensions.autoDisableScopes')
    auto = auto if isinstance(auto, int) else DEFAULT_AUTO_DISABLE
    scan = current.get('extensions.startupScanScopes')
    scan = scan if isinstance(scan, int) else 0
    if not text.strip():
        text = '// Settings for this Thunderbird profile.\n'
    if 'Voice Companion' not in text:
        text += '// Added by Voice Companion so its mail connection add-on turns on.\n'
    text = _set_pref(text, 'extensions.autoDisableScopes', auto & ~SCOPE_PROFILE)
    text = _set_pref(text, 'extensions.startupScanScopes', scan | SCOPE_PROFILE)
    (profile / 'extensions').mkdir(exist_ok=True)
    write_atomic(addon_file(profile), data, profile)  # temporary file outside extensions: Thunderbird never sees it
    try:
        write_atomic(user_js, text.encode('utf-8', 'surrogateescape'))
    except BaseException:
        try: addon_file(profile).unlink()
        except OSError: pass
        raise


def package_version(package):
    """The version in the add-on package's manifest.json, or None if it cannot be read."""
    try:
        with zipfile.ZipFile(package) as archive:
            return str(json.loads(archive.read('manifest.json').decode('utf-8')).get('version') or '') or None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def version_tuple(text):
    return tuple(int(part) for part in re.findall(r'\d+', str(text or '')))


def refresh_addon(profile, package):
    """Replace an older copy of the add-on. Only while Thunderbird is closed: it locks the file."""
    try:
        if addon_file(profile).read_bytes() != Path(package).read_bytes():
            write_atomic(addon_file(profile), Path(package).read_bytes(), profile)
    except OSError:
        pass


# ---- Starting Thunderbird when a Thunderbird account is used ----------------

class NotReady(Exception):
    """Thunderbird needs a step the user must agree to; Setup.start() explains it and asks."""


def start_in_background(system, package, relay, progress, limit=WAIT_SECONDS):
    """Connect Thunderbird's add-on without asking. Returns True when Voice Companion started Thunderbird.

    Says nothing when it connects within two seconds; then one short line, and
    another every 15 seconds. Raises NotReady for anything that needs a yes."""
    if relay is None:
        raise NotReady('no relay')
    if relay.connected.is_set():
        return False
    program, version = find_program(system)
    major = major_version(version)
    if not program or (major is not None and major < MIN_VERSION):
        raise NotReady('program')
    profile, _ = choose_profile(system)
    if profile is None:
        raise NotReady('profile')
    state = addon_state(profile)
    running = system.running()
    if state in ('missing', 'off', 'blocked') or (state == 'waiting' and running):
        raise NotReady('add-on')
    launched = False
    if not running:
        refresh_addon(profile, package)
        try:
            system.launch_background(program)
        except OSError as exc:
            raise NotReady('start') from exc
        launched = True
    if relay.connected.wait(NOTICE_SECONDS):
        return launched
    progress(STARTING_QUIETLY if launched else WAITING_QUIETLY)
    waited = NOTICE_SECONDS
    while waited < limit:
        if relay.connected.wait(PROGRESS_SECONDS):
            return launched
        waited += PROGRESS_SECONDS
        if waited < limit:
            progress(STILL_STARTING)
    return launched


# ---- Installing Thunderbird ---------------------------------------------------

def _run(system, command, timeout, progress, every=PROGRESS_SECONDS):
    """Run a program, saying STILL_INSTALLING every 15 seconds. Its exit code, or None if it took too long."""
    try:
        process = system.start_process(command)
    except OSError:
        return None
    started = last = system.clock()
    while process.poll() is None:
        now = system.clock()
        if now - started >= timeout:
            try: process.kill()
            except OSError: pass
            return None
        if now - last >= every:
            progress(STILL_INSTALLING); last = now
        system.sleep(1)
    return process.returncode


def install_program(system, progress):
    """Install Thunderbird. Returns the path to thunderbird.exe, or None. Never hangs: every step has a time limit."""
    if system.has_winget():
        _run(system, ['winget', 'install', '--id', WINGET_ID, '-e', '--silent',
                      '--accept-package-agreements', '--accept-source-agreements'], INSTALL_SECONDS, progress)
        # Success is Thunderbird being there, whatever the exit code (it may already be installed).
        program, _ = find_program(system)
        if program:
            return program
    folder = Path(tempfile.mkdtemp(prefix='voice-companion-thunderbird-'))
    installer = folder / 'Thunderbird Setup.exe'
    try:
        try:
            system.download(INSTALLER_URL, installer)
        except OSError:
            return None
        status, subject = system.signer(installer)
        # Only Mozilla's own signed installer runs.
        if status != 'Valid' or not re.search(r'(?:^|,\s*)O="?' + re.escape(SIGNER) + r'"?(?:,|$)', subject):
            return None
        if _run(system, [str(installer), '/S'], INSTALL_SECONDS, progress) is None:
            return None
        program, _ = find_program(system)
        return program
    finally:
        for item in (installer, folder):
            try: item.unlink() if item.is_file() else item.rmdir()
            except OSError: pass


def _until(system, check, seconds):
    deadline = system.clock() + seconds
    while not check():
        if system.clock() >= deadline:
            return False
        system.sleep(1)
    return True


# ---- The spoken flow --------------------------------------------------------

class Setup:
    """start() and answer() each return a Step: what to say, and the yes or no question now waiting."""

    def __init__(self, system, package, connect, relay, progress, ready=None, adding=False):
        self.system, self.package = system, Path(package)
        self.connect, self.relay, self.progress = connect, relay, progress
        # ready(setup) -> Step: what happens once Thunderbird is connected (default: connect its accounts).
        self.ready, self.adding = ready, adding
        self.program = self.profile = None
        self.version = None    # Thunderbird's major version, when Windows records it

    def _step(self, message, question):
        """While adding an account, "say add account and choose Thunderbird" becomes "say add Thunderbird account"."""
        if self.adding:
            message = message.replace('say add account and choose Thunderbird', 'say add Thunderbird account')
        return Step(message, question)

    def addon_outdated(self):
        """True when the connected add-on is older than the one this copy of Voice Companion ships."""
        relay = self._relay()
        bundled = package_version(self.package)
        if relay is None or not bundled:
            return False
        return version_tuple(getattr(relay, 'version', '')) < version_tuple(bundled)

    def _relay(self):
        try:
            return self.relay()
        except OSError:
            return None

    def _connected(self):
        relay = self._relay()
        return relay is not None and relay.connected.is_set()

    def _finish(self):
        from mail_accounts import AccountError
        if self.ready is not None:
            return self.ready(self)
        try:
            address = self.connect()
        except (AccountError, OSError, ValueError) as exc:
            return Step(str(exc), None)
        return Step('Thunderbird account ' + address + ' is connected and selected. Say check email to open it.', None)

    def _wait(self, first):
        relay = self._relay()
        if relay is None:
            return self._step(NO_RELAY, None)
        self.progress(first)
        waited = 0
        while waited < WAIT_SECONDS:
            if waited:
                self.progress(STILL_WAITING)
            if relay.connected.wait(PROGRESS_SECONDS):
                return self._finish()
            waited += PROGRESS_SECONDS
        return self._step(NO_CONNECTION, None)

    def start(self):
        self.program, version = find_program(self.system)
        self.version = major_version(version)
        if self._connected():
            return self._finish()
        if not self.program:
            return self._step(NOT_INSTALLED, 'install_program')
        major = self.version
        if major is not None and major < MIN_VERSION:
            return self._step(TOO_OLD, None)
        running = self.system.running()
        self.profile, has_account = choose_profile(self.system)
        if not has_account:
            if not self.adding:
                return self._step(NO_ACCOUNT_ADD, 'add_first')
            if self.profile is None:
                # Never opened: Thunderbird makes its profile on its first start.
                if running:
                    return self._step(FIRST_CLOSE_FAILED, None)
                return self._first_start()
        state = addon_state(self.profile)
        if state == 'missing':
            return self._step(ADDON_MISSING, 'install')
        if state == 'off':
            return self._step(ADDON_OFF, None)
        if state == 'blocked':
            return self._step(ADDON_BLOCKED, None)
        if state == 'waiting' and running:
            return self._step(ADDON_RESTART, None)
        if not running:
            refresh_addon(self.profile, self.package)
            return self._step(NOT_RUNNING, 'start')
        return self._wait(WAITING)

    def _launch(self):
        try:
            self.system.launch(self.program)
            return True
        except OSError:
            return False

    def _install_everything(self):
        """After one yes: install Thunderbird, let it make its profile, add the connection, start it."""
        self.progress(INSTALLING)
        self.program = install_program(self.system, self.progress)
        if not self.program:
            return self._step(INSTALL_FAILED, None)
        return self._first_start(INSTALLED)

    def _first_start(self, before=''):
        """Start Thunderbird once so it makes its profile, close it, add the connection, start it again."""
        self.progress(before + FIRST_START)
        try:
            self.system.launch_background(self.program)
        except OSError:
            return self._step(FIRST_START_FAILED, None)
        # Thunderbird's first start makes its profile; it needs the add-on before its next start.
        if not _until(self.system, lambda: choose_profile(self.system)[0] is not None and
                      (choose_profile(self.system)[0] / 'prefs.js').is_file(), PROFILE_SECONDS):
            return self._step(FIRST_START_FAILED, None)
        self.profile, _ = choose_profile(self.system)
        self.system.close_program()
        if not _until(self.system, lambda: not self.system.running(), CLOSE_SECONDS):
            return self._step(FIRST_CLOSE_FAILED, None)
        try:
            install_addon(self.profile, self.package)
        except OSError:
            return self._step(ADDON_FAILED, None)
        try:
            self.system.launch_background(self.program)
        except OSError:
            return self._step(START_FAILED, None)
        return self._wait(STARTING)

    def answer(self, question, yes):
        if question == 'install_program':
            if not yes: return self._step(NOT_INSTALLING, None)
            return self._install_everything()
        if question == 'download':
            if not yes: return self._step(NOT_OPENING_SITE, None)
            try:
                self.system.open_site(SITE_URL)
            except OSError:
                return self._step(SITE_FAILED, None)
            return self._step(OPENING_SITE, None)
        if question == 'start_for_account':
            if not yes: return self._step(NOT_STARTING_FOR_ACCOUNT, None)
            return self._step(STARTING_FOR_ACCOUNT if self._launch() else START_FAILED, None)
        if question == 'install':
            if not yes: return self._step(ADDON_NOT_INSTALLED, None)
            try:
                install_addon(self.profile, self.package)
            except OSError:
                return self._step(ADDON_FAILED, None)
            if self.system.running():
                return self._step(ADDON_INSTALLED + ADDON_RESTART, None)
            return self._step(ADDON_INSTALLED + NOT_RUNNING, 'start')
        if question == 'start':
            if not yes: return self._step(NOT_STARTING, None)
            if not self._launch():
                return self._step(START_FAILED, None)
            return self._wait(STARTING)
        return self._step('', None)
