"""Chrome and extension setup. Registry, processes, network and Chrome files are fakes."""
import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chrome_setup
from chrome_setup import ChromeSetup, MESSAGES, WindowsChrome

HERE = Path(__file__).parent


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.deleted = []

    def read(self, root, key, name=''):
        return self.values.get((root, key, name))

    def write(self, root, key, name, value):
        self.values[(root, key, name)] = value

    def delete(self, root, key):
        self.deleted.append((root, key))
        for item in [k for k in self.values if k[:2] == (root, key)]:
            del self.values[item]


class FakeWinreg:
    """Behaves like winreg: values are typed tuples, missing keys raise FileNotFoundError."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, KEY_SET_VALUE, REG_SZ = 'HKCU', 'HKLM', 2, 1

    def __init__(self):
        self.keys = {}

    class Handle:
        def __init__(self, key): self.key = key
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def OpenKey(self, root, key):
        if (root, key.casefold()) not in self.keys: raise FileNotFoundError(2, 'missing')
        return self.Handle((root, key.casefold()))

    def CreateKeyEx(self, root, key, reserved, access):
        self.keys.setdefault((root, key.casefold()), {})
        return self.Handle((root, key.casefold()))

    def QueryValueEx(self, handle, name):
        if name not in self.keys[handle.key]: raise FileNotFoundError(2, 'missing')
        return self.keys[handle.key][name]

    def SetValueEx(self, handle, name, reserved, kind, value):
        self.keys[handle.key][name] = (value, kind)

    def DeleteKey(self, root, key):
        if self.keys.pop((root, key.casefold()), None) is None: raise FileNotFoundError(2, 'missing')


class FakeSystem:
    def __init__(self, chrome=True, state=None, install=True):
        self.chrome, self.state, self.install = chrome, state, install
        self.added = self.removed = 0
        self.opened = []
        self.install_started = threading.Event()

    def supported(self): return True
    def find_chrome(self): return Path('C:/Chrome/chrome.exe') if self.chrome else None
    def extension_state(self): return self.state
    def add_extension(self): self.added += 1
    def remove_extension(self): self.removed += 1
    def open_chrome(self, address=None):
        self.opened.append(address); return True
    def install_chrome(self, progress, canceled):
        self.install_started.set()
        if self.install: self.chrome = True
        return self.install


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


class FakeFocus:
    def __init__(self): self.started = self.stopped = 0; self.last_spoken = -1e9
    def start(self): self.started += 1
    def stop(self): self.stopped += 1


def flow(system=None, linked=None):
    linked = linked if linked is not None else []
    clock = Clock()
    setup = ChromeSetup(system or FakeSystem(), lambda: bool(linked), clock=clock, focus_reader=FakeFocus())
    return setup, clock, linked


def finish_install(setup):
    setup.worker.join(5)


class StoreIdentityTests(unittest.TestCase):
    def test_store_identity_is_the_published_item_and_the_native_host_allows_both(self):
        import browser_bridge_host
        self.assertEqual(chrome_setup.STORE_ID, 'ijojiipjhcfaajafdbinnbehcenfchcc')
        self.assertEqual(chrome_setup.STORE_ID, browser_bridge_host.CHROME_STORE_EXTENSION_ID)
        self.assertEqual(chrome_setup.DEVELOPER_ID, (HERE/'regular_browser_extension'/'extension-id.txt').read_text().strip())
        self.assertEqual(chrome_setup.STORE_PAGE, 'https://chromewebstore.google.com/detail/ijojiipjhcfaajafdbinnbehcenfchcc')
        with tempfile.TemporaryDirectory() as folder:
            extension = Path(folder)/'regular_browser_extension'; extension.mkdir()
            (extension/'manifest.json').write_text((HERE/'regular_browser_extension'/'manifest.json').read_text())
            with patch('sys.executable', str(Path(folder)/'VoiceCompanion-BrowserHost.exe')), patch('thunderbird_bridge.register'):
                browser_bridge_host.register()
            origins = json.loads((Path(folder)/'browser-native-host.json').read_text())['allowed_origins']
        self.assertIn('chrome-extension://' + chrome_setup.STORE_ID + '/', origins)
        self.assertIn('chrome-extension://' + chrome_setup.DEVELOPER_ID + '/', origins)

    def test_installer_offers_unchecked_setup_and_uninstall_removes_the_added_extension(self):
        script = (HERE/'VoiceCompanion.iss').read_text(encoding='utf-8')
        self.assertIn('Name: "setupchrome"; Description: "Set up Chrome and the Voice Companion extension when Voice Companion opens"; Flags: unchecked', script)
        self.assertIn('UsePreviousTasks=no', script)
        self.assertIn("{localappdata}\\VoiceCompanion\\setup-chrome.request", script)
        self.assertIn("RegDeleteKeyIncludingSubkeys(HKCU, 'Software\\Google\\Chrome\\Extensions\\" + chrome_setup.STORE_ID + "')", script)
        self.assertIn("'test_chrome_setup'", (HERE/'build-windows.ps1').read_text(encoding='utf-8'))


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.environ = {'LOCALAPPDATA': str(self.folder/'local'), 'ProgramFiles': str(self.folder/'pf'), 'SystemRoot': 'C:\\Windows'}

    def chrome_file(self, *parts):
        path = self.folder.joinpath(*parts); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'MZ'); return path

    def test_finds_chrome_from_app_paths_uninstall_entry_or_standard_folder(self):
        system = WindowsChrome(FakeRegistry(), self.environ)
        self.assertIsNone(system.find_chrome())
        standard = self.chrome_file('pf', 'Google', 'Chrome', 'Application', 'chrome.exe')
        self.assertEqual(system.find_chrome(), standard)
        listed = self.chrome_file('elsewhere', 'chrome.exe')
        registry = FakeRegistry({('HKLM', chrome_setup.UNINSTALL_KEYS[1][1], 'InstallLocation'): str(listed.parent)})
        self.assertEqual(WindowsChrome(registry, self.environ).find_chrome(), listed)
        app_path = self.chrome_file('app path', 'chrome.exe')
        registry.write('HKCU', chrome_setup.APP_PATHS_KEY, '', '"' + str(app_path) + '"')
        self.assertEqual(WindowsChrome(registry, self.environ).find_chrome(), app_path)

    def test_registry_path_that_no_longer_exists_is_ignored(self):
        registry = FakeRegistry({('HKLM', chrome_setup.APP_PATHS_KEY, ''): str(self.folder/'gone'/'chrome.exe')})
        self.assertIsNone(WindowsChrome(registry, self.environ).find_chrome())

    def write_profile(self, name, data, file='Secure Preferences'):
        profile = self.folder/'local'/'Google'/'Chrome'/'User Data'/name
        profile.mkdir(parents=True, exist_ok=True)
        (profile/file).write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')

    def test_extension_state_reads_every_profile_and_tolerates_damage(self):
        system = WindowsChrome(FakeRegistry(), self.environ)
        self.assertIsNone(system.extension_state())
        self.write_profile('Default', '{damaged')
        self.write_profile('Profile 1', {'extensions': {'settings': {'other': {}}}}, 'Preferences')
        self.assertIsNone(system.extension_state())
        self.write_profile('Profile 2', {'extensions': {'external_uninstalls': [chrome_setup.STORE_ID]}}, 'Preferences')
        self.assertEqual(system.extension_state(), 'removed')
        self.write_profile('Profile 3', {'extensions': {'settings': {chrome_setup.STORE_ID: {'disable_reasons': [8192]}}}})
        self.assertEqual(system.extension_state(), 'disabled')
        self.write_profile('Profile 4', {'extensions': {'settings': {chrome_setup.DEVELOPER_ID: {'disable_reasons': 0, 'state': 1}}}})
        self.assertEqual(system.extension_state(), 'enabled')

    def test_extension_entry_points_chrome_at_the_web_store_and_only_our_entry_is_removed(self):
        winreg = FakeWinreg()
        system = WindowsChrome(chrome_setup.Registry(winreg), self.environ)
        system.remove_extension()
        system.add_extension()
        self.assertEqual(winreg.keys[('HKCU', chrome_setup.EXTENSION_KEY.casefold())],
                         {'update_url': ('https://clients2.google.com/service/update2/crx', winreg.REG_SZ)})
        self.assertEqual(chrome_setup.EXTENSION_KEY, 'Software\\Google\\Chrome\\Extensions\\ijojiipjhcfaajafdbinnbehcenfchcc')
        system.remove_extension()
        self.assertNotIn(('HKCU', chrome_setup.EXTENSION_KEY.casefold()), winreg.keys)
        registry = FakeRegistry({('HKCU', chrome_setup.EXTENSION_KEY, 'update_url'): 'https://example.com/other'})
        WindowsChrome(registry, self.environ).remove_extension()
        self.assertEqual(registry.deleted, [])

    def test_open_chrome_passes_the_address_as_one_argument(self):
        chrome = self.chrome_file('pf', 'Google', 'Chrome', 'Application', 'chrome.exe')
        calls = []
        system = WindowsChrome(FakeRegistry(), self.environ, popen=lambda args, **kw: calls.append(args))
        self.assertTrue(system.open_chrome(chrome_setup.STORE_PAGE))
        self.assertEqual(calls, [[str(chrome), chrome_setup.STORE_PAGE]])
        self.assertFalse(WindowsChrome(FakeRegistry(), {'LOCALAPPDATA': str(self.folder/'none')}).open_chrome())


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.environ = {'LOCALAPPDATA': str(self.folder), 'SystemRoot': 'C:\\Windows'}
        self.chrome = self.folder/'Google'/'Chrome'/'Application'/'chrome.exe'
        self.calls = []

    def response(self, data=b'MZ installer'):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return lambda request, timeout: (self.calls.append(('download', request.full_url, timeout)), Response(data))[1]

    def runner(self, signed=True, installs=True, winget_installs=False, timeout=False):
        def run(args, **kw):
            self.assertIn('timeout', kw)
            if args[0].endswith('powershell.exe'):
                self.calls.append(('verify', kw['env']['VC_SIGNED_FILE']))
                return subprocess.CompletedProcess(args, 0 if signed else 1)
            if args[0] == 'winget':
                self.calls.append(('winget', tuple(args[1:])))
                if winget_installs: self.make_chrome()
                return subprocess.CompletedProcess(args, -1978335189)
            self.calls.append(('installer', tuple(args[1:])))
            self.assertTrue(Path(args[0]).is_file())
            if timeout: raise subprocess.TimeoutExpired(args, kw['timeout'])
            if installs: self.make_chrome()
            return subprocess.CompletedProcess(args, 0)
        return run

    def make_chrome(self):
        self.chrome.parent.mkdir(parents=True, exist_ok=True); self.chrome.write_bytes(b'MZ')

    def system(self, run, winget=None):
        return WindowsChrome(FakeRegistry(), self.environ, run=run, urlopen=self.response(),
                             which=lambda name: winget, sleep=lambda s: None)

    def test_signed_google_installer_runs_silently_for_this_user(self):
        progress = []
        self.assertTrue(self.system(self.runner()).install_chrome(progress.append, threading.Event()))
        self.assertEqual(self.calls[0], ('download', 'https://dl.google.com/chrome/install/latest/chrome_installer.exe', 30))
        self.assertEqual(self.calls[1][0], 'verify')
        self.assertEqual(self.calls[2], ('installer', ('/silent', '/install')))
        self.assertEqual(progress, [])

    def test_unsigned_installer_never_runs_and_winget_is_tried_with_a_permission_tip(self):
        progress = []
        system = self.system(self.runner(signed=False, winget_installs=True), winget='winget')
        self.assertTrue(system.install_chrome(progress.append, threading.Event()))
        self.assertNotIn('installer', [c[0] for c in self.calls])
        self.assertEqual(self.calls[-1], ('winget', chrome_setup.WINGET_ARGUMENTS))
        self.assertEqual(progress, [MESSAGES['second_way']])

    def test_failures_and_timeouts_report_false_without_raising(self):
        self.assertFalse(self.system(self.runner(timeout=True)).install_chrome(lambda m: None, threading.Event()))
        self.assertFalse(self.system(self.runner(installs=False), winget='winget').install_chrome(lambda m: None, threading.Event()))
        offline = WindowsChrome(FakeRegistry(), self.environ, run=self.runner(), which=lambda n: None, sleep=lambda s: None,
                                urlopen=lambda *a, **k: (_ for _ in ()).throw(OSError('offline')))
        self.assertFalse(offline.install_chrome(lambda m: None, threading.Event()))

    def test_cancel_stops_before_anything_runs(self):
        canceled = threading.Event(); canceled.set()
        self.assertFalse(self.system(self.runner(), winget='winget').install_chrome(lambda m: None, canceled))
        self.assertEqual([c[0] for c in self.calls], ['download'])


class FlowTests(unittest.TestCase):
    def test_missing_chrome_asks_installs_then_asks_for_the_extension_and_announces_the_link(self):
        system = FakeSystem(chrome=False)
        setup, clock, linked = flow(system)
        self.assertEqual(setup.start(), MESSAGES['ask_chrome'])
        self.assertEqual(setup.question, 'install_chrome')
        self.assertEqual(setup.respond('yes'), MESSAGES['installing'])
        finish_install(setup)
        self.assertEqual(setup.poll(), [MESSAGES['installed']])
        self.assertEqual(setup.question, 'add_extension')
        self.assertEqual(setup.respond('yes please'), MESSAGES['added'])
        self.assertEqual((system.added, system.opened, setup.focus.started), (1, [None], 1))
        clock.now += 5; self.assertEqual(setup.poll(), [])
        linked.append(True)
        self.assertEqual(setup.poll(), [MESSAGES['linked']])
        self.assertTrue(setup.finished); self.assertEqual(setup.focus.stopped, 1)

    def test_no_changes_nothing_at_each_question(self):
        system = FakeSystem(chrome=False)
        setup, _, _ = flow(system)
        setup.start()
        self.assertEqual(setup.respond('maybe'), MESSAGES['answer'])
        self.assertEqual(setup.respond('no'), MESSAGES['no_chrome'])
        self.assertTrue(setup.finished); self.assertFalse(system.install_started.is_set())
        system = FakeSystem()
        setup, _, _ = flow(system)
        self.assertEqual(setup.start(), MESSAGES['ask_extension'])
        self.assertEqual(setup.respond('no thanks'), MESSAGES['no_extension'])
        self.assertEqual((system.added, system.opened), (0, []))

    def test_failed_install_says_what_to_do_and_progress_is_spoken_while_installing(self):
        system = FakeSystem(chrome=False, install=False)
        gate = threading.Event()
        original = system.install_chrome
        system.install_chrome = lambda progress, canceled: (gate.wait(5), original(progress, canceled))[1]
        setup, clock, _ = flow(system)
        setup.start(); setup.respond('yes')
        clock.now += 15
        self.assertEqual(setup.poll(), [MESSAGES['still_installing']])
        clock.now += 5
        self.assertEqual(setup.poll(), [])
        self.assertEqual(setup.respond('status'), MESSAGES['installing'])
        gate.set(); finish_install(setup)
        self.assertEqual(setup.poll(), [MESSAGES['install_failed']])
        self.assertTrue(setup.finished)

    def test_cancel_while_installing_stops_setup(self):
        system = FakeSystem(chrome=False)
        setup, _, _ = flow(system)
        setup.start(); setup.respond('yes')
        self.assertEqual(setup.respond('cancel'), MESSAGES['canceled_install'])
        self.assertTrue(setup.canceled.is_set()); finish_install(setup)
        self.assertEqual(setup.poll(), [])

    def test_waiting_gives_a_menu_hint_then_offers_the_store_page(self):
        system = FakeSystem()
        setup, clock, linked = flow(system)
        setup.start(); setup.respond('yes')
        clock.now += 15; self.assertEqual(setup.poll(), [MESSAGES['waiting']])
        clock.now += 25; self.assertEqual(setup.poll(), [MESSAGES['menu_hint']])
        self.assertIn(MESSAGES['menu_hint'], setup.respond('status'))
        clock.now += 80; self.assertEqual(setup.poll(), [MESSAGES['ask_store']])
        self.assertEqual(setup.focus.stopped, 1)
        self.assertEqual(setup.respond('yes'), MESSAGES['store'])
        self.assertEqual(system.removed, 1)
        self.assertEqual(system.opened[-1], 'https://chromewebstore.google.com/detail/ijojiipjhcfaajafdbinnbehcenfchcc')
        clock.now += 120; self.assertEqual(setup.poll(), [MESSAGES['store_failed']])
        self.assertTrue(setup.finished)

    def test_extension_the_user_removed_goes_straight_to_the_store(self):
        system = FakeSystem(state='removed')
        setup, _, linked = flow(system)
        setup.start()
        self.assertEqual(setup.respond('yes'), MESSAGES['store'])
        self.assertEqual(system.added, 0)
        linked.append(True); self.assertEqual(setup.poll(), [MESSAGES['linked']])

    def test_store_route_keeps_an_extension_that_is_on(self):
        system = FakeSystem()
        setup, clock, _ = flow(system)
        setup.start(); setup.respond('yes')
        system.state = 'enabled'
        clock.now += 120; setup.poll(); setup.respond('yes')
        self.assertEqual(system.removed, 0)

    def test_already_linked_or_extension_present(self):
        setup, _, _ = flow(linked=[True])
        self.assertEqual(setup.start(), MESSAGES['linked']); self.assertTrue(setup.finished)
        system = FakeSystem(state='enabled')
        setup, clock, _ = flow(system)
        self.assertEqual(setup.start(), MESSAGES['already_added'])
        self.assertIsNone(setup.question); self.assertEqual(system.opened, [None])
        clock.now += 60; self.assertEqual(setup.poll(), [MESSAGES['not_linking']])

    def test_quiet_start_from_the_installer_says_nothing_when_already_set_up(self):
        setup, _, _ = flow(FakeSystem(state='enabled'))
        self.assertIsNone(setup.start(quiet=True)); self.assertTrue(setup.finished)
        setup, _, _ = flow(FakeSystem())
        self.assertEqual(setup.start(quiet=True), MESSAGES['ask_extension'])

    def test_registry_failure_falls_back_to_the_store(self):
        system = FakeSystem()
        system.add_extension = lambda: (_ for _ in ()).throw(PermissionError('denied'))
        setup, _, _ = flow(system)
        setup.start()
        self.assertEqual(setup.respond('yes'), MESSAGES['store'])

    def test_windows_only(self):
        system = FakeSystem(); system.supported = lambda: False
        setup, _, _ = flow(system)
        self.assertEqual(setup.start(), MESSAGES['windows_only'])

    def test_focus_words_are_short_and_never_empty_names(self):
        self.assertEqual(chrome_setup.focus_words('ControlType.Button\tEnable extension\r\n'), 'Enable extension, button.')
        self.assertEqual(chrome_setup.focus_words('ControlType.MenuItem\tVoice  Companion Browser Connection added'),
                         'Voice Companion Browser Connection added, menu item.')
        self.assertEqual(chrome_setup.focus_words('ControlType.Pane\tNew Tab - Google Chrome'), 'New Tab - Google Chrome.')
        self.assertEqual(chrome_setup.focus_words('ControlType.Button\t'), '')

    def test_focus_reader_only_reads_and_stops_its_process(self):
        lines = io.StringIO('ControlType.Button\tRemove from Chrome\nControlType.Button\tEnable extension\n')
        process = SimpleNamespace(stdout=lines, killed=False)
        process.kill = lambda: setattr(process, 'killed', True)
        spoken = []
        reader = chrome_setup.FocusReader(spoken.append, popen=lambda args, **kw: process)
        reader.start(); reader.stop()
        self.assertTrue(process.killed)
        self.assertNotIn('Invoke', chrome_setup.FOCUS_SCRIPT)
        self.assertIn('FocusedElement', chrome_setup.FOCUS_SCRIPT)

    def test_spoken_messages_are_plain(self):
        from documentation_quality import reading_level
        for key, message in MESSAGES.items():
            self.assertLessEqual(reading_level(message), 8, key)


class CompanionTests(unittest.TestCase):
    def setUp(self):
        import companion
        self.app = companion
        self.system = FakeSystem(chrome=False)
        self.spoken = []
        patches = [patch.object(companion, 'speak', self.spoken.append), patch.object(companion, 'speak_status', self.spoken.append),
                   patch.object(companion, 'CHROME_SETUP', None), patch.object(companion, 'CHROME_SETUP_RETURN', 'awake'),
                   patch.object(chrome_setup, 'WindowsChrome', lambda: self.system),
                   patch.object(chrome_setup, 'FocusReader', lambda speak: FakeFocus()),
                   patch.object(chrome_setup, 'relay_connected', lambda app: False)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def test_spoken_command_with_punctuation_starts_setup_and_yes_starts_the_install(self):
        self.assertEqual(self.app._handle('Set up Chrome.', 'awake'), 'chrome_setup')
        self.assertEqual(self.spoken[-1], MESSAGES['ask_chrome'])
        self.assertEqual(self.app.app_context('chrome_setup').get('confirmation'), 'yes')
        self.assertEqual(self.app._handle('Yes.', 'chrome_setup'), 'chrome_setup')
        self.assertEqual(self.spoken[-1], MESSAGES['installing'])
        self.app.CHROME_SETUP.worker.join(5)
        self.assertEqual(chrome_setup.poll(self.app, 'chrome_setup'), 'chrome_setup')
        self.assertEqual(self.spoken[-1], MESSAGES['installed'])
        self.assertEqual(self.app._handle('No', 'chrome_setup'), 'awake')
        self.assertIsNone(self.app.CHROME_SETUP)

    def test_other_phrasings_and_keyboard_answers(self):
        for phrase in ('Install the browser extension.', 'connect chrome', 'Install Google Chrome'):
            self.app.CHROME_SETUP = None
            self.assertEqual(self.app._handle(phrase, 'awake'), 'chrome_setup', phrase)
        self.assertEqual(self.app._navigation_key('n', 'chrome_setup'), 'no')
        self.assertEqual(self.app._navigation_key('Escape', 'chrome_setup'), 'no')
        self.app.CONFIRM_CHOICE = 'yes'
        self.assertEqual(self.app._navigation_key('Enter', 'chrome_setup'), 'yes')
        self.app._handle('yes', 'chrome_setup'); self.app.CHROME_SETUP.worker.join(5)
        self.assertIsNone(self.app.CHROME_SETUP.question if self.app.CHROME_SETUP.state == 'installing' else None)
        self.assertEqual(self.app._navigation_key('Escape', 'chrome_setup'), 'cancel')
        self.assertEqual(self.app._navigation_key('F5', 'chrome_setup'), 'status')

    def test_words_in_a_note_or_search_are_text_not_setup(self):
        with patch.object(self.app, 'INPUT_MODE', 'mixed'):
            self.assertTrue(self.app.writing_dictation('note'))
        self.app._handle('install chrome', 'note')
        self.assertIsNone(self.app.CHROME_SETUP)
        self.app._handle('install chrome', 'search')
        self.assertIsNone(self.app.CHROME_SETUP)

    def test_main_menu_cancels_and_exit_still_works(self):
        self.app._handle('set up chrome', 'awake')
        self.assertEqual(self.app._handle('main menu', 'chrome_setup'), 'awake')
        self.assertEqual(self.spoken[-1], MESSAGES['canceled'])
        self.app._handle('set up chrome', 'awake')
        setup = self.app.CHROME_SETUP
        with patch.object(self.app, 'handle', return_value='exit') as handle:
            self.assertEqual(self.app._handle('Exit Companion.', 'chrome_setup'), 'exit')
        handle.assert_called_once_with('Exit Companion.', 'awake', typed=True)
        self.assertTrue(setup.finished)

    def test_options_button_runs_the_same_command(self):
        from settings_model import fields
        self.assertIn('chrome_setup', {f.key for f in fields('Web browser', {}) if f.kind == 'action'})
        self.assertEqual(self.app.SETTINGS_ACTIONS['chrome_setup'], 'set up chrome')
        with patch.object(self.app, 'SETTINGS_RETURN_MODE', 'awake'):
            self.assertEqual(self.app.settings_action('chrome_setup', 'awake'), 'chrome_setup')

    def test_installer_request_starts_setup_once_after_startup(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(self.app, 'APP', Path(folder)):
            self.assertEqual(chrome_setup.startup_request(self.app, 'sleep'), 'sleep')
            (Path(folder)/chrome_setup.REQUEST_FILE).write_text('yes')
            self.assertEqual(chrome_setup.startup_request(self.app, 'update_offer'), 'update_offer')
            self.assertTrue((Path(folder)/chrome_setup.REQUEST_FILE).exists())
            self.assertEqual(chrome_setup.startup_request(self.app, 'sleep'), 'chrome_setup')
            self.assertFalse((Path(folder)/chrome_setup.REQUEST_FILE).exists())
            self.assertEqual(self.app.CHROME_SETUP_RETURN, 'awake')
            self.assertEqual(self.app._handle('no', 'chrome_setup'), 'awake')

    def test_poll_returns_to_the_starting_screen_when_setup_ends(self):
        self.system.chrome = True
        self.app._handle('set up chrome', 'awake')
        self.app._handle('yes', 'chrome_setup')
        with patch.object(chrome_setup, 'relay_connected', lambda app: True):
            self.assertEqual(chrome_setup.poll(self.app, 'chrome_setup'), 'awake')
        self.assertEqual(self.spoken[-1], MESSAGES['linked'])
        self.assertIsNone(self.app.CHROME_SETUP)


if __name__ == '__main__':
    unittest.main()
