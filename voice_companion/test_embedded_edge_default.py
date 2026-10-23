"""Embedded Edge (WebView2) is the default guided browser, with safe fallbacks."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import web_assistant
from test_web_assistant import FakeBackend
from web_assistant import GOOGLE_SIGN_IN_NOTICE, WebError, WebSession


class DefaultBrowserTests(unittest.TestCase):
    def test_new_users_get_embedded_edge(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(WebSession(Path(folder), FakeBackend()).browser, 'webview2')

    def test_a_saved_choice_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'browser-choice.txt').write_text('chrome', encoding='utf-8')
            session = WebSession(Path(folder), FakeBackend())
            self.assertEqual(session.browser, 'chrome')
            self.assertEqual(session.take_browser_notice(), '')

    def test_a_removed_browser_becomes_embedded_edge_and_is_said_once(self):
        for old, name in (('firefox', 'Firefox'), ('edge', 'Edge'), ('brave', 'Brave'), ('Firefox\n', 'Firefox')):
            with self.subTest(old=old), tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'browser-choice.txt').write_text(old, encoding='utf-8')
                # Options shows embedded Edge without using up the notice.
                self.assertEqual(web_assistant.browser_setting(folder), 'embedded Edge')
                session = WebSession(Path(folder), FakeBackend())
                self.assertEqual(session.browser, 'webview2')
                self.assertEqual((Path(folder) / 'browser-choice.txt').read_text(encoding='utf-8'), 'webview2')
                said = session._refresh(read=False)
                self.assertTrue(said.startswith('Your guided browser was ' + name + '. Guided websites now use embedded Edge or Chrome'), said)
                self.assertIn('say use Chrome', said)
                self.assertNotIn('Your guided browser', session._refresh(read=False))
                # A new session (the next start of Companion) says nothing more.
                self.assertEqual(WebSession(Path(folder), FakeBackend()).take_browser_notice(), '')

    def test_the_web_browser_menu_gives_the_notice(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'browser-choice.txt').write_text('brave', encoding='utf-8')
            session = WebSession(Path(folder), FakeBackend())
            said = []
            with mock.patch.object(companion, 'web_session', session), mock.patch.object(companion, 'speak', said.append), \
                    mock.patch('browser_cdp_relay.prepare'), mock.patch('browser_cdp_relay.current', return_value=None):
                self.assertEqual(companion.open_browser_menu(), 'browser_menu')
            self.assertTrue(said[-1].startswith('Your guided browser was Brave.'), said)
            self.assertIn('Web browser menu', said[-1])


class BrowserChoiceTests(unittest.TestCase):
    """Only embedded Edge and Chrome are guided browsers; the others are explained."""
    def session(self, folder):
        session = WebSession(Path(folder), FakeBackend())
        session.injected_backend = False
        return session

    def test_use_chrome_and_use_embedded_edge(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder)
            for spoken, stored, name in (('Use Chrome.', 'chrome', 'Chrome'), ('switch to embedded Edge', 'webview2', 'embedded Edge'),
                                         ('use Google Chrome', 'chrome', 'Chrome'), ('Use WebView2.', 'webview2', 'embedded Edge')):
                with self.subTest(spoken=spoken):
                    self.assertTrue(session.command(spoken).startswith('Selected ' + name + '.'))
                    self.assertEqual((Path(folder) / 'browser-choice.txt').read_text(encoding='utf-8'), stored)

    def test_removed_browsers_are_explained_and_change_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder)
            session.command('use chrome')
            for spoken, name in (('Use Firefox.', 'Firefox'), ('use edge', 'Edge'), ('Switch to Brave browser', 'Brave'), ('use Microsoft Edge', 'Edge')):
                with self.subTest(spoken=spoken):
                    said = session.command(spoken)
                    self.assertTrue(said.startswith(name + ' is no longer a guided browser.'), said)
                    self.assertIn('Say use embedded Edge, or say use Chrome.', said)
                    self.assertEqual((Path(folder) / 'browser-choice.txt').read_text(encoding='utf-8'), 'chrome')
            self.assertIn('Voice Companion extension', session.command('use edge'))
            self.assertNotIn('extension', session.command('use firefox'))

    def test_which_browser_names_the_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            said = self.session(folder).command('which browser')
            self.assertEqual(said, 'Guided websites use embedded Edge. Say use Chrome or use embedded Edge to change it.')

    def test_options_lists_only_embedded_edge_and_chrome(self):
        from settings_model import SettingsSession, fields
        field = next(f for f in fields('Web browser', {}) if f.key == 'browser')
        self.assertEqual(field.choices, ('embedded Edge', 'Chrome'))
        settings = SettingsSession({'browser': 'embedded Edge'}, {})
        for value, shown in (('chrome', 'Chrome'), ('webview2', 'embedded Edge'), ('firefox', 'embedded Edge')):
            settings.set('browser', value); self.assertEqual(settings.values['browser'], shown)
        self.assertEqual(web_assistant.browser_key('embedded Edge'), 'webview2')
        self.assertEqual(web_assistant.browser_key('Chrome'), 'chrome')

    def test_an_unknown_saved_choice_falls_back_to_embedded_edge(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'browser-choice.txt').write_text('netscape', encoding='utf-8')
            self.assertEqual(WebSession(Path(folder), FakeBackend()).browser, 'webview2')


class LinkedExtensionTests(unittest.TestCase):
    """A tab linked through the browser extension is the user's own choice."""
    def adopt(self, saved=None):
        relay = mock.Mock(browser='chrome'); relay.connected.is_set.return_value = True
        with tempfile.TemporaryDirectory() as folder:
            if saved: (Path(folder) / 'browser-choice.txt').write_text(saved, encoding='utf-8')
            embedded = FakeBackend(); embedded.browser = 'webview2'; embedded.close = mock.Mock()
            session = WebSession(Path(folder), embedded)
            session.injected_backend = False
            created = []
            class Connected:
                regular = True
                def __init__(self, profile, browser, relay):
                    self.browser = relay.browser; self.started = 0; created.append(self)
                def start(self): self.started += 1
            with mock.patch('browser_cdp_relay.current', return_value=relay), \
                 mock.patch('connected_browser.ConnectedBrowserBackend', Connected):
                session._refresh = lambda read=None: ''
                return session.adopt_connected_browser(), created, session

    def test_default_embedded_edge_still_uses_a_linked_extension_tab(self):
        adopted, created, session = self.adopt()
        self.assertTrue(adopted)
        self.assertEqual([backend.started for backend in created], [1])
        self.assertEqual(session.browser, 'chrome')
        self.assertIs(session.backend, created[0])

    def test_chosen_embedded_edge_ignores_the_extension(self):
        adopted, created, session = self.adopt('webview2')
        self.assertFalse(adopted)
        self.assertEqual(created, [])

    def test_no_linked_tab_keeps_embedded_edge(self):
        with tempfile.TemporaryDirectory() as folder:
            session = WebSession(Path(folder), FakeBackend()); session.injected_backend = False
            with mock.patch('browser_cdp_relay.current', return_value=None):
                self.assertFalse(session.adopt_connected_browser())
            self.assertEqual(session.browser, 'webview2')


class FallbackTests(unittest.TestCase):
    class Embedded(FakeBackend):
        browser = 'webview2'
        def goto(self, url): raise WebError('Embedded Edge could not connect.')
        def close(self): self.closed = True

    def session(self, folder):
        session = WebSession(Path(folder), FakeBackend())
        session.injected_backend = False
        session.backend = self.Embedded()
        return session

    def test_embedded_edge_that_cannot_start_falls_back_to_chrome(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder)
            chrome = FakeBackend(); chrome.browser = 'chrome'
            with mock.patch.object(web_assistant, 'BrowserBackend', return_value=chrome) as created:
                notice = session._navigate('https://example.com/')
            created.assert_called_once_with(Path(folder) / 'Browser Profile chrome', 'chrome')
            self.assertEqual(notice, 'Embedded Edge could not start. Using Chrome for now. Ask your helper to check the Microsoft WebView2 component. ')
            self.assertIs(session.backend, chrome)
            self.assertEqual(session.browser, 'webview2')  # the saved choice is kept

    def test_without_chrome_it_says_plainly_what_to_do(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder)
            missing = FakeBackend(); missing.browser = 'chrome'; missing.close = mock.Mock()
            missing.goto = mock.Mock(side_effect=WebError('The guided Chrome browser could not start.'))
            with mock.patch.object(web_assistant, 'BrowserBackend', return_value=missing):
                said = session.open('example.com')
            self.assertEqual(said, 'Embedded Edge could not start, and Chrome is not installed. Say set up Chrome to install Chrome. '
                                   'Or ask your helper to check the Microsoft WebView2 component.')
            self.assertFalse(session.navigation_succeeded)
            missing.close.assert_called_once()

    def test_a_linked_regular_browser_is_never_used_as_the_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder)
            chrome = FakeBackend(); chrome.browser = 'chrome'
            with mock.patch('browser_cdp_relay.current', return_value=mock.Mock(browser='edge')), \
                    mock.patch.object(web_assistant, 'BrowserBackend', return_value=chrome):
                session._navigate('https://example.com/')
            self.assertIs(session.backend, chrome)


class GoogleSignInTests(unittest.TestCase):
    def summary(self, browser, url):
        backend = FakeBackend(); backend.browser = browser; backend.url = url
        with tempfile.TemporaryDirectory() as folder:
            return WebSession(Path(folder), backend)._refresh(read=False)

    def test_google_sign_in_in_embedded_edge_is_explained(self):
        self.assertTrue(self.summary('webview2', 'https://accounts.google.com/signin').startswith(GOOGLE_SIGN_IN_NOTICE))

    def test_other_pages_and_browsers_are_unchanged(self):
        self.assertTrue(self.summary('webview2', 'https://www.va.gov/').startswith('Page loaded'))
        self.assertTrue(self.summary('chrome', 'https://accounts.google.com/signin').startswith('Page loaded'))

    def test_the_notice_points_to_chrome(self):
        self.assertIn('say use Chrome', GOOGLE_SIGN_IN_NOTICE)
        self.assertNotIn('Firefox', GOOGLE_SIGN_IN_NOTICE)



class EmbeddedStartRetryTests(unittest.TestCase):
    """A slow first start must not send the user to Chrome, and a real failure
    must keep its reason for the logs."""
    def backend(self, outcomes):
        import webview2_backend
        attempts = []
        class Pipe:
            def __init__(self, folder, headless, executable):
                attempts.append(1)
                outcome = outcomes[len(attempts) - 1]
                if isinstance(outcome, Exception): raise outcome
                self.endpoint = 'ws://127.0.0.1:1/token'; self.closed = mock.Mock(is_set=lambda: False)
            def close(self): pass
        page = mock.Mock(is_closed=lambda: False)
        driver = mock.Mock(); driver.chromium.connect_over_cdp.return_value = mock.Mock(contexts=[mock.Mock(pages=[page])])
        folder = tempfile.mkdtemp()
        backend = webview2_backend.WebView2Backend(Path(folder), headless=True, driver=driver)
        patcher = mock.patch.object(webview2_backend, 'WebViewPipe', Pipe); patcher.start(); self.addCleanup(patcher.stop)
        return backend, attempts, page

    def test_second_try_succeeds_after_a_slow_first_start(self):
        backend, attempts, page = self.backend([WebError('Embedded Edge could not start.'), 'ok'])
        backend.start()
        self.assertEqual(len(attempts), 2)
        self.assertIs(backend.page, page)
        self.assertIsNone(backend.start_failure)

    def test_real_failure_keeps_its_reason(self):
        backend, attempts, _ = self.backend([TimeoutError('connect timed out'), TimeoutError('connect timed out again')])
        with self.assertRaises(WebError) as raised:
            backend.start()
        self.assertEqual(len(attempts), 2)
        self.assertIn('connect timed out again', backend.start_failure)
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)
        self.assertIn('Embedded Edge could not connect', str(raised.exception))

if __name__ == '__main__':
    unittest.main()
