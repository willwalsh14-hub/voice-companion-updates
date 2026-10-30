"""The mail host waits for Voice Companion and connects again by itself (Will, October 29, 2026).

Thunderbird starts the host once, through its add-on. Before this, the host ended
whenever Voice Companion closed, and Thunderbird had to start it again on its own
timer. Thunderbird slows that timer while its window is minimized or off the
taskbar, so after Voice Companion started again, mail hung until the user pressed
Alt+Tab to bring Thunderbird up. Now the host stays and finds Voice Companion again.

Real relay, real host code, real pipes; Thunderbird is a small stand-in that answers.
The first Voice Companion is a real process that is stopped, as when it closes or updates.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import regular_browser
import thunderbird_bridge
from browser_bridge_host import read_message, write_message


class FakeThunderbird:
    """Thunderbird's side of the native connection: answers every request with its method name."""

    def __init__(self):
        to_host_read, self.to_host = os.pipe()
        self.from_host, from_host_write = os.pipe()
        self.host_in = os.fdopen(to_host_read, 'rb', buffering=0)
        self.host_out = os.fdopen(from_host_write, 'wb', buffering=0)
        self.writer = os.fdopen(self.to_host, 'wb', buffering=0)
        self.reader = os.fdopen(self.from_host, 'rb', buffering=0)
        threading.Thread(target=self._answer, daemon=True).start()

    def _answer(self):
        try:
            while True:
                message = read_message(self.reader, max_size=1 << 20)
                if message is None: return
                write_message(self.writer, {'id': message['id'], 'result': message['method']})
        except Exception:
            return

    def close(self):
        for stream in (self.writer, self.reader):
            try: stream.close()
            except OSError: pass


class HostReconnectTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='vc-mail-host-'))
        if os.name == 'nt':
            self.endpoint, self.family = None, 'AF_PIPE'
        else:
            self.endpoint, self.family = str(self.folder / 'mail.sock'), 'AF_UNIX'
        patcher = mock.patch.object(regular_browser, 'data_folder', lambda: self.folder)
        patcher.start(); self.addCleanup(patcher.stop)
        self.thunderbird = FakeThunderbird()
        self.relays = []
        self.addCleanup(self._close_all)

    def _close_all(self):
        self.thunderbird.close()
        for relay in self.relays:
            try: relay.close()
            except OSError: pass

    def relay(self):
        relay = thunderbird_bridge.MailRelay(self.folder, self.endpoint, self.family)
        self.relays.append(relay)
        return relay

    def start_host(self):
        self.host = threading.Thread(target=thunderbird_bridge.native_stream,
                                     args=({'version': '1.7.1'}, self.thunderbird.host_in, self.thunderbird.host_out,
                                           self.endpoint, self.family, 0.05), daemon=True)
        self.host.start()

    def companion_process(self):
        """A real Voice Companion relay in its own process. Says 'connected' once Thunderbird's host
        connects and a request is answered, then waits to be stopped."""
        script = ('import sys, time\n'
                  'import thunderbird_bridge\n'
                  'relay = thunderbird_bridge.MailRelay(sys.argv[1], sys.argv[2] or None, sys.argv[3])\n'
                  'print("listening", flush=True)\n'
                  'relay.connected.wait(20)\n'
                  'print("connected", relay.request("about", timeout=10), flush=True)\n'
                  'time.sleep(60)\n')
        process = subprocess.Popen([sys.executable, '-c', script, str(self.folder), self.endpoint or '', self.family],
                                   cwd=str(Path(__file__).parent), stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (process.kill(), process.wait(), process.stdout.close()))
        self.assertEqual(process.stdout.readline().strip(), 'listening')
        return process

    def test_voice_companion_starting_again_connects_without_thunderbird(self):
        first = self.companion_process()
        self.start_host()
        self.assertEqual(first.stdout.readline().strip(), 'connected about', 'Thunderbird did not connect the first time')
        first.kill(); first.wait()      # Voice Companion closes, as for an update or Alt+F4
        if self.endpoint:
            Path(self.endpoint).unlink(missing_ok=True)   # a stopped process leaves its socket file (Windows pipes go away)
        time.sleep(0.3)
        self.assertTrue(self.host.is_alive(), 'The host ended when Voice Companion closed')
        second = self.relay()
        self.assertTrue(second.connected.wait(5), 'Mail did not connect again after Voice Companion started again')
        self.assertEqual(second.version, '1.7.1')
        self.assertEqual(second.request('inbox', timeout=5), 'inbox')

    def test_host_started_before_voice_companion_waits_for_it(self):
        self.start_host()
        time.sleep(0.3)
        self.assertTrue(self.host.is_alive())
        relay = self.relay()
        self.assertTrue(relay.connected.wait(5), 'The host did not find Voice Companion once it started')
        self.assertEqual(relay.request('accounts', timeout=5), 'accounts')

    def test_thunderbird_closing_ends_the_host(self):
        self.relay()
        self.start_host()
        self.assertTrue(self.relays[0].connected.wait(5))
        self.thunderbird.writer.close()
        self.host.join(5)
        self.assertFalse(self.host.is_alive(), 'The host kept running after Thunderbird closed')

    def test_thunderbird_closing_while_voice_companion_is_closed_ends_the_host(self):
        self.start_host()
        time.sleep(0.2)
        self.thunderbird.writer.close()
        self.host.join(5)
        self.assertFalse(self.host.is_alive())


if __name__ == '__main__':
    unittest.main()
