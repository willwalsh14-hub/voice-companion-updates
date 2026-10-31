"""An older Voice Companion connection in Thunderbird is replaced by itself (Will, October 30, 2026:
"nothing on the user's end"). Thunderbird loads a new add-on only when it starts, so Voice Companion
restarts it once, the normal way: never forced, never while a message is being written, and a
Thunderbird the person opened only while they are in Voice Companion."""
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import thunderbird_setup
from test_thunderbird_setup import FakeSystem, INSTALLED, SetupCase, registered
from thunderbird_bridge import ADDON_ID


class StaleRelay:
    """Thunderbird's add-on as Voice Companion's relay sees it: version, requests, connection."""
    def __init__(self, system, version='1.7.1', composing=0, quits=True, new_version='1.8.0', profile=''):
        self.system, self.version, self.composing, self.quits, self.new_version = system, version, composing, quits, new_version
        self.connected = threading.Event(); self.connected.set()
        self.profile, self.asked, self.pending = profile, [], {}
        self.pipe = object()
        system.reconnect = self.reconnect

    def request(self, method, params=None, timeout=60, **_):
        self.asked.append(method)
        if method == 'about':
            return {'profile': self.profile, 'composing': self.composing, 'version': '155.0'}
        if method == 'quit':
            if self.composing:
                return {'quit': False, 'reason': 'composing'}
            if self.quits:
                self.system.is_running = False; self.connected.clear()
                return {'quit': True}
            return {'quit': False, 'reason': 'error'}
        if method == 'minimize':
            return {}
        raise AssertionError(method)

    def reconnect(self):
        # Thunderbird started again, with whatever add-on is in its profile.
        self.version = json.loads(zipfile.ZipFile(self.addon).read('manifest.json'))['version']
        self.pipe = object(); self.connected.set()


class RestartingSystem(FakeSystem):
    reconnect = None

    def launch_background(self, path):
        super().launch_background(path); self.reconnect()

    def launch(self, path):
        super().launch(path); self.reconnect()


def package(path, version):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'version': version}))
    return path


class StaleCase(SetupCase):
    def make(self, **relay_options):
        system = RestartingSystem(self.home, registered('155.0 (x64 en-US)'), files={INSTALLED}, running=True)
        folder = self.profile()
        (folder / 'extensions').mkdir(exist_ok=True)
        package(folder / 'extensions' / (ADDON_ID + '.xpi'), '1.7.1')
        package(self.package, '1.8.0')
        relay = StaleRelay(system, profile=str(folder), **relay_options)
        relay.addon = folder / 'extensions' / (ADDON_ID + '.xpi')
        return system, relay, folder


class RefreshTests(StaleCase):
    def test_an_old_add_on_is_replaced_and_thunderbird_comes_back(self):
        system, relay, folder = self.make()
        said, log = [], []
        self.assertTrue(thunderbird_setup.addon_behind(relay, self.package))
        result = thunderbird_setup.refresh_running_addon(system, self.package, relay, said.append, note=log.append)
        self.assertEqual(result, 'updated')
        self.assertEqual(relay.version, '1.8.0')
        self.assertEqual(relay.addon.read_bytes(), self.package.read_bytes())
        self.assertEqual(said, [thunderbird_setup.ADDON_UPDATING])
        self.assertEqual(system.background, [INSTALLED])          # started again out of the way
        self.assertNotIn('closes', system.__dict__)                # never forced closed
        self.assertIn('extensions.webextensions.background-delayed-startup', (folder / 'user.js').read_text())
        self.assertFalse(thunderbird_setup.addon_behind(relay, self.package))
        self.assertTrue(any('updated to 1.8.0' in line for line in log))

    def test_a_thunderbird_the_person_opened_comes_back_as_they_open_it(self):
        system, relay, _ = self.make()
        self.assertEqual(thunderbird_setup.refresh_running_addon(system, self.package, relay, background=False), 'updated')
        self.assertEqual(system.launched, [INSTALLED])
        self.assertNotIn('background', system.__dict__)

    def test_never_while_a_message_is_being_written(self):
        system, relay, _ = self.make(composing=1)
        said = []
        self.assertEqual(thunderbird_setup.refresh_running_addon(system, self.package, relay, said.append), 'composing')
        self.assertEqual(said, [])
        self.assertNotIn('quit', relay.asked)
        self.assertTrue(system.is_running and relay.connected.is_set())

    def test_a_thunderbird_that_will_not_close_is_left_alone(self):
        system, relay, _ = self.make(quits=False)
        self.assertEqual(thunderbird_setup.refresh_running_addon(system, self.package, relay), 'not closed')
        self.assertNotIn('closes', system.__dict__)
        self.assertEqual(relay.addon.read_bytes()[:2], b'PK')
        self.assertNotEqual(relay.addon.read_bytes(), self.package.read_bytes())   # the running copy was not touched

    def test_a_current_add_on_changes_nothing(self):
        system, relay, _ = self.make(version='1.8.0')
        self.assertEqual(thunderbird_setup.refresh_running_addon(system, self.package, relay), 'current')
        self.assertEqual(relay.asked, [])
        relay.version = '2.0.0'   # newer than this copy carries: never "updated" backwards
        self.assertEqual(thunderbird_setup.refresh_running_addon(system, self.package, relay), 'current')


class CompanionPollTests(StaleCase):
    """When the main loop starts the update: only when nothing waits on Thunderbird."""
    def setUp(self):
        super().setUp()
        import companion
        self.app = companion
        self.said = []
        patches = [mock.patch.object(companion, 'speak_status', self.said.append),
                   mock.patch.object(companion, 'mail_log', lambda text: None),
                   mock.patch.object(companion, 'addon_package', lambda: self.package),
                   mock.patch.object(companion, 'ADDON_REFRESH', type(companion.ADDON_REFRESH)(
                       connection=None, retry_at=0.0, thread=None, results=__import__('queue').Queue())),
                   mock.patch.object(companion, 'THUNDERBIRD_STARTED', True),
                   mock.patch.object(companion, 'THUNDERBIRD_WATCH', None),
                   mock.patch.object(companion, 'THUNDERBIRD_SETUP', None),
                   mock.patch.object(companion, 'THUNDERBIRD_SIGN_IN', None),
                   mock.patch.object(companion, 'THUNDERBIRD_STARTING', False),
                   mock.patch.object(companion, 'ACCOUNT_ENTRY', None)]
        for patcher in patches:
            patcher.start(); self.addCleanup(patcher.stop)

    def poll(self, system, relay):
        with mock.patch('thunderbird_bridge.current', lambda: relay):
            self.app.poll_thunderbird_addon(system)
            thread = self.app.ADDON_REFRESH.thread
            if thread is not None:
                thread.join(10)
            self.app.poll_thunderbird_addon(system)

    def test_it_runs_once_and_says_when_it_is_done(self):
        system, relay, _ = self.make()
        with mock.patch.object(self.app, 'quiet_thunderbird') as quiet:
            self.poll(system, relay)
        self.assertEqual(self.said, [thunderbird_setup.ADDON_UPDATING, thunderbird_setup.ADDON_UPDATED])
        quiet.assert_called_once_with(relay)
        self.assertEqual(relay.version, '1.8.0')
        # The new connection is current: nothing more happens.
        self.said.clear()
        self.poll(system, relay)
        self.assertEqual(self.said, [])

    def test_it_waits_while_a_request_or_a_setup_is_busy(self):
        system, relay, _ = self.make()
        relay.pending = {7: 'a message list'}
        self.poll(system, relay)
        self.assertEqual(relay.asked, [])
        relay.pending = {}
        with mock.patch.object(self.app, 'THUNDERBIRD_SIGN_IN', 'https://login.example'):
            self.poll(system, relay)
        self.assertEqual(relay.asked, [])
        with mock.patch.object(self.app, 'quiet_thunderbird'):
            self.poll(system, relay)
        self.assertEqual(relay.version, '1.8.0')

    def test_a_message_being_written_is_tried_again_later(self):
        system, relay, _ = self.make(composing=1)
        self.poll(system, relay)
        self.assertEqual(self.said, [])
        self.assertGreater(self.app.ADDON_REFRESH.retry_at, 0)
        self.assertIsNone(self.app.ADDON_REFRESH.connection)

    def test_a_thunderbird_the_person_opened_waits_until_they_are_in_voice_companion(self):
        system, relay, _ = self.make()
        desktop = mock.Mock(); desktop.own_foreground.return_value = False
        with mock.patch.object(self.app, 'THUNDERBIRD_STARTED', False), \
             mock.patch.object(self.app, 'thunderbird_desktop', lambda: desktop):
            self.poll(system, relay)
            self.assertEqual(relay.asked, [])
            desktop.own_foreground.return_value = True
            self.app.ADDON_REFRESH.retry_at = 0
            with mock.patch.object(self.app, 'quiet_thunderbird') as quiet:
                self.poll(system, relay)
        self.assertEqual(system.launched, [INSTALLED])   # opened as the person opens it, not hidden
        quiet.assert_not_called()


if __name__ == '__main__':
    unittest.main()
