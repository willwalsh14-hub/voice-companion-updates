"""Exercise authenticated full-duplex transport, rejection and reconnect behavior."""
import json
import tempfile
from pathlib import Path
import time
import unittest
from multiprocessing.connection import Client
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus
from browser_cdp_relay import BrowserRelay,encode,decode

# The extension that ships with this Voice Companion: an older one is first asked to update
# (test_extension_update covers that), which would come before reset here.
SHIPPED=json.loads((Path(__file__).parent/'regular_browser_extension'/'manifest.json').read_text(encoding='utf-8'))['version']


class RelayTests(unittest.TestCase):
    def test_origin_token_round_trip_and_reconnect_do_not_replay_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            relay=BrowserRelay(folder,('127.0.0.1',0),family='AF_INET')
            native=Client(relay.listener.address,family='AF_INET',authkey=relay.key)
            try:
                native.send_bytes(encode({'method':'VoiceCompanion.ready','browser':'edge','version':SHIPPED}))
                self.assertTrue(relay.connected.wait(2));self.assertEqual(relay.browser,'edge');self.assertEqual(relay.generation,1)
                for url,origin in [(relay.endpoint,'https://untrusted.test'),(relay.endpoint+'wrong',None)]:
                    with self.assertRaises(InvalidStatus) as rejected:
                        with connect(url,origin=origin,open_timeout=2):pass
                    self.assertEqual(rejected.exception.response.status_code,403)
                with connect(relay.endpoint,open_timeout=2) as driver:
                    self.assertEqual(decode(native.recv_bytes()),{'method':'VoiceCompanion.reset'})
                    driver.send('{"id":1,"sessionId":"tab","method":"Input.dispatchKeyEvent","params":{"key":"Enter"}}')
                    command=decode(native.recv_bytes());self.assertEqual(command['id'],1);self.assertEqual(command['params']['key'],'Enter')
                    native.send_bytes(encode({'id':1,'sessionId':'tab','result':{'value':'Café'}}))
                    self.assertEqual(decode(driver.recv()),{'id':1,'sessionId':'tab','result':{'value':'Café'}})
                self.assertTrue(relay.client_closed.wait(2));self.assertTrue(relay.connected.is_set())
                with connect(relay.endpoint,open_timeout=2) as driver:
                    self.assertEqual(decode(native.recv_bytes()),{'method':'VoiceCompanion.reset'})
                    self.assertFalse(native.poll(.03),'An old activation was replayed on reconnect.')
                    driver.send('{"id":2,"method":"Page.getFrameTree"}')
                    self.assertEqual(decode(native.recv_bytes())['id'],2)
                    native.send_bytes(encode({'id':2,'result':{}}));self.assertEqual(decode(driver.recv())['id'],2)
                native.close()
                deadline=time.monotonic()+2
                while relay.connected.is_set() and time.monotonic()<deadline:time.sleep(.01)
                self.assertFalse(relay.connected.is_set())
                native=Client(relay.listener.address,family='AF_INET',authkey=relay.key)
                native.send_bytes(encode({'method':'VoiceCompanion.ready','browser':'edge','version':SHIPPED}))
                self.assertTrue(relay.connected.wait(2));self.assertEqual(relay.generation,2)
                with connect(relay.endpoint,open_timeout=2):
                    self.assertEqual(decode(native.recv_bytes()),{'method':'VoiceCompanion.reset'})
                    self.assertFalse(native.poll(.03),'A browser restart replayed an old action.')
            finally:native.close();relay.close()

if __name__=='__main__':unittest.main()
