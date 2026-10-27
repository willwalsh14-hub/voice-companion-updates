"""Real Chromium checks for page dialogs, unsaved-change prompts and changing pages.

Run with the other real-browser checks (tools/run_checks.py --live). Pages are
synthetic and served by the test; nothing reaches the internet.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from web_assistant import BrowserBackend, WebSession

FORM = '''<title>Form</title><h1>Application</h1><a href="https://fixture.test/next">Next page</a>
<form><label for="note">Note</label><input id="note"></form>
<button onclick="alert('Saved!')">Save</button><button onclick="alert('')">Empty</button>
<button onclick="window.answer=confirm('Delete item?')">Remove</button>
<button onclick="window.answer=prompt('Your name')">Name</button>
<script>document.getElementById('note').addEventListener('input',()=>window.dirty=1);
addEventListener('beforeunload',e=>{if(window.dirty){e.preventDefault();e.returnValue='';}});</script>'''


class LiveDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        if not Path(cls.driver.chromium.executable_path).is_file():
            cls.driver.stop()
            if os.getenv('VOICE_COMPANION_REQUIRE_WEB_TESTS'): raise RuntimeError('The Chromium runtime is required for web release checks.')
            raise unittest.SkipTest('Install the Chromium runtime for live DOM checks.')
        cls.browser = cls.driver.chromium.launch(headless=True, timeout=15000)

    @classmethod
    def tearDownClass(cls): cls.browser.close(); cls.driver.stop()

    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**', lambda route: route.fulfill(
            content_type='text/html', body='<title>Next</title><h1>Arrived</h1>' if route.request.url.endswith('/next') else FORM))
        self.page = self.context.new_page(); self.page.set_default_timeout(5000)
        self.page.goto('https://fixture.test/', timeout=30000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session._refresh()

    def at(self, label):
        item = next(e for e in self.session.browse.elements if e.get('label') == label)
        self.session.browse.index = self.session.browse.elements.index(item)
        self.session.browse.position = self.session.reading.position = item['start']
        return self.session.keyboard('Activate')

    def test_page_messages_read_cleanly(self):
        self.assertTrue(self.at('Save').startswith('Page message: Saved! The website'), self.session)
        self.assertTrue(self.at('Empty').startswith('The page showed a message with no words. The website'))
        said = self.at('Remove')
        self.assertTrue(said.startswith('Website confirmation canceled: Delete item? The website'), said)
        self.assertFalse(self.page.evaluate('window.answer'))
        said = self.at('Name')
        self.assertTrue(said.startswith('The page asked you to type an answer, so it was canceled. It asked: Your name. The website'), said)
        self.assertIsNone(self.page.evaluate('window.answer'))
        # Each message is said once.
        self.assertEqual(self.session.refresh_changed(), '')

    def test_unsaved_changes_keep_the_page_and_a_second_try_leaves(self):
        self.page.click('#note'); self.page.keyboard.type('hello')
        notice = 'This page has changes that are not saved, so it stayed open. To leave it anyway, try again. The changes will be lost.'
        started = time.monotonic()
        self.assertEqual(self.at('Next page'), notice)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(self.page.url, 'https://fixture.test/')
        self.assertEqual(self.session.open('https://fixture.test/next').split(' Arrived')[0], 'Page loaded: Next. Site: fixture.test.')
        # The same is true when an address is opened.
        self.page.go_back(); self.session._refresh()
        self.page.click('#note'); self.page.keyboard.type('again')
        self.assertEqual(self.session.open('https://fixture.test/next'), notice)
        self.assertTrue(self.session.open('https://fixture.test/next').startswith('Page loaded: Next.'))

    def test_unsaved_changes_and_forward(self):
        notice = 'This page has changes that are not saved, so it stayed open. To leave it anyway, try again. The changes will be lost.'
        self.assertTrue(self.session.open('https://fixture.test/next').startswith('Page loaded: Next.'))
        self.assertIn('Form', self.session.keyboard('Back'))
        self.page.click('#note'); self.page.keyboard.type('typed')
        started = time.monotonic()
        self.assertEqual(self.session.keyboard('Forward'), notice)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(self.page.url, 'https://fixture.test/')
        self.assertIn('Next', self.session.keyboard('Forward'))


if __name__ == '__main__':
    unittest.main()
