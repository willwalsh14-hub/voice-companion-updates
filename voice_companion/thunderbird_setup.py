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
INSTALL_SECONDS = 10 * 60     # each way: winget, then Mozilla's installer
PROFILE_SECONDS = 90
CLOSE_SECONDS = 30
WINGET_ID = 'Mozilla.Thunderbird'
# thunderbird-latest-ssl is the newest Release (monthly, 154 and newer have Microsoft Graph); Mozilla made it
# the default download in Thunderbird 135. thunderbird-esr-latest-ssl would be ESR: never used.
INSTALLER_URL = 'https://download.mozilla.org/?product=thunderbird-latest-ssl&os=win64&lang=en-US'
VERSIONS_URL = 'https://product-details.mozilla.org/1.0/thunderbird_versions.json'
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
TOO_OLD = ('Thunderbird on this computer is too old for Voice Companion. It needs version 128 or newer. '
           'To update it, say update Thunderbird. ' + AGAIN)
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
                       'The steps are in Email Setup Helper, section 3.')
ADDON_FAILED = ('The Voice Companion connection could not be installed. Your Thunderbird email was not changed. '
                'Ask a helper to install it by hand. The steps are in Email Setup Helper, section 3.')
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
                 'Thunderbird to try again. If it still does not connect, ask a helper to check Email Setup Helper, section 3.')
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

    def wake_windows(self, stage):
        """See wake(). Returns the handles of the windows shown, to minimize again later."""
        import window_hiding
        desktop = window_hiding.Win32Desktop()
        mains = [w for w in desktop.windows() if w.kind == window_hiding.MAIN_CLASS and not w.owned]
        changed = []
        if stage == 1:
            for window in mains:
                if window.minimized:
                    desktop.user.ShowWindow(window.handle, 4)   # SW_SHOWNOACTIVATE: on screen, no keyboard
                    changed.append(window.handle)
            return changed
        if mains:
            before = desktop.foreground()
            target = mains[0].handle
            if desktop.user.IsIconic(target):
                changed.append(target)
            desktop.bring_forward(target)
            import time
            time.sleep(0.4)
            if before:
                desktop.bring_forward(before)
        return changed

    def settle_windows(self, handles):
        import window_hiding
        desktop = window_hiding.Win32Desktop()
        for handle in handles:
            if desktop.exists(handle):
                desktop.minimize(handle)

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

    signer_problem = ''    # why the last signature check did not say Valid, in Windows' words (for error reports)

    def signer(self, path):
        """(signature status, signer subject) from Windows' own Authenticode check.

        Windows PowerShell is run by its full path, without PSModulePath: started from PowerShell 7,
        it would inherit 7's module path and fail to load Get-AuthenticodeSignature."""
        script = ('$s = Get-AuthenticodeSignature -LiteralPath $env:VC_FILE; '
                  '"$($s.Status)|$($s.SignerCertificate.Subject)|$($s.StatusMessage)"')
        powershell = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
        env = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
        env['VC_FILE'] = str(path)
        try:
            done = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                                  text=True, timeout=60, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError) as exc:
            self.signer_problem = type(exc).__name__ + ': ' + str(exc)
            return '', ''
        status, _, rest = done.stdout.strip().partition('|')
        subject, _, message = rest.rpartition('|') if rest.count('|') else (rest, '', '')
        self.signer_problem = '' if status.strip() == 'Valid' else (message.strip() + ' ' + done.stderr.strip()).strip()[-600:]
        return status.strip(), subject.strip()

    def latest_release(self):
        """The newest Thunderbird Release version from Mozilla's product details, or None."""
        from urllib import request
        try:
            with request.urlopen(VERSIONS_URL, timeout=20) as response:
                found = json.loads(response.read(1 << 20).decode('utf-8')).get('LATEST_THUNDERBIRD_VERSION')
        except (OSError, ValueError, AttributeError):
            return None
        return str(found) if found else None

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


# Thunderbird waits to start add-ons until its main window has restored its tabs. Started
# minimized (or off the taskbar), that can wait until the user brings the window up, so mail
# hung until Alt+Tab (Will, October 29, 2026). With this off, the mail connection add-on starts
# with Thunderbird. Mozilla's own setting; it changes nothing else.
PROMPT_START = 'extensions.webextensions.background-delayed-startup'


# Windows 11 runs a program whose windows are all minimized in "efficiency mode", and Thunderbird
# puts its background parts there itself. The mail connection add-on is such a part, so it could
# wait until Thunderbird came to the front (Will, October 30, 2026). Mozilla's own setting.
NO_ECO = 'dom.ipc.processPriorityManager.backgroundUsesEcoQoS'
PROMPT_PREFS = ((PROMPT_START, 'false'), (NO_ECO, 'false'))


def start_addons_promptly(profile):
    """Make sure the profile's user.js has PROMPT_PREFS. Thunderbird reads them at its next start.
    True when they are set. Never raises: Thunderbird still starts without them."""
    user_js = Path(profile) / 'user.js'
    try:
        try:
            text = user_js.read_bytes().decode('utf-8', 'surrogateescape')
        except FileNotFoundError:
            text = ''
        current = read_prefs(user_js)
        if all(current.get(name) is False for name, _ in PROMPT_PREFS):
            return True
        if not text.strip():
            text = '// Settings for this Thunderbird profile.\n'
        if 'connection add-on starts with Thunderbird' not in text:
            text += '// Added by Voice Companion so its mail connection add-on starts with Thunderbird, even minimized.\n'
        for name, value in PROMPT_PREFS:
            text = _set_pref(text, name, value)
        write_atomic(user_js, text.encode('utf-8', 'surrogateescape'))
        return True
    except OSError:
        return False


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
    for name, value in PROMPT_PREFS:
        text = _set_pref(text, name, value)
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
    """Replace an older copy of the add-on. Only while Thunderbird is closed: it locks the file.
    True when the profile now has this copy; False when the file could not be read or written."""
    try:
        if addon_file(profile).read_bytes() != Path(package).read_bytes():
            write_atomic(addon_file(profile), Path(package).read_bytes(), profile)
        return True
    except OSError:
        return False


# ---- Starting Thunderbird when a Thunderbird account is used ----------------

_START_LOCK = __import__('threading').Lock()


class NotReady(Exception):
    """Thunderbird needs a step the user must agree to; Setup.start() explains it and asks."""


# The two wake-ups while waiting for the connection: after 2 seconds, then after this many more.
WAKE_SECONDS = (3, 4)


def wake(system, stage, note=None):
    """Stage 1: show Thunderbird's minimized windows without the keyboard. Stage 2: bring its
    main window to the front, then give the keyboard back. Returns the windows it changed."""
    try:
        changed = system.wake_windows(stage)
    except Exception as exc:
        changed = []
        if note: note('[thunderbird] wake-up %d failed: %s' % (stage, exc))
        return changed
    if note: note('[thunderbird] not connected yet; wake-up %d changed %d window(s)' % (stage, len(changed)))
    return changed


def settle(system, handles, note=None):
    """Put the windows a wake-up showed back the way they were (minimized)."""
    try:
        system.settle_windows(handles)
    except Exception as exc:
        if note: note('[thunderbird] could not put the windows back: %s' % exc)


# The add-on running in Thunderbird is older than the one Voice Companion carries (Will, October 30,
# 2026: "nothing on the user's end"). Thunderbird loads a new add-on only when it starts, so it is
# restarted once, the normal way, and never while a message is being written.
ADDON_UPDATING = 'Updating the Voice Companion connection in Thunderbird. Thunderbird restarts for a moment.'
ADDON_UPDATED = 'The Voice Companion connection in Thunderbird is updated.'
ADDON_NOT_REPLACED = ('The Voice Companion connection in Thunderbird could not be updated. Another program may be '
                      'using its file. Email works as before. Restart the computer, and Voice Companion tries again.')
ADDON_UPDATE_SLOW = ('Thunderbird has not started again yet. Your email is safe. '
                     'Say check email, and Voice Companion starts it.')
DISCONNECT_SECONDS = 15


def addon_behind(relay, package):
    """True when the connected add-on is older than the one in this copy of Voice Companion."""
    bundled = package_version(package)
    if relay is None or not relay.connected.is_set() or not bundled:
        return False
    return version_tuple(getattr(relay, 'version', '')) < version_tuple(bundled)


def refresh_running_addon(system, package, relay, progress=None, background=True, note=None):
    """Close Thunderbird the normal way, put the new add-on in its profile, and start it again:
    in the background when Voice Companion had started it, else as the person opens it.

    Returns 'current' (nothing to do or nothing possible), 'composing' (a message is being
    written: try later), 'not closed' (Thunderbird did not close; nothing changed),
    'not replaced' (the add-on file was in use; Thunderbird started again as it was),
    'not started', or 'updated'."""
    if not addon_behind(relay, package):
        return 'current'
    def log(text):
        if note: note('[thunderbird] ' + text)
    def wait(check, seconds):
        until = system.clock() + seconds
        while not check():
            if system.clock() >= until:
                return False
            system.sleep(0.5)
        return True
    with _START_LOCK:
        try:
            about = relay.request('about', {}, timeout=10) or {}
        except Exception:
            about = {}
        if about.get('composing'):
            log('add-on update waits: a message is being written')
            return 'composing'
        profile = Path(about['profile']) if about.get('profile') and Path(about['profile']).is_dir() else choose_profile(system)[0]
        program, _ = find_program(system)
        if profile is None or not program:
            log('add-on update skipped: no profile or program found')
            return 'current'
        if progress: progress(ADDON_UPDATING)
        log('updating the add-on from %s' % getattr(relay, 'version', ''))
        try:
            answer = relay.request('quit', {}, timeout=10) or {}
        except Exception:
            answer = {}
        if answer.get('reason') == 'composing':
            return 'composing'
        # Never forced: only Thunderbird's own normal close, which keeps every window's work.
        if not answer.get('quit') or not wait(lambda: not system.running(), CLOSE_SECONDS):
            log('add-on update stopped: Thunderbird did not close')
            return 'not closed'
        wait(lambda: not relay.connected.is_set(), DISCONNECT_SECONDS)
        replaced = refresh_addon(profile, package)
        start_addons_promptly(profile)
        try:
            (system.launch_background if background else system.launch)(program)
        except OSError:
            log('add-on update: Thunderbird did not start again')
            return 'not started'
    if not replaced:
        # Thunderbird is back with the old add-on; restarting it again would not help.
        log('add-on update: the add-on file could not be replaced')
        return 'not replaced'
    if not wait(lambda: relay.connected.is_set() and not addon_behind(relay, package), WAIT_SECONDS):
        log('add-on update: no connection after the restart')
        return 'not started'
    log('add-on updated to %s' % getattr(relay, 'version', ''))
    return 'updated'


def start_in_background(system, package, relay, progress, limit=WAIT_SECONDS, note=None):
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
    with _START_LOCK:
        # Startup and a mail command can both start Thunderbird; only one may launch it.
        if relay.connected.is_set():
            return False
        state = addon_state(profile)
        running = system.running()
        if state in ('missing', 'off', 'blocked') or (state == 'waiting' and running):
            raise NotReady('add-on')
        launched = False
        if not running:
            refresh_addon(profile, package)
            start_addons_promptly(profile)
            try:
                system.launch_background(program)
            except OSError as exc:
                raise NotReady('start') from exc
            launched = True
    import user_stop   # Escape or closing Companion stops this wait (user_stop.Stopped)
    woken = []
    try:
        if user_stop.wait(relay.connected, NOTICE_SECONDS):
            return launched
        progress(STARTING_QUIETLY if launched else WAITING_QUIETLY)
        # Will, October 30, 2026: on his computer the connection came only after he pressed Alt+Tab to
        # Thunderbird and back, in both window choices. Windows lets a program with no window on
        # screen run slowly. So Voice Companion does the same, for him: first Thunderbird's window is
        # shown without the keyboard; if that is not enough, it comes to the front for a moment and
        # the keyboard comes straight back. Then it goes back out of the way.
        waited = NOTICE_SECONDS
        for stage, wait_for in ((1, WAKE_SECONDS[0]), (2, WAKE_SECONDS[1])):
            woken += wake(system, stage, note)
            if user_stop.wait(relay.connected, wait_for):
                return launched
            waited += wait_for
        while waited < limit:
            if user_stop.wait(relay.connected, PROGRESS_SECONDS):
                return launched
            waited += PROGRESS_SECONDS
            if waited < limit:
                progress(STILL_STARTING)
        return launched
    finally:
        if woken:
            settle(system, woken, note)


# ---- Installing Thunderbird ---------------------------------------------------

def _run(system, command, timeout, progress, every=PROGRESS_SECONDS, message=STILL_INSTALLING):
    """Run a program, saying message every 15 seconds. Its exit code, or None if it took too long."""
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
            progress(message); last = now
        system.sleep(1)
    return process.returncode


def signed_by_mozilla(system, path):
    """Only Mozilla's own signed installer runs: Windows' Authenticode check, organization Mozilla Corporation."""
    status, subject = system.signer(path)
    try:
        system.last_signature = (status, subject)
    except AttributeError:
        pass
    return status == 'Valid' and bool(re.search(r'(?:^|,\s*)O="?' + re.escape(SIGNER) + r'"?(?:,|$)', subject))


def install_program(system, progress):
    """Install Thunderbird (the Release channel). Returns the path to thunderbird.exe, or None.
    Never hangs: every step has a time limit."""
    from thunderbird_upgrade import install_release, installed, is_current
    if system.has_winget():
        # Mozilla.Thunderbird is the Release channel (Mozilla.Thunderbird.ESR would be ESR).
        _run(system, ['winget', 'install', '--id', WINGET_ID, '-e', '--silent',
                      '--accept-package-agreements', '--accept-source-agreements'], INSTALL_SECONDS, progress)
        # Success is Thunderbird being there, whatever the exit code (it may already be installed).
        program, version = find_program(system)
        if program:
            info = installed(system, program, version)
            if is_current(info) or info.store or info.major is None:
                return program
            # An ESR or older copy was already there: Mozilla's Release installer, into the same folder.
            install_release(system, info.folder, progress)   # if it fails, the older copy still works
            return program
    folder = Path(tempfile.mkdtemp(prefix='voice-companion-thunderbird-'))
    installer = folder / 'Thunderbird Setup.exe'
    try:
        try:
            system.download(INSTALLER_URL, installer)
        except OSError:
            return None
        if not signed_by_mozilla(system, installer):
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

    def __init__(self, system, package, connect, relay, progress, ready=None, adding=False, upgrader=None):
        self.system, self.package = system, Path(package)
        self.connect, self.relay, self.progress = connect, relay, progress
        # ready(setup) -> Step: what happens once Thunderbird is connected (default: connect its accounts).
        self.ready, self.adding = ready, adding
        self.program = self.profile = None
        self.version = None    # Thunderbird's major version, when Windows records it
        # upgrader() -> thunderbird_upgrade.Upgrade: offers the move to the Release channel (154 or newer).
        self.upgrader, self.upgrade = upgrader, None

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
        if self.program:
            # The program folder's own version (application.ini) is right even when the registry lags.
            from thunderbird_upgrade import installed
            info = installed(self.system, self.program, version)
            if info is not None and info.major is not None:
                self.version = info.major
        if self._connected():
            return self._finish()
        if not self.program:
            return self._step(NOT_INSTALLED, 'install_program')
        major = self.version
        offered = self._offer_upgrade(major is not None and major < MIN_VERSION)
        if offered is not None:
            return offered
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
            if self.adding:
                # The user asked to add an account: start Thunderbird quietly, no extra question.
                try:
                    self.system.launch_background(self.program)
                except OSError:
                    return self._step(START_FAILED, None)
                return self._wait(STARTING)
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

    def _offer_upgrade(self, too_old):
        """Too old for the add-on: always offer the update. Adding an account to ESR or a Thunderbird
        before 154: offer it once (Microsoft 365 needs it), when it is due. None: go on as before."""
        if self.upgrader is None or self.upgrade is not None or not (too_old or self.adding):
            return None
        try:
            self.upgrade = self.upgrader()
            step = self.upgrade.check() if too_old else self.upgrade.offer()
        except Exception:
            return None
        return step if step is not None and step.question == 'upgrade' else None

    def _after_upgrade(self, yes):
        import thunderbird_upgrade
        step = self.upgrade.answer('upgrade', yes)
        updated = yes and not self.upgrade.report.get('problems') and \
            thunderbird_upgrade.is_current(thunderbird_upgrade.installed(self.system))
        if not (updated or not yes):
            return step                    # it did not work: say so, and stop here
        rest = self.start()
        if self.version is not None and self.version < MIN_VERSION and not updated:
            return rest
        return Step((step.message + ' ' + rest.message).strip(), rest.question)

    def answer(self, question, yes):
        if question == 'upgrade' and self.upgrade is not None:
            return self._after_upgrade(yes)
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
