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
            (Path(folder) / 'browser-choice.txt').write_text('firefox', encoding='utf-8')
            self.assertEqual(WebSession(Path(folder), FakeBackend()).browser, 'firefox')

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
    def test_embedded_edge_that_cannot_start_falls_back_to_firefox(self):
        class Embedded(FakeBackend):
            browser = 'webview2'
            def goto(self, url): raise WebError('Embedded Edge could not connect.')
            def close(self): self.closed = True

        with tempfile.TemporaryDirectory() as folder:
            session = WebSession(Path(folder), FakeBackend())
            session.injected_backend = False
            session.backend = Embedded()
            firefox = FakeBackend(); firefox.browser = 'firefox'
            with mock.patch.object(web_assistant, 'BrowserBackend', return_value=firefox) as created:
                notice = session._navigate('https://example.com/')
            created.assert_called_once_with(Path(folder) / 'Browser Profile firefox', 'firefox')
            self.assertIn('Embedded Edge could not start', notice)
            self.assertIs(session.backend, firefox)
            self.assertEqual(session.browser, 'webview2')  # the saved choice is kept


class GoogleSignInTests(unittest.TestCase):
    def summary(self, browser, url):
        backend = FakeBackend(); backend.browser = browser; backend.url = url
        with tempfile.TemporaryDirectory() as folder:
            return WebSession(Path(folder), backend)._refresh(read=False)

    def test_google_sign_in_in_embedded_edge_is_explained(self):
        self.assertTrue(self.summary('webview2', 'https://accounts.google.com/signin').startswith(GOOGLE_SIGN_IN_NOTICE))

    def test_other_pages_and_browsers_are_unchanged(self):
        self.assertTrue(self.summary('webview2', 'https://www.va.gov/').startswith('Page loaded'))
        self.assertTrue(self.summary('firefox', 'https://accounts.google.com/signin').startswith('Page loaded'))



class FindBraveTests(unittest.TestCase):
    def test_usual_folders_then_the_path_windows_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / 'Local' / 'BraveSoftware/Brave-Browser/Application/brave.exe'
            other = root / 'Other' / 'brave.exe'
            env = {'PROGRAMFILES': str(root / 'PF'), 'LOCALAPPDATA': str(root / 'Local')}
            self.assertIsNone(web_assistant.find_brave(env, lambda name: None))
            other.parent.mkdir(parents=True); other.write_text('x')
            self.assertEqual(web_assistant.find_brave(env, lambda name: '"' + str(other) + '"'), other)
            local.parent.mkdir(parents=True); local.write_text('x')
            self.assertEqual(web_assistant.find_brave(env, lambda name: str(other)), local)

if __name__ == '__main__':
    unittest.main()
