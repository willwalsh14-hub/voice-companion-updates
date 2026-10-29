"""The browser extension reports its version; an older one is asked to update.

Chrome installs extension updates from the Chrome Web Store on its own, but
only every few hours and only when the extension is idle. When Voice Companion
ships a newer extension than the one running, the relay asks the extension to
check the store now (once), and the user hears one plain sentence about it,
once for each new version.

The relay's loopback WebSocket server is replaced here, so these tests run on
Linux too; test_browser_cdp_relay covers the WebSocket side on Windows.
"""
import io
import json
import tempfile
import time
import unittest
from multiprocessing.connection import Client
from pathlib import Path
from unittest import mock

import browser_cdp_relay
from browser_cdp_relay import BrowserRelay, decode, encode


UPDATING = 'The Chrome extension is updating. Chrome checks for the new version on its own; it may take a little while.'


class NoWebSocketServer:
    """Stands in for websockets.sync.server.serve: the pipe side is what is tested."""
    def __init__(self, *args, **kwargs):
        self.socket = mock.Mock()
        self.socket.getsockname.return_value = ('127.0.0.1', 1)
    def serve_forever(self):
        pass
    def shutdown(self):
        pass

SHIPPED=json.loads((Path(__file__).parent/'regular_browser_extension'/'manifest.json').read_text(encoding='utf-8'))['version']


class ExtensionVersionTests(unittest.TestCase):
    """An older extension is asked to update once, and the user hears about it once."""
    def connect(self,relay,version,browser='chrome'):
        native=Client(relay.listener.address,family='AF_INET',authkey=relay.key)
        self.addCleanup(native.close)
        hello={'method':'VoiceCompanion.ready','browser':browser}
        if version is not None:hello['version']=version
        native.send_bytes(encode(hello))
        self.assertTrue(relay.connected.wait(2))
        return native

    def relay(self,folder):
        import websockets.sync.server
        with mock.patch.object(websockets.sync.server,'serve',NoWebSocketServer):
            relay=BrowserRelay(folder,('127.0.0.1',0),family='AF_INET')
        self.addCleanup(relay.close)
        return relay

    def disconnect(self,relay,native):
        native.close()
        deadline=time.monotonic()+2
        while relay.connected.is_set() and time.monotonic()<deadline:time.sleep(.01)

    def test_shipped_version_is_read_from_the_installed_extension(self):
        self.assertEqual(browser_cdp_relay.shipped_extension_version(),SHIPPED)
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(browser_cdp_relay.shipped_extension_version(Path(folder)))

    def test_versions_compare_as_numbers(self):
        older=browser_cdp_relay.extension_is_older
        self.assertTrue(older('0.4.9','0.4.10'))
        self.assertFalse(older('0.4.10','0.4.9'))
        self.assertFalse(older('0.4.3','0.4.3'))
        self.assertTrue(older(None,'0.4.3'),'Copies from before 0.4.3 do not report a version.')
        self.assertTrue(older('','0.4.3'))
        self.assertFalse(older('not a version','0.4.3'))
        self.assertFalse(older('0.4.1',None))

    def test_older_extension_is_asked_to_check_the_store_once(self):
        with tempfile.TemporaryDirectory() as folder:
            relay=self.relay(folder)
            native=self.connect(relay,'0.0.1')
            self.assertEqual(relay.extension_version,'0.0.1')
            self.assertEqual(decode(native.recv_bytes()),{'method':'VoiceCompanion.checkForUpdate','version':SHIPPED})
            # The update reloads the extension. If it comes back old, it is not asked again (no reload loop).
            self.disconnect(relay,native)
            native=self.connect(relay,'0.0.1')
            self.assertFalse(native.poll(.1),'The extension was asked to update twice.')

    def test_current_or_newer_extension_is_left_alone(self):
        for version in (SHIPPED,'99.0'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as folder:
                relay=self.relay(folder)
                native=self.connect(relay,version)
                self.assertFalse(native.poll(.1))
                self.assertEqual(browser_cdp_relay.extension_update_notice(relay),'')

    def test_user_hears_once_per_new_version_even_after_restarting(self):
        with tempfile.TemporaryDirectory() as folder:
            relay=self.relay(folder)
            self.connect(relay,'0.0.1')
            message=browser_cdp_relay.extension_update_notice(relay)
            self.assertEqual(message,'The Chrome extension is updating. Chrome checks for the new version on its own; it may take a little while.')
            self.assertEqual(browser_cdp_relay.extension_update_notice(relay),'')
            relay.close()
            # Companion restarts; the extension is still old. Already told for this version.
            again=self.relay(folder)
            self.connect(again,'0.0.1')
            self.assertEqual(browser_cdp_relay.extension_update_notice(again),'')

    def test_updated_extension_brings_no_message(self):
        with tempfile.TemporaryDirectory() as folder:
            relay=self.relay(folder)
            native=self.connect(relay,'0.0.1')
            self.disconnect(relay,native)
            self.connect(relay,SHIPPED)
            self.assertEqual(browser_cdp_relay.extension_update_notice(relay),'')

    def test_message_names_the_browser_in_use(self):
        with tempfile.TemporaryDirectory() as folder:
            relay=self.relay(folder)
            self.connect(relay,None,browser='edge')
            self.assertEqual(browser_cdp_relay.extension_update_notice(relay),
                             'The Edge extension is updating. Edge checks for the new version on its own; it may take a little while.')

    def test_no_relay_or_damaged_record_never_raises(self):
        self.assertEqual(browser_cdp_relay.extension_update_notice(None),'')
        with tempfile.TemporaryDirectory() as folder:
            relay=self.relay(folder)
            self.connect(relay,'0.0.1')
            (Path(folder)/browser_cdp_relay.UPDATE_TOLD_FILE).mkdir()  # Cannot be read or replaced.
            self.assertIn('is updating',browser_cdp_relay.extension_update_notice(relay))
            self.assertEqual(browser_cdp_relay.extension_update_notice(relay),'','Said twice in one session.')

    def test_native_host_passes_the_version_on(self):
        received=[]
        class Pipe:
            def __init__(self,*args,**kwargs):pass
            def send_bytes(self,data):received.append(decode(data))
            def recv_bytes(self,limit):raise EOFError
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'browser-cdp.key').write_bytes(b'k')
            with mock.patch.object(browser_cdp_relay,'Client',Pipe),mock.patch('regular_browser.data_folder',return_value=Path(folder)):
                browser_cdp_relay.native_stream({'protocol':2,'command':'cdp','browser':'chrome','version':'0.4.3'},io.BytesIO(b''),io.BytesIO())
        self.assertEqual(received[0],{'method':'VoiceCompanion.ready','browser':'chrome','version':'0.4.3'})


class SpokenWhenWebBrowserOpensTests(unittest.TestCase):
    """The user hears the update sentence as the Web browser opens, before the menu."""
    def setUp(self):
        import companion
        import chrome_setup
        from test_web_assistant import FakeBackend
        from web_assistant import WebSession
        self.app, self.spoken = companion, []
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        backend = FakeBackend(); backend.regular, backend.browser = True, 'chrome'  # Your own Chrome, linked.
        self.session = WebSession(temp.name, backend)
        told = iter([UPDATING, ''])
        relay = mock.Mock(update_notice=lambda: next(told, ''))
        patches = [mock.patch.object(companion, 'speak', self.spoken.append),
                   mock.patch.object(companion, 'speak_status', self.spoken.append),
                   mock.patch.object(companion, 'web_session', self.session), mock.patch.object(companion, 'APP_WINDOW', None),
                   mock.patch.object(chrome_setup, 'missing_for_web', lambda *args: None),
                   mock.patch('browser_cdp_relay.prepare'), mock.patch.object(browser_cdp_relay, '_RELAY', relay)]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def test_said_once_before_the_menu(self):
        self.assertEqual(self.app.open_browser_menu(True), 'browser_menu')
        self.assertTrue(self.spoken[-1].startswith(UPDATING + ' Web browser menu.'), self.spoken[-1])
        self.app.open_browser_menu(True)
        self.assertTrue(self.spoken[-1].startswith('Web browser menu.'), self.spoken[-1])


if __name__ == '__main__':
    unittest.main()
