"""The YouTube page reader and player, in real Chromium, on saved pages shaped like youtube.com.

Run with the other real-browser checks (tools/run_checks.py --live). Nothing
reaches the internet: youtube_fixtures.routes serves every page.
"""
import os
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

import youtube_fixtures as fixtures
from youtube_page import CHANGED, YouTubePage, YouTubeError, search_url


class LiveYouTube(unittest.TestCase):
    """One headless Chromium for the class."""
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

    def open(self, url='https://www.youtube.com/', overrides=None):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.served = fixtures.routes(self.context, overrides)
        self.page = self.context.new_page(); self.page.set_default_timeout(5000)
        self.page.goto(url, wait_until='domcontentloaded')
        self.yt = YouTubePage(lambda: self.page)
        self.yt.wait_ready(5000)
        return self.yt


class ListReadingTests(LiveYouTube):
    def test_home_reads_both_layouts_live_shorts_and_ads_and_never_a_hidden_page(self):
        items, facts = self.open().read_list()
        from youtube_page import spoken_item
        said = [spoken_item(i) for i in items]
        self.assertEqual(said[0], 'Never Gonna Give You Up. Rick Astley. 3 minutes 33 seconds. 1.6 billion views. 15 years ago')
        self.assertEqual(said[1], 'How to bake bread. Kitchen Basics. 10 minutes 5 seconds. 12 thousand views. 2 days ago')
        self.assertEqual(said[2], 'Live now. Morning news. News Now. 5.2 thousand watching')
        self.assertEqual(said[3], 'Ad: Try Brand Coffee today. Brand Coffee')   # ads are listed and named, never hidden
        self.assertEqual(said[4], 'Short. Cat jumps. 2.1 million views')
        self.assertNotIn('Old hidden result', ' '.join(said))
        self.assertEqual(items[0]['id'], 'vid00000001')
        self.assertEqual(items[4]['id'], 'short000001')
        self.assertIs(facts['signedIn'], False)

    def test_more_videos_load_at_the_end_of_a_long_list(self):
        yt = self.open()
        self.assertEqual(len(yt.read_list()[0]), 5)
        self.assertTrue(yt.load_more())
        items = yt.read_list()[0]
        self.assertEqual(items[-1]['title'], 'Garden tour')
        self.assertEqual(items[-1]['duration'], 3723)

    def test_search_results_with_a_channel_and_a_playlist_and_filters_in_the_address(self):
        url = search_url('bread', {'upload date': 'week', 'type': 'video', 'sort': 'view count'})
        yt = self.open(url)
        self.assertEqual(self.page.evaluate('window.searchFilter'), 'CAMSBAgDEAE=')
        from youtube_page import spoken_item
        said = [spoken_item(i) for i in yt.read_list()[0]]
        self.assertEqual(said, ['Bread for beginners. Kitchen Basics. 12 minutes. 1,234 views. 3 weeks ago',
                                'Channel: Kitchen Basics. 1.2 million subscribers. 300 videos',
                                'Playlist: Bread recipes. Kitchen Basics. 25 videos'])

    def test_a_signed_out_library_page_says_it_needs_sign_in(self):
        items, facts = self.open('https://www.youtube.com/feed/history').read_list()
        self.assertEqual(items, [])
        self.assertTrue(facts['promoSignIn'])

    def test_watch_later_rows(self):
        items, facts = self.open('https://www.youtube.com/playlist?list=WL').read_list()
        self.assertEqual([i['title'] for i in items], ['Saved talk', 'Saved song'])
        self.assertEqual(items[0]['views'], '9K views')
        self.assertEqual(items[0]['duration'], 2710)
        self.assertIs(facts['signedIn'], True)

    def test_a_redesigned_page_reads_as_nothing_without_a_crash(self):
        items, facts = self.open(overrides={'/': fixtures.changed}).read_list()
        self.assertEqual(items, [])
        self.assertTrue(facts['app'])

    def test_a_closed_page_is_a_plain_message(self):
        yt = self.open()
        self.page.close()
        with self.assertRaises(YouTubeError) as caught:
            yt.read_list()
        self.assertIn('browser closed', str(caught.exception))


class PlayerTests(LiveYouTube):
    def setUp(self):
        self.yt = self.open('https://www.youtube.com/watch?v=vid00000001')
        self.yt.wait_for_player(5000)

    def test_play_falls_back_to_a_real_click_when_the_browser_blocks_sound(self):
        self.page.evaluate('window.blockScriptPlay=true')
        state = self.yt.state()
        self.assertTrue(state['paused'])
        self.assertTrue(self.yt.play())
        self.assertEqual(self.page.evaluate('window.playerCalls').count('blocked'), 1)
        self.assertTrue(self.yt.pause())
        self.page.evaluate('window.blockScriptPlay=false')
        self.assertTrue(self.yt.play())
        self.assertEqual(self.page.evaluate('window.playerCalls').count('blocked'), 1)   # the player call worked

    def test_seek_rate_volume_quality_and_mute(self):
        self.assertEqual(self.yt.seek(500)['time'], 213)   # the end, not past it
        self.assertEqual(self.yt.seek(43)['time'], 43)
        self.assertEqual(self.yt.set_rate(1.6)[0], 1.5)    # the nearest speed the player has
        self.assertEqual(self.yt.set_volume(140), 100)
        self.assertEqual(self.yt.set_volume(30), 30)
        self.assertTrue(self.yt.set_muted(True))
        self.assertEqual(self.yt.qualities()[0], 'hd1080')
        self.assertEqual(self.yt.set_quality('large'), 'large')

    def test_captions_autoplay_full_screen_and_next(self):
        captions = self.yt.toggle_captions()
        self.assertTrue(captions['on'])
        self.assertEqual(captions['text'], 'Hello and welcome to the show.')
        self.assertFalse(self.yt.toggle_autoplay())
        self.assertTrue(self.yt.toggle_fullscreen())
        self.yt.next_video()
        self.assertEqual(self.yt.state()['title'], 'How to bake bread')

    def test_ads_are_read_and_skip_is_pressed_only_on_request(self):
        self.page.evaluate('window.startAd(false)')
        ad = self.yt.state()['ad']
        self.assertTrue(ad['showing']); self.assertFalse(ad['skippable'])
        self.assertIn('Ad 1 of 2', ad['text'])
        self.assertFalse(self.yt.skip_ad())
        self.page.evaluate('window.allowSkip()')
        self.assertTrue(self.yt.state()['ad']['skippable'])
        self.assertTrue(self.yt.skip_ad())
        self.assertFalse(self.yt.state()['ad']['showing'])

    def test_details_chapters_and_comments(self):
        details = self.yt.details()
        self.assertEqual(details['channel'], 'Rick Astley')
        self.assertEqual(details['subscribers'], '4.2M subscribers')
        self.assertIn('0:43 First verse', details['description'])
        self.assertEqual([c['title'] for c in details['chapters']], ['Intro', 'First verse', 'Chorus'])
        from youtube_page import like_count, subscribed_state, spoken_comment
        self.assertEqual(like_count(details), '18,000,000 likes')
        self.assertIs(subscribed_state(details), False)
        comments = self.yt.comments(10)
        self.assertEqual([spoken_comment(c) for c in comments['threads']],
                         ['@fan1. 2 days ago. A classic. 1.2 thousand likes. 3 replies', '@fan2. 1 week ago. Still great'])

    def test_like_subscribe_unsubscribe_and_save(self):
        self.assertTrue(self.yt.like())
        self.assertTrue(self.yt.subscribe(True))
        self.assertFalse(self.yt.subscribe(False))
        options = self.yt.save_options()
        self.assertEqual(options, [{'label': 'Watch later', 'checked': False}, {'label': 'Favorites', 'checked': True}])
        self.assertTrue(self.yt.toggle_option('Watch later'))
        self.yt.close_dialog()

    def test_posting_a_comment_types_it_and_presses_comment(self):
        self.assertTrue(self.yt.post_comment('Great song!'))
        self.assertEqual(self.page.evaluate('window.posted'), 'Great song!')


if __name__ == '__main__':
    unittest.main()
