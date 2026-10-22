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


if __name__ == '__main__':
    unittest.main()
