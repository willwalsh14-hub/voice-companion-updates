"""A radio stream that stops after it started, in a real browser (October 2026).

The player page (media_player.PLAYER_HTML) plays a live sound stream from a
local server that sends sound in real time, then stalls or drops. The stream
check (MediaHub.check_stream) must report it once; a user pause must not be
reported; Play must load the stream again.

Companion's player is Firefox, so this check needs Playwright's Firefox (the
Windows build has it). Chromium is not a fair stand-in: it would not start this
paced stream even from a plain page.
"""
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from media_hub import MediaHub
from media_player import MediaPlayer, PLAYER_HTML

RATE = 8000


def wav_header():
    """A WAV header for a stream with no known end, as live stations send."""
    data = 0x7FFFFFF0
    return (b'RIFF' + struct.pack('<I', data + 36) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b'data' + struct.pack('<I', data))


class Station(BaseHTTPRequestHandler):
    """Sends sound in real time. server.behaviour: 'stall' stops sending but keeps the
    connection open; 'drop' closes it; 'steady' keeps sending."""

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(wav_header())
            start = time.monotonic()
            chunk = b'\x00\x00' * (RATE // 4)
            sent = 0.0
            while not self.server.stopping.is_set():
                if self.server.behaviour != 'steady' and time.monotonic() - start > self.server.good_seconds:
                    if self.server.behaviour == 'drop':
                        return
                    self.server.stopping.wait(0.2)
                    continue
                # Stay half a second ahead of real time, as a live station does.
                if sent < time.monotonic() - start + 0.5:
                    self.wfile.write(chunk); self.wfile.flush(); sent += 0.25
                else:
                    time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class RadioStreamLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.driver = sync_playwright().start()
        except Exception as error:
            raise unittest.SkipTest('Playwright is unavailable: ' + str(error))
        if not Path(cls.driver.firefox.executable_path).is_file():
            cls.driver.stop()
            raise unittest.SkipTest('Firefox runtime is needed for the radio stream check.')
        cls.browser = cls.driver.firefox.launch(headless=True, firefox_user_prefs={'media.autoplay.default': 0},
                                                env={**os.environ, 'MOZ_DISABLE_CONTENT_SANDBOX': '1', 'MOZ_DISABLE_RDD_SANDBOX': '1',
                                                     'MOZ_DISABLE_GMP_SANDBOX': '1'})

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.driver.stop()

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Station)
        self.server.daemon_threads = True
        self.server.behaviour, self.server.good_seconds, self.server.stopping = 'steady', 3, threading.Event()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.stopping.set)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        folder = Path(temp.name); (folder / 'audio-player.html').write_text(PLAYER_HTML, encoding='utf-8')
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.add_init_script("addEventListener('DOMContentLoaded',()=>{const a=document.querySelector('audio');if(a)a.muted=true})")
        self.page.goto((folder / 'audio-player.html').as_uri())
        self.player = MediaPlayer(folder); self.player.start_timeout_ms = 8000
        self.player.context, self.player.page, self.player.start = self.context, self.page, lambda: None
        self.hub = MediaHub(folder, player=self.player)
        self.hub._watch.stall_seconds = 3
        self.url = 'http://127.0.0.1:%d/stream' % self.server.server_address[1]

    def watch(self, seconds):
        """Check the stream every half second for a while, as the main loop does."""
        found = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            alert = self.hub.check_stream(time.monotonic())
            if alert: found.append(alert[1])
            time.sleep(0.5)
        return found

    def test_a_stall_is_reported_once_and_play_loads_it_again(self):
        self.server.behaviour = 'stall'
        self.player.open(self.url, 'Test FM', 'radio')
        self.assertTrue(self.player.streaming())
        found = self.watch(self.server.good_seconds + 9)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith('Test FM stopped playing.'), found[0])
        self.assertTrue(self.player.status()['failed'])
        self.assertIn('Stopped, because the stream failed.', self.hub.now_playing())
        # Play tries again: the stream loads and plays.
        self.server.behaviour = 'steady'
        self.player.control('play')
        self.assertFalse(self.player.status()['failed'])
        before = self.player.status()['position']
        time.sleep(1.5)
        self.assertGreater(self.player.status()['position'], before)
        self.assertEqual(self.watch(3), [])

    def test_a_dropped_stream_is_reported(self):
        self.server.behaviour = 'drop'
        self.player.open(self.url, 'Test FM', 'radio')
        found = self.watch(self.server.good_seconds + 9)
        self.assertEqual(len(found), 1, found)

    def test_steady_playback_and_a_user_pause_are_not_reported(self):
        self.player.open(self.url, 'Test FM', 'radio')
        self.assertEqual(self.watch(5), [])
        self.assertEqual(self.hub.now_playing(), 'Station: Test FM. Playing.')
        self.player.control('pause')
        self.assertEqual(self.watch(6), [])
        self.assertEqual(self.hub.now_playing(), 'Station: Test FM. Paused.')
        # The window's own Pause button is a user pause too.
        self.player.control('play')
        self.page.evaluate("document.querySelectorAll('button')[1].click()")
        self.assertEqual(self.watch(6), [])


if __name__ == '__main__':
    unittest.main()
