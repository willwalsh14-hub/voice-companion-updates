"""Back and Forward in a real browser: Alt+Left, Alt+Right, the menus and voice.

Two Chromium set-ups are used. The guided Chrome browser is started by the
automation driver, which turns the back/forward cache off. Embedded Edge
(WebView2) and a user's own browser keep that cache on: going back shows the
kept page at once, without the page load events the driver waits for. Before
this was handled, Back waited 30 seconds there and then said there was no
previous page, although the browser had gone back.
"""
import os
import tempfile
import time
import unittest

from web_assistant import BrowserBackend, WebSession

PAGES = {
    '/first': '<title>First page</title><h1>First heading</h1><p>Intro text.</p>'
              '<a href="https://history.test/second">Second page link</a><p>Text after the link.</p>'
              '<iframe title="Frame box" src="https://history.test/frame"></iframe>',
    '/second': '<title>Second page</title><h1>Second heading</h1><p>Second text.</p>'
               '<a href="https://history.test/third">Third page link</a>',
    '/third': '<title>Third page</title><h1>Third heading</h1>',
    '/frame': '<p>Words inside the frame.</p>',
    # A page whose ad frame moves to another address by itself. That adds a
    # history entry for the frame only; Back must not stop there.
    '/ads': '<title>Ad page</title><h1>Ad heading</h1>'
            '<iframe title="Advertisement" src="https://history.test/ad1"></iframe>',
    '/ad1': '<p>First ad</p><script>setTimeout(()=>{location.href="https://history.test/ad2"},50)</script>',
    '/ad2': '<p>Second ad</p>',
    '/jump': '<title>Jump page</title><h1>Jump heading</h1><a href="#part">Go to part two</a>'
             + '<p>Filler.</p>' * 40 + '<h2 id="part">Part two</h2><p>Part two text.</p>',
}


class HistoryTests:
    """Shared by every browser set-up below (and by embedded Edge on Windows).

    path names how Back finds the previous page: 'list' reads the browser's
    history list (Chrome, and embedded Edge through its host), 'navigation'
    uses the page's own navigation list, and 'steps' steps one entry at a time
    when neither is available."""
    keep_cache = False
    path = 'list'

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.driver = sync_playwright().start()
        except Exception as error:
            raise unittest.SkipTest('Playwright is unavailable: ' + str(error))
        options = {'headless': True}
        if cls.keep_cache:
            # The full Chromium build in its new headless mode keeps the cache,
            # as WebView2 and everyday browsers do.
            options.update(channel='chromium', ignore_default_args=['--disable-back-forward-cache'])
        try:
            cls.browser = cls.driver.chromium.launch(**options)
        except Exception as error:
            cls.driver.stop()
            if os.getenv('VOICE_COMPANION_REQUIRE_WEB_TESTS'):
                raise
            raise unittest.SkipTest('Chromium is unavailable: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.driver.stop()

    def new_page(self):
        context = getattr(self, 'persistent_context', None) or self.browser.new_context()
        if hasattr(self, 'persistent_context'):
            self.addCleanup(lambda: context.unroute_all(behavior='wait'))
        else:
            self.addCleanup(context.close)
        context.route('https://history.test/**', lambda route: route.fulfill(
            content_type='text/html', body=PAGES.get('/' + route.request.url.split('/', 3)[3].split('#')[0], '<p>Missing</p>')))
        page = context.new_page(); page.set_default_timeout(10000)
        if hasattr(self, 'persistent_context'):
            self.addCleanup(page.close)
        return context, page

    def setUp(self):
        self.context, self.page = self.new_page()
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context
        self.backend.start = lambda: None
        if self.path != 'list':
            self.backend.CDP_HISTORY = False
        if self.path == 'steps':
            from unittest import mock
            import web_assistant
            patcher = mock.patch.object(web_assistant, 'NAVIGATION_JUMP', '()=>null')
            patcher.start(); self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.spoken = []
        self.session = WebSession(self.temp.name, self.backend, progress=self.spoken.append)
        self.session.auto_read = False

    def follow(self, label):
        item = next(e for e in self.session.browse.elements if e.get('label') == label)
        self.session.browse.index = self.session.browse.elements.index(item)
        self.session.reading.position = self.session.browse.position = item['start']
        return self.session.keyboard('Activate')

    def timed(self, action):
        started = time.monotonic(); result = action()
        return result, time.monotonic() - started

    def test_alt_left_returns_to_the_link_that_was_followed(self):
        self.assertIn('First page', self.session.open('https://history.test/first'))
        self.assertIn('Second page', self.follow('Second page link'))
        result, seconds = self.timed(lambda: self.session.keyboard('Back'))
        self.assertLess(seconds, 5, result)
        self.assertTrue(result.startswith('Page loaded: First page.'), result)
        self.assertEqual(self.page.url, 'https://history.test/first')
        # The reading place is the link that was followed, and it is said.
        self.assertEqual(self.session.browse.current()['label'], 'Second page link')
        self.assertIn('Second page link', result)
        self.assertEqual(self.session.reading.position, self.session.browse.current()['start'])
        # The page is read from the right document, frames included.
        self.assertIn('Intro text.', self.session.reading.text)
        self.assertNotIn('Second heading', self.session.reading.text)
        self.assertIn('Words inside the frame.', self.session.reading.text)
        self.assertEqual(self.spoken[-1], 'Going back.')

    def test_forward_and_both_ends_of_history(self):
        self.page.goto('about:blank')
        self.session.open('https://history.test/first')
        # The browser's blank start page is not a page to go back to.
        result, seconds = self.timed(lambda: self.session.keyboard('Back'))
        self.assertLess(seconds, 5, result)
        self.assertEqual(result, 'There is no previous page.')
        self.assertEqual(self.page.url, 'https://history.test/first')
        self.assertEqual(self.session.snapshot['url'], 'https://history.test/first')
        self.follow('Second page link')
        self.assertEqual(self.session.keyboard('Forward'), 'There is no next page.')
        self.assertIn('First page', self.session.command('go back'))
        result = self.session.keyboard('Forward')
        self.assertTrue(result.startswith('Page loaded: Second page.'), result)
        self.assertEqual(self.page.url, 'https://history.test/second')
        self.assertEqual(self.spoken[-1], 'Going forward.')
        # Voice: "back" with a recognizer's punctuation.
        self.assertIn('First page', self.session.command('Back.'))
        self.assertIn('Second page', self.session.command('Go forward.'))

    def test_back_skips_history_entries_made_only_by_an_ad_frame(self):
        self.session.open('https://history.test/first')
        self.follow('Second page link')
        self.session.open('https://history.test/ads')
        deadline = time.monotonic() + 5
        while 'ad2' not in ' '.join(f.url for f in self.page.frames) and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        result, seconds = self.timed(lambda: self.session.command('go back'))
        self.assertLess(seconds, 8, result)
        self.assertTrue(result.startswith('Page loaded: Second page.'), result)
        self.assertEqual(self.page.url, 'https://history.test/second')

    def test_back_works_from_a_links_list_and_the_browser_menu(self):
        self.session.open('https://history.test/first')
        self.follow('Second page link')
        self.session.command('list links')
        self.assertEqual(self.session.list_focus, 'links')
        # Alt+Left goes back even while the links list is open.
        self.assertIn('First page', self.session.keyboard('Back'))
        self.assertIsNone(self.session.list_focus)
        self.follow('Second page link')
        import companion
        from unittest import mock
        notices = []
        with mock.patch.object(companion, 'web_session', self.session), \
                mock.patch.object(companion, 'speak', notices.append), \
                mock.patch.object(companion, 'speak_status', notices.append):
            self.session.menu.start()
            self.assertEqual(companion.handle_browser_menu('go back'), 'web')
        self.assertTrue(any(str(n).startswith('Page loaded: First page.') for n in notices), notices)
        self.assertEqual(self.page.url, 'https://history.test/first')

    def test_back_after_a_jump_within_the_page_returns_to_the_jump_link(self):
        self.session.open('https://history.test/jump')
        self.follow('Go to part two')
        self.assertEqual(self.session.reading.position, self.session.reading.text.index('Part two'))
        result = self.session.keyboard('Back')
        self.assertEqual(self.page.url, 'https://history.test/jump')
        self.assertEqual(self.session.browse.current()['label'], 'Go to part two', result)
        self.assertIn('Go to part two', result)


class GuidedChromeHistoryTests(HistoryTests, unittest.TestCase):
    """Chromium as the guided Chrome browser runs it: no back/forward cache."""


class CachedPageHistoryTests(HistoryTests, unittest.TestCase):
    """Chromium with the back/forward cache, like everyday browsers."""
    keep_cache = True


class NavigationListHistoryTests(HistoryTests, unittest.TestCase):
    """Without the browser's history list: the page's own navigation list."""
    keep_cache = True
    path = 'navigation'


class SteppedHistoryTests(HistoryTests, unittest.TestCase):
    """Neither list: one history entry at a time, the last resort."""
    path = 'steps'

    def test_back_skips_history_entries_made_only_by_an_ad_frame(self):
        # One step at a time can skip an ad that moved once, but not an ad
        # that moves again each time it is shown. Back must still end quickly.
        self.session.open('https://history.test/first')
        self.follow('Second page link')
        self.session.open('https://history.test/ads')
        result, seconds = self.timed(lambda: self.session.command('go back'))
        self.assertLess(seconds, 15, result)
        self.assertTrue(result.startswith(('Page loaded: Second page.', 'There is no previous page.')), result)


class EmbeddedEdgeHistoryListTests(unittest.TestCase):
    """Embedded Edge asks its host for the history list (no second protocol session)."""

    class Host:
        def __init__(self, entries, index):
            self.history = {'entries': entries, 'currentIndex': index}
            self.calls = []; self.page_order = ['main-view']; self.active_session = 'main-view'
            self.closed = __import__('threading').Event()
        def call(self, method, params=None, session=None, timeout=5):
            self.calls.append((method, params, session))
            return self.history if method == 'Page.getNavigationHistory' else {}

    def backend(self, entries, index):
        from unittest import mock
        from webview2_backend import WebView2Backend
        backend = WebView2Backend(tempfile.gettempdir())
        backend.host = self.Host(entries, index)
        backend.page = mock.Mock(); backend.context = mock.Mock(pages=[backend.page])
        return backend

    def test_frame_entries_and_the_blank_start_page_are_skipped(self):
        entries = [{'id': 1, 'url': 'about:blank'}, {'id': 2, 'url': 'https://a.test/'},
                   {'id': 3, 'url': 'https://b.test/'}, {'id': 4, 'url': 'https://b.test/'}]
        backend = self.backend(entries, 3)
        self.assertEqual(backend._history_jump(-1), 'started')
        self.assertEqual(backend.host.calls[-1], ('Page.navigateToHistoryEntry', {'entryId': 2}, 'main-view'))
        backend = self.backend(entries, 1)
        self.assertEqual(backend._history_jump(-1), 'none')
        self.assertEqual(backend._history_jump(1), 'started')
        self.assertEqual(backend.host.calls[-1][1], {'entryId': 3})
        self.assertEqual(self.backend(entries, 3)._history_jump(1), 'none')
        backend.page.evaluate.assert_not_called()

    def test_without_a_host_answer_the_page_navigation_list_is_used(self):
        backend = self.backend([], 0)
        def refuse(*args, **kwargs):
            raise RuntimeError('no answer')
        backend.host.call = refuse
        backend.page.evaluate.return_value = 'started'
        self.assertEqual(backend._history_jump(-1), 'started')
        backend.page.evaluate.return_value = None
        self.assertIsNone(backend._history_jump(-1))


if __name__ == '__main__':
    unittest.main()
