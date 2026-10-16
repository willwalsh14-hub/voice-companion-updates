import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from multiprocessing.connection import Client
from regular_browser import BrowserConnection, send, receive

class ConnectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'The shipped transport uses Windows named pipes')
    def test_authenticated_round_trip_and_expired_action_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            endpoint = r'\\.\pipe\VC-test-' + os.path.basename(folder) if os.name == 'nt' else str(Path(folder)/'pipe')
            server = BrowserConnection(folder, endpoint, 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX')
            client = Client(endpoint, family='AF_PIPE' if os.name == 'nt' else 'AF_UNIX', authkey=server.key)
            try:
                send(client, {'command':'poll','connected':True}); self.assertEqual(receive(client), {})
                result = []
                worker = threading.Thread(target=lambda:result.append(server.request('activate', timeout=2)))
                worker.start()
                deadline = time.monotonic()+1
                while server.requests.empty() and time.monotonic()<deadline: time.sleep(.001)
                send(client, {'command':'poll','connected':True}); self.assertEqual(receive(client), {'action':'activate'})
                send(client, {'command':'poll','connected':True,'result':'Express, radio button, checked.'});receive(client)
                worker.join(2); self.assertEqual(result, ['Express, radio button, checked.'])
                self.assertIn('not be repeated',server.request('space',timeout=.01))
                send(client, {'command':'poll','connected':True}); self.assertEqual(receive(client), {})
                with self.assertRaises(ValueError):server.request('eval')
            finally:
                client.close();server.listener.close()

    def test_regular_browser_alias_opens_the_unchanged_browser_menu(self):
        import companion
        with patch.object(companion,'open_browser_menu',return_value='browser_menu') as menu:
            self.assertEqual(companion._handle('regular browser','awake'),'browser_menu')
            menu.assert_called_once()
        self.assertNotIn(('Regular browser connection','regular browser'),companion.MAIN_MENU_CHOICES)

    def test_native_manifest_origin_matches_packaged_extension(self):
        import browser_bridge_host
        with tempfile.TemporaryDirectory() as folder:
            extension=Path(folder)/'regular_browser_extension';extension.mkdir()
            source=Path(__file__).parent/'regular_browser_extension'/'manifest.json'
            (extension/'manifest.json').write_text(source.read_text())
            executable=Path(folder)/'VoiceCompanion-BrowserHost.exe'
            with patch('sys.executable',str(executable)):browser_bridge_host.register()
            manifest=json.loads((Path(folder)/'browser-native-host.json').read_text())
            self.assertEqual(Path(manifest['path']).resolve(),executable.resolve())
            identity=(source.parent/'extension-id.txt').read_text().strip()
            self.assertEqual(manifest['allowed_origins'],[
                'chrome-extension://'+identity+'/',
                'chrome-extension://ijojiipjhcfaajafdbinnbehcenfchcc/'])

if __name__=='__main__':unittest.main()
