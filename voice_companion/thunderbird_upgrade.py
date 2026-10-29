"""Move Thunderbird to the current Release channel (154 or newer) with one yes.

Why: Thunderbird 154 (the monthly Release channel) added Microsoft Graph for
Microsoft 365 accounts. The yearly ESR channel (153) does not have it.

What Mozilla gives, and what Voice Companion uses (see the pull request for sources):
  - download.mozilla.org product thunderbird-latest-ssl: the newest Release
    (Mozilla made monthly Release the default download in Thunderbird 135).
    thunderbird-esr-latest-ssl is ESR; Voice Companion never uses it.
  - winget Mozilla.Thunderbird is Release; Mozilla.Thunderbird.ESR is ESR.
  - Mozilla's advice for moving from ESR to Release: install Release into the
    same folder as ESR. Thunderbird picks its profile per install folder
    (installs.ini, profiles.ini [Install...] Default=), so the same folder opens
    the same profile, with every account, password and the Voice Companion add-on.

How, after one yes:
  1. Note the accounts: from the profile's prefs.js, and, when the add-on is
     connected, from Thunderbird itself (accounts, and whether each Inbox opens).
     If Thunderbird is closed, it is started quietly first to see this.
  2. Close Thunderbird the normal way. Never while a message is being written.
  3. Copy the profile's key files (prefs.js, logins.json, key4.db, cert9.db)
     and profiles.ini and installs.ini to a backup folder beside the profiles.
  4. Download Mozilla's Release installer, run it only when Windows confirms it
     is signed by Mozilla Corporation, into the folder Thunderbird is in now.
  5. Start Thunderbird and check: the same profile, the same accounts with the
     same types and addresses, every Inbox that opened before opens now.
  6. Anything different: close Thunderbird, put the backed-up files back, start
     it again, and say so. The new Thunderbird is not removed.

Existing accounts are never changed, converted or signed in again: nothing here
touches account settings, passwords or sign-in tokens. Microsoft Graph is used
only for accounts the person adds later.

After this, Thunderbird's own updater keeps it current. Nothing in Voice
Companion turns that off or installs ESR.
"""
import json
import os
import re
import shutil
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import thunderbird_setup as setup
from thunderbird_setup import Step

RELEASE_MIN = 154
RELEASE_URL = 'https://download.mozilla.org/?product=thunderbird-latest-ssl&os=win64&lang=en-US'
PROFILE_FILES = ('prefs.js', 'logins.json', 'key4.db', 'cert9.db')
ROOT_FILES = ('profiles.ini', 'installs.ini')
BACKUP_FOLDER = 'Voice Companion backups'
KEEP_BACKUPS = 3
OFFER_FILE = 'thunderbird-update.json'
OFFER_DAYS = 7               # after "no", the automatic question waits this long ("update Thunderbird" always works)
DISCONNECT_SECONDS = 15
INBOX_TRIES, INBOX_WAIT_SECONDS = 4, 5
MAIL_TYPES = ('imap', 'pop3', 'ews', 'graph', 'owl')    # accounts whose Inbox is checked before and after

QUESTION = ('Thunderbird needs an update to work with Microsoft 365. Update it now? It takes a few minutes. '
            'Your email and settings stay as they are. Say yes or no.')
NOT_NOW = 'Okay. Thunderbird was not changed. To update it later, say update Thunderbird.'
GETTING_READY = 'Getting Thunderbird ready for the update.'
COMPOSING = ('A message is being written in Thunderbird, so Thunderbird was not updated. Nothing was changed. '
             'Finish or close the message, then say update Thunderbird.')
CLOSING = 'Closing Thunderbird for the update.'
NOT_CLOSED = ('Thunderbird did not close, so it was not updated. Nothing was changed. If Thunderbird is asking a question, '
              'say show Thunderbird and answer it. Then say update Thunderbird.')
BACKUP_FAILED = ('Voice Companion could not make a safety copy of your Thunderbird settings, so it did not update Thunderbird. '
                 'Nothing was changed.')
UPDATING = 'Updating Thunderbird. This takes a few minutes. If Windows asks whether to allow changes, press Alt plus Y.'
STILL_UPDATING = 'Still updating Thunderbird.'
FAILED = ('Thunderbird could not be updated. Your Thunderbird still works as before, and your email was not changed. '
          'Check the internet connection, then say update Thunderbird to try again.')
CHECKING = 'Thunderbird is updated. Starting it and checking your email accounts.'
DONE = 'Thunderbird is updated to version {version}. All your email accounts are here, as before.'
NOT_CONNECTED = ('Thunderbird is updated to version {version}. Your email accounts were not changed, but the Voice Companion '
                 'connection did not start, so they could not be checked. Close Thunderbird, then say check email.')
RESTORED = ('Thunderbird is updated, but your email accounts did not come back the same way. Voice Companion put your '
            'Thunderbird settings back as they were. Nothing was lost. Ask a helper to check Thunderbird.')
RESTORE_WAITING = ('Thunderbird is updated, but your email accounts did not come back the same way, and Thunderbird did not close. '
                   'Nothing was lost. Close Thunderbird, then say update Thunderbird. Voice Companion then puts your settings back.')
RESTORE_FAILED = ('Thunderbird is updated, but your email accounts did not come back the same way, and Voice Companion could not '
                  'put your settings back by itself. Nothing was lost: a copy is in the Voice Companion backups folder beside your '
                  'Thunderbird profiles. Ask a helper to check Thunderbird.')
UP_TO_DATE = 'Thunderbird is up to date. It is version {version}, and it works with Microsoft 365. It keeps itself up to date.'
STORE = ('Thunderbird on this computer came from the Microsoft Store. Voice Companion cannot update that kind by itself. '
         'Ask a helper to install Thunderbird from thunderbird dot net.')
NOT_INSTALLED = 'Thunderbird is not on this computer. To install it, say add Thunderbird account.'
UNKNOWN = ('Voice Companion could not tell which version of Thunderbird is on this computer, so it did not change it. '
           'Ask a helper to check Help, then About Thunderbird.')

Installed = namedtuple('Installed', 'program folder version major channel store')

# Asked once in each Voice Companion session at most (offer()).
OFFERED = False


# ---- Which Thunderbird is installed --------------------------------------------

def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def installed(system, program=None, registry_version=''):
    """Installed(...) for thunderbird.exe, or None. Version from application.ini, else the registry;
    channel from defaults/pref/channel-prefs.js ("release", "esr"), else "esr" in the registry version."""
    if program is None:
        program, registry_version = setup.find_program(system)
    if not program:
        return None
    folder = Path(program).parent
    version = ''
    for line in _read(folder / 'application.ini').splitlines():
        if line.strip().lower().startswith('version='):
            version = line.split('=', 1)[1].strip()
            break
    if not version:
        found = re.match(r'\s*(\d+(?:\.\d+)*)', registry_version or '')
        version = found[1] if found else ''
    found = re.search(r'pref\(\s*"app\.update\.channel"\s*,\s*"([^"]+)"', _read(folder / 'defaults' / 'pref' / 'channel-prefs.js'))
    channel = found[1].lower() if found else ''
    if not channel and re.search(r'\desr\b', registry_version or '', re.I):
        channel = 'esr'
    store = 'windowsapps' in str(program).replace('/', '\\').lower()
    return Installed(str(program), folder, version, setup.major_version(version), channel, store)


def needs_release(info):
    """True when Thunderbird is ESR or older than 154 (and not the Store kind, which cannot be moved)."""
    if info is None or info.store or info.major is None:
        return False
    return info.major < RELEASE_MIN or info.channel == 'esr'


def is_current(info):
    return info is not None and info.major is not None and info.major >= RELEASE_MIN and info.channel != 'esr'


# ---- The accounts, before and after --------------------------------------------

def prefs_accounts(profile):
    """{account key: (server type, sorted lower-case addresses)} from the profile's prefs.js."""
    prefs = setup.read_prefs(Path(profile) / 'prefs.js') if profile else {}
    found = {}
    for key in str(prefs.get('mail.accountmanager.accounts', '')).split(','):
        key = key.strip()
        if not key:
            continue
        server = str(prefs.get('mail.account.' + key + '.server', '')).strip()
        kind = str(prefs.get('mail.server.' + server + '.type', '')).strip().lower()
        identities = [i.strip() for i in str(prefs.get('mail.account.' + key + '.identities', '')).split(',') if i.strip()]
        addresses = sorted(str(prefs.get('mail.identity.' + i + '.useremail', '')).strip().lower() for i in identities)
        found[key] = (kind, tuple(addresses))
    return found


def addon_accounts(found):
    """The same shape from the add-on's accounts answer (accounts with an address only)."""
    accounts = {}
    for account in found or []:
        if not isinstance(account, dict) or not account.get('id'):
            continue
        addresses = sorted(str(i.get('email', '')).strip().lower() for i in account.get('identities') or [] if isinstance(i, dict))
        accounts[str(account['id'])] = (str(account.get('type', '')).lower(), tuple(addresses))
    return accounts


def differences(before, after, what):
    """Plain notes on what is missing or changed. Accounts added since are not a problem."""
    notes = []
    for key, (kind, addresses) in sorted(before.items()):
        if key not in after:
            notes.append(what + ': ' + key + ' (' + (kind or 'unknown') + ') is missing')
        elif after[key] != (kind, addresses):
            notes.append(what + ': ' + key + ' changed from ' + repr((kind, addresses)) + ' to ' + repr(after[key]))
    return notes


def same_folder(one, two):
    def clean(path):
        return os.path.normcase(os.path.normpath(str(path).replace('/', os.sep).replace('\\', os.sep))).rstrip(os.sep)
    return bool(one) and bool(two) and clean(one) == clean(two)


# ---- Safety copy of the profile's key files --------------------------------------

def profile_root(system, profile):
    """The folder holding profiles.ini for this profile."""
    profile = Path(profile)
    for root in setup._profile_roots(system):
        try:
            profile.relative_to(root)
            return root
        except ValueError:
            continue
    return profile.parent.parent if profile.parent.name.lower() == 'profiles' else profile.parent


def _copy(source, target):
    """Copy through a temporary file, then replace: never half a file."""
    target = Path(target)
    temporary = target.with_name(target.name + '.voice-companion.tmp')
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    except BaseException:
        try: temporary.unlink()
        except OSError: pass
        raise


def backup(profile, root, stamp=None):
    """Copy the key files. Returns the backup folder. Its backup.json is written last, so a
    backup that did not finish is never used."""
    stamp = stamp or time.strftime('%Y%m%d-%H%M%S')
    folder = Path(root) / BACKUP_FOLDER / ('before-update-' + stamp)
    (folder / 'profile').mkdir(parents=True, exist_ok=True)
    (folder / 'root').mkdir(exist_ok=True)
    files = {}
    for kind, base, names in (('profile', Path(profile), PROFILE_FILES), ('root', Path(root), ROOT_FILES)):
        for name in names:
            source = base / name
            if source.is_file():
                _copy(source, folder / kind / name)
                files[kind + '/' + name] = True
            else:
                files[kind + '/' + name] = False
    manifest = json.dumps({'profile': str(profile), 'root': str(root), 'files': files}, indent=2).encode('utf-8')
    setup.write_atomic(folder / 'backup.json', manifest)
    _prune(Path(root) / BACKUP_FOLDER)
    return folder


def _prune(parent):
    try:
        found = sorted((p for p in parent.iterdir() if p.is_dir() and (p / 'backup.json').is_file()), key=lambda p: p.name)
    except OSError:
        return
    for old in found[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


def restore(folder):
    """Put the backed-up files back. A file that did not exist before is moved aside, never deleted."""
    folder = Path(folder)
    data = json.loads((folder / 'backup.json').read_text(encoding='utf-8'))
    places = {'profile': Path(data['profile']), 'root': Path(data['root'])}
    for name, existed in data['files'].items():
        kind, _, file_name = name.partition('/')
        target = places[kind] / file_name
        if existed:
            _copy(folder / kind / file_name, target)
        elif target.exists():
            os.replace(target, target.with_name(file_name + '.voice-companion-moved'))


# ---- Remembering the question and an unfinished restore -----------------------------

def _memory(data_folder):
    try:
        found = json.loads((Path(data_folder) / OFFER_FILE).read_text(encoding='utf-8'))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(data_folder, **changes):
    data = _memory(data_folder)
    data.update(changes)
    data = {key: value for key, value in data.items() if value is not None}
    try:
        Path(data_folder).mkdir(parents=True, exist_ok=True)
        setup.write_atomic(Path(data_folder) / OFFER_FILE, json.dumps(data).encode('utf-8'))
    except OSError:
        pass   # only means the question may come again sooner


def offer_due(system, data_folder, now=None):
    """True when the automatic question should be asked: not yet this session, not said no to lately."""
    if OFFERED:
        return False
    declined = _memory(data_folder).get('declined')
    now = time.time() if now is None else now
    if isinstance(declined, (int, float)) and 0 <= now - declined < OFFER_DAYS * 24 * 3600:
        return False
    try:
        return needs_release(installed(system))
    except OSError:
        return False


# ---- Installing the Release ------------------------------------------------------

def install_release(system, folder, progress, report=None):
    """Mozilla's signed Release installer, run into the folder Thunderbird is in now. True when it ran.
    report, when given, gets why it did not (a failure names itself)."""
    report = {} if report is None else report
    temporary = Path(tempfile.mkdtemp(prefix='voice-companion-thunderbird-'))
    installer = temporary / 'Thunderbird Setup.exe'
    try:
        try:
            system.download(RELEASE_URL, installer)
        except OSError as exc:
            report['stopped'] = 'download: ' + type(exc).__name__ + ': ' + str(exc)[:200]
            return False
        if not setup.signed_by_mozilla(system, installer):
            status, subject = getattr(system, 'last_signature', ('', ''))
            report['stopped'] = ('signature: ' + str(status) + ' ' + str(subject) + ' ' +
                                 str(getattr(system, 'signer_problem', ''))).strip()[:700]
            return False
        target = str(folder)
        if '"' in target:
            report['stopped'] = 'folder name'
            return False
        # One command line, so the folder (with spaces) reaches the installer as one quoted value.
        command = '"' + str(installer) + '" /S /InstallDirectoryPath="' + target.rstrip('\\/') + '"'
        code = setup._run(system, command, setup.INSTALL_SECONDS, progress, message=STILL_UPDATING)
        if code != 0:
            report['stopped'] = 'installer: ' + ('took too long' if code is None else 'exit code ' + str(code))
        return code == 0
    finally:
        for item in (installer, temporary):
            try: item.unlink() if item.is_file() else item.rmdir()
            except OSError: pass


# ---- The spoken flow ---------------------------------------------------------------

class Upgrade:
    """check() for "update Thunderbird", offer() for the automatic question; answer() after yes or no.

    started(relay) is called once the updated Thunderbird is connected (Voice Companion then puts its
    window where the person chose)."""

    def __init__(self, system, package, relay, progress, data_folder, started=None, clock=time.time, hint=''):
        self.system, self.package, self.relay, self.progress = system, Path(package), relay, progress
        self.data_folder, self.started, self.clock = Path(data_folder), started, clock
        self.hint = hint     # said after "no" and after a finished update (the automatic question: what to do next)
        self.info = None
        self.report = {}    # what was checked, for tests and the error log (never addresses)

    # -- asking

    def check(self):
        global OFFERED
        OFFERED = True
        waiting = _memory(self.data_folder).get('restore')
        if waiting and Path(waiting, 'backup.json').is_file():
            return self._finish_restore(Path(waiting))
        self.info = installed(self.system)
        if self.info is None:
            return Step(NOT_INSTALLED, None)
        if self.info.store:
            return Step(STORE, None)
        if self.info.major is None:
            return Step(UNKNOWN, None)
        if not needs_release(self.info):
            return Step(UP_TO_DATE.format(version=self.info.version), None)
        return Step(QUESTION, 'upgrade')

    def offer(self):
        """The question, or None when it is not due."""
        if not offer_due(self.system, self.data_folder, self.clock()):
            return None
        step = self.check()
        return step if step.question == 'upgrade' else None

    def answer(self, question, yes):
        if question != 'upgrade':
            return Step('', None)
        if not yes:
            _remember(self.data_folder, declined=self.clock())
            return Step((NOT_NOW + ' ' + self.hint).strip(), None)
        _remember(self.data_folder, declined=None)
        if self.info is None:
            self.info = installed(self.system)
        if not needs_release(self.info):
            return self.check()
        return self.run()

    # -- talking to Thunderbird

    def _relay(self):
        try:
            return self.relay()
        except OSError:
            return None

    def _connected(self):
        relay = self._relay()
        return relay is not None and relay.connected.is_set()

    def _ask(self, method, params=None, timeout=30):
        relay = self._relay()
        if relay is None or not relay.connected.is_set():
            return None
        try:
            return relay.request(method, params or {}, timeout=timeout)
        except Exception:
            return None

    def _wait(self, check, seconds, every=None, message=''):
        """Wait until check() is true, saying message every `every` seconds. True when it came true."""
        started = last = self.system.clock()
        while not check():
            now = self.system.clock()
            if now - started >= seconds:
                return False
            if every and message and now - last >= every:
                self.progress(message); last = now
            self.system.sleep(1)
        return True

    def _snapshot(self):
        """What Thunderbird itself says: profile, version, composing, accounts and which Inboxes open."""
        about = self._ask('about') or {}
        found = self._ask('accounts')
        accounts = addon_accounts(found) if isinstance(found, list) else None
        inboxes = {}
        for key, (kind, _) in (accounts or {}).items():
            if kind in MAIL_TYPES:
                answer = self._ask('inbox', {'accountId': key})
                inboxes[key] = isinstance(answer, dict) and bool(answer.get('id'))
        return {'about': about if isinstance(about, dict) else {}, 'accounts': accounts, 'inboxes': inboxes}

    def _close(self):
        """Close Thunderbird the normal way. 'closed', 'composing' or 'open'."""
        if not self.system.running():
            return 'closed'
        self.progress(CLOSING)
        if self._connected():
            answer = self._ask('quit') or {}
            if answer.get('reason') == 'composing':
                return 'composing'
            if not answer.get('quit'):
                self.system.close_program()
        else:
            self.system.close_program()
        if not self._wait(lambda: not self.system.running(), setup.CLOSE_SECONDS):
            return 'open'
        # The add-on's connection ends a moment after Thunderbird does.
        self._wait(lambda: not self._connected(), DISCONNECT_SECONDS)
        return 'closed'

    def _start(self, profile):
        """Start Thunderbird in the background and wait for the add-on. True when it connected."""
        if profile is not None:
            setup.refresh_addon(profile, self.package)
        try:
            self.system.launch_background(self.info.program)
        except OSError:
            return False
        return self._wait(self._connected, setup.WAIT_SECONDS, setup.PROGRESS_SECONDS, setup.STILL_STARTING)

    # -- the update itself

    def run(self):
        info = self.info
        profile, _ = setup.choose_profile(self.system)
        before = {'prefs': prefs_accounts(profile) if profile else {}}
        launched_for_check = False
        was_open = self.system.running()
        if profile is not None and not self._connected() and not self.system.running() and \
                setup.addon_state(profile) in ('active', 'waiting'):
            # Started quietly first, so its accounts and Inboxes can be compared afterwards.
            self.progress(GETTING_READY)
            launched_for_check = self._start(profile)
        if self._connected():
            seen = self._snapshot()
            if seen['about'].get('composing'):
                return Step(COMPOSING, None)
            running_profile = seen['about'].get('profile')
            if running_profile and Path(running_profile).is_dir() and not same_folder(running_profile, profile or ''):
                profile = Path(running_profile)   # the profile Thunderbird really uses
                before['prefs'] = prefs_accounts(profile)
            before.update(addon=seen['accounts'], inboxes=seen['inboxes'])
        self.report['before'] = {'accounts': len(before['prefs']), 'checked_in_thunderbird': before.get('addon') is not None,
                                 'started_for_check': launched_for_check}
        with setup._START_LOCK:
            closed = self._close()
            if closed == 'composing':
                return Step(COMPOSING, None)
            if closed == 'open':
                return Step(NOT_CLOSED, None)
            saved = None
            if profile is not None:
                try:
                    saved = backup(profile, profile_root(self.system, profile))
                except OSError:
                    if was_open or launched_for_check:
                        self._start(profile)
                    return Step(BACKUP_FAILED, None)
            self.progress(UPDATING)
            self.report['install'] = {}
            ran = install_release(self.system, info.folder, self.progress, self.report['install'])
            after = installed(self.system, info.program)
            if ran and after is not None and is_current(after):
                latest = _latest(self.system)
                if latest and setup.version_tuple(after.version) < setup.version_tuple(latest):
                    # Mozilla's download was a step behind: try once more, else its own updater finishes.
                    install_release(self.system, info.folder, self.progress)
                    after = installed(self.system, info.program) or after
            self.report['after_install'] = {'ran': ran, 'version': after.version if after else '',
                                            'channel': after.channel if after else ''}
            if not ran or after is None or not is_current(after) or \
                    setup.version_tuple(after.version) < setup.version_tuple(info.version):
                if was_open or launched_for_check:
                    self._start(profile)    # the old Thunderbird, as it was
                return Step(FAILED, None)
            self.info = after
        self.progress(CHECKING)
        connected = self._start(profile)
        problems = self._verify(profile, before, connected)
        self.report['problems'] = problems
        if problems:
            return self._restore(saved, profile)
        if connected and self.started is not None:
            try:
                self.started(self._relay())
            except Exception:
                pass
        if connected:
            return Step((DONE.format(version=after.version) + ' ' + self.hint).strip(), None)
        return Step(NOT_CONNECTED.format(version=after.version), None)

    def _verify(self, profile, before, connected):
        problems = []
        if profile is None:
            return problems
        problems += differences(before['prefs'], prefs_accounts(profile), 'settings')
        if not connected:
            # Thunderbird holds parent.lock in the profile it opened.
            if not (Path(profile) / 'parent.lock').exists():
                problems.append('profile: Thunderbird did not open the same profile')
            return problems
        seen = self._snapshot()
        opened = seen['about'].get('profile')
        if opened and not same_folder(opened, profile):
            problems.append('profile: Thunderbird opened a different profile')
        if not opened and not (Path(profile) / 'parent.lock').exists():
            problems.append('profile: Thunderbird did not open the same profile')
        if seen['accounts'] is None:
            problems.append('accounts: Thunderbird did not list its accounts')
            return problems
        expected = before.get('addon')
        if expected is None:
            # Not seen in Thunderbird before: every account with an address in prefs.js must be listed.
            expected = {key: value for key, value in before['prefs'].items() if value[1]}
        problems += differences(expected, seen['accounts'], 'accounts')
        wanted = before.get('inboxes')
        if wanted is None:
            wanted = {key: True for key, (kind, _) in expected.items() if kind in MAIL_TYPES}
        for key, worked in sorted(wanted.items()):
            if worked and not seen['inboxes'].get(key) and not self._inbox_opens(key):
                problems.append('inbox: ' + key + ' did not open')
        return problems

    def _inbox_opens(self, key, tries=INBOX_TRIES):
        """Just after it starts, Thunderbird may still be loading an account's folders: ask a few times."""
        for attempt in range(tries):
            answer = self._ask('inbox', {'accountId': key})
            if isinstance(answer, dict) and answer.get('id'):
                return True
            if attempt + 1 < tries:
                self.system.sleep(INBOX_WAIT_SECONDS)
        return False

    def _restore(self, saved, profile):
        if saved is None:
            return Step(RESTORE_FAILED, None)
        if self._close() != 'closed':
            _remember(self.data_folder, restore=str(saved))
            return Step(RESTORE_WAITING, None)
        try:
            restore(saved)
        except (OSError, ValueError, KeyError):
            return Step(RESTORE_FAILED, None)
        _remember(self.data_folder, restore=None)
        self._start(profile)
        return Step(RESTORED, None)

    def _finish_restore(self, saved):
        """An earlier update left settings to put back, because Thunderbird would not close."""
        if self.system.running() and self._close() != 'closed':
            return Step(RESTORE_WAITING, None)
        try:
            restore(saved)
        except (OSError, ValueError, KeyError):
            return Step(RESTORE_FAILED, None)
        _remember(self.data_folder, restore=None)
        return Step(RESTORED, None)


def _latest(system):
    try:
        return system.latest_release()
    except (AttributeError, OSError, ValueError):
        return None
