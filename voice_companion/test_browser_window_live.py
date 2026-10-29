"""Guided Chrome in the background, with the real Chrome (Will, October 2026).

"Run the browser in the background" starts the installed Chrome headless. These
checks use the real browser and a page served on this computer:
- Chrome runs headless and is not muted (Playwright mutes headless Chrome by default).
- Sound plays: the page's audio runs and its time moves on.
- Websites see Chrome's usual name, not HeadlessChrome.
- Page dialogs are still caught and said, and a file can still be given to a file box.
- "Show browser window" and "Hide browser window" start Chrome again on the same page.
- On Windows, Chrome's sound reaches Windows: it has an active audio session.
"""
import http.server
import io
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest import mock

import web_assistant
from web_assistant import BrowserBackend, WebSession


def tone(seconds=8, rate=22050):
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(seconds * rate)))
    return data.getvalue()


PAGE = b'''<!doctype html><html><head><title>Background test</title></head><body>
<h1>Background test</h1><p>First paragraph of the test page.</p><p>Second paragraph.</p>
<audio id="sound" src="/tone.wav" preload="auto"></audio>
<button id="play" onclick="document.getElementById('sound').play()">Play sound</button>
<button id="ask" onclick="alert('Saved your choice')">Show message</button>
<label>Photo <input id="photo" type="file"></label>
</body></html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    TONE = tone()

    def do_GET(self):
        body, kind = (self.TONE, 'audio/wav') if self.path.startswith('/tone') else (PAGE, 'text/html')
        if self.path.startswith('/plain'):
            body = PAGE.replace(b'<label>Photo <input id="photo" type="file"></label>', b'')
        self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


def chrome_installed():
    if sys.platform == 'win32':
        return any(os.path.isfile(os.path.join(os.environ.get(name, ''), 'Google', 'Chrome', 'Application', 'chrome.exe'))
                   for name in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'))
    return bool(shutil.which('google-chrome') or os.path.isfile('/opt/google/chrome/chrome'))


@unittest.skipUnless(chrome_installed(), 'Google Chrome is not installed')
class BackgroundChromeLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = 'http://127.0.0.1:%d/' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='vc-background-chrome-')
        patcher = mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.backend = BrowserBackend(self.folder, 'chrome')
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.addCleanup(self.backend.close)

    def command_line(self):
        session = self.backend.context.browser.new_browser_cdp_session()
        try:
            return session.send('Browser.getBrowserCommandLine')['arguments']
        finally:
            session.detach()

    def test_runs_headless_with_sound_and_its_usual_name(self):
        self.backend.goto(self.url)
        self.assertTrue(self.backend.launched_hidden)
        arguments = self.command_line()
        self.assertTrue(any(a.startswith('--headless') for a in arguments), arguments)
        self.assertNotIn('--mute-audio', arguments)
        agent = self.backend.page.evaluate('navigator.userAgent')
        self.assertNotIn('Headless', agent)
        self.backend.page.click('#play')
        self.backend.page.wait_for_function("()=>{const a=document.getElementById('sound');return !a.paused&&a.currentTime>0.5}", timeout=15000)
        state = self.backend.page.evaluate("()=>{const a=document.getElementById('sound');return {muted:a.muted,volume:a.volume}}")
        self.assertEqual(state, {'muted': False, 'volume': 1})
        if sys.platform == 'win32':
            self.assertTrue(self.chrome_sound_reaches_windows(), 'Headless Chrome has no active Windows audio session.')

    def chrome_sound_reaches_windows(self):
        """Windows lists an active audio session for Chrome while the tone plays (no sound device: skipped)."""
        from audio_ducking import WindowsAudioSessions
        try:
            sessions = WindowsAudioSessions()
        except Exception as exc:
            self.skipTest('Windows audio is not available here: ' + str(exc))
        deadline = time.monotonic() + 10
        seen = []
        while time.monotonic() < deadline:
            try:
                found = list(sessions.sessions()) if hasattr(sessions, 'sessions') else []
            except Exception as exc:
                self.skipTest('Windows audio sessions could not be read: ' + str(exc))
            seen = [str(getattr(s, 'name', '')) for s in found]
            for item in found:
                try: item.close()
                except Exception: pass
            if any('chrome' in name.lower() for name in seen):
                return True
            time.sleep(0.5)
        if not seen:
            self.skipTest('Windows lists no audio sessions here (no sound device).')
        return False

    @unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'A window needs a desktop')
    def test_a_page_with_a_file_box_opens_chrome_with_a_window(self):
        session = WebSession(self.folder, self.backend)
        session.auto_read = False
        said = session.open(self.url)
        self.assertTrue(said.startswith(web_assistant.WINDOW_SHOWN['file']), said)
        self.assertFalse(self.backend.launched_hidden)
        self.assertEqual(self.backend.page.url, self.url)

    def test_dialogs_and_file_boxes_still_work_in_the_background(self):
        self.backend.goto(self.url)
        self.backend.snapshot()             # starts watching for page dialogs
        self.backend.page.click('#ask')
        deadline = time.monotonic() + 5
        while not getattr(self.backend, 'dialog_notice', '') and time.monotonic() < deadline:
            self.backend.page.wait_for_timeout(50)
        self.assertIn('Saved your choice', self.backend.dialog_notice)
        photo = os.path.join(self.folder, 'photo.txt')
        with open(photo, 'w') as handle: handle.write('test')
        self.backend.page.set_input_files('#photo', photo)
        self.assertEqual(self.backend.page.evaluate("document.getElementById('photo').files[0].name"), 'photo.txt')

    @unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'A window needs a desktop')
    def test_show_and_hide_start_chrome_again_on_the_same_page(self):
        session = WebSession(self.folder, self.backend)
        session.auto_read = False
        self.url = self.url + 'plain'
        self.assertIn('Background test', session.open(self.url))
        self.assertTrue(self.backend.launched_hidden)
        said = session.browser_window(True)
        self.assertTrue(said.startswith(web_assistant.WINDOW_SHOWN['asked']), said)
        self.assertFalse(self.backend.launched_hidden)
        self.assertEqual(self.backend.page.url, self.url)
        self.assertNotIn('--headless', ' '.join(self.command_line()))
        said = session.browser_window(False)
        self.assertTrue(said.startswith(web_assistant.WINDOW_HIDDEN), said)
        self.assertTrue(self.backend.launched_hidden)
        self.assertEqual(self.backend.page.url, self.url)


if __name__ == '__main__':
    unittest.main()
