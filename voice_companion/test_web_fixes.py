"""Regression checks for web and browser bugs found in the October 2026 review."""
import tempfile
import unittest

from browser_navigation import looks_like_address, prepare_address
from test_web_assistant import FakeBackend
from web_assistant import BrowserBackend, WebSession
from web_browse import BrowseCursor


class SpokenAddressTests(unittest.TestCase):
    def test_dictated_addresses_open_the_intended_site(self):
        cases = {
            'VA dot gov.': 'https://va.gov',
            'example.com.': 'https://example.com',
            'example dot com forward slash news': 'https://example.com/news',
            'example dot com slash news slash today': 'https://example.com/news/today',
            'example dot com dot': 'https://example.com',
            'my dash site dot com': 'https://my-site.com',
            'slashdot dot org': 'https://slashdot.org',
            'dotdash dot com': 'https://dotdash.com',
            'http://example.com/a?b=1': 'http://example.com/a?b=1',
        }
        for spoken, expected in cases.items():
            self.assertEqual(prepare_address(spoken)[0], expected, spoken)

    def test_unsafe_or_invalid_addresses_are_still_refused(self):
        for spoken in ('javascript:alert(1)', 'file:///c:/x', 'example', 'exa mple', 'dot'):
            with self.assertRaises(ValueError, msg=spoken):
                prepare_address(spoken)

    def test_recognizer_spelling_and_commas_still_open_the_site(self):
        """Offline speech spells short names letter by letter ("v a dot gov"), other
        recognizers write "V.A." or add a comma ("Go to, VA dot gov."). These searched the web
        or matched a link on the page instead of offering to open the site."""
        from browser_navigation import spoken_address_request
        cases = {
            'v a dot gov': 'https://va.gov',
            'Go to v a dot gov.': 'https://va.gov',
            'V.A. dot gov.': 'https://va.gov',
            'Go to V.A. dot gov.': 'https://va.gov',
            'w w w dot google dot com': 'https://www.google.com',
            'Go to, VA dot gov.': 'https://va.gov',
            'Open website, VA dot gov.': 'https://va.gov',
            'Open, VA dot gov.': 'https://va.gov',
            'go to the VA dot gov': None,
            'what is a dot com company': None,
            'Go to my news': None,
            'open the door': None,
        }
        for spoken, expected in cases.items():
            self.assertEqual(spoken_address_request(spoken), expected, spoken)
        self.assertEqual(prepare_address('V.A. dot gov.')[0], 'https://va.gov')

    def test_typed_search_box_text(self):
        for text in ('va.gov.', 'Example.com.', 'example.com/path', 'https://x.example', '192.168.1.1'):
            self.assertTrue(looks_like_address(text), text)
        for text in ('98.6', '3.14', 'hello world.com', 'weather today'):
            self.assertFalse(looks_like_address(text), text)


class TableMoveTests(unittest.TestCase):
    @staticmethod
    def cell(key, label, row, column, **extra):
        return dict(key=key, role='cell', label=label, table='t', row=row, column=column, start=0, end=1, **extra)

    def test_left_and_up_leave_a_spanned_cell_in_one_step(self):
        cells = [dict(key='t', role='table', label='T', start=0, end=1), self.cell('h', 'Name', 0, 0, colspan=3),
                 self.cell('a', 'A', 1, 0), self.cell('b', 'B', 1, 1), self.cell('c', 'C', 1, 2)]
        cursor = BrowseCursor({'elements': cells}); cursor.index = 4; cursor.table = 't'; cursor.row = 1; cursor.column = 2
        self.assertTrue(cursor.table_move('Up').startswith('Name'))
        self.assertEqual(cursor.table_move('Left'), 'Start of table.')
        cells = [self.cell('d', 'Dept', 0, 0, rowspan=3), self.cell('x', 'X', 0, 1),
                 self.cell('y', 'Y', 1, 1), self.cell('z', 'Z', 2, 1)]
        cursor = BrowseCursor({'elements': cells}); cursor.index = 3; cursor.table = 't'; cursor.row = 2; cursor.column = 1
        self.assertTrue(cursor.table_move('Left').startswith('Dept'))
        self.assertEqual(cursor.table_move('Up'), 'Start of table.')


class FindAfterLinksListTests(unittest.TestCase):
    def test_no_on_a_find_suggestion_does_not_jump_into_an_old_links_list(self):
        fresh = WebSession(tempfile.mkdtemp(), FakeBackend()); fresh._refresh(read=False)
        fresh.command('find log in')
        expected = fresh.command('no')
        session = WebSession(tempfile.mkdtemp(), FakeBackend()); session._refresh(read=False)
        session.command('list links'); session.command('escape')
        session.command('find log in')
        self.assertEqual(session.command('no'), expected)
        self.assertIsNone(session.list_focus)


class LiveSnapshotTests(unittest.TestCase):
    """Run in a real Chromium when one is installed; skipped otherwise."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            if getattr(cls, 'playwright', None): cls.playwright.stop()
            raise unittest.SkipTest('Chromium is unavailable: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()

    def session(self, html=None, url=None):
        context = self.browser.new_context(); page = context.new_page()
        self.addCleanup(context.close)
        if html is not None: page.set_content(html)
        backend = BrowserBackend(tempfile.mkdtemp(), 'chrome')
        backend.page = page; backend.context = context; backend.playwright = self.playwright
        session = WebSession(tempfile.mkdtemp(), backend)
        session._refresh(read=False)
        return session, backend, page

    def test_cloned_carousel_links_get_their_own_keys(self):
        html = ('<main><h1>News</h1><div id=track><div class=slide><a href="https://example.com/a">Story A</a></div></div>'
                '<button id=init onclick="const s=document.querySelector(\'.slide\');'
                'document.getElementById(\'track\').append(s.cloneNode(true));this.remove()">Init</button></main>')
        session, backend, page = self.session(html)
        page.evaluate("document.getElementById('init').click()")
        session._refresh(read=False)
        keys = [c['key'] for c in session.snapshot['controls'] if c['role'] == 'link']
        self.assertEqual(len(keys), 2); self.assertEqual(len(set(keys)), 2)
        backend._element(session.link_items()[0])
        session._refresh(read=False)
        self.assertEqual([c['key'] for c in session.snapshot['controls'] if c['role'] == 'link'], keys)

    def test_combo_box_with_non_ascii_owner_id(self):
        html = ('<main><p>Pick a state</p><div role="combobox" aria-label="Estado" aria-expanded="false" '
                'aria-controls="opciones-región" tabindex=0>Elija</div><ul id="opciones-región" role="listbox">'
                '<li role="option" onclick="this.parentNode.previousElementSibling.textContent=\'Texas\'">Texas</li>'
                '<li role="option">Ohio</li></ul></main>')
        session, backend, page = self.session(html)
        item = next(c for c in session.snapshot['controls'] if c.get('role') == 'combobox')
        backend.choose_form_option(item, 'Texas')
        self.assertEqual(page.inner_text('[role=combobox]'), 'Texas')

    def test_page_without_a_body_can_be_read(self):
        context = self.browser.new_context(); page = context.new_page(); self.addCleanup(context.close)
        page.goto('data:image/svg+xml,' + '<svg xmlns="http://www.w3.org/2000/svg"><title>Logo</title><text x="5" y="20">Logo</text></svg>')
        self.assertTrue(page.evaluate('document.body===null'))
        backend = BrowserBackend(tempfile.mkdtemp(), 'chrome')
        backend.page = page; backend.context = context; backend.playwright = self.playwright
        WebSession(tempfile.mkdtemp(), backend)._refresh(read=False)


class PageLoadFailureTests(unittest.TestCase):
    """No internet, a wrong address and a slow site all said "The page did not open.
    Check the address and internet connection." Search said only that Google could
    not open, even when the guided browser itself could not start (October 31, 2026)."""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.session = WebSession(self.folder.name, FakeBackend())
        self.session.open('example.com')

    def failing(self, error):
        def goto(url): raise error
        self.session.backend.goto = goto
        self.session.backend.page = type('Page', (), {'url': 'https://example.com/', 'reload': lambda page, **_: goto('')})()

    def test_the_browser_reason_is_said_for_open_search_and_reload(self):
        from playwright.sync_api import Error, TimeoutError
        cases = (
            (Error('Page.goto: net::ERR_INTERNET_DISCONNECTED at https://va.gov/'), 'not be connected to the internet'),
            (Error('Page.goto: net::ERR_NAME_NOT_RESOLVED at https://va.gov/'), 'No website was found at that address'),
            (TimeoutError('Page.goto: Timeout 30000ms exceeded.'), 'took too long to open'),
            (Error('Page.goto: net::ERR_CONNECTION_REFUSED at https://va.gov/'), 'The website did not answer'),
            (Error('Page.goto: net::ERR_CERT_DATE_INVALID at https://va.gov/'), 'failed a security check'))
        for error, said in cases:
            with self.subTest(error=str(error)):
                self.failing(error)
                for result in (self.session.open('va.gov'), self.session.search('weather'), self.session.keyboard('Refresh')):
                    self.assertIn(said, result)
                    self.assertNotIn('net::', result)

    def test_an_unknown_failure_keeps_the_plain_message(self):
        self.failing(RuntimeError('Target closed'))
        self.assertEqual(self.session.open('va.gov'), 'The page did not open. Check the address and internet connection.')
        self.assertIn('Google could not open.', self.session.search('weather'))

    def test_search_says_why_the_guided_browser_could_not_start(self):
        from web_assistant import WebError
        self.failing(WebError('Embedded Edge could not start, and Chrome is not installed. Say set up Chrome to install Chrome.'))
        self.assertIn('Say set up Chrome', self.session.search('weather'))


class UntitledPageTests(unittest.TestCase):
    """A page with no title said "You are on , at example.com" and "the window
    titled . The site is"; a field with no name said "Focused , edit field"."""
    def test_no_empty_names_are_spoken(self):
        backend = FakeBackend()
        backend.snapshot = lambda: {'title': '', 'url': backend.url, 'text': '', 'controls': []}
        backend.focused_control = lambda: {'label': '', 'tag': 'input', 'type': 'text', 'value': ''}
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        session = WebSession(folder.name, backend)
        session.open('example.com')
        self.assertTrue(session.command('help').startswith('You are on example.com. '))
        self.assertIn(' window. The site is example.com.', session.command('ask for help'))
        self.assertEqual(session.command('where am i'), 'Focused Unlabeled, edit field. Current value blank.')

    def test_page_loaded_names_no_empty_title_and_one_period(self):
        for title, said in (('', 'Page loaded. Site: example.com.'), ('Welcome!', 'Page loaded: Welcome! Site: example.com.'),
                            ('News.', 'Page loaded: News. Site: example.com.')):
            with self.subTest(title=title):
                backend = FakeBackend()
                backend.snapshot = lambda: {'title': title, 'url': backend.url, 'controls': [], 'text': 'Some words.'}
                folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
                session = WebSession(folder.name, backend); session.auto_read = False
                self.assertEqual(session.open('example.com'), said)

    def test_a_page_answer_has_one_period_and_no_empty_title(self):
        backend = FakeBackend()
        backend.snapshot = lambda: {'title': '', 'url': backend.url, 'controls': [],
                                    'text': 'The clinic opens at nine in the morning. Parking is free for veterans.'}
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        session = WebSession(folder.name, backend)
        session.open('example.com')
        self.assertEqual(session.command('What does this page say about the clinic opening?'),
                         'This page says: The clinic opens at nine in the morning. Source: example.com. '
                         'This is an excerpt; check the page for the full context.')


if __name__ == '__main__':
    unittest.main()
