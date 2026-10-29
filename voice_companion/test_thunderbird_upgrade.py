"""Moving Thunderbird from ESR to the current Release (154 or newer) with one yes.

Will, October 2026: Thunderbird 154 added Microsoft Graph for Microsoft 365; his
Thunderbird is ESR, which does not have it. Voice Companion should update it with
no tech work, and never disturb accounts that work today.

Windows is faked at its edge (registry, processes, download, signature, installer).
Thunderbird's program folder and profile are real files in a temp folder. The fake
Thunderbird behaves like the real one where it matters here: it opens the profile
profiles.ini names for its install folder, lists accounts from prefs.js, asks before
quitting while a message is being written, and its add-on connects when it starts.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import thunderbird_setup
import thunderbird_upgrade as upgrade
from test_thunderbird_setup import FakeSystem, registered
from thunderbird_bridge import ADDON_ID, ThunderbirdError

PREFS = '\n'.join([
    '// Mozilla User Preferences',
    'user_pref("mail.accountmanager.accounts", "account1,account2,account3");',
    'user_pref("mail.account.account1.identities", "id1");',
    'user_pref("mail.account.account1.server", "server1");',
    'user_pref("mail.account.account2.identities", "id2");',
    'user_pref("mail.account.account2.server", "server2");',
    'user_pref("mail.account.account3.server", "server3");',
    'user_pref("mail.identity.id1.useremail", "will@gmail.com");',
    'user_pref("mail.identity.id2.useremail", "will@work.example");',
    'user_pref("mail.server.server1.type", "imap");',
    'user_pref("mail.server.server1.hostname", "imap.gmail.com");',
    'user_pref("mail.server.server1.authMethod", 10);',
    'user_pref("mail.server.server2.type", "ews");',
    'user_pref("mail.server.server3.type", "none");',
    ''])
PROFILES_INI = ('[Install8216C80C92C4E828]\nDefault=Profiles/abc.default-release\nLocked=1\n\n'
                '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abc.default-release\nDefault=1\n\n'
                '[General]\nStartWithLastProfile=1\nVersion=2\n')
INSTALLS_INI = '[8216C80C92C4E828]\nDefault=Profiles/abc.default-release\nLocked=1\n'


class FakeThunderbird:
    """The running program, its profile choice, and its add-on's answers (thunderbird_bridge.MailRelay)."""

    def __init__(self, case):
        self.case = case
        self.connected = threading.Event()
        self.running = False
        self.profile = None
        self.composing = 0
        self.refuses_to_close = False
        self.new_profile_after_update = False     # like a Thunderbird that did not find its profile
        self.drops_account = None                 # an account Thunderbird loses after the update
        self.broken_inbox = None                  # an account whose Inbox no longer opens
        self.addon = True
        self.requests = []
        self.starts = 0

    def version(self):
        text = (self.case.program_folder / 'application.ini').read_text(encoding='utf-8')
        return text.split('Version=', 1)[1].splitlines()[0]

    def channel(self):
        text = (self.case.program_folder / 'defaults' / 'pref' / 'channel-prefs.js').read_text(encoding='utf-8')
        return 'esr' if '"esr"' in text else 'release'

    def start(self):
        self.running, self.starts = True, self.starts + 1
        root = self.case.root
        updated = self.version().startswith('155')
        if updated and self.new_profile_after_update and self.starts > 1 and not getattr(self, 'made_new', False):
            # As Thunderbird does for an install it does not know: a new, empty profile, set as its default.
            self.made_new = True
            fresh = root / 'Profiles' / 'xyz.default-release-1'
            fresh.mkdir(parents=True)
            (fresh / 'prefs.js').write_text('// empty\n', encoding='utf-8')
            (root / 'installs.ini').write_text('[8216C80C92C4E828]\nDefault=Profiles/xyz.default-release-1\nLocked=1\n', encoding='utf-8')
            text = (root / 'profiles.ini').read_text(encoding='utf-8').replace('Profiles/abc.default-release\nLocked',
                                                                              'Profiles/xyz.default-release-1\nLocked')
            (root / 'profiles.ini').write_text(text + '\n[Profile1]\nName=default-release-1\nIsRelative=1\nPath=Profiles/xyz.default-release-1\n',
                                               encoding='utf-8')
        if updated and self.drops_account and self.starts > 1:
            prefs = self.case.profile_folder / 'prefs.js'
            prefs.write_text(prefs.read_text(encoding='utf-8').replace('account1,account2,account3', 'account1,account3'), encoding='utf-8')
            self.drops_account = None
        default = (root / 'installs.ini').read_text(encoding='utf-8').split('Default=', 1)[1].splitlines()[0]
        self.profile = root / default
        (self.profile / 'parent.lock').write_bytes(b'')
        if self.addon:
            self.connected.set()

    def stop(self):
        self.running = False
        self.connected.clear()
        if self.profile is not None:
            try: (self.profile / 'parent.lock').unlink()
            except OSError: pass

    def request(self, method, params=None, timeout=60):
        self.requests.append(method)
        if not self.connected.is_set():
            raise OSError('not connected')
        if method == 'about':
            return {'version': self.version(), 'channel': self.channel(), 'profile': str(self.profile), 'composing': self.composing}
        if method == 'accounts':
            found = []
            for key, (kind, addresses) in upgrade.prefs_accounts(self.profile).items():
                if addresses:
                    found.append({'id': key, 'type': kind, 'name': key,
                                  'identities': [{'id': 'id' + key, 'email': a} for a in addresses]})
            return found
        if method == 'inbox':
            if params['accountId'] == self.broken_inbox:
                raise ThunderbirdError('This account has no inbox in Thunderbird.')
            return {'id': params['accountId'] + '://Inbox', 'name': 'Inbox'}
        if method == 'quit':
            if self.composing:
                return {'quit': False, 'reason': 'composing'}
            if not self.refuses_to_close:
                self.stop()
            return {'quit': True}
        raise AssertionError('unexpected request ' + method)


class UpgradeSystem(FakeSystem):
    """FakeSystem with a real program folder and Mozilla's installer writing the new version there."""

    def __init__(self, case, version='153.6.0', channel='esr'):
        program = case.program_folder / 'thunderbird.exe'
        super().__init__(case.home, registered(version + ('esr' if channel == 'esr' else '') + ' (x64 en-US)', str(program)),
                         {str(program).replace('\\', '/')})
        self.case, self.thunderbird = case, case.thunderbird
        self.release = '155.0'
        self.latest = '155.0'
        self.installs = []

    def running(self):
        return self.thunderbird.running

    def launch_background(self, path):
        self.__dict__.setdefault('background', []).append(str(path))
        self.thunderbird.start()

    def close_program(self):
        self.__dict__['closes'] = self.__dict__.get('closes', 0) + 1
        if not self.thunderbird.refuses_to_close and not self.thunderbird.composing:
            self.thunderbird.stop()

    winget_leaves_esr = False     # winget found Thunderbird already there and changed nothing

    def start_process(self, command):
        self.__dict__.setdefault('commands', []).append(command)
        process = InstallerProcess(self)
        if self.winget_leaves_esr and isinstance(command, list) and command[0] == 'winget':
            process.returncode = 0
        return process

    def finish_install(self):
        if self.install_works:
            self.installs.append(self.release)
            self.case.write_program(self.release, 'release')
            self.values.update(registered(self.release + ' (x64 en-US)', str(self.case.program_folder / 'thunderbird.exe')))

    def latest_release(self):
        return self.latest


class InstallerProcess:
    def __init__(self, system):
        self.system, self.returncode = system, None

    def poll(self):
        if self.returncode is None:
            self.returncode = 0 if self.system.install_works else 1
            self.system.finish_install()
        return self.returncode

    def kill(self):
        pass


class UpgradeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.data = self.home / 'Voice Companion'
        self.package = self.home / 'Voice Companion Mail Connection.xpi'
        self.package.write_bytes(b'PK add-on version 1.7')
        self.program_folder = self.home / 'Program Files' / 'Mozilla Thunderbird'
        (self.program_folder / 'defaults' / 'pref').mkdir(parents=True)
        (self.program_folder / 'thunderbird.exe').write_bytes(b'MZ')
        self.write_program('153.6.0', 'esr')
        self.root = self.home / 'Roaming' / 'Thunderbird'
        self.profile_folder = self.root / 'Profiles' / 'abc.default-release'
        self.profile_folder.mkdir(parents=True)
        (self.profile_folder / 'prefs.js').write_text(PREFS, encoding='utf-8')
        (self.profile_folder / 'logins.json').write_text('{"logins": ["sealed"]}', encoding='utf-8')
        (self.profile_folder / 'key4.db').write_bytes(b'key database')
        (self.profile_folder / 'cert9.db').write_bytes(b'certificates')
        (self.profile_folder / 'extensions').mkdir()
        (self.profile_folder / 'extensions' / (ADDON_ID + '.xpi')).write_bytes(self.package.read_bytes())
        (self.profile_folder / 'extensions.json').write_text(json.dumps({'addons': [{'id': ADDON_ID, 'active': True}]}), encoding='utf-8')
        (self.root / 'profiles.ini').write_text(PROFILES_INI, encoding='utf-8')
        (self.root / 'installs.ini').write_text(INSTALLS_INI, encoding='utf-8')
        self.thunderbird = FakeThunderbird(self)
        self.progress, self.started = [], []
        upgrade.OFFERED = False
        self.addCleanup(setattr, upgrade, 'OFFERED', False)

    def write_program(self, version, channel):
        (self.program_folder / 'application.ini').write_text('[App]\nVendor=Mozilla\nName=Thunderbird\nVersion=' + version + '\n',
                                                             encoding='utf-8')
        (self.program_folder / 'defaults' / 'pref' / 'channel-prefs.js').write_text(
            '//@line 2 "channel-prefs.js"\npref("app.update.channel", "' + channel + '");\n', encoding='utf-8')

    def system(self, **settings):
        system = UpgradeSystem(self, **settings)
        return system

    def flow(self, system, now=1_000_000.0):
        return upgrade.Upgrade(system, self.package, lambda: self.thunderbird, self.progress.append, self.data,
                               started=self.started.append, clock=lambda: now)

    def key_files(self):
        files = {name: (self.profile_folder / name).read_bytes() for name in upgrade.PROFILE_FILES}
        files.update({name: (self.root / name).read_bytes() for name in upgrade.ROOT_FILES})
        return files


class FindingTheChannelTests(UpgradeCase):
    def test_esr_and_release_are_told_apart_by_the_program_folder(self):
        system = self.system()
        info = upgrade.installed(system)
        self.assertEqual((info.version, info.major, info.channel, info.store), ('153.6.0', 153, 'esr', False))
        self.assertTrue(upgrade.needs_release(info))
        self.write_program('155.0', 'release')
        info = upgrade.installed(system)
        self.assertEqual((info.major, info.channel), (155, 'release'))
        self.assertFalse(upgrade.needs_release(info))
        self.assertTrue(upgrade.is_current(info))

    def test_without_its_files_the_registry_version_names_esr(self):
        (self.program_folder / 'application.ini').unlink()
        (self.program_folder / 'defaults' / 'pref' / 'channel-prefs.js').unlink()
        info = upgrade.installed(self.system())
        self.assertEqual((info.version, info.channel), ('153.6.0', 'esr'))
        # An older Release (before Graph) also needs the update.
        self.assertTrue(upgrade.needs_release(upgrade.Installed('x', Path('.'), '150.0', 150, 'release', False)))

    def test_microsoft_store_thunderbird_is_never_moved(self):
        store = upgrade.Installed(r'C:\Program Files\WindowsApps\Mozilla.Thunderbird\thunderbird.exe', Path('.'), '153.6.0', 153, 'esr', True)
        self.assertFalse(upgrade.needs_release(store))
        system = self.system()
        system.values = registered('153.6.0esr (x64 en-US)', store.program)
        system.files = {store.program.replace('\\', '/')}
        step = self.flow(system).check()
        self.assertIsNone(step.question)
        self.assertIn('Microsoft Store', step.message)


class AskingTests(UpgradeCase):
    def test_the_question_is_plain_and_one_yes(self):
        step = self.flow(self.system()).check()
        self.assertEqual(step.question, 'upgrade')
        self.assertEqual(step.message, 'Thunderbird needs an update to work with Microsoft 365. Update it now? It takes a few '
                                       'minutes. Your email and settings stay as they are. Say yes or no.')

    def test_up_to_date_release_is_not_asked_about(self):
        self.write_program('155.0.1', 'release')
        flow = self.flow(self.system())
        self.assertIsNone(flow.offer())
        step = flow.check()
        self.assertIsNone(step.question)
        self.assertIn('up to date. It is version 155.0.1', step.message)

    def test_asked_once_a_session_and_not_again_for_a_week_after_no(self):
        system = self.system()
        flow = self.flow(system)
        self.assertEqual(flow.offer().question, 'upgrade')
        self.assertIsNone(self.flow(system).offer(), 'once in a session')
        step = flow.answer('upgrade', False)
        self.assertEqual(step.message, 'Okay. Thunderbird was not changed. To update it later, say update Thunderbird.')
        self.assertEqual(system.__dict__.get('commands'), None)
        upgrade.OFFERED = False          # the next session
        self.assertIsNone(self.flow(system, now=1_000_000.0 + 6 * 86400).offer())
        self.assertEqual(self.flow(system, now=1_000_000.0 + 8 * 86400).offer().question, 'upgrade')
        # "Update Thunderbird" always asks, whatever was said before.
        self.assertEqual(self.flow(system).check().question, 'upgrade')

    def test_not_installed(self):
        system = FakeSystem(self.home / 'another computer')
        step = upgrade.Upgrade(system, self.package, lambda: None, self.progress.append, self.data).check()
        self.assertEqual(step.message, upgrade.NOT_INSTALLED)


class UpdatingTests(UpgradeCase):
    def test_closed_thunderbird_is_checked_updated_into_the_same_folder_and_checked_again(self):
        system = self.system()
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, 'Thunderbird is updated to version 155.0. All your email accounts are here, as before.')
        # Started once before (to see its accounts and Inboxes), closed the normal way, started again.
        self.assertEqual(self.thunderbird.starts, 2)
        self.assertIn('quit', self.thunderbird.requests)
        self.assertEqual(system.__dict__.get('closes'), None, 'the add-on closed it; nothing was forced')
        # Mozilla's signed Release installer, run silently into the same folder.
        self.assertEqual(system.downloads, [upgrade.RELEASE_URL])
        self.assertEqual(len(system.checked), 1)
        [command] = system.commands
        self.assertRegex(command, r'^"[^"]+Thunderbird Setup\.exe" /S /InstallDirectoryPath="' +
                         str(self.program_folder).replace('\\', '\\\\') + '"$')
        self.assertEqual(upgrade.installed(system).channel, 'release')
        # The same profile opened; no account, password, sign-in or setting changed.
        self.assertEqual(self.thunderbird.profile, self.profile_folder)
        self.assertEqual(self.key_files(), before)
        self.assertEqual(flow.report['problems'], [])
        self.assertTrue(flow.report['before']['checked_in_thunderbird'])
        # A safety copy was made first, and the window goes where the person chose.
        [saved] = (self.root / upgrade.BACKUP_FOLDER).iterdir()
        self.assertEqual((saved / 'profile' / 'logins.json').read_bytes(), before['logins.json'])
        self.assertEqual((saved / 'root' / 'installs.ini').read_bytes(), before['installs.ini'])
        self.assertEqual(len(self.started), 1)
        self.assertIn('Updating Thunderbird. This takes a few minutes.', ' '.join(self.progress))

    def test_open_thunderbird_with_a_message_being_written_is_left_alone(self):
        system = self.system()
        self.thunderbird.start()
        self.thunderbird.composing = 1
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        step = flow.answer('upgrade', True)
        self.assertIn('A message is being written in Thunderbird', step.message)
        self.assertIn('Nothing was changed', step.message)
        self.assertTrue(self.thunderbird.running)
        self.assertNotIn('quit', self.thunderbird.requests)
        self.assertEqual(system.__dict__.get('commands'), None)
        self.assertEqual(self.key_files(), before)
        self.assertFalse((self.root / upgrade.BACKUP_FOLDER).exists())

    def test_thunderbird_that_does_not_close_is_not_updated(self):
        system = self.system()
        self.thunderbird.start()
        self.thunderbird.refuses_to_close = True
        flow = self.flow(system)
        flow.check()
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, upgrade.NOT_CLOSED)
        self.assertEqual(system.__dict__.get('commands'), None)
        self.assertEqual(upgrade.installed(system).channel, 'esr')

    def test_unsigned_installer_never_runs_and_the_old_thunderbird_comes_back(self):
        system = self.system()
        system.signature = ('HashMismatch', 'CN=Someone Else, O=Someone Else')
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, upgrade.FAILED)
        self.assertEqual(system.__dict__.get('commands'), None)
        self.assertEqual(upgrade.installed(system).version, '153.6.0')
        self.assertTrue(self.thunderbird.running, 'the old Thunderbird is started again')
        self.assertEqual(self.key_files(), before)

    def test_download_failure_changes_nothing(self):
        system = self.system()
        system.download = lambda url, path: (_ for _ in ()).throw(OSError('offline'))
        flow = self.flow(system)
        flow.check()
        self.assertEqual(flow.answer('upgrade', True).message, upgrade.FAILED)

    def test_a_download_a_step_behind_is_installed_again_once(self):
        system = self.system()
        system.latest = '155.0.1'
        flow = self.flow(system)
        flow.check()
        flow.answer('upgrade', True)
        self.assertEqual(len(system.commands), 2)

    def test_thunderbird_without_the_add_on_is_compared_by_its_settings(self):
        system = self.system()
        self.thunderbird.addon = False
        flow = self.flow(system)
        flow.check()
        step = flow.answer('upgrade', True)
        self.assertIn('version 155.0', step.message)
        self.assertIn('could not be checked', step.message)
        self.assertEqual(flow.report['problems'], [])


class CheckingAndPuttingBackTests(UpgradeCase):
    def assert_put_back(self, flow, system, before):
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, upgrade.RESTORED)
        self.assertIn('Nothing was lost', step.message)
        self.assertEqual(self.key_files(), before)
        self.assertEqual(upgrade.installed(system).version, '155.0', 'the new Thunderbird is not removed')
        self.assertTrue(self.thunderbird.running, 'started again with the settings put back')
        return step

    def test_a_new_empty_profile_is_noticed_and_the_profile_list_put_back(self):
        system = self.system()
        self.thunderbird.new_profile_after_update = True
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        self.assert_put_back(flow, system, before)
        self.assertIn('profile: Thunderbird opened a different profile', flow.report['problems'])
        self.assertEqual(self.thunderbird.profile, self.profile_folder, 'the right profile opens again')

    def test_a_missing_account_is_noticed_and_the_settings_put_back(self):
        system = self.system()
        self.thunderbird.drops_account = 'account2'
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        self.assert_put_back(flow, system, before)
        self.assertTrue(any(p.startswith('settings: account2') for p in flow.report['problems']), flow.report['problems'])

    def test_an_inbox_that_opened_before_must_open_after(self):
        system = self.system()
        flow = self.flow(system)
        flow.check()
        original = self.thunderbird.start

        def start():
            original()
            if self.thunderbird.version().startswith('155'):
                self.thunderbird.broken_inbox = 'account2'
        self.thunderbird.start = start
        self.assert_put_back(flow, system, self.key_files())
        self.assertIn('inbox: account2 did not open', flow.report['problems'])

    def test_settings_put_back_later_when_thunderbird_would_not_close(self):
        system = self.system()
        self.thunderbird.drops_account = 'account2'
        before = self.key_files()
        flow = self.flow(system)
        flow.check()
        original = self.thunderbird.start

        def start():
            original()
            if self.thunderbird.version().startswith('155'):
                self.thunderbird.refuses_to_close = True
        self.thunderbird.start = start
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, upgrade.RESTORE_WAITING)
        self.assertNotEqual(self.key_files(), before)
        # The person closes Thunderbird and says update Thunderbird: the settings go back.
        self.thunderbird.refuses_to_close = False
        self.thunderbird.stop()
        upgrade.OFFERED = False
        step = self.flow(system).check()
        self.assertEqual(step.message, upgrade.RESTORED)
        self.assertEqual(self.key_files(), before)
        self.assertEqual(self.flow(system).check().question, None, 'nothing left to put back')

    def test_a_file_that_was_not_there_before_is_moved_aside_never_deleted(self):
        (self.root / 'installs.ini').unlink()
        saved = upgrade.backup(self.profile_folder, self.root, stamp='one')
        (self.root / 'installs.ini').write_text('[X]\nDefault=Profiles/new\n', encoding='utf-8')
        (self.profile_folder / 'prefs.js').write_text('changed', encoding='utf-8')
        upgrade.restore(saved)
        self.assertFalse((self.root / 'installs.ini').exists())
        self.assertTrue((self.root / 'installs.ini.voice-companion-moved').is_file())
        self.assertEqual((self.profile_folder / 'prefs.js').read_text(encoding='utf-8'), PREFS)

    def test_only_the_newest_three_backups_are_kept(self):
        for stamp in ('1', '2', '3', '4'):
            upgrade.backup(self.profile_folder, self.root, stamp=stamp)
        kept = sorted(p.name for p in (self.root / upgrade.BACKUP_FOLDER).iterdir())
        self.assertEqual(kept, ['before-update-2', 'before-update-3', 'before-update-4'])

    def test_accounts_are_compared_by_key_type_and_address(self):
        before = upgrade.prefs_accounts(self.profile_folder)
        self.assertEqual(before, {'account1': ('imap', ('will@gmail.com',)), 'account2': ('ews', ('will@work.example',)),
                                  'account3': ('none', ())})
        self.assertEqual(upgrade.differences(before, dict(before, account4=('graph', ('new@contoso.example',))), 'x'), [],
                         'an account added since is fine')
        self.assertEqual(upgrade.differences(before, dict(before, account2=('imap', ('will@work.example',))), 'x'),
                         ["x: account2 changed from ('ews', ('will@work.example',)) to ('imap', ('will@work.example',))"])


class SetupFlowTests(UpgradeCase):
    """The question inside "add Thunderbird account", and for a Thunderbird too old for the add-on."""

    def setup_flow(self, system, adding=True):
        ready = lambda flow: thunderbird_setup.Step('What email address should be added?', 'address')
        return thunderbird_setup.Setup(system, self.package, lambda: 'x', lambda: self.thunderbird, self.progress.append,
                                       ready=ready, adding=adding, upgrader=lambda: self.flow(system))

    def test_adding_an_account_to_esr_offers_the_update_first_and_no_carries_on(self):
        system = self.system()
        flow = self.setup_flow(system)
        step = flow.start()
        self.assertEqual(step.message, upgrade.QUESTION)
        self.assertEqual(step.question, 'upgrade')
        step = flow.answer('upgrade', False)
        self.assertTrue(step.message.startswith(upgrade.NOT_NOW + ' '))
        self.assertTrue(step.message.endswith('What email address should be added?'))
        self.assertEqual(step.question, 'address')
        self.assertEqual(upgrade.installed(system).channel, 'esr')
        # Asked once: the next add goes straight on.
        self.assertEqual(self.setup_flow(system).start().question, 'address')

    def test_yes_updates_then_carries_on_adding(self):
        system = self.system()
        flow = self.setup_flow(system)
        flow.start()
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, 'Thunderbird is updated to version 155.0. All your email accounts are here, as before. '
                                       'What email address should be added?')
        self.assertEqual(step.question, 'address')
        self.assertEqual(flow.version, 155)

    def test_a_failed_update_stops_with_its_reason(self):
        system = self.system()
        system.install_works = False
        flow = self.setup_flow(system)
        flow.start()
        step = flow.answer('upgrade', True)
        self.assertEqual(step.message, upgrade.FAILED)
        self.assertIsNone(step.question)

    def test_too_old_for_the_add_on_is_offered_the_update(self):
        self.write_program('115.16.0', 'esr')
        system = self.system(version='115.16.0')
        flow = self.setup_flow(system, adding=False)
        step = flow.start()
        self.assertEqual(step.question, 'upgrade')
        step = flow.answer('upgrade', False)
        self.assertIn('too old', step.message)
        self.assertIn('say update Thunderbird', step.message)
        self.assertIsNone(step.question)

    def test_a_current_release_is_not_asked_about(self):
        self.write_program('155.0', 'release')
        self.assertEqual(self.setup_flow(self.system(version='155.0', channel='release')).start().question, 'address')


class FreshInstallTests(UpgradeCase):
    def test_an_esr_copy_found_after_winget_is_moved_to_release_in_its_folder(self):
        system = self.system()
        system.winget, system.winget_leaves_esr = True, True
        program = thunderbird_setup.install_program(system, self.progress.append)
        self.assertEqual(program, str(self.program_folder / 'thunderbird.exe'))
        self.assertEqual(system.commands[0][:4], ['winget', 'install', '--id', 'Mozilla.Thunderbird'])
        self.assertIn('/InstallDirectoryPath=', system.commands[-1])
        self.assertEqual(upgrade.installed(system).channel, 'release')


class CompanionTests(UpgradeCase):
    """ "Update Thunderbird", the answers, and when the question may come by itself."""

    def run_commands(self, flow, *commands, mode='awake'):
        import companion
        from unittest import mock
        spoken = []
        with mock.patch.object(companion, 'new_thunderbird_upgrade', lambda hint='': flow), \
             mock.patch.object(companion, 'speak', spoken.append), mock.patch.object(companion, 'speak_status', spoken.append), \
             mock.patch.object(companion, 'TEXT_MODE', True), mock.patch.object(companion, 'APP_WINDOW', None), \
             mock.patch.object(companion, 'APP', self.data), mock.patch.object(companion, 'THUNDERBIRD_SETUP', None), \
             mock.patch.object(companion, 'account_setup', False):
            for command in commands:
                spoken.append('>' + command)
                mode = companion.handle(command, mode)
            return spoken, companion.THUNDERBIRD_SETUP

    def test_update_thunderbird_asks_and_no_changes_nothing(self):
        system = self.system()
        spoken, pending = self.run_commands(self.flow(system), 'Update Thunderbird.', 'No, thanks.')
        self.assertIn(upgrade.QUESTION, spoken)
        self.assertIn(upgrade.NOT_NOW, spoken)
        self.assertIsNone(pending)
        self.assertEqual(system.__dict__.get('commands'), None)

    def test_yes_updates(self):
        system = self.system()
        spoken, pending = self.run_commands(self.flow(system), 'update thunderbird', 'Yes, please.')
        self.assertIn('Thunderbird is updated to version 155.0. All your email accounts are here, as before.', spoken)
        self.assertIsNone(pending)

    def test_another_command_is_not_an_answer_and_runs(self):
        system = self.system()
        spoken, pending = self.run_commands(self.flow(system), 'update thunderbird', 'what time is it')
        self.assertIsNone(pending)
        self.assertNotIn(upgrade.NOT_NOW, spoken)
        after = spoken[spoken.index('>what time is it') + 1:]
        self.assertTrue(after and 'Thunderbird' not in ' '.join(after), after)
        self.assertEqual(system.__dict__.get('commands'), None)
        self.assertFalse((self.data / upgrade.OFFER_FILE).exists(), 'not remembered as a no')

    def test_up_to_date_says_so(self):
        self.write_program('155.0', 'release')
        spoken, pending = self.run_commands(self.flow(self.system()), 'update thunderbird')
        self.assertIn('Thunderbird is up to date. It is version 155.0, and it works with Microsoft 365. It keeps itself up to date.',
                      spoken)
        self.assertIsNone(pending)

    def test_the_question_comes_by_itself_only_at_a_sensible_time(self):
        import companion
        from unittest import mock
        system = self.system()
        relay = self.thunderbird
        with mock.patch.object(companion, 'new_thunderbird_upgrade', lambda hint='': self.flow(system)), \
             mock.patch.object(companion, 'THUNDERBIRD_SETUP', None), mock.patch.object(companion, 'ACCOUNT_ENTRY', None), \
             mock.patch.object(companion, 'email_draft', None):
            # Not from a background check (no command running), and not while writing.
            self.assertIsNone(companion.thunderbird_update_offer(relay))
            with mock.patch.object(companion, '_HANDLE_DEPTH', 1):
                for mode in ('document', 'email_draft', 'note'):
                    with mock.patch.object(companion.TRANSCRIPT, 'mode', mode):
                        self.assertIsNone(companion.thunderbird_update_offer(relay), mode)
                found = []
                worker = threading.Thread(target=lambda: found.append(companion.thunderbird_update_offer(relay)))
                worker.start(); worker.join()
                self.assertEqual(found, [None], 'never from another thread')
                with mock.patch.object(companion.TRANSCRIPT, 'mode', 'mailbox'):
                    self.thunderbird.connected.set()
                    self.assertIsNone(companion.thunderbird_update_offer(relay), 'Thunderbird already connected')
                    self.thunderbird.connected.clear()
                    flow, step = companion.thunderbird_update_offer(relay)
                    self.assertEqual(step.question, 'upgrade')
                    self.assertIsNone(companion.thunderbird_update_offer(relay), 'once a session')

    def test_first_email_use_asks_instead_of_starting_thunderbird(self):
        import companion
        from unittest import mock
        from mailbox_access import MailboxError
        system = self.system()
        flow = self.flow(system)
        with mock.patch.object(companion, 'thunderbird_update_offer', lambda relay=None: (flow, flow.check())), \
             mock.patch.object(companion, 'mail_relay', lambda: self.thunderbird), \
             mock.patch.object(companion, 'THUNDERBIRD_SETUP', None), mock.patch.object(companion, 'CONFIRM_CHOICE', 'no'):
            with self.assertRaises(MailboxError) as raised:
                companion.thunderbird_starter(system)
            self.assertEqual(str(raised.exception), upgrade.QUESTION)
            self.assertEqual(companion.THUNDERBIRD_SETUP['question'], 'upgrade')
        self.assertFalse(self.thunderbird.running, 'Thunderbird is not started before the answer')


if __name__ == '__main__':
    unittest.main()
