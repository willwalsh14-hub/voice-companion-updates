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



class ScreenTests(LiveYouTube):
    """The YouTube screen through companion: voice, keys, speech, and the real page."""
    def setUp(self):
        import io, tempfile
        from contextlib import ExitStack, redirect_stdout
        from unittest.mock import patch
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
        # The browser stays open between steps, unless a test checks closing it.
        app.PREFERENCES['web_keep_open'] = True

    def press(self, key, mode='youtube'):
        request = self.app.navigation_key(key, mode)
        return self.app.handle_keyboard(request, mode) if request else mode

    def say(self, words, mode='youtube'):
        return self.app.handle(words, mode)

    def last(self):
        return self.said[-1]

    def poll(self, mode='youtube'):
        self.ys.SCREEN.next_check = 0
        before = len(self.said)
        self.ys.poll(self.app, mode)
        return self.said[before:]

    def home(self):
        self.assertEqual(self.say('YouTube.', 'awake'), 'youtube')
        self.assertEqual(self.say('Home.'), 'youtube')
        self.assertTrue(self.last().startswith('Home. 5 items.'), self.last())

    def playing(self):
        self.home()
        self.assertEqual(self.press('Enter'), 'youtube')
        self.assertEqual(self.last(), 'Playing Never Gonna Give You Up.')
        return self.ys.SCREEN

    def test_youtube_opens_from_the_main_menu_and_the_media_menu(self):
        self.assertEqual(self.say('YouTube.', 'awake'), 'youtube')
        self.assertEqual(self.last(), 'YouTube menu. Search (S), 1 of 8. Control+F.')
        self.press('Down')
        self.assertEqual(self.last(), 'Home (H), 2 of 8.')
        self.assertEqual(self.press('Enter'), 'youtube')
        self.assertEqual(self.last(), 'Home. 5 items. Never Gonna Give You Up. Rick Astley. 3 minutes 33 seconds. '
                                      '1.6 billion views. 15 years ago, 1 of 5.')
        self.press('Down')
        self.assertEqual(self.last(), 'How to bake bread. Kitchen Basics. 10 minutes 5 seconds. 12 thousand views. 2 days ago, 2 of 5.')
        self.press('Up'); self.press('Up')
        self.assertTrue(self.last().startswith('Start of list. Never Gonna'))
        # The window shows the same list, with the selected line.
        names, index, _ = self.app.menu_state('youtube')
        self.assertEqual((len(names), index), (5, 0))
        # Escape goes to the YouTube menu, on Home; then to the Media menu, on YouTube.
        self.assertEqual(self.press('Escape'), 'youtube')
        self.assertEqual(self.last(), 'YouTube menu. Home (H), 2 of 8.')
        self.assertEqual(self.press('Escape'), 'media_menu')
        self.assertEqual(self.last(), 'Media menu. YouTube (Y), 4 of 4.')
        self.assertEqual(self.press('Enter', 'media_menu'), 'youtube')
        self.assertEqual(self.last(), 'YouTube menu. Home (H), 2 of 8.')
        # Signed out, the last item signs in.
        self.press('End')
        self.assertEqual(self.last(), 'Sign in (S), 8 of 8.')

    def test_end_of_the_list_loads_more_videos(self):
        self.home()
        self.press('End')
        self.press('Down')
        self.assertEqual(self.last(), 'Garden tour. Green Thumb. 1 hour 2 minutes 3 seconds. 800 views. 1 month ago, 6 of 6.')
        self.press('Down')
        self.assertTrue(self.last().startswith('End of list. Garden tour'))

    def test_a_video_plays_in_its_own_tab_and_the_list_stays(self):
        screen = self.playing()
        self.assertIsNot(screen.video_page, screen.list_page)
        self.press('Down')
        self.assertTrue(self.last().startswith('How to bake bread'))
        self.press('F3')
        self.assertEqual(self.last(), 'YouTube: Never Gonna Give You Up. Channel: Rick Astley. Playing. Position 0 seconds of 3 minutes 33 seconds.')
        self.assertEqual(self.say("What's playing?", 'awake'), 'awake')
        self.assertTrue(self.last().startswith('YouTube: Never Gonna Give You Up.'))

    def test_play_plays_the_chosen_video_before_any_video_plays(self):
        # A new veteran hears a video's name and says "Play", not "That one".
        self.assertEqual(self.say('Search YouTube for bread.', 'awake'), 'youtube')
        self.press('Down')
        self.say('Play.')
        self.assertEqual(self.last(), 'No video is playing. Choose a video and press Enter.')   # a channel, not a video
        self.press('Up')
        self.assertEqual(self.say('Play.'), 'youtube')
        chosen = self.ys.SCREEN.current()
        self.assertEqual(chosen['title'], 'Bread for beginners')
        self.assertEqual(self.ys.SCREEN.played[-1]['id'], chosen['id'])   # the saved watch page names it as it likes
        self.assertTrue(self.last().startswith('Playing '), self.last())
        self.say('Pause.')
        self.assertEqual(self.say('Play.'), 'youtube')
        self.assertEqual(self.last(), 'Playing.')   # Play resumes the video; it does not start the chosen one

    def test_stop_and_resume_mean_the_video_while_one_is_loaded(self):
        # Said "Stop" and "Resume" were taken as Companion's voice: silence, and the video played on.
        screen = self.playing()
        self.say('Pause.')
        self.assertEqual(self.say('Resume.'), 'youtube')
        self.assertEqual(self.last(), 'Playing.')
        self.assertEqual(self.say('Stop.'), 'youtube')
        self.assertEqual(self.last(), 'Video stopped.')
        self.assertFalse(screen.has_video())
        before = len(self.said)
        self.say('Stop.')   # nothing loaded: Stop silences Companion's voice, as on other screens
        self.assertEqual(self.said[before:], [])
        # With nothing playing, F3 and "What's playing?" here point to the videos, not to the radio.
        self.press('F3')
        self.assertEqual(self.last(), 'Nothing is playing. Choose a video and press Enter to play it.')
        self.say("What's playing?")
        self.assertEqual(self.last(), 'Nothing is playing. Choose a video and press Enter to play it.')
        self.say("What's playing?", 'awake')
        self.assertTrue(self.last().startswith('Nothing is playing. To start, say Play and a station name'))

    def test_the_keyboard_comes_back_after_a_browser_tab_opens(self):
        from unittest.mock import Mock
        window = Mock(); self.app.APP_WINDOW = window
        self.say('YouTube.', 'awake')
        self.assertEqual(window.reclaim_focus.call_count, 0)   # the menu opens no browser
        self.say('Home.')
        self.assertEqual(window.reclaim_focus.call_count, 1)
        self.press('Down')
        self.assertEqual(window.reclaim_focus.call_count, 1)   # moving in a list opens nothing
        self.press('Enter')
        self.assertEqual(window.reclaim_focus.call_count, 2)   # the video's own tab

    def test_player_keys(self):
        self.playing()
        steps = [('K', 'Paused.'), ('Space', 'Playing.'), ('Right', 'Forward 10 seconds. 10 seconds of 3 minutes 33 seconds.'),
                 ('Right', 'Forward 10 seconds. 20 seconds of 3 minutes 33 seconds.'),
                 ('J', 'Back 10 seconds. 10 seconds of 3 minutes 33 seconds.'), ('Left', 'Start of the video.'),
                 ('5', '50 percent. Now at 1 minute 46 seconds of 3 minutes 33 seconds.'), ('0', 'Start of the video.'),
                 ('period', 'Speed 1.25.'), ('comma', 'Normal speed.'), ('Ctrl+Down', 'Volume 90 percent.'),
                 ('Ctrl+Up', 'Volume 100 percent.'), ('Ctrl+Up', 'Loudest. Volume 100 percent.'), ('M', 'Sound off.'), ('M', 'Sound on.'),
                 ('C', 'Captions on. Press R to read the current caption.'), ('R', 'Hello and welcome to the show.'),
                 ('A', 'Autoplay off. Playback stops when the video ends.'), ('F', 'Video full screen on. Press F again to turn it off.'),
                 ('Ctrl+Right', 'Chapter: First verse. Now at 43 seconds of 3 minutes 33 seconds.'),
                 ('Ctrl+Left', 'Chapter: Intro. Now at 0 seconds of 3 minutes 33 seconds.'), ('S', 'No ad is playing.')]
        for key, expected in steps:
            with self.subTest(key=key):
                self.assertEqual(self.press(key), 'youtube')
                self.assertEqual(self.last(), expected)
        self.say('skip amount 30 seconds')
        self.assertEqual(self.last(), 'J, L and the arrows now skip 30 seconds.')
        self.press('L')
        self.assertEqual(self.last(), 'Forward 30 seconds. 30 seconds of 3 minutes 33 seconds.')
        self.say('Forward 1 minute.')
        self.assertEqual(self.last(), 'Forward 1 minute. 1 minute 30 seconds of 3 minutes 33 seconds.')
        self.say('Video speed 1.5.')
        self.assertEqual(self.last(), 'Speed 1.5.')
        self.press('Q')
        self.assertEqual(self.last(), 'Video quality. Now 720p, HD. 720p, HD, current, 2 of 7. Press Enter to choose.')
        self.press('Down'); self.press('Enter')
        self.assertEqual(self.last(), 'Quality 480p.')
        self.press('H')
        self.assertEqual(self.last(), 'Chapters. Chorus. 1 minute 25 seconds, 3 of 3. Press Enter to go there.')
        self.press('Home'); self.press('Enter')
        self.assertEqual(self.last(), 'Intro. Now at 0 seconds of 3 minutes 33 seconds.')
        self.press('Escape')
        self.assertTrue(self.last().startswith('Back to Home.'))

    def test_details_description_comments_up_next_and_next_video(self):
        self.playing()
        self.press('I')
        self.assertEqual(self.last(), 'Never Gonna Give You Up. Channel: Rick Astley. 4.2 million subscribers. Length: 3 minutes 33 seconds. '
                                      '1.6 billion views. 15 years ago. 18,000,000 likes. You are not subscribed. Press D for the description.')
        self.press('D')
        self.assertTrue(self.last().startswith('Description. The official video for Never Gonna Give You Up. 0:00 Intro'), self.last())
        self.press('O')
        self.assertEqual(self.last(), 'Comments, 2 Comments. @fan1. 2 days ago. A classic. 1.2 thousand likes. 3 replies, 1 of 2.')
        self.press('Down')
        self.assertEqual(self.last(), '@fan2. 1 week ago. Still great, 2 of 2.')
        self.press('Escape')
        self.press('X')
        self.assertEqual(self.last(), 'Up next. 1 item. How to bake bread. Kitchen Basics. 10 minutes 5 seconds. 12 thousand views. 2 days ago, 1 of 1.')
        self.press('N')
        self.assertEqual(self.last(), 'Next video. Playing How to bake bread.')
        self.press('P')
        self.assertEqual(self.last(), 'Previous video. Playing Never Gonna Give You Up.')

    def test_ads_are_said_and_skip_waits_for_you(self):
        screen = self.playing()
        self.assertEqual(self.poll(), [])
        screen.video_page.evaluate('window.startAd(false)')
        self.assertEqual(self.poll(), ['An ad is playing. Ad 1 of 2.'])
        self.press('S')
        self.assertEqual(self.last(), 'This ad cannot be skipped yet. You will hear when Skip is ready.')
        self.press('Right')
        self.assertEqual(self.last(), 'An ad is playing. You cannot move in an ad.')
        self.press('F3')
        self.assertEqual(self.last(), 'YouTube: Never Gonna Give You Up. Channel: Rick Astley. An ad is playing. Ad 1 of 2.')
        self.assertEqual(self.poll(), [])
        screen.video_page.evaluate('window.allowSkip()')
        self.assertEqual(self.poll(), ['Skip is ready. Press S to skip the ad.'])
        self.assertFalse(screen.video_page.evaluate('!!window.adSkipped'))   # never skipped by itself
        self.press('S')
        self.assertEqual(self.last(), 'Ad skipped.')
        self.assertEqual(self.poll(), [])
        # An ad that ends by itself is said too.
        screen.video_page.evaluate('window.startAd(false)'); self.poll()
        screen.video_page.evaluate('window.endAd()')
        self.assertEqual(self.poll(), ['The ad ended. Back to Never Gonna Give You Up.'])
        # Elsewhere in the app, ad news is not spoken over your work.
        screen.video_page.evaluate('window.startAd(true)')
        self.assertEqual(self.poll('awake'), [])

    def test_autoplay_moving_on_and_the_end_are_said(self):
        screen = self.playing()
        screen.video_page.evaluate('document.getElementById("movie_player").nextVideo()')
        self.assertEqual(self.poll(), ['Next video. Playing How to bake bread.'])
        screen.video_page.evaluate('window.endVideo()')
        self.assertEqual(self.poll(), ['The video ended.'])

    def test_account_actions_ask_first_and_then_change_the_account(self):
        screen = self.playing()
        page = screen.video_page
        self.press('T')
        self.assertEqual(self.last(), 'Like Never Gonna Give You Up? Say Yes or No.')
        self.assertEqual(self.app.app_context('youtube').get('confirmation'), 'no')
        self.press('Enter')   # No is chosen first
        self.assertTrue(self.last().startswith('Nothing changed.'))
        self.assertEqual(page.evaluate('document.querySelector("like-button-view-model button").getAttribute("aria-pressed")'), 'false')
        self.press('T'); self.press('Y')
        self.assertEqual(self.last(), 'Liked.')
        self.press('T')
        self.assertEqual(self.last(), 'You already like this video. To take your like back, say remove like.')
        self.press('U')
        self.assertEqual(self.last(), 'Subscribe to Rick Astley? Say Yes or No.')
        self.say('Yes, please.')
        self.assertEqual(self.last(), 'Subscribed.')
        self.say('Unsubscribe.'); self.say('yes')
        self.assertEqual(self.last(), 'Unsubscribed.')
        self.press('W')
        self.assertEqual(self.last(), 'Save to. Watch later, not saved here, 1 of 2. Press Enter to save to the list you hear, or to remove it.')
        self.press('Enter')
        self.assertEqual(self.last(), 'Save this video to Watch later? Say Yes or No.')
        self.press('Up'); self.press('Enter')
        self.assertEqual(self.last(), 'Saved to Watch later.')
        self.say('Save to Watch later.')
        self.assertEqual(self.last(), 'This video is already in Watch later.')
        self.say('Post comment: Great song!')
        self.assertEqual(self.last(), 'Post this comment on Never Gonna Give You Up? Your comment: Great song!. Everyone can see it. Say Yes or No.')
        self.say('No.')
        self.assertTrue(self.last().startswith('Your comment was not posted.'))
        self.assertIsNone(page.evaluate('window.posted'))
        self.say('Post comment: Great song!'); self.say('Yes.')
        self.assertEqual(self.last(), 'Comment posted.')
        self.assertEqual(page.evaluate('window.posted'), 'Great song!')

    def test_signed_out_account_actions_say_how_to_sign_in(self):
        self.context.unroute('https://www.youtube.com/**')
        fixtures.routes(self.context, {'/watch': lambda: fixtures.watch(signed_in=False)})
        self.playing()
        for key in ('T', 'U', 'W'):
            self.press(key)
            self.assertTrue(self.last().startswith('You are not signed in to YouTube. To '), self.last())
            self.assertIn('say sign in', self.last())
        self.say('History')
        self.assertEqual(self.last(), 'You are not signed in to YouTube. To see history, say sign in. '
                                      "You type your own email and password on Google's page.")

    def test_embedded_edge_offers_chrome_for_account_features_and_never_signs_in_itself(self):
        self.backend.browser = 'webview2'
        screen = self.playing()
        self.press('T')
        self.assertEqual(self.last(), 'Google does not allow signing in inside embedded Edge, so YouTube is signed out here. '
                                      'To like videos, switch to Chrome, where you can sign in. Web pages will also open in Chrome. '
                                      'Switch to Chrome now? Say Yes or No.')
        self.say('No')
        self.assertTrue(self.last().startswith('YouTube stays in embedded Edge, signed out.'))
        self.say('History')
        self.assertIn('To see your library, switch to Chrome', self.last())
        from unittest.mock import patch
        def choose(name):
            self.backend.browser = name
            return 'Selected Chrome. Say open website followed by the address. This uses a separate browser profile.'
        with patch('chrome_setup.missing_for_web', return_value=None), patch.object(self.web, 'choose_browser', side_effect=choose):
            self.say('Yes')
        self.assertEqual(self.last(), 'YouTube now uses Chrome. To sign in, say sign in. Playing Never Gonna Give You Up.')
        self.assertTrue(screen.has_video())
        # Signing in opens Google's own page on the web screen; Companion types nothing.
        self.context.route('https://accounts.google.com/**', lambda route: route.fulfill(
            content_type='text/html', body='<title>Sign in - Google Accounts</title><h1>Sign in</h1><label>Email or phone<input type=email></label>'))
        self.assertEqual(self.say('Sign in.'), 'web')
        self.assertTrue(self.last().startswith('Google sign-in page. Type your own email and password.'), self.last())
        self.assertTrue(any(url.startswith('https://accounts.google.com/') for url in [self.page.url]), self.page.url)
        # Back from Google's page, the list and the video load again with the account.
        self.assertEqual(self.say('Back to YouTube.', 'web'), 'youtube')
        self.assertTrue(self.last().startswith('You are still signed out. YouTube. Home. 5 items.'), self.last())
        self.assertTrue(screen.has_video())

    def test_search_filters_and_channels(self):
        # This results page lists no filters of its own, so the screen builds YouTube's codes.
        self.say('YouTube', 'awake')
        self.say('Search for bread.')
        self.assertEqual(self.last(), 'Results for bread. 3 items. Bread for beginners. Kitchen Basics. 12 minutes. 1,234 views. '
                                      '3 weeks ago, 1 of 3. Press Control+Shift+F for filters.')
        self.say('This week.')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This week. 3 items.'), self.last())
        self.assertIn('sp=EgIIAw%3D%3D', self.served[-1])
        self.say('Sort by popularity')
        self.assertIn('Filters: This week, sorted by popularity.', self.last())
        self.assertIn('sp=CAMSAggD', self.served[-1])
        self.say('Sort by upload date')   # YouTube took this away in 2026
        self.assertEqual(self.last(), 'YouTube no longer sorts results by upload date. To find new videos, say Today or This week.')
        self.press('Ctrl+Shift+F')
        self.assertEqual(self.last(), 'Filters. On now: This week, sorted by popularity. Upload date: This week (U), 1 of 6.')
        self.press('Enter')
        self.assertEqual(self.last(), 'Upload date. This week, on (T), 3 of 5.')
        self.press('Down')
        self.assertEqual(self.last(), 'This month (T), 4 of 5.')
        self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This month, sorted by popularity. 3 items.'), self.last())
        self.assertIn('sp=CAMSAggE', self.served[-1])
        self.press('Ctrl+Shift+F'); self.press('Down'); self.press('Enter')
        self.assertEqual(self.last(), 'Type. Any type, on (A), 1 of 6.')
        self.press('Letter:s')
        self.assertEqual(self.last(), 'Shorts (S), 3 of 6.')
        self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This month, Shorts, sorted by popularity.'), self.last())
        self.press('Ctrl+Shift+F'); self.press('End')
        self.assertEqual(self.last(), 'Clear filters (C), 6 of 6.')
        self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. 3 items.'), self.last())
        self.assertTrue(self.served[-1].endswith('search_query=bread'), self.served[-1])
        self.press('Down'); self.press('Enter')
        self.assertTrue(self.last().startswith('Channel: Kitchen Basics. 5 items.'), self.last())
        self.press('Escape')
        self.assertTrue(self.last().startswith('Back to Results for bread.'))
        self.press('Escape')
        self.assertEqual(self.last(), 'YouTube menu. Search (S), 1 of 8. Control+F.')
        # From any screen, in one sentence.
        self.assertEqual(self.say('Play cats on YouTube', 'awake'), 'youtube')
        self.assertTrue(self.last().startswith('Results for cats.'), self.last())

    def test_filters_the_results_page_lists_are_used_as_youtube_combines_them(self):
        self.context.unroute('https://www.youtube.com/**')
        self.served = fixtures.routes(self.context, listed_filters=True)
        state = lambda: fixtures.filter_state(__import__('urllib.parse').parse.parse_qs(
            __import__('urllib.parse').parse.urlsplit(self.served[-1]).query).get('sp', [''])[0])
        self.say('YouTube', 'awake')
        self.press('Ctrl+F')
        self.app.handle_keyboard('bread', 'youtube')
        self.assertTrue(self.last().startswith('Results for bread. 2 items.'), self.last())
        self.press('Ctrl+Shift+F')
        # YouTube's own groups, in the screen's order and names: Duration is Length, Prioritize is Sort.
        self.assertEqual(self.last(), 'Filters. No filters are on. Upload date: Any time (U), 1 of 6.')
        self.assertEqual(self.app.menu_state('youtube')[0], ['Upload date: Any time', 'Type: Any type', 'Length: Any length',
                                                             'Features: No features', 'Sort: Relevance', 'Clear filters'])
        self.press('Enter')
        self.assertEqual(self.app.menu_state('youtube')[0], ['Any time, on', 'Today', 'This week', 'This month', 'This year'])
        self.press('Down'); self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: Today. 2 items.'), self.last())
        self.assertEqual(state(), {'d': 'today'})
        # A second filter joins the first, through the page's own link.
        self.press('Ctrl+Shift+F'); self.press('Down'); self.press('Enter')
        self.assertEqual(self.app.menu_state('youtube')[0], ['Any type, on', 'Videos', 'Shorts', 'Channels', 'Playlists', 'Movies'])
        self.press('Letter:s'); self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: Today, Shorts.'), self.last())
        self.assertEqual(state(), {'d': 'today', 't': 'shorts'})
        self.say('Sort by popularity.')
        self.assertTrue(self.last().startswith('Results for bread. Filters: Today, Shorts, sorted by popularity.'), self.last())
        self.assertEqual(state(), {'d': 'today', 't': 'shorts', 's': 'popularity'})
        self.say('This week.')   # one choice per group: This week takes the place of Today
        self.assertEqual(state(), {'d': 'week', 't': 'shorts', 's': 'popularity'})
        # Channels: YouTube does not allow a length with it.
        self.say('Only channels.')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This week, Channels, sorted by popularity. 1 item. Channel:'), self.last())
        self.press('Ctrl+Shift+F'); self.press('Down'); self.press('Down'); self.press('Enter')
        self.press('Down')
        self.assertEqual(self.last(), 'Under 3 minutes, not offered with the filters that are on (U), 2 of 4.')
        self.press('Enter')
        self.assertEqual(self.last(), 'Under 3 minutes is not offered with the filters that are on. Choose another, or say clear filters.')
        # Taking one filter away: its group's first choice.
        self.press('Escape'); self.press('Up'); self.press('Enter'); self.press('Home'); self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This week, sorted by popularity. 2 items.'), self.last())
        self.assertEqual(state(), {'d': 'week', 's': 'popularity'})
        # Several features can be on together, as on YouTube.
        self.say('Live.'); self.say('4K.')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This week, Live, 4K, sorted by popularity.'), self.last())
        self.say('Movies')
        self.assertEqual(self.last(), 'No results for bread with these filters: This week, Movies, Live, 4K, sorted by popularity. '
                                      'Say clear filters, or press Control+Shift+F to change them.')
        # The lists show the filters as they are after a search that found nothing.
        self.press('Ctrl+Shift+F')
        self.assertEqual(self.last(), 'Filters. On now: This week, Movies, Live, 4K, sorted by popularity. Upload date: This week (U), 1 of 6.')
        self.press('Down'); self.press('Down'); self.press('Enter'); self.press('Down'); self.press('Enter')
        self.assertTrue(self.last().startswith('No results for bread with these filters: This week, Movies, Under 3 minutes,'), self.last())
        self.press('Escape'); self.press('Up'); self.press('Enter'); self.press('Letter:v'); self.press('Enter')
        self.assertTrue(self.last().startswith('Results for bread. Filters: This week, Videos, Under 3 minutes, Live, 4K, sorted by popularity.'), self.last())
        self.say('Clear filters.')
        self.assertTrue(self.last().startswith('Results for bread. 2 items.'), self.last())
        self.assertEqual(state(), {})

    def test_a_search_typed_with_keys_or_said_and_confirmed(self):
        self.say('YouTube', 'awake')
        self.assertEqual(self.press('Enter'), 'youtube')   # Search, first in the menu
        self.assertEqual(self.last(), self.ys.SEARCH_PROMPT)
        self.assertEqual(self.app.handle_keyboard('bread', 'youtube'), 'youtube')
        self.assertTrue(self.last().startswith('Results for bread. 3 items.'), self.last())
        self.press('Escape')
        self.assertEqual(self.last(), 'YouTube menu. Search (S), 1 of 8. Control+F.')
        # Said: the words come back, then Confirm searches.
        self.press('Ctrl+F')
        self.say('Bread recipes.')
        self.assertEqual(self.last(), 'Bread recipes. Press Enter or say confirm to search.')
        self.assertFalse(any('recipes' in url for url in self.served))
        self.say('Confirm.')
        self.assertTrue(self.last().startswith('Results for Bread recipes. 3 items.'), self.last())
        self.assertIn('search_query=Bread+recipes', self.served[-1])
        # From a video list, Control+F asks too, and Escape goes back to the list.
        self.press('Ctrl+F'); self.press('Escape')
        self.assertTrue(self.last().startswith('Search canceled. Results for Bread recipes. Bread for beginners.'), self.last())

    def test_sign_out_asks_first_and_uses_googles_page(self):
        signed = {'in': True}
        self.context.unroute('https://www.youtube.com/**')
        fixtures.routes(self.context, {'/': lambda: fixtures.home(signed_in=signed['in'])})
        self.context.route('https://accounts.google.com/**', lambda route: (signed.update({'in': False}), route.fulfill(
            content_type='text/html', body='<title>Signed out</title><p>You are signed out.</p>'))[-1])
        self.home()
        self.press('Escape'); self.press('End')
        self.assertEqual(self.last(), 'Sign out (S), 8 of 8.')
        self.press('Enter')
        self.assertEqual(self.last(), 'Sign out of YouTube? This browser is signed out of your Google account too. Say Yes or No.')
        self.say('No.')
        self.assertTrue(signed['in'])
        self.press('Enter'); self.say('Yes.')
        self.assertFalse(signed['in'])
        self.assertEqual(self.last(), 'You are signed out of YouTube. YouTube menu. Sign in (S), 8 of 8.')

    def test_an_empty_home_says_to_search(self):
        self.context.unroute('https://www.youtube.com/**')
        fixtures.routes(self.context, {'/': fixtures.home_empty})
        self.assertEqual(self.say('YouTube', 'awake'), 'youtube')
        self.say('Home')
        self.assertEqual(self.last(), 'Home. YouTube shows no Home videos yet. It picks them from what you watch. '
                                      'Press Control+F to search, or say search for, and your words, such as search for jazz.')
        self.press('Down')
        self.assertEqual(self.last(), self.ys.HOME_EMPTY)

    def test_more_videos_never_come_from_another_list(self):
        self.say('YouTube', 'awake')
        self.say('Search for bread')
        self.press('Down'); self.press('Enter')   # the channel opens in the list tab
        self.press('Escape')
        self.assertTrue(self.last().startswith('Back to Results for bread.'))
        self.press('End'); self.press('Down')
        self.assertTrue(self.last().startswith('End of list. Playlist: Bread recipes'), self.last())

    def test_open_in_browser_shows_the_page_you_are_on(self):
        screen = self.playing()
        self.press('O')
        self.assertEqual(self.say('Open in browser'), 'web')
        self.assertIs(self.web.backend.page, screen.video_page)
        self.assertEqual(self.say('Back to YouTube', 'web'), 'youtube')
        self.press('Escape')
        self.assertEqual(self.say('Open in browser'), 'web')
        self.assertIs(self.web.backend.page, screen.list_page)

    def test_the_main_menu_closes_the_browser_and_youtube_starts_again(self):
        screen = self.playing()
        self.app.PREFERENCES['web_keep_open'] = False
        closed = []
        self.backend.close = lambda: closed.append(True) or [p.close() for p in list(self.context.pages)]
        self.assertEqual(self.say('main menu'), 'awake')
        self.assertEqual(closed, [True])
        self.assertFalse(screen.alive(screen.list_page))
        # Back on YouTube, the lists are kept and a video plays in its own tab again.
        self.backend.start = lambda: setattr(self.backend, 'page', self.context.new_page())
        self.assertEqual(self.say('YouTube', 'awake'), 'youtube')
        self.assertEqual(self.last(), 'YouTube menu. Search (S), 1 of 8. Control+F.')
        self.say('Home')
        self.press('Enter')
        self.assertEqual(self.last(), 'Playing Never Gonna Give You Up.')
        self.assertIsNot(screen.video_page, screen.list_page)

    def test_a_changed_page_says_so_and_offers_the_page(self):
        self.context.unroute('https://www.youtube.com/**')
        fixtures.routes(self.context, {'/': fixtures.changed})
        self.assertEqual(self.say('YouTube', 'awake'), 'youtube')
        self.say('Home')
        self.assertEqual(self.last(), CHANGED)
        self.assertEqual(self.say('Open in browser'), 'web')
        self.assertTrue(self.last().startswith('Web page. Page loaded: YouTube.'), self.last())

    def test_other_media_pauses_and_takes_over(self):
        from unittest.mock import Mock
        player = Mock(kind='radio', session=5); player.status.return_value = {'paused': False}
        hub = Mock(player=player); hub.now_playing.return_value = 'Station: WGN. Playing.'
        self.app.MEDIA_HUB = hub
        self.home(); self.press('Enter')
        self.assertEqual(self.last(), 'Playing Never Gonna Give You Up. The radio is paused.')
        player.control.assert_called_with('pause')
        # The radio started again: the video pauses, and F3 tells about the radio.
        player.session = 6
        self.assertEqual(self.poll(), ['YouTube paused.'])
        self.assertTrue(self.ys.SCREEN.player.state()['paused'])
        self.assertEqual(self.app.now_playing(), 'Station: WGN. Playing.')

    def test_control_p_main_menu_and_a_closed_browser(self):
        screen = self.playing()
        self.assertEqual(self.app.handle_keyboard(self.app.navigation_key('Ctrl+P', 'awake'), 'awake'), 'awake')
        self.assertEqual(self.last(), 'Video paused.')
        self.say('Resume video.', 'awake')
        self.assertEqual(self.last(), 'Playing.')
        # The main menu stops the video unless audio keeps playing in the background.
        self.app.PREFERENCES['keep_audio_playing'] = True
        self.assertEqual(self.say('main menu'), 'awake')
        self.assertTrue(screen.has_video())
        self.app.PREFERENCES['keep_audio_playing'] = False
        self.say('YouTube', 'awake')
        self.assertEqual(self.say('main menu'), 'awake')
        self.assertFalse(screen.has_video())
        self.assertEqual(self.app.now_playing(), __import__('media_hub').NOTHING_PLAYING)
        # A browser that closes under the video is said once.
        self.home(); self.press('Enter')
        screen.video_page.close()
        self.assertEqual(self.poll(), ['The browser closed, so the video stopped.'])
        self.press('K')
        self.assertEqual(self.last(), 'No video is playing. Choose a video and press Enter.')


if __name__ == '__main__':
    unittest.main()
