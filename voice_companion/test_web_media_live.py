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


class LiveBrowser(unittest.TestCase):
    """One headless Chromium for the class. It has no tests of its own."""
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


class LiveDialogTests(LiveBrowser):

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


class SlowSiteTests(LiveBrowser):
    """A real local web server that takes longer than the click wait to answer."""
    DELAY = 12

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        delay = cls.DELAY

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path.startswith('/slow'):
                    time.sleep(delay); body = b'<title>Slow</title><h1>Slow page arrived</h1>'
                else:
                    body = (b'<title>Home</title><h1>Home</h1><a href="/slow">Slow page</a>'
                            b'<form action="/slow"><label>Search<input name="q"></label><button>Find</button></form>')
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(body)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass(); cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.page = self.context.new_page(); self.page.set_default_timeout(5000)
        self.page.goto('http://127.0.0.1:%d/' % self.server.server_address[1], timeout=30000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session._refresh()

    at = LiveDialogTests.at

    def test_a_slow_link_opens_its_page_instead_of_reporting_a_failure(self):
        said = self.at('Slow page')
        self.assertTrue(said.startswith('Page loaded: Slow.'), said)
        self.assertIn('Slow page arrived', said)

    def test_a_slow_form_button_opens_its_page(self):
        said = self.at('Find')
        self.assertTrue(said.startswith('Page loaded: Slow.'), said)


SHOP = """<title>Shop</title><h1>Shop</h1><ul>
<li><a href="/blue"><img src="/blue.png" alt="Blue hat"></a></li>
<li><a href="/red"><img src="/images/red.png"></a></li>
<li>Shoes<ul><li><a href="/size">Size guide</a></li></ul></li>
<li></li></ul>"""


class NestedListTests(LiveBrowser):
    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**', lambda route: route.fulfill(status=404) if route.request.url.endswith('.png')
                           else route.fulfill(content_type='text/html', body=SHOP))
        self.page = self.context.new_page(); self.page.goto('https://fixture.test/', timeout=30000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session._refresh()

    def test_list_items_holding_only_a_link_or_image_are_named_by_it(self):
        items = [self.session.keyboard('Quick:i') for _ in range(5)]
        self.assertEqual(items, ['Blue hat, list item 1 of 4.', 'Unlabeled link on fixture.test, list item 2 of 4.',
                                 'Shoes, list item 3 of 4.', 'Size guide, list item 1 of 1.', 'Empty list item, list item 4 of 4.'])

    def test_a_link_with_an_unnamed_image_reads_as_a_link_everywhere(self):
        self.session.keyboard('PageTop')
        lines = [self.session.keyboard('Read:Down') for _ in range(3)]
        self.assertEqual(lines[1], 'Link Unlabeled link on fixture.test')
        self.assertNotIn('graphic', ' '.join(lines))


ACCOUNT = """<title>Home</title><h1>Account</h1><p>Intro text.</p><button id="open" onclick="d.hidden=false;d.querySelector('button').focus()">Open settings</button><p>After button</p>
<div id="d" role="dialog" aria-modal="true" aria-label="Settings" hidden><h2>Settings</h2><p>Choose things.</p>
<button onclick="d.hidden=true;document.getElementById('open').focus()">Close</button></div>
<script>const d=document.getElementById('d');d.addEventListener('keydown',e=>{if(e.key==='Escape'){d.hidden=true;document.getElementById('open').focus();}})</script>"""


class DialogFocusTests(LiveBrowser):
    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**', lambda route: route.fulfill(content_type='text/html', body=ACCOUNT))
        self.page = self.context.new_page(); self.page.goto('https://fixture.test/', timeout=30000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session._refresh()

    def test_a_dialog_says_it_opened_and_closing_it_returns_to_the_button_that_opened_it(self):
        self.session.keyboard('Quick:b')
        said = self.session.keyboard('Activate')
        self.assertTrue(said.startswith('Dialog opened. Settings'), said)
        self.assertNotIn('Page loaded', said)
        # Escape closes it and the cursor is back on Open settings.
        self.assertEqual(self.session.keyboard('Escape'), 'Dialog closed. Open settings, button.')
        self.assertEqual(self.session.keyboard('Read:Down'), 'After button')
        # The same when its Close button closes it.
        self.session.keyboard('Read:Up'); self.session.keyboard('Activate')
        self.session.keyboard('Quick:b')
        self.assertEqual(self.session.keyboard('Activate'), 'Dialog closed. Open settings, button.')


ARTICLE = """<title>Benefits</title><h1>Benefits</h1><p>Read this first.</p><p>Your eligibility depends on your service dates.</p>
<p>More text.</p><p>Eligibility also needs a form.</p><a href="/apply">Apply now</a>"""


class FindTextTests(LiveBrowser):
    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**', lambda route: route.fulfill(content_type='text/html', body=ARTICLE))
        self.page = self.context.new_page(); self.page.goto('https://fixture.test/', timeout=30000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session._refresh()

    def test_find_moves_to_words_in_the_page_text_when_no_link_has_them(self):
        self.assertEqual(self.session.command('Find eligibility.'), 'Found: Your eligibility depends on your service dates.')
        self.assertEqual(self.session.keyboard('Read:Down'), 'More text.')
        self.assertEqual(self.session.command('find eligibility'), 'Found: Eligibility also needs a form.')
        self.assertEqual(self.session.command('find eligibility'), 'Found from the top of the page: Your eligibility depends on your service dates.')
        # A link or button with the words is still offered first.
        self.assertTrue(self.session.command('find apply').startswith('I found Apply now'))
        self.assertTrue(self.session.command('find zebra').startswith('I could not find zebra on this page.'))


if __name__ == '__main__':
    unittest.main()
