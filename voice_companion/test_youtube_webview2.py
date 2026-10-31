"""YouTube in the real embedded Edge (WebView2) on Windows.

The same saved youtube.com-shaped pages and the same checks as
test_youtube_live.py, run in Companion's own WebView2 host instead of
Chromium: reading lists, the player, ads, account questions, and a video in
a second tab (Target.createTarget in webview2_host.cs). One more check uses
the real WebView2 backend as the web session's browser, as the app does.
"""
import os
import tempfile
import unittest
from pathlib import Path

import test_youtube_live as base   # a module, so its Chromium classes do not run here
import youtube_fixtures as fixtures


class EmbeddedEdge:
    """One WebView2 host for the class. Pages come from its own browser context."""
    @classmethod
    def setUpClass(cls):
        host = Path(os.getenv('VOICE_COMPANION_WEBVIEW2_HOST', str(Path(__file__).parent / 'webview2' / 'VoiceCompanion-WebView2.exe')))
        if os.name != 'nt' or not host.is_file():
            if os.getenv('VOICE_COMPANION_REQUIRE_WEBVIEW2'):
                raise RuntimeError('Build the WebView2 host before testing.')
            raise unittest.SkipTest('Windows WebView2 host is not built.')
        from webview2_backend import WebView2Backend
        cls.profile = tempfile.TemporaryDirectory(prefix='Companion-YouTube-WebView2-', ignore_cleanup_errors=True)
        cls.adapter = WebView2Backend(cls.profile.name, True, host.resolve())
        cls.adapter.start()

    @classmethod
    def tearDownClass(cls):
        cls.adapter.close(); cls.profile.cleanup()

    def open(self, url='https://www.youtube.com/', overrides=None):
        self.context = self.adapter.context
        self.context.unroute('https://www.youtube.com/**')
        self.served = fixtures.routes(self.context, overrides)
        self.page = self.adapter.page
        first = self.page

        def close_other_tabs():
            for page in list(self.context.pages):
                if page is not first:
                    try: page.close()
                    except Exception: pass
        self.addCleanup(close_other_tabs)
        self.page.set_default_timeout(10000)
        self.page.goto(url, wait_until='domcontentloaded')
        from youtube_page import YouTubePage
        self.yt = YouTubePage(lambda: self.page)
        self.yt.wait_ready(8000)
        return self.yt


class EmbeddedListTests(EmbeddedEdge, base.ListReadingTests):
    def test_a_closed_page_is_a_plain_message(self):
        self.skipTest('The embedded Edge window keeps its first tab.')


class EmbeddedPlayerTests(EmbeddedEdge, base.PlayerTests):
    pass


class EmbeddedScreenTests(EmbeddedEdge, base.ScreenTests):
    def test_the_main_menu_closes_the_browser_and_youtube_starts_again(self):
        self.skipTest('It closes every tab; the class shares one embedded Edge window. Chromium runs it.')

    def test_the_real_embedded_edge_backend_plays_in_a_second_tab_and_asks_before_account_changes(self):
        # The web session uses the WebView2 backend itself, as in the app.
        self.web.backend = self.adapter
        self.assertEqual(self.say('YouTube.', 'awake'), 'youtube')
        self.assertEqual(self.last(), 'YouTube menu. Search (S), 1 of 9. Control+F.')
        self.press('Down'); self.press('Enter')
        self.assertTrue(self.last().startswith('Home. 5 items.'), self.last())
        self.press('Enter')
        self.assertEqual(self.last(), 'Playing Never Gonna Give You Up.')
        screen = self.ys.SCREEN
        self.assertIsNot(screen.video_page, screen.list_page, 'The video should have its own tab.')
        self.press('Escape')   # from the player back to the list (since 0.2.181)
        self.assertTrue(self.last().startswith('Back to Home.'), self.last())
        self.press('Down')
        self.assertTrue(self.last().startswith('How to bake bread'), self.last())
        self.press('Space')
        self.assertEqual(self.last(), 'Paused.')
        self.press('T')
        self.assertTrue(self.last().startswith('Google does not allow signing in inside embedded Edge'), self.last())
        self.say('No')
        self.assertEqual(self.say('Open in browser'), 'web')
        self.assertIs(self.adapter.page, screen.list_page)


if __name__ == '__main__':
    unittest.main()
