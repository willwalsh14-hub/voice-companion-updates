"""Thunderbird account setup explains itself and installs its own connection.

Before, "Add account, Thunderbird" only asked the add-on for accounts. When
Thunderbird was missing, closed, or had no add-on, the user heard "Account setup
did not finish. Thunderbird is not connected." and nothing told them why or what
to do. Now each missing piece is found in order and the next step is spoken.

Windows is faked at its edge: the registry, the process list, starting a program
and opening a website. Thunderbird's folders are real files in a temp folder.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import thunderbird_setup
from mail_accounts import AccountError
from thunderbird_bridge import ADDON_ID
from thunderbird_setup import Setup

INSTALLED = 'C:/Program Files/Mozilla Thunderbird/thunderbird.exe'
ACCOUNT_PREFS = ('user_pref("mail.accountmanager.accounts", "account1,account2");\n'
                 'user_pref("mail.account.account1.identities", "id1");\n'
                 'user_pref("mail.account.account1.server", "server1");\n'
                 'user_pref("mail.account.account2.server", "server2");\n')
LOCAL_ONLY_PREFS = ('user_pref("mail.accountmanager.accounts", "account1");\n'
                    'user_pref("mail.account.account1.server", "server1");\n')


class FakeSystem:
    """The Windows edge: registry values, running programs, launching, websites."""

    def __init__(self, home, registry=None, files=(), running=False):
        self.environ = {'APPDATA': str(home / 'Roaming'), 'LOCALAPPDATA': str(home / 'Local'),
                        'ProgramFiles': str(home / 'Program Files'), 'ProgramFiles(x86)': str(home / 'Program Files (x86)')}
        self.values = dict(registry or {})
        self.files = set(files)
        self.is_running = running
        self.launched, self.opened = [], []

    def registry(self, hive, key, name=''):
        return self.values.get((hive, key, name))

    def exists(self, path):
        return str(path).replace('\\', '/') in self.files or Path(path).is_file()

    def running(self):
        return self.is_running

    def launch(self, path):
        self.launched.append(str(path)); self.is_running = True

    def open_site(self, url):
        self.opened.append(url)

    # Installing and quiet starts, as Windows would do them. Time is simulated.
    winget = True
    install_works = True
    signature = ('Valid', 'CN=Mozilla Corporation, O=Mozilla Corporation, L=San Francisco, S=California, C=US')
    now = 0.0
    install_polls = 3          # the installer finishes after this many checks
    profile_after_launch = None    # a function that makes Thunderbird's profile on its first start

    def has_winget(self):
        return self.winget

    def start_process(self, command):
        self.__dict__.setdefault('commands', []).append(list(command))
        return FakeProcess(self)

    def finish_install(self):
        if self.install_works:
            self.values.update(registered()); self.files.add(INSTALLED)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def download(self, url, path):
        self.__dict__.setdefault('downloads', []).append(url)
        Path(path).write_bytes(b'MZ installer')

    def signer(self, path):
        self.__dict__.setdefault('checked', []).append(Path(path).name)
        return self.signature

    def launch_background(self, path):
        self.__dict__.setdefault('background', []).append(str(path)); self.is_running = True
        if self.profile_after_launch:
            make, self.profile_after_launch = self.profile_after_launch, None
            make()

    def close_program(self):
        self.__dict__['closes'] = self.__dict__.get('closes', 0) + 1; self.is_running = False


class FakeProcess:
    def __init__(self, system):
        self.system, self.polls, self.returncode, self.killed = system, 0, None, False

    def poll(self):
        self.polls += 1
        if self.polls > self.system.install_polls:
            self.returncode = 0 if self.system.install_works else 1
            self.system.finish_install()
        return self.returncode

    def kill(self):
        self.killed = True


class FakeConnected(threading.Event):
    """MailRelay.connected: an Event. Waiting returns at once instead of sleeping."""

    def __init__(self, connect_after):
        super().__init__()
        self.waits = []
        self.connect_after = connect_after   # number of waits before the add-on connects

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.connect_after is not None and len(self.waits) > self.connect_after:
            self.set()
        return self.is_set()


class FakeRelay:
    def __init__(self, connect_after=None):
        self.connected = FakeConnected(connect_after)

    @property
    def waits(self):
        return self.connected.waits


def registered(version='128.3.0esr (x64 en-US)', path=INSTALLED):
    key = r'Software\Mozilla\Mozilla Thunderbird'
    return {('HKLM', key, 'CurrentVersion'): version, ('HKLM', key + '\\' + version + r'\Main', 'PathToExe'): path}


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.package = self.home / 'Voice Companion Mail Connection.xpi'
        self.package.write_bytes(b'PK add-on version 1')
        self.progress, self.connects = [], []

    def profile(self, prefs=ACCOUNT_PREFS, name='abc.default-release', ini=None):
        root = self.home / 'Roaming' / 'Thunderbird'
        folder = root / 'Profiles' / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'prefs.js').write_text('// Mozilla User Preferences\n' + prefs, encoding='utf-8')
        (root / 'profiles.ini').write_text(ini or (
            '[Install8216C80C92C4E828]\nDefault=Profiles/' + name + '\nLocked=1\n\n'
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/' + name + '\n\n'
            '[General]\nStartWithLastProfile=1\nVersion=2\n'), encoding='utf-8')
        return folder

    def add_on(self, folder, **entry):
        (folder / 'extensions').mkdir(exist_ok=True)
        (folder / 'extensions' / (ADDON_ID + '.xpi')).write_bytes(self.package.read_bytes())
        if entry:
            (folder / 'extensions.json').write_text(json.dumps({'schemaVersion': 36, 'addons': [
                {'id': 'other@example.com', 'active': True}, dict({'id': ADDON_ID, 'location': 'app-profile'}, **entry)]}),
                encoding='utf-8')

    def setup(self, system, relay=None, connect=None):
        def connected_address():
            self.connects.append(True)
            if connect: return connect()
            return 'me@gmail.com'
        return Setup(system, self.package, connected_address, lambda: relay, self.progress.append)


class FindingThunderbirdTests(SetupCase):
    def test_not_installed_offers_to_install_it_and_installs_only_on_yes(self):
        # Before 0.2.160 this opened thunderbird dot net and left the install to a helper.
        system = FakeSystem(self.home)
        flow = self.setup(system)
        step = flow.start()
        self.assertEqual(step.question, 'install_program')
        self.assertIn('Thunderbird is not on this computer', step.message)
        self.assertIn('free email program', step.message)
        self.assertIn('install it for you', step.message)
        self.assertIn('Say yes or no', step.message)
        self.assertNotIn('did not finish', step.message)
        self.assertEqual(system.__dict__.get('commands'), None)     # nothing runs by itself
        step = flow.answer('install_program', False)
        self.assertIn('Nothing was installed', step.message)
        self.assertIsNone(step.question)
        self.assertEqual(system.__dict__.get('commands'), None)

    def test_no_to_download_opens_nothing(self):
        system = FakeSystem(self.home)
        flow = self.setup(system)
        flow.start()
        step = flow.answer('download', False)
        self.assertEqual(system.opened, [])
        self.assertIn('Nothing was opened', step.message)

    def test_found_by_registry_app_paths_mail_client_or_folder(self):
        app_paths = r'Software\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe'
        client = r'Software\Clients\Mail\Mozilla Thunderbird\shell\open\command'
        program_files = str(self.home / 'Program Files' / 'Mozilla Thunderbird' / 'thunderbird.exe')
        cases = {
            'version key': (registered(), {INSTALLED}, INSTALLED),
            'per-user version key': ({(('HKCU',) + k[1:]): v for k, v in registered().items()}, {INSTALLED}, INSTALLED),
            'app paths': ({('HKCU', app_paths, ''): INSTALLED}, {INSTALLED}, INSTALLED),
            'mail client': ({('HKLM', client, ''): '"' + INSTALLED + '" -mail'}, {INSTALLED}, INSTALLED),
            'program files': ({}, {program_files.replace('\\', '/')}, program_files),
        }
        for name, (registry, files, expected) in cases.items():
            with self.subTest(name):
                found, _ = thunderbird_setup.find_program(FakeSystem(self.home, registry, files))
                self.assertEqual(str(found).replace('\\', '/'), expected.replace('\\', '/'))

    def test_leftover_registry_entry_without_the_program_is_not_installed(self):
        found, _ = thunderbird_setup.find_program(FakeSystem(self.home, registered()))
        self.assertIsNone(found)

    def test_old_thunderbird_must_be_updated(self):
        system = FakeSystem(self.home, registered('115.16.0esr (x64 en-US)'), {INSTALLED})
        self.profile()
        step = self.setup(system).start()
        self.assertIsNone(step.question)
        self.assertIn('too old', step.message)
        self.assertIn('128', step.message)


class AccountAndProfileTests(SetupCase):
    def test_installed_never_opened_offers_to_add_an_account(self):
        # Before 0.2.160 a helper had to add the account in Thunderbird; now Voice Companion offers to.
        system = FakeSystem(self.home, registered(), {INSTALLED})
        step = self.setup(system).start()
        self.assertEqual(step.question, 'add_first')
        self.assertIn('no email account is set up in it yet', step.message)
        self.assertIn('Add an email account to Thunderbird? Say yes or no.', step.message)
        self.assertEqual(system.launched, [])

    def test_local_folders_alone_is_not_an_email_account(self):
        self.profile(LOCAL_ONLY_PREFS)
        system = FakeSystem(self.home, registered(), {INSTALLED}, running=True)
        step = self.setup(system).start()
        self.assertEqual(step.question, 'add_first')   # Voice Companion offers to add one
        self.assertIn('no email account is set up in it yet', step.message)

    def test_profile_with_an_account_is_preferred_and_absolute_paths_work(self):
        empty = self.profile(LOCAL_ONLY_PREFS, 'empty.default')
        real = self.home / 'Elsewhere' / 'mail'
        real.mkdir(parents=True)
        (real / 'prefs.js').write_text(ACCOUNT_PREFS, encoding='utf-8')
        (self.home / 'Roaming' / 'Thunderbird' / 'profiles.ini').write_text(
            '[Profile1]\nName=old\nIsRelative=1\nPath=Profiles/empty.default\nDefault=1\n\n'
            '[Profile0]\nName=mail\nIsRelative=0\nPath=' + str(real) + '\n', encoding='utf-8')
        profile, has_account = thunderbird_setup.choose_profile(FakeSystem(self.home))
        self.assertEqual((profile, has_account), (real, True))
        self.assertTrue(empty.is_dir())

    def test_damaged_profiles_file_or_prefs_does_not_stop_setup(self):
        root = self.home / 'Roaming' / 'Thunderbird'; root.mkdir(parents=True)
        (root / 'profiles.ini').write_bytes(b'\xff\xfe[[[not an ini')
        system = FakeSystem(self.home, registered(), {INSTALLED})
        self.assertEqual(self.setup(system).start().question, 'add_first')
        folder = self.profile('user_pref("mail.accountmanager.accounts", ')
        self.assertFalse(thunderbird_setup.has_email_account(folder))


class AddOnTests(SetupCase):
    def test_missing_add_on_is_installed_after_yes_then_thunderbird_starts(self):
        folder = self.profile()
        (folder / 'user.js').write_text('user_pref("browser.display.use_document_fonts", 0);\n', encoding='utf-8')
        system = FakeSystem(self.home, registered(), {INSTALLED})
        relay = FakeRelay(connect_after=1)
        flow = self.setup(system, relay)
        step = flow.start()
        self.assertEqual(step.question, 'install')
        self.assertIn('Install the Voice Companion connection in Thunderbird now? Say yes or no.', step.message)
        self.assertFalse((folder / 'extensions').exists())     # nothing changes before yes
        step = flow.answer('install', True)
        self.assertEqual((folder / 'extensions' / (ADDON_ID + '.xpi')).read_bytes(), self.package.read_bytes())
        user = (folder / 'user.js').read_text(encoding='utf-8')
        self.assertIn('user_pref("browser.display.use_document_fonts", 0);', user)   # the helper's settings stay
        self.assertIn('user_pref("extensions.autoDisableScopes", 14);', user)
        self.assertIn('user_pref("extensions.startupScanScopes", 1);', user)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['extensions', 'prefs.js', 'user.js'])  # no temp files left
        self.assertEqual(step.question, 'start')
        self.assertTrue(step.message.startswith('The Voice Companion connection is installed.'))
        step = flow.answer('start', True)
        self.assertEqual(system.launched, [INSTALLED])
        self.assertEqual(self.progress[0], 'Starting Thunderbird.')
        self.assertEqual(step.message, 'Thunderbird account me@gmail.com is connected and selected. Say check email to open it.')

    def test_existing_scope_settings_are_adjusted_not_duplicated(self):
        folder = self.profile()
        (folder / 'user.js').write_text('user_pref("extensions.autoDisableScopes", 11);\nuser_pref("extensions.startupScanScopes", 4);\n',
                                        encoding='utf-8')
        thunderbird_setup.install_addon(folder, self.package)
        user = (folder / 'user.js').read_text(encoding='utf-8')
        self.assertEqual(user.count('autoDisableScopes'), 1)
        self.assertIn('user_pref("extensions.autoDisableScopes", 10);', user)
        self.assertIn('user_pref("extensions.startupScanScopes", 5);', user)

    def test_install_while_thunderbird_is_open_asks_for_a_restart(self):
        folder = self.profile()
        system = FakeSystem(self.home, registered(), {INSTALLED}, running=True)
        flow = self.setup(system, FakeRelay())
        self.assertEqual(flow.start().question, 'install')
        step = flow.answer('install', True)
        self.assertIsNone(step.question)
        self.assertIn('Close Thunderbird, then say add account', step.message)
        self.assertTrue((folder / 'extensions' / (ADDON_ID + '.xpi')).is_file())
        # Thunderbird has not loaded it yet: the next try asks for the restart again.
        self.assertIn('must restart', self.setup(system, FakeRelay()).start().message)

    def test_no_to_install_changes_nothing(self):
        folder = self.profile()
        system = FakeSystem(self.home, registered(), {INSTALLED})
        flow = self.setup(system)
        flow.start()
        step = flow.answer('install', False)
        self.assertFalse((folder / 'extensions').exists())
        self.assertFalse((folder / 'user.js').exists())
        self.assertIn('Email Setup Helper, section 3', step.message)

    def test_install_failure_is_explained(self):
        folder = self.profile()
        system = FakeSystem(self.home, registered(), {INSTALLED})
        flow = self.setup(system)
        flow.start()
        with mock.patch.object(thunderbird_setup.os, 'replace', side_effect=PermissionError('locked')):
            step = flow.answer('install', True)
        self.assertIn('could not be installed', step.message)
        self.assertEqual(list(folder.glob('*.tmp')) + list(folder.glob('extensions/*')), [])

    def test_turned_off_or_blocked_add_on_is_explained(self):
        folder = self.profile()
        system = FakeSystem(self.home, registered(), {INSTALLED})
        self.add_on(folder, active=False, userDisabled=True)
        step = self.setup(system).start()
        self.assertIn('turned off in Thunderbird', step.message)
        self.assertIn('Add-ons and Themes', step.message)
        self.add_on(folder, active=False, userDisabled=False, appDisabled=True)
        self.assertIn('update Thunderbird', self.setup(system).start().message)

    def test_newer_add_on_replaces_the_old_copy_while_thunderbird_is_closed(self):
        folder = self.profile()
        self.add_on(folder, active=True)
        self.package.write_bytes(b'PK add-on version 2')
        system = FakeSystem(self.home, registered(), {INSTALLED})
        self.assertEqual(self.setup(system).start().question, 'start')
        self.assertEqual((folder / 'extensions' / (ADDON_ID + '.xpi')).read_bytes(), b'PK add-on version 2')


class StartingAndConnectingTests(SetupCase):
    def ready(self, running=False):
        folder = self.profile()
        self.add_on(folder, active=True)
        return FakeSystem(self.home, registered(), {INSTALLED}, running=running)

    def test_closed_thunderbird_is_started_only_on_yes(self):
        system = self.ready()
        flow = self.setup(system, FakeRelay(connect_after=0))
        step = flow.start()
        self.assertEqual(step.message, 'Thunderbird is not open. Start Thunderbird now? Say yes or no.')
        self.assertEqual(system.launched, [])
        step = flow.answer('start', False)
        self.assertEqual(system.launched, [])
        self.assertIn('Thunderbird was not started', step.message)

    def test_waits_a_minute_with_progress_every_15_seconds(self):
        system = self.ready()
        relay = FakeRelay()
        flow = self.setup(system, relay)
        flow.start()
        step = flow.answer('start', True)
        self.assertEqual(relay.waits, [15, 15, 15, 15])
        self.assertEqual(self.progress, ['Starting Thunderbird.'] + ['Still waiting for Thunderbird to connect.'] * 3)
        self.assertIn('did not connect after one minute', step.message)
        self.assertEqual(self.connects, [])

    def test_open_thunderbird_connects_without_questions(self):
        system = self.ready(running=True)
        relay = FakeRelay(connect_after=0)
        step = self.setup(system, relay).start()
        self.assertEqual(self.progress, ['Thunderbird is open. Waiting for it to connect.'])
        self.assertIn('is connected and selected', step.message)

    def test_already_connected_skips_every_check(self):
        relay = FakeRelay(); relay.connected.set()
        system = FakeSystem(self.home)   # even when the program is somewhere unusual
        step = self.setup(system, relay).start()
        self.assertIn('me@gmail.com is connected', step.message)

    def test_connected_without_email_accounts(self):
        system = self.ready(running=True)
        relay = FakeRelay(); relay.connected.set()
        def no_accounts():
            raise AccountError('Thunderbird is connected, but it has no email accounts yet.')
        step = self.setup(system, relay, no_accounts).start()
        self.assertIn('has no email accounts yet', step.message)

    def test_start_failure_is_explained(self):
        system = self.ready()
        system.launch = mock.Mock(side_effect=OSError('missing'))
        flow = self.setup(system, FakeRelay())
        flow.start()
        self.assertIn('Thunderbird could not start', flow.answer('start', True).message)


class SpokenWordingTests(unittest.TestCase):
    def test_no_setup_message_mentions_sign_in(self):
        for name in dir(thunderbird_setup):
            value = getattr(thunderbird_setup, name)
            if name.isupper() and isinstance(value, str) and ' ' in value:
                self.assertNotIn('sign-in', value.lower(), name)
                self.assertNotIn('did not finish', value.lower(), name)

    def test_no_accounts_message_from_the_add_on(self):
        import mail_accounts
        with mock.patch('thunderbird_mailbox.accounts', return_value=[{'id': 'account1', 'identities': []}]):
            with self.assertRaises(AccountError) as raised:
                mail_accounts.thunderbird_connect(object())
        self.assertIn('Thunderbird is connected, but it has no email accounts yet', str(raised.exception))


class CompanionTests(unittest.TestCase):
    """The spoken flow through the app's own command handling."""

    def run_commands(self, flow, *commands):
        import companion
        spoken = []
        with mock.patch.object(companion, 'new_thunderbird_setup', lambda adding=False: flow), \
             mock.patch.object(companion, 'speak', spoken.append), mock.patch.object(companion, 'speak_status', spoken.append), \
             mock.patch.object(companion, 'TEXT_MODE', True), mock.patch.object(companion, 'APP_WINDOW', None), \
             mock.patch.object(companion, 'APP', Path(tempfile.mkdtemp())), \
             mock.patch.object(companion, 'account_setup', False), mock.patch.object(companion, 'THUNDERBIRD_SETUP', None):
            for command in commands:
                spoken.append('>' + command)
                companion.handle(command, 'awake')
            return spoken, companion.THUNDERBIRD_SETUP

    def flow(self, home):
        system = FakeSystem(home)
        return system, Setup(system, home / 'x.xpi', lambda: 'me@gmail.com', lambda: None, lambda text: None)

    def test_add_account_thunderbird_explains_instead_of_sign_in_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            system, flow = self.flow(Path(temp))
            spoken, pending = self.run_commands(flow, 'add account', 'Thunderbird.', 'Yes, please.')
        joined = ' '.join(spoken[spoken.index('>Thunderbird.'):])   # the type menu before it names other sign-ins
        self.assertNotIn('did not finish', joined)
        self.assertNotIn('sign-in', joined)
        self.assertIn('Thunderbird is not on this computer', joined)
        self.assertEqual(system.commands[0][:3], ['winget', 'install', '--id'])   # one yes installs it
        self.assertIsNone(pending)

    def test_any_other_answer_is_no_and_returns_to_normal(self):
        with tempfile.TemporaryDirectory() as temp:
            system, flow = self.flow(Path(temp))
            spoken, pending = self.run_commands(flow, 'connect thunderbird', 'check email')
        self.assertEqual(system.__dict__.get('commands'), None)
        self.assertIn('Nothing was installed', ' '.join(spoken))
        self.assertIsNone(pending)

    def test_repeat_keeps_the_question(self):
        with tempfile.TemporaryDirectory() as temp:
            system, flow = self.flow(Path(temp))
            spoken, pending = self.run_commands(flow, 'connect thunderbird', 'repeat')
        self.assertEqual(sum('Say yes or no' in line for line in spoken), 2)
        self.assertEqual(pending['question'], 'install_program')

    def test_keyboard_answers_the_question(self):
        import companion
        with mock.patch.object(companion, 'THUNDERBIRD_SETUP', {'question': 'start'}), \
             mock.patch.object(companion, 'speak', lambda text: None), mock.patch.object(companion, 'CONFIRM_CHOICE', 'yes'):
            self.assertEqual(companion.navigation_key('Y', 'awake'), 'yes')
            self.assertEqual(companion.navigation_key('Escape', 'awake'), 'no')
            self.assertEqual(companion.navigation_key('Enter', 'awake'), 'yes')
            self.assertEqual(companion.app_context('awake').get('confirmation'), 'yes')


if __name__ == '__main__':
    unittest.main()
