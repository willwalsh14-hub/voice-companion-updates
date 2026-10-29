"""YouTube without a browser: search filters, spoken words, keys, and where YouTube's words count.

The page itself is tested in real Chromium in test_youtube_live.py.
"""
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import youtube_page as yp
import youtube_screen as ys


class SearchFilterTests(unittest.TestCase):
    def test_filters_build_the_address_youtube_uses(self):
        # The values YouTube's own filter panel puts in the address.
        self.assertEqual(yp.filter_code({'type': 'video'}), 'EgIQAQ==')
        self.assertEqual(yp.filter_code({'features': 'live'}), 'EgJAAQ==')
        self.assertEqual(yp.filter_code({'features': '4k'}), 'EgJwAQ==')
        self.assertEqual(yp.filter_code({'features': 'subtitles'}), 'EgIoAQ==')
        self.assertEqual(yp.filter_code({'length': 'short'}), 'EgIYAQ==')
        self.assertEqual(yp.filter_code({'upload date': 'today'}), 'EgIIAg==')
        self.assertEqual(yp.filter_code({'sort': 'upload date'}), 'CAI=')
        self.assertEqual(yp.filter_code({'upload date': 'week', 'type': 'video', 'sort': 'view count'}), 'CAMSBAgDEAE=')
        self.assertEqual(yp.filter_code({}), '')
        self.assertEqual(yp.search_url('cats  and dogs'), 'https://www.youtube.com/results?search_query=cats+and+dogs')
        self.assertTrue(yp.search_url('cats', {'type': 'channel'}).endswith('&sp=EgIQAg%3D%3D'))

    def test_spoken_filters_tolerate_speech(self):
        cases = {'Only channels.': ('type', 'channel'), 'This week': ('upload date', 'week'),
                 'Sort by view count.': ('sort', 'view count'), 'sorted by date': ('sort', 'upload date'),
                 'With subtitles': ('features', 'subtitles'), 'Filter by long videos': ('length', 'long'),
                 'Under 4 minutes': ('length', 'short'), '4K': ('features', '4k'), 'Relevance': ('sort', 'relevance')}
        for words, expected in cases.items():
            with self.subTest(words=words):
                self.assertEqual(yp.filter_request(words), expected)
        self.assertIsNone(yp.filter_request('cats'))
        self.assertEqual(yp.filter_summary({'upload date': 'week', 'sort': 'rating'}), 'This week, sorted by rating')
        self.assertEqual(yp.filter_summary(yp.NO_FILTERS), '')


class SpokenWordTests(unittest.TestCase):
    def test_counts_times_and_items(self):
        self.assertEqual(yp.spoken_count('1.2M views'), '1.2 million views')
        self.assertEqual(yp.spoken_count('15K watching'), '15 thousand watching')
        self.assertEqual(yp.spoken_count('1,234 views'), '1,234 views')
        self.assertEqual(yp.clock_seconds('1:02:03'), 3723)
        self.assertIsNone(yp.clock_seconds('LIVE'))
        self.assertEqual(ys.spoken_seconds('30 seconds'), 30)
        self.assertEqual(ys.spoken_seconds('one minute'), 60)
        self.assertEqual(ys.spoken_seconds('2 minutes 30 seconds'), 150)
        self.assertEqual(ys.spoken_seconds('ninety seconds'), 90)
        self.assertIsNone(ys.spoken_seconds('banana'))
        self.assertEqual(ys.normalized('Video speed 1.5.'), 'video speed 1.5')
        self.assertEqual(ys.normalized('Yes, please.'), 'yes')

    def test_an_item_read_only_from_its_accessible_name(self):
        item = yp.item_from_raw({'container': 'link', 'aria': 'Cats by Pets 1,234 views 2 days ago 3 minutes, 5 seconds',
                                 'href': '/watch?v=abc123&t=4'})
        self.assertEqual((item['title'], item['channel'], item['duration'], item['id']), ('Cats', 'Pets', 185, 'abc123'))
        self.assertEqual(yp.spoken_item(item), 'Cats. Pets. 3 minutes 5 seconds. 1,234 views. 2 days ago')
        self.assertIsNone(yp.item_from_raw({'container': 'link', 'href': '/watch?v=x'}))   # no name: left out, no crash

    def test_chapters_written_in_a_description(self):
        self.assertEqual(yp.chapters_from_description('Hello\n0:00 Intro\n1:10 - Part one\n12:30 The end'),
                         [{'title': 'Intro', 'time': 0}, {'title': 'Part one', 'time': 70}, {'title': 'The end', 'time': 750}])
        self.assertEqual(yp.chapters_from_description('Meet me at 1:30 today.'), [])

    def test_addresses(self):
        self.assertEqual(yp.video_id('https://youtu.be/abc'), 'abc')
        self.assertEqual(yp.video_id('https://www.youtube.com/shorts/xyz'), 'xyz')
        self.assertEqual(yp.video_id('https://example.com/watch?v=abc'), '')


class ScreenWordTests(unittest.TestCase):
    """Where YouTube's words count, with no browser at all."""
    def setUp(self):
        import companion as app
        self.app = app
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
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
        self.stack.enter_context(patch.object(ys, 'SCREEN', None))
        # Nothing here may start a browser.
        self.started = self.stack.enter_context(patch.object(ys.YouTubeScreen, 'go', side_effect=AssertionError('browser started')))

    def press(self, key, mode='youtube'):
        request = self.app.navigation_key(key, mode)
        return self.app.handle_keyboard(request, mode) if request else mode

    def test_youtube_words_are_text_in_a_note(self):
        self.assertEqual(self.app.handle('YouTube.', 'note'), 'note')
        self.assertIn('YouTube', ' '.join(self.app.note_buffer))
        self.assertEqual(self.app.handle('Search YouTube for cats.', 'note'), 'note')
        self.started.assert_not_called()

    def test_play_on_youtube_is_never_a_radio_station(self):
        self.assertTrue(ys.claims('Play cats on YouTube.'))
        self.assertTrue(ys.claims('Play the next video'))
        self.assertIsNone(self.app.station_asked('Play cats on YouTube.', 'awake'))
        self.assertIsNone(self.app.station_asked('play the next video', 'awake'))
        self.assertEqual(self.app.station_asked('Play WGN', 'awake'), 'WGN')

    def test_youtube_alone_is_a_link_name_on_a_web_page(self):
        self.app.web_session = Mock(keyboard_field=None, snapshot={'url': 'https://example.com'})
        self.assertIsNone(ys.handle(self.app, 'YouTube', 'web'))
        self.assertEqual(self.app.handle('youtube', 'youtube'), 'youtube')   # on its own screen it says where you are

    def test_youtube_alone_is_a_favorite_or_help_topic_name_on_those_screens(self):
        self.app.web_session = Mock(keyboard_field=None)
        self.app.web_session.menu.level = 'menu'
        self.assertIsNone(ys.handle(self.app, 'YouTube', 'browser_menu'))
        self.assertIsNone(ys.handle(self.app, 'Watch YouTube', 'help'))
        self.started.assert_not_called()

    def test_video_keys_without_a_video_say_what_to_do(self):
        for key in ('K', 'Space', 'J', 'L', 'Right', '5', 'C', 'R', 'Q', 'N', 'P', 'A', 'F', 'H', 'S', 'I', 'D', 'O', 'T', 'U', 'W',
                    'Ctrl+Up', 'comma', 'M'):
            with self.subTest(key=key):
                self.said.clear()
                self.assertEqual(self.press(key), 'youtube')
                self.assertEqual(self.said[-1], ys.NO_VIDEO)

    def test_the_window_gives_letters_to_the_screen_not_to_first_letter_search(self):
        context = self.app.app_context('youtube')
        self.assertFalse(context['menu_letters'])
        from input_parity import screen_key
        self.assertEqual(screen_key('youtube', 'k', 0, context), 'K')
        self.assertEqual(screen_key('youtube', 'comma', 0, context), 'comma')
        # Some keyboards name the key by its character (seen with Tk on X11).
        self.assertEqual(screen_key('youtube', ',', 0, context), 'comma')
        self.assertEqual(screen_key('youtube', '.', 0, context), 'period')
        self.assertIsNone(screen_key('youtube', 'k', 0, context, typed_text='search'))   # a typed command keeps its letters
        self.assertEqual(screen_key('youtube', 'f', 4, context, typed_text='search'), 'Ctrl+F')

    def test_a_question_owns_the_keys_until_it_is_answered(self):
        screen = ys.screen(self.app)
        screen.video = {'id': 'x', 'title': 'A video', 'channel': 'C'}
        screen.ask(('like', True), 'Like A video? Say Yes or No.')
        self.assertEqual(self.app.app_context('youtube')['confirmation'], 'no')
        self.assertIsNone(self.app.menu_state('youtube'))
        self.assertIsNone(self.app.navigation_key('Up', 'youtube'))
        self.assertEqual(self.said[-1], 'Yes.')
        self.assertEqual(self.app.navigation_key('Enter', 'youtube'), 'yes')
        self.assertEqual(self.app.navigation_key('Escape', 'youtube'), 'no')
        self.assertEqual(self.app.handle('Banana', 'youtube'), 'youtube')
        self.assertEqual(self.said[-1], 'Say Yes or No.')

    def test_nothing_playing_leaves_radio_and_control_p_alone(self):
        hub = Mock(); hub.now_playing.return_value = 'Station: WGN. Playing.'; hub.command.return_value = 'Audio paused.'
        hub.player.kind = 'radio'
        self.app.MEDIA_HUB = hub
        self.assertEqual(self.app.now_playing(), 'Station: WGN. Playing.')
        self.app.handle('toggle audio', 'awake')
        self.assertEqual(self.said[-1], 'Audio paused.')

    def test_f1_opens_the_youtube_chapter_of_the_guide(self):
        self.assertEqual(self.press('F1'), 'help')
        self.assertTrue(self.said[-1].startswith('User guide. Watch YouTube. Open YouTube'), self.said[-1])

    def test_unexpected_page_trouble_is_said_plainly(self):
        screen = ys.screen(self.app)
        with patch.object(ys.YouTubeScreen, 'where', side_effect=TypeError('boom')):
            self.assertEqual(self.app.handle('where am i', 'youtube'), 'youtube')
        self.assertEqual(self.said[-1], ys.UNEXPECTED)


if __name__ == '__main__':
    unittest.main()
