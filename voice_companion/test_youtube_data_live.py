"""YouTube lists from the page's own data, in real Chromium (Will, October 29, 2026).

"No results past a few at a time ... the Home tab is only displaying a few items, video
history is showing things out of order, says sign in when I'm already signed in ...
subscriptions only gets a few channels when I should get all of them."

The saved pages here are shaped like youtube.com now: the list is in ytInitialData,
and the drawn page is empty, as in a browser that draws little in the background.
The next part of a list is asked for with YouTube's continuation token, and answered
by the fixture as youtube.com answers. Nothing reaches the internet.
"""
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

import youtube_fixtures as fixtures
from youtube_page import YouTubePage, spoken_item


class DataPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        if not Path(cls.driver.chromium.executable_path).is_file():
            cls.driver.stop(); raise unittest.SkipTest('Install the Chromium runtime for live DOM checks.')
        cls.browser = cls.driver.chromium.launch(headless=True, timeout=15000)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.driver.stop()

    def open(self, url='https://www.youtube.com/', signed_in=True, pages=None):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.add_cookies([{'name': 'SAPISID', 'value': 'abc123/xyz', 'domain': '.youtube.com', 'path': '/', 'secure': True}])
        self.asked = fixtures.data_routes(self.context, signed_in, pages)
        self.page = self.context.new_page(); self.page.set_default_timeout(5000)
        self.page.goto(url, wait_until='domcontentloaded')
        self.yt = YouTubePage(lambda: self.page)
        self.yt.wait_ready(5000)
        return self.yt


class ReaderTests(DataPages):
    def test_home_is_read_from_the_data_with_shorts_and_ads_and_a_token(self):
        items, facts = self.open().read_list()
        self.assertEqual(facts['source'], 'data')
        self.assertIs(facts['signedIn'], True)
        self.assertEqual(facts['token'], 'home-2')
        said = [spoken_item(i) for i in items]
        self.assertEqual(said[0], 'Home video 1. Channel 1. 1 minute 1 second. 10 views. 1 day ago')
        self.assertEqual(said[3], 'Short. Dog surfs. 3 million views')
        self.assertEqual(said[4], 'Ad: Try Brand Tea')
        self.assertEqual(len(items), 5)

    def test_the_next_part_comes_with_the_pages_own_sign_in(self):
        yt = self.open()
        items, token = yt.next_part('home-2')
        self.assertEqual(len(items), 20)
        self.assertEqual(items[0]['title'], 'Home video 4')
        self.assertEqual(token, 'home-3')
        path, body, headers = self.asked[-1]
        self.assertEqual(path, '/youtubei/v1/browse')
        self.assertEqual(body['continuation'], 'home-2')
        self.assertEqual(body['context']['client']['clientName'], 'WEB')
        self.assertRegex(headers.get('authorization', ''), r'^SAPISIDHASH \d+_[0-9a-f]{40}$')
        items, token = yt.next_part('home-3')
        self.assertEqual((len(items), token), (5, ''))

    def test_search_results_and_their_next_part(self):
        items, facts = self.open('https://www.youtube.com/results?search_query=bread').read_list()
        self.assertEqual([i['kind'] for i in items], ['video', 'channel', 'playlist', 'video'])
        self.assertEqual(spoken_item(items[1]), 'Channel: Bread Channel. 45.6 thousand subscribers')
        self.assertEqual(spoken_item(items[2]), 'Playlist: Bread recipes. Kitchen Basics. 12 videos')
        more, token = self.yt.next_part(facts['token'], search=True)
        self.assertEqual(self.asked[-1][0], '/youtubei/v1/search')
        self.assertEqual([i['title'] for i in more], ['Rye bread'])

    def test_history_keeps_youtubes_order(self):
        items, facts = self.open('https://www.youtube.com/feed/history').read_list()
        self.assertEqual([i['id'] for i in items], ['hist000001', 'hist000002', 'hist000003', 'hist000004', 'hist000005'])

    def test_signed_in_comes_from_youtubes_own_setting(self):
        self.assertIs(self.open().signed_in(), True)
        self.assertIs(self.open(signed_in=False).signed_in(), False)

    def test_a_signed_out_library_says_so(self):
        items, facts = self.open('https://www.youtube.com/feed/history', signed_in=False,
                                 pages={'/feed/history': fixtures.data_signed_out_library}).read_list()
        self.assertEqual(items, [])
        self.assertTrue(facts['promoSignIn'])
        self.assertIn('Sign in to see it', facts['empty'])


class ScreenTests(DataPages):
    def setUp(self):
        import companion as app
        import youtube_screen
        from web_assistant import BrowserBackend, WebSession
        self.app, self.ys = app, youtube_screen
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.open('about:blank')
        self.backend = BrowserBackend(self.root); self.backend.page = self.page; self.backend.context = self.context
        self.backend.start = lambda: None
        self.backend.browser = 'chrome'
        self.web = WebSession(self.root, self.backend)
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': self.web, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'help_session': None, 'tutorial_session': None,
                    'MEDIA_HUB': None, 'PREFERENCES': dict(app.PREFERENCES), 'SLEEP_RETURN_MODE': 'awake',
                    'FILE_READER': None, 'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None, 'ACCOUNT_PICKER': None,
                    'ACCOUNT_ENTRY': None, 'THUNDERBIRD_SETUP': None, 'CONFIRM_CHOICE': 'yes', 'note_buffer': []}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        speak = lambda text, *a, **k: self.said.append(str(text))
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            self.stack.enter_context(patch.object(app, name, side_effect=speak))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))
        self.stack.enter_context(patch.object(youtube_screen, 'SCREEN', None))
        app.PREFERENCES['web_keep_open'] = True

    def press(self, key, mode='youtube'):
        request = self.app.navigation_key(key, mode)
        return self.app.handle_keyboard(request, mode) if request else mode

    def say(self, words, mode='youtube'):
        return self.app.handle(words, mode)

    def last(self):
        return self.said[-1]

    def test_home_pages_down_through_every_part(self):
        self.say('YouTube.', 'awake'); self.say('Home.')
        self.assertTrue(self.last().startswith('Home. 5 items, and more load as you move down. Home video 1.'), self.last())
        self.press('Next')                     # Page Down: ten at a time, the next part loads
        self.assertTrue(self.last().startswith('Home video 9.') and self.last().endswith(', 11 of 25.'), self.last())
        self.press('Next')
        self.assertTrue(self.last().startswith('Home video 19.'), self.last())
        self.assertEqual(len(self.ys.SCREEN.view()['items']), 30)   # the last part came before it was needed
        for _ in range(3): self.press('Next')
        self.assertTrue(self.last().endswith(' of 30.'), self.last())   # every part, 28 videos, a Short and an ad
        self.press('End'); self.press('Down')
        self.assertTrue(self.last().startswith('End of list. Home video 28.'), self.last())
        self.press('Prior')
        self.assertTrue(self.last().endswith(', 20 of 30.'), self.last())

    def test_every_subscribed_channel_is_listed(self):
        self.say('YouTube.', 'awake'); self.say('My subscriptions.')
        self.assertTrue(self.last().startswith('Subscriptions. 60 items. Channel: Channel 001.'), self.last())
        self.press('End')
        self.assertTrue(self.last().startswith('Channel: Channel 060.'), self.last())

    def test_search_results_continue(self):
        self.say('YouTube.', 'awake'); self.say('Search for bread.')
        self.press('End'); self.press('Down')
        self.assertTrue(self.last().startswith('Rye bread. Baker.'), self.last())

    def test_signed_out_your_own_lists_say_to_sign_in(self):
        # Real youtube.com sends a signed-out browser from /feed/channels to Google's sign-in page.
        self.context.route('https://accounts.google.com/**', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<title>Sign in - Google Accounts</title><h1>Sign in</h1>'))
        self.context.route('https://www.youtube.com/feed/channels', lambda route: route.fulfill(
            status=200, content_type='text/html',
            body='<script>location.replace("https://accounts.google.com/ServiceLogin?service=youtube")</script>'))
        self.say('YouTube.', 'awake'); self.say('Subscriptions.')
        self.assertEqual(self.last(), self.ys.SIGN_IN_NEEDED.format(action='see subscriptions'))
        self.assertIs(self.ys.SCREEN.signed_in, False)

    def test_the_account_item_reads_sign_out_when_signed_in(self):
        self.say('YouTube.', 'awake')
        self.assertEqual(self.app.menu_state('youtube')[0][-1], 'Sign in or sign out')
        self.say('Home.'); self.press('Escape')
        self.assertEqual(self.app.menu_state('youtube')[0][-1], 'Sign out')
        self.press('End')
        self.assertEqual(self.last(), 'Sign out (S), 9 of 9.')
        self.press('Enter')
        self.assertTrue(self.last().startswith('Sign out of YouTube?'), self.last())


if __name__ == '__main__':
    unittest.main()
