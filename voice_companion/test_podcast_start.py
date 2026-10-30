"""Podcast episodes that start slowly, saved copies, and a player in a bad state (Will, October 29, 2026).

"Podcasts aren't playing at all ... it says it could not start." A long episode behind
a few redirects can take more than the 12 second wait to send its first part. That
was called a failure. Now:
  - the episode keeps loading and plays as soon as it can; Companion says Loading;
  - if it then cannot play after all, Companion says so once;
  - a downloaded copy is played instead of the internet address;
  - a player that failed is started fresh and tried once more;
  - why it failed is written to media-errors.txt for a helper (never the address).
"""
import http.server
import io
import math
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from media_hub import MediaHub
from media_player import PLAYER_HTML, MediaPlayer
from test_podcast_player import player_in


def tone(seconds=4, rate=16000):
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 440 * n / rate))) for n in range(seconds * rate)))
    return data.getvalue()


class SlowHandler(http.server.BaseHTTPRequestHandler):
    WAV = tone()
    DELAY = 1.5

    def do_GET(self):
        time.sleep(self.DELAY)          # a slow podcast host
        if self.path.startswith('/missing'):
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(len(self.WAV))); self.end_headers()
        try: self.wfile.write(self.WAV)
        except OSError: pass

    def log_message(self, *args): pass


class SlowStartTests(unittest.TestCase):
    """The real player page in real Chromium (the app uses Firefox; the page is the same)."""
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.driver = sync_playwright().start()
        if not Path(cls.driver.chromium.executable_path).is_file():
            cls.driver.stop(); raise unittest.SkipTest('Chromium is needed for the player page.')
        cls.browser = cls.driver.chromium.launch(headless=True, args=['--autoplay-policy=no-user-gesture-required'])
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.browser.close(); cls.driver.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        (folder / 'audio-player.html').write_text(PLAYER_HTML, encoding='utf-8')
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto((folder / 'audio-player.html').as_uri())
        self.player = MediaPlayer(folder)
        self.player.context, self.player.page, self.player.start = self.context, self.page, lambda: None
        self.player.start_timeout_ms = 400
        self.hub = MediaHub(folder, player=self.player)

    def test_a_slow_episode_keeps_loading_and_then_plays(self):
        self.player.open(self.base + 'episode.wav', 'Slow episode', 'podcast')
        self.assertTrue(self.player.loading)
        self.assertEqual(self.player.kind, 'podcast')
        self.page.wait_for_function("()=>{const a=document.getElementById('audio');return !a.paused&&a.currentTime>0.3}", timeout=10000)
        self.assertIsNone(self.hub.check_stream(time.monotonic()))
        self.assertFalse(self.player.loading)

    def test_a_slow_episode_that_then_fails_is_said_once(self):
        self.player.open(self.base + 'missing.wav', 'Gone episode', 'podcast')
        self.assertTrue(self.player.loading)
        alert = None
        deadline = time.monotonic() + 10
        while alert is None and time.monotonic() < deadline:
            time.sleep(0.2)
            alert = self.hub.check_stream(time.monotonic())
        self.assertIsNotNone(alert, 'A failed episode was never said')
        self.assertEqual(alert[1], 'Gone episode could not play. The podcast website may be busy. Try again, or choose another episode.')
        self.assertIsNone(self.hub.check_stream(time.monotonic()))


class PlayEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.player, self.page = player_in(self.folder / 'Audio Player')
        self.hub = MediaHub(self.temp.name, player=self.player)
        self.episode = {'title': 'Part One', 'url': 'https://pod.example/1.mp3', 'show': 'Show', 'feed': 'https://pod.example/feed'}
        self.hub.episodes = [self.episode]

    def test_a_downloaded_episode_plays_from_the_saved_copy(self):
        saved = self.hub.episode_file(self.episode)
        saved.parent.mkdir(parents=True, exist_ok=True); saved.write_bytes(b'ID3 audio')
        self.assertEqual(self.hub.play_episode(1), 'Playing Part One. Say pause, back 15 seconds, or next episode.')
        self.assertEqual(self.page.audio['src'], saved.as_uri())

    def test_an_offline_copy_downloaded_for_you_is_used_too(self):
        folder = self.folder / 'Podcast Downloads'; folder.mkdir()
        (folder / 'Show - auto.mp3').write_bytes(b'ID3 audio')
        (self.folder / 'podcast-auto-files.json').write_text(
            '{"https://pod.example/feed": {"https://pod.example/1.mp3": "Show - auto.mp3"}}', encoding='utf-8')
        self.hub.play_episode(1)
        self.assertEqual(self.page.audio['src'], (folder / 'Show - auto.mp3').as_uri())

    def test_without_a_copy_the_internet_address_is_played(self):
        self.hub.play_episode(1)
        self.assertEqual(self.page.audio['src'], 'https://pod.example/1.mp3')

    def test_a_failed_start_is_tried_once_more_on_a_fresh_player(self):
        calls, closes = [], []
        real_open = self.player.open

        def flaky(url, title, kind):
            calls.append(url)
            if len(calls) == 1:
                self.player.last_start_error = 'AbortError: aborted'
                raise OSError('This audio format could not start in the player.')
            return real_open(url, title, kind)
        self.player.open = flaky
        self.player.close = lambda: closes.append(True)
        self.assertEqual(self.hub.play_episode(1), 'Playing Part One. Say pause, back 15 seconds, or next episode.')
        self.assertEqual(len(calls), 2); self.assertEqual(closes, [True])
        log = (self.folder / 'media-errors.txt').read_text(encoding='utf-8')
        self.assertIn('podcast: OSError: This audio format could not start in the player. | player: AbortError: aborted', log)
        self.assertNotIn('pod.example', log)

    def test_two_failures_say_what_to_do(self):
        def broken(*args): raise OSError('no player')
        self.player.open = broken
        self.player.close = lambda: None
        self.assertEqual(self.hub.play_episode(1), 'This episode could not start. Try again, or choose another episode.')

    def test_loading_is_said(self):
        real_open = self.player.open

        def slow(url, title, kind):
            real_open(url, title, kind); self.player.loading = True
        self.player.open = slow
        self.assertEqual(self.hub.play_episode(1), 'Loading Part One. It starts in a moment. Say pause, back 15 seconds, or next episode.')


class DamagedProfileTests(unittest.TestCase):
    def test_a_profile_that_will_not_start_is_set_aside_once(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / 'Audio Player'
        (folder / 'profile').mkdir(parents=True); (folder / 'profile' / 'parent.lock').write_text('x')
        launches = []

        class Page:
            def goto(self, url): pass

        class Context:
            pages = [Page()]

        class Firefox:
            def launch_persistent_context(self, path, **kwargs):
                launches.append(Path(path).exists())
                if len(launches) == 1: raise RuntimeError('Firefox exited early')
                return Context()

        class Driver:
            firefox = Firefox()
            def stop(self): pass
        import shared_playwright
        from unittest.mock import patch
        with patch.object(shared_playwright, 'start', lambda folder=None: Driver()):
            player = MediaPlayer(folder); player.start()
        self.assertEqual(launches, [True, False])
        self.assertTrue((folder / 'profile-set-aside' / 'parent.lock').exists())


if __name__ == '__main__':
    unittest.main()
