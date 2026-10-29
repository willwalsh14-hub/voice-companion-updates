"""Guided Chrome with or without its window (Options, Web browser, "Guided Chrome window").

Will, October 2026: "Show the browser window, as before" is the default and must
start Chrome exactly as before. "Run the browser in the background" starts it
with no window, and a page that needs a person (Google sign-in, a check that you
are a person, a payment) brings a real window, on the same page and place.

Chrome itself is faked at Playwright's edge; the session and backend are real.
"""
import tempfile
import unittest
from unittest import mock

import web_assistant
from web_assistant import BrowserBackend, WebSession, window_need


class FakePage:
    def __init__(self, browser):
        self.browser = browser; self.url = 'about:blank'; self.fronted = 0; self.closed = False
    def is_closed(self): return self.closed
    def set_default_timeout(self, value): pass
    def evaluate(self, script, *args):
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36' \
            if self.browser.kwargs.get('headless') else 'Mozilla/5.0 Chrome/141.0.0.0'
    def bring_to_front(self): self.fronted += 1


class FakeContext:
    def __init__(self, kwargs):
        self.kwargs = kwargs; self.headers = None; self.scripts = []
        self.pages = [FakePage(self)]
    def new_page(self): return self.pages[0]
    def set_extra_http_headers(self, headers): self.headers = headers
    def add_init_script(self, script): self.scripts.append(script)
    def close(self): self.pages[0].closed = True


class FakeDriver:
    def __init__(self): self.launches = []; self.chromium = self
    def launch_persistent_context(self, folder, **kwargs):
        self.launches.append(kwargs); return FakeContext(kwargs)
    def stop(self): pass


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.driver = FakeDriver()
        patcher = mock.patch('shared_playwright.start', lambda folder=None: self.driver)
        patcher.start(); self.addCleanup(patcher.stop)

    def launch(self, background):
        with mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', background):
            backend = BrowserBackend(self.temp.name, 'chrome'); backend.start()
        return backend

    def test_default_starts_chrome_exactly_as_before(self):
        backend = self.launch(False)
        self.assertEqual(self.driver.launches, [{'headless': False, 'accept_downloads': False, 'channel': 'chrome'}])
        self.assertFalse(backend.launched_hidden)
        self.assertIsNone(backend.context.headers)
        self.assertEqual(backend.context.scripts, [])

    def test_background_starts_headless_with_sound_and_its_usual_name(self):
        backend = self.launch(True)
        self.assertEqual(self.driver.launches, [{'headless': True, 'accept_downloads': False, 'channel': 'chrome',
                                                 'ignore_default_args': ['--mute-audio']}])
        self.assertTrue(backend.launched_hidden)
        self.assertNotIn('Headless', backend.context.headers['User-Agent'])
        self.assertIn('Chrome/141', backend.context.headers['User-Agent'])
        self.assertEqual(len(backend.context.scripts), 1)

    def test_a_shown_reason_starts_with_a_window_even_in_the_background(self):
        with mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', True):
            backend = BrowserBackend(self.temp.name, 'chrome'); backend.window_reason = 'sign-in'; backend.start()
        self.assertEqual(self.driver.launches[-1]['headless'], False)

    def test_only_guided_chrome_runs_in_the_background(self):
        with mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', True):
            regular = BrowserBackend(self.temp.name, 'chrome'); regular.regular = True
            self.assertFalse(regular.runs_hidden())
            self.assertTrue(BrowserBackend(self.temp.name, 'chrome').runs_hidden())


class Guided(BrowserBackend):
    """A guided Chrome whose pages are made up, so the session's switching can be followed."""
    def __init__(self, folder, pages):
        super().__init__(folder, 'chrome')
        self.pages, self.url, self.starts, self.closes, self.fronted = pages, '', [], 0, 0
        self.launched_hidden = None
    def start(self):
        if self.page is None:
            self.launched_hidden = self.runs_hidden(); self.starts.append(self.launched_hidden); self.page = object()
    def close(self):
        self.closes += 1; self.page = None
    def goto(self, url):
        self.start(); self.url = url
    def snapshot(self):
        return dict(self.pages[self.url], url=self.url)
    def bring_window_forward(self):
        self.fronted += 1; return True
    def wait_ready(self): self.page_ready = True
    def document_stamp(self): return None
    def settle(self): pass
    def refocus(self): return self.page is not None


ARTICLE = {'title': 'News', 'text': 'Top story. Second story. Third story.', 'controls': []}
GOOGLE = {'title': 'Sign in - Google Accounts', 'text': 'Sign in. Use your Google Account.', 'controls': []}
CHECK = {'title': 'Just a moment', 'text': 'Checking your browser.', 'controls': [], 'challenge': True}
PAYMENT = {'title': 'Checkout', 'text': 'Pay now.', 'controls': [
    {'key': '1', 'role': 'field', 'label': 'Card number', 'type': 'text', 'tag': 'input', 'href': '', 'autocomplete': 'cc-number'}]}


class SwitchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.backend = Guided(self.temp.name, {'https://news.example/': ARTICLE, 'https://accounts.google.com/signin': GOOGLE,
                                                'https://shop.example/check': CHECK, 'https://shop.example/pay': PAYMENT})
        self.said = []
        self.session = WebSession(self.temp.name, self.backend, progress=self.said.append)
        self.session.auto_read = False

    def test_ordinary_page_stays_in_the_background(self):
        result = self.session.open('https://news.example/')
        self.assertEqual(self.backend.starts, [True])
        self.assertIn('Page', result)
        self.assertEqual(self.backend.fronted, 0)

    def test_google_sign_in_reopens_with_a_window_in_front(self):
        result = self.session.open('https://accounts.google.com/signin')
        self.assertEqual(self.backend.starts, [True, False])
        self.assertEqual(self.backend.url, 'https://accounts.google.com/signin')
        self.assertTrue(result.startswith(web_assistant.WINDOW_SHOWN['sign-in'] + web_assistant.WINDOW_BACK))
        self.assertEqual(self.backend.fronted, 1)
        self.assertEqual(self.said, [web_assistant.WINDOW_SWITCHING])
        # It stays shown on the next pages, until asked.
        self.session.open('https://news.example/')
        self.assertEqual(self.backend.starts, [True, False])

    def test_person_check_and_payment_show_the_window(self):
        self.assertTrue(self.session.open('https://shop.example/check').startswith(web_assistant.WINDOW_SHOWN['check']))
        self.assertEqual(window_need(dict(PAYMENT, url='https://shop.example/pay')), 'payment')
        self.assertIsNone(window_need(dict(ARTICLE, url='https://news.example/')))

    def test_show_and_hide_keep_the_page_and_reading_place(self):
        self.session.open('https://news.example/')
        self.session.reading.position = 11
        self.session.browse.position = 11
        result = self.session.browser_window(True)
        self.assertTrue(result.startswith(web_assistant.WINDOW_SHOWN['asked']))
        self.assertEqual(self.backend.starts, [True, False])
        self.assertEqual(self.backend.url, 'https://news.example/')
        self.assertEqual(self.session.browser_window(True), web_assistant.WINDOW_ALREADY_SHOWN)
        result = self.session.browser_window(False)
        self.assertTrue(result.startswith(web_assistant.WINDOW_HIDDEN))
        self.assertEqual(self.backend.starts, [True, False, True])
        self.assertEqual(self.backend.url, 'https://news.example/')
        self.assertEqual(self.session.browser_window(False), web_assistant.WINDOW_ALREADY_HIDDEN)

    def test_hide_refused_while_the_page_still_needs_the_window(self):
        self.session.open('https://accounts.google.com/signin')
        self.assertEqual(self.session.browser_window(False), web_assistant.WINDOW_STILL_NEEDED)

    def test_as_before_setting_explains_the_option(self):
        with mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', False):
            self.session.open('https://accounts.google.com/signin')
            self.assertEqual(self.backend.starts, [False])
            self.assertEqual(self.session.browser_window(False), web_assistant.WINDOW_AS_BEFORE)
            self.assertEqual(self.session.browser_window(True), web_assistant.WINDOW_ALREADY_SHOWN)

    def test_leaving_the_web_returns_to_the_background_next_time(self):
        self.session.open('https://accounts.google.com/signin')
        self.session.leave(False)
        self.assertIsNone(self.backend.window_reason)

    def test_option_change_reopens_an_open_chrome_on_the_same_page(self):
        with mock.patch.object(web_assistant, 'CHROME_IN_BACKGROUND', False):
            self.session.open('https://news.example/')
        self.assertEqual(self.backend.starts, [False])
        self.assertEqual(self.session.window_option_changed(), 'Chrome opened again the new way, on the same page.')
        self.assertEqual(self.backend.starts, [False, True])
        self.assertEqual(self.session.window_option_changed(), '')

    def test_embedded_edge_is_not_affected(self):
        class Edge(Guided):
            def guided_chrome(self): return False
        session = WebSession(self.temp.name, Edge(self.temp.name, {}))
        self.assertEqual(session.browser_window(False), web_assistant.WINDOW_NOT_CHROME)


if __name__ == '__main__':
    unittest.main()
