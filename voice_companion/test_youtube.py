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
        self.assertEqual(yp.filter_code({'sort': 'popularity'}), 'CAM=')
        self.assertEqual(yp.filter_code({'upload date': 'week', 'type': 'video', 'sort': 'popularity'}), 'CAMSBAgDEAE=')
        self.assertEqual(yp.filter_code({}), '')
        self.assertEqual(yp.search_url('cats  and dogs'), 'https://www.youtube.com/results?search_query=cats+and+dogs')
        self.assertTrue(yp.search_url('cats', {'type': 'channel'}).endswith('&sp=EgIQAg%3D%3D'))

    def test_spoken_filters_tolerate_speech(self):
        cases = {'Only channels.': ('type', 'channel'), 'This week': ('upload date', 'week'),
                 'Sort by view count.': ('sort', 'popularity'), 'Prioritize popularity': ('sort', 'popularity'),
                 'With subtitles': ('features', 'subtitles'), 'Filter by long videos': ('length', 'long'),
                 'Under 4 minutes': ('length', 'short'), '4K': ('features', '4k'), 'Relevance': ('sort', 'relevance')}
        for words, expected in cases.items():
            with self.subTest(words=words):
                self.assertEqual(yp.filter_request(words), expected)
        self.assertIsNone(yp.filter_request('cats'))
        self.assertEqual(yp.filter_summary({'upload date': 'week', 'sort': 'popularity'}), 'This week, sorted by popularity')
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

    def test_youtubes_short_ages_and_a_bare_count_are_said_in_words(self):
        # Real youtube.com, October 2026: a channel's videos say "960K" and "2w ago".
        item = yp.item_from_raw({'container': 'yt-lockup-view-model video', 'title': 'Moon base', 'href': '/watch?v=abc',
                                 'rows': [['960K', '2w ago']], 'badge': '9:01'})
        self.assertEqual(yp.spoken_item(item), 'Moon base. 9 minutes 1 second. 960 thousand views. 2 weeks ago')
        self.assertEqual([yp.spoken_age(t) for t in ('1mo ago', '9y ago', '3d ago', '1h ago', 'Streamed 5h ago')],
                         ['1 month ago', '9 years ago', '3 days ago', '1 hour ago', 'Streamed 5 hours ago'])
        self.assertEqual(yp.spoken_age('2 weeks ago'), '2 weeks ago')

    def test_chapters_written_in_a_description(self):
        self.assertEqual(yp.chapters_from_description('Hello\n0:00 Intro\n1:10 - Part one\n12:30 The end'),
                         [{'title': 'Intro', 'time': 0}, {'title': 'Part one', 'time': 70}, {'title': 'The end', 'time': 750}])
        self.assertEqual(yp.chapters_from_description('Meet me at 1:30 today.'), [])

    def test_addresses(self):
        self.assertEqual(yp.video_id('https://youtu.be/abc'), 'abc')
        self.assertEqual(yp.video_id('https://www.youtube.com/shorts/xyz'), 'xyz')
        self.assertEqual(yp.video_id('https://example.com/watch?v=abc'), '')


class NoBrowser(unittest.TestCase):
    """Companion with the YouTube screen and no browser at all."""
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


class ScreenWordTests(NoBrowser):
    """Where YouTube's words count."""
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
        for key in ('Space', 'Right', '5', 'C', 'R', 'Q', 'N', 'P', 'A', 'F', 'H', 'S', 'I', 'D', 'O', 'T', 'U', 'W',
                    'Ctrl+Up', 'comma', 'M'):
            with self.subTest(key=key):
                self.said.clear()
                self.assertEqual(self.press(key), 'youtube')
                self.assertEqual(self.said[-1], ys.NO_VIDEO)

    def test_k_is_no_longer_play_or_pause(self):
        # Will, October 30, 2026: Space and Enter play and pause; K is gone.
        self.assertNotIn('K', ys.KEYS)
        self.assertEqual(ys.KEYS['Space'], 'play or pause video')
        self.said.clear()
        self.assertEqual(self.press('K'), 'youtube')
        self.assertNotIn(ys.NO_VIDEO, self.said)
        screen = ys.screen(self.app)
        screen.push_player()
        from input_parity import screen_command
        self.assertIsNone(screen_command('K', 'youtube'))
        self.assertEqual(ys.navigation_key(self.app, 'Enter'), 'play or pause video')
        self.assertEqual(ys.navigation_key(self.app, 'Space'), 'play or pause video')
        self.assertIsNone(ys.navigation_key(self.app, 'K'))
        self.assertNotIn(' K', ys.HELP)

    def test_the_window_gives_letters_to_the_screen_not_to_first_letter_search(self):
        context = self.app.app_context('youtube')
        self.assertFalse(context['menu_letters'])
        from input_parity import screen_key
        self.assertEqual(screen_key('youtube', 'm', 0, context), 'M')
        self.assertEqual(screen_key('youtube', 'comma', 0, context), 'comma')
        # Some keyboards name the key by its character (seen with Tk on X11).
        self.assertEqual(screen_key('youtube', ',', 0, context), 'comma')
        self.assertEqual(screen_key('youtube', '.', 0, context), 'period')
        self.assertIsNone(screen_key('youtube', 'm', 0, context, typed_text='search'))   # a typed command keeps its letters
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

    def test_a_letter_typed_after_other_letters_is_never_a_video_key(self):
        # Typing "caf" sent F, which turned on full screen: "F" was taken for a function key.
        from input_parity import screen_key
        context = self.app.app_context('youtube')
        for letter in 'fjklmc':
            with self.subTest(letter=letter):
                self.assertIsNone(screen_key('youtube', letter, 0, context, typed_text='ca'))
        self.assertEqual(screen_key('youtube', 'F1', 0, context, typed_text='ca'), 'F1')
        self.assertEqual(screen_key('youtube', 'F5', 0, context, typed_text='ca'), 'F5')

    def test_unexpected_page_trouble_is_said_plainly(self):
        screen = ys.screen(self.app)
        with patch.object(ys.YouTubeScreen, 'where', side_effect=TypeError('boom')):
            self.assertEqual(self.app.handle('where am i', 'youtube'), 'youtube')
        self.assertEqual(self.said[-1], ys.UNEXPECTED)


class MenuAndSearchTests(NoBrowser):
    """The YouTube menu and the search box, with no browser: lists and searches are recorded, not loaded."""
    def setUp(self):
        super().setUp()
        self.searched, self.loaded = [], []
        def search(screen, query=None):
            self.searched.append(query)
            return 'Results for ' + str(query) + '.'
        def load(screen, name, lead=''):
            self.loaded.append(name)
            return name + ' list.'
        self.stack.enter_context(patch.object(ys.YouTubeScreen, 'search', search))
        self.stack.enter_context(patch.object(ys.YouTubeScreen, 'load', load))

    def test_youtube_opens_on_the_youtube_menu(self):
        self.assertEqual(self.app.handle('YouTube.', 'media_menu'), 'youtube')
        self.assertEqual(self.said[-1], 'YouTube menu. Search (S), 1 of 9. Control+F.')
        self.started.assert_not_called()   # the menu is said at once; no page loads for it
        names, index, _ = self.app.menu_state('youtube')
        self.assertEqual(names, ['Search', 'Home', 'Subscriptions', 'New from subscriptions', 'History', 'Watch later', 'Liked videos',
                                 'My playlists', 'Sign in or sign out'])   # not known yet: no page has said
        self.assertEqual(index, 0)
        self.press('Down')
        self.assertEqual(self.said[-1], 'Home (H), 2 of 9.')
        self.press('Right')   # as in the other menus, Right and Left move too
        self.assertEqual(self.said[-1], 'Subscriptions (S), 3 of 9.')
        self.press('Home'); self.press('Up')
        self.assertEqual(self.said[-1], 'Start of list. Search (S), 1 of 9. Control+F.')
        self.press('End'); self.press('Down')
        self.assertEqual(self.said[-1], 'End of list. Sign in or sign out (S), 9 of 9.')

    def test_the_account_item_says_what_youtube_said_last_time(self):
        # Will, October 29: it said Sign in while he was signed in. A page's answer is kept for next time.
        s = ys.screen(self.app)
        s.signed_in = True
        self.assertEqual((self.root / 'youtube-account.json').read_text(encoding='utf-8'), '{"signed_in": true}')
        with patch.object(ys, 'SCREEN', None):
            fresh = ys.screen(self.app)
            self.assertIs(fresh.signed_in, True)
            self.assertEqual(fresh.label({'kind': 'menu'}, ('@account', '', '@account')), 'Sign out')
            fresh.signed_in = False
            self.assertEqual(fresh.label({'kind': 'menu'}, ('@account', '', '@account')), 'Sign in')

    def test_first_letters_move_in_the_menu_and_are_not_video_keys(self):
        from input_parity import screen_key
        self.app.handle('YouTube.', 'awake')
        context = self.app.app_context('youtube')
        self.assertTrue(context['menu_letters'])
        self.assertIsNone(screen_key('youtube', 'h', 0, context))   # H lists chapters only on a video list
        self.assertEqual(screen_key('youtube', 'f', 4, context), 'Ctrl+F')
        self.press('Letter:h')
        self.assertEqual(self.said[-1], 'Home (H), 2 of 9.')
        self.press('Letter:h')
        self.assertEqual(self.said[-1], 'History (H), 5 of 9.')
        self.press('Letter:z')
        self.assertEqual(self.said[-1], 'No menu item starts with Z.')

    def test_enter_on_a_menu_item_opens_it_and_escape_comes_back(self):
        self.app.handle('YouTube.', 'awake')
        self.press('Down'); self.press('Enter')
        self.assertEqual(self.loaded, ['home'])
        for name, feed in (('Watch later', 'watch_later'), ('My playlists', 'playlists'), ('Liked videos', 'liked'),
                           ('History', 'history'), ('Subscriptions', 'channels'), ('New from subscriptions', 'subscriptions')):
            with self.subTest(name=name):
                self.assertEqual(self.app.handle(name, 'youtube'), 'youtube')
                self.assertEqual(self.loaded[-1], feed)
        # Escape on the menu goes back to the Media menu, on YouTube.
        self.assertEqual(self.press('Escape'), 'media_menu')
        self.assertEqual(self.said[-1], 'Media menu. YouTube (Y), 4 of 4.')

    def test_control_f_asks_for_words_and_typing_goes_into_the_box(self):
        from input_parity import screen_key
        self.app.handle('YouTube.', 'awake')
        self.press('Down')
        self.assertEqual(self.press('Ctrl+F'), 'youtube')
        self.assertEqual(self.said[-1], ys.SEARCH_PROMPT)
        context = self.app.app_context('youtube')
        self.assertTrue(context['task_input'])
        self.assertFalse(context['menu_letters'])
        self.assertIsNone(self.app.menu_state('youtube'))
        for letter in 'kjlfmcs':
            with self.subTest(letter=letter):
                self.assertIsNone(screen_key('youtube', letter, 0, context))
                self.assertIsNone(screen_key('youtube', letter, 0, context, typed_text='ca'))
        # The window sends typed words as a typed command; they search at once.
        self.assertEqual(self.app.handle_keyboard('cats and dogs', 'youtube'), 'youtube')
        self.assertEqual(self.searched, ['cats and dogs'])
        self.assertNotIn('task_input', self.app.app_context('youtube'))

    def test_choosing_search_in_the_menu_or_saying_it_asks_too(self):
        self.app.handle('YouTube.', 'awake')
        self.press('Enter')
        self.assertEqual(self.said[-1], ys.SEARCH_PROMPT)
        self.press('Escape')
        self.assertEqual(self.said[-1], 'Search canceled. YouTube menu. Search (S), 1 of 9. Control+F.')
        self.assertNotIn('task_input', self.app.app_context('youtube'))
        self.app.handle('Search YouTube.', 'youtube')
        self.assertEqual(self.said[-1], ys.SEARCH_PROMPT)
        self.app.handle('Cancel.', 'youtube')
        self.assertTrue(self.said[-1].startswith('Search canceled.'))
        # From another screen, "Search YouTube" opens YouTube and asks.
        self.assertEqual(self.app.handle('Search YouTube.', 'awake'), 'youtube')
        self.assertEqual(self.said[-1], ys.SEARCH_PROMPT)
        self.assertEqual(self.searched, [])

    def test_dictated_words_are_said_back_and_confirmed(self):
        self.app.handle('YouTube.', 'awake')
        self.app.handle('Search.', 'youtube')
        self.assertEqual(self.app.handle('Confirm.', 'youtube'), 'youtube')
        self.assertEqual(self.said[-1], 'Say or type what to search for first. Escape cancels.')
        for confirm in ('Confirm.', 'Okay.', 'Confirm that.', 'Yes.', 'OK'):
            with self.subTest(confirm=confirm):
                self.searched.clear()
                self.app.handle('Search.', 'youtube')
                self.assertEqual(self.app.handle('Jazz piano.', 'youtube'), 'youtube')
                self.assertEqual(self.said[-1], 'Jazz piano. Press Enter or say confirm to search.')
                self.assertEqual(self.searched, [])   # a misheard word never searches by itself
                request = self.app.app_context('youtube')['entry_request']
                self.assertEqual(request[1], 'Jazz piano')   # the words wait in the box, ready to change
                self.app.handle(confirm, 'youtube')
                self.assertEqual(self.searched, ['Jazz piano'])
                self.assertEqual(self.app.app_context('youtube')['entry_request'][1], '')   # the box is empty again
        # Enter with an empty box searches the words said.
        self.searched.clear()
        self.app.handle('Search.', 'youtube')
        self.app.handle('Search for bread recipes', 'youtube')
        self.assertEqual(self.said[-1], 'bread recipes. Press Enter or say confirm to search.')
        self.press('Enter')
        self.assertEqual(self.searched, ['bread recipes'])
        # A new word replaces the old one before it is confirmed.
        self.searched.clear()
        self.app.handle('Search.', 'youtube')
        self.app.handle('Cats.', 'youtube'); self.app.handle('Dogs.', 'youtube'); self.app.handle('Yes.', 'youtube')
        self.assertEqual(self.searched, ['Dogs'])

    def test_words_in_the_search_box_are_not_commands(self):
        self.app.handle('YouTube.', 'awake')
        for words in ('Time.', 'Bigger text', 'History', 'Next video', 'Like', 'Help with the dishes'):
            with self.subTest(words=words):
                self.app.handle('Search.', 'youtube')
                self.assertEqual(self.app.handle(words, 'youtube'), 'youtube')
                self.assertEqual(self.said[-1], words.rstrip('?.') + '. Press Enter or say confirm to search.')
                self.app.handle('Cancel', 'youtube')
        self.assertEqual(self.loaded, [])
        # Help, sleep and Escape still work while the box is open.
        self.app.handle('Search.', 'youtube')
        self.assertEqual(self.app.handle('Help with YouTube', 'youtube'), 'help')
        self.app.handle('Search.', 'youtube')
        with patch.object(self.app, 'speech_control', return_value=None):
            self.assertEqual(self.app.handle('Go to sleep.', 'youtube'), 'sleep')
        self.app.handle('Wake up.', 'sleep')
        self.app.handle('Search.', 'youtube')
        self.assertEqual(self.press('Escape'), 'youtube')
        self.assertTrue(self.said[-1].startswith('Search canceled.'))

    def test_escape_from_a_list_goes_back_to_the_menu(self):
        screen = ys.screen(self.app)
        self.app.handle('YouTube.', 'awake')
        self.press('Down')
        screen.push({'kind': 'list', 'name': 'home', 'title': 'Home', 'url': yp.feed_url('home'),
                     'items': [{'kind': 'video', 'id': 'a', 'title': 'A', 'url': '', 'channel': ''}]})
        self.assertFalse(self.app.app_context('youtube')['menu_letters'])   # letters are video keys on a list
        self.assertEqual(self.press('Escape'), 'youtube')
        self.assertEqual(self.said[-1], 'YouTube menu. Home (H), 2 of 9.')
        self.assertEqual(self.app.handle('YouTube menu', 'youtube'), 'youtube')
        self.assertEqual(self.said[-1], 'YouTube menu. Home (H), 2 of 9.')
        # F5 on the menu has no list to load again; it never opens Home by itself.
        self.press('F5')
        self.assertEqual(self.said[-1], 'There is no list here to load again. Home (H), 2 of 9.')
        self.assertEqual(self.loaded, [])


class FilterTests(unittest.TestCase):
    def test_a_filter_code_reads_back_as_filters(self):
        for filters in ({'upload date': 'week', 'type': 'video', 'sort': 'popularity'}, {'features': 'live', 'length': 'long'},
                        {'type': 'shorts'}, {}):
            with self.subTest(filters=filters):
                self.assertEqual(yp.decode_filters(yp.filter_code(filters)), dict(yp.NO_FILTERS, **filters))
        self.assertEqual(yp.decode_filters('EgIIAw%253D%253D'), dict(yp.NO_FILTERS, **{'upload date': 'week'}))   # as in a link
        self.assertEqual(yp.decode_filters('not a code!'), yp.NO_FILTERS)
        self.assertEqual(yp.decode_filters(''), yp.NO_FILTERS)

    """YouTube's search filters as they are now (January 2026: Prioritize, Shorts, no Last hour)."""
    def test_the_filter_choices_youtube_offers_now(self):
        labels = {name: [label for _, label, _ in yp.FILTERS[name][1]] for name in yp.FILTER_ORDER}
        self.assertEqual(labels['upload date'], ['Any time', 'Today', 'This week', 'This month', 'This year'])
        self.assertEqual(labels['type'], ['Any type', 'Videos', 'Shorts', 'Channels', 'Playlists', 'Movies'])
        self.assertEqual(labels['length'], ['Any length', 'Under 3 minutes', '3 to 20 minutes', 'Over 20 minutes'])
        self.assertEqual(labels['sort'], ['Relevance', 'Popularity'])
        self.assertEqual(yp.filter_code({'sort': 'popularity'}), 'CAM=')
        self.assertEqual(yp.filter_code({'upload date': 'week', 'type': 'video', 'sort': 'popularity'}), 'CAMSBAgDEAE=')
        self.assertEqual(yp.filter_code({'length': 'medium'}), 'EgIYAw==')

    def test_a_filter_youtube_took_away_says_what_to_use_instead(self):
        self.assertEqual(yp.filter_request('Sort by view count'), ('sort', 'popularity'))
        self.assertEqual(yp.filter_request('Most popular'), ('sort', 'popularity'))
        self.assertEqual(yp.filter_request('Only shorts'), ('type', 'shorts'))
        self.assertEqual(yp.filter_request('Under 3 minutes'), ('length', 'short'))
        self.assertEqual(yp.filter_request('sort by upload date'), ('gone', 'sort by date'))
        self.assertEqual(yp.filter_request('Last hour'), ('gone', 'last hour'))
        self.assertEqual(yp.filter_request('Sort by rating'), ('gone', 'sort by rating'))

    def test_filters_youtube_gives_on_the_page_are_read_as_lists(self):
        raw = [{'title': 'Upload date', 'filters': [
                    {'label': 'Today', 'url': '/results?search_query=cats&sp=EgIIAg%253D%253D', 'params': 'EgIIAg%3D%3D', 'selected': False, 'disabled': False},
                    {'label': 'This week', 'url': '/results?search_query=cats', 'params': '', 'selected': True, 'disabled': False}]},
               {'title': 'Prioritize', 'filters': [
                    {'label': 'Relevance', 'url': '', 'params': '', 'selected': False, 'disabled': True},
                    {'label': 'Popularity', 'url': '/results?search_query=cats&sp=CAMSAggD', 'params': '', 'selected': False, 'disabled': False}]},
               {'title': 'Duration', 'filters': []}, 'junk']
        groups = yp.page_filter_groups(raw)
        self.assertEqual([g['name'] for g in groups], ['upload date', 'sort'])
        today, week = groups[0]['choices']
        self.assertEqual((today['label'], today['sp'], today['on']), ('Today', 'EgIIAg==', False))
        self.assertEqual((week['label'], week['sp'], week['on']), ('This week', '', True))   # its link takes it away
        self.assertTrue(groups[1]['choices'][0]['disabled'])
        self.assertEqual(groups[1]['choices'][1]['sp'], 'CAMSAggD')


if __name__ == '__main__':
    unittest.main()


class OwnChromeRescueTests(unittest.TestCase):
    """Will, October 30, 2026: videos played about a minute in Voice Companion's Chrome, then YouTube said
    "Something went wrong". The same video played in his own Chrome, so the video moves there."""

    def screen(self, linked=True, engine='chrome'):
        root = Path(tempfile.mkdtemp())
        m = Mock(PREFERENCES={}, APP=root, APP_WINDOW=None)
        s = ys.YouTubeScreen(m)
        s.video = {'id': 'vid1', 'title': 'Mr. Ghost', 'channel': 'Radio', 'live': False}
        s.views = [{'kind': 'menu', 'name': 'menu', 'title': 'YouTube menu', 'items': [], 'index': 0},
                   {'kind': 'list', 'name': 'search', 'title': 'Results for ghost', 'url': 'https://www.youtube.com/results?search_query=ghost',
                    'items': [], 'index': 0}]
        web = Mock(); web.adopt_connected_browser.return_value = True
        s.web = lambda: web
        s.engine = lambda: engine
        s.link_own_chrome = Mock(return_value=linked)
        s.close_pages = Mock(); s.go = Mock(return_value='')
        s.play = Mock(return_value=ys.OWN_CHROME_MOVED + 'Playing Mr. Ghost.')
        s.player = Mock()
        return s, m

    STATE = {'error': 'Something went wrong. Refresh or try again later. Learn more', 'playability': '', 'time': 65}

    def test_a_video_youtube_stops_goes_on_in_your_own_chrome_from_where_it_stopped(self):
        s, m = self.screen()
        said = s.own_chrome_rescue(dict(self.STATE), at=64)
        self.assertEqual(said, ys.OWN_CHROME_MOVED + 'Playing Mr. Ghost.')
        self.assertIn('vid1', s.link_own_chrome.call_args[0][0])
        self.assertIn('&t=64s', s.link_own_chrome.call_args[0][0])
        s.go.assert_called_once_with('https://www.youtube.com/results?search_query=ghost')   # the results come too
        s.player.seek.assert_called_once_with(64)
        self.assertIs(m.PREFERENCES['youtube_own_chrome'], True)   # next time YouTube starts there

    def test_without_the_extension_youtubes_words_are_said(self):
        s, m = self.screen(linked=None)
        self.assertEqual(s.own_chrome_rescue(dict(self.STATE)), 'YouTube says: Something went wrong. Refresh or try again later. '
                         'Press Enter to try again, or choose another video. ' + ys.OWN_CHROME_NEEDED)
        # Written once for a helper, not twice.
        self.assertEqual(len((m.APP / 'youtube-errors.txt').read_text(encoding='utf-8').splitlines()), 1)

    def test_a_sign_in_check_or_your_own_chrome_is_never_moved(self):
        s, _ = self.screen()
        self.assertEqual(s.own_chrome_rescue(dict(self.STATE, playability='LOGIN_REQUIRED')), '')
        s, _ = self.screen(engine='regular')
        self.assertEqual(s.own_chrome_rescue(dict(self.STATE)), '')
        s.link_own_chrome.assert_not_called()

    def test_your_own_chrome_that_does_not_link_says_so(self):
        s, _ = self.screen(linked=ys.OWN_CHROME_NOT_LINKED)
        said = s.own_chrome_rescue(dict(self.STATE))
        self.assertTrue(said.startswith(ys.OWN_CHROME_NOT_LINKED + ' YouTube says: Something went wrong.'), said)


class LinkOwnChromeTests(unittest.TestCase):
    def screen(self):
        m = Mock(PREFERENCES={}, APP=Path(tempfile.mkdtemp()), APP_WINDOW=None)
        return ys.YouTubeScreen(m), m

    def test_linking(self):
        import threading
        import browser_cdp_relay
        import chrome_setup
        s, m = self.screen()
        relay = Mock(); relay.connected = threading.Event()
        with patch.object(browser_cdp_relay, 'prepared', lambda: None):
            self.assertIsNone(s.link_own_chrome('https://www.youtube.com/'))
        relay.connected.set()
        with patch.object(browser_cdp_relay, 'prepared', lambda: relay):
            self.assertIs(s.link_own_chrome('https://www.youtube.com/'), True)   # already linked: nothing opens
        relay.connected.clear()
        opened = []

        class Chrome:
            state = 'enabled'
            def supported(self): return True
            def extension_state(self): return self.state
            def open_chrome(self, address):
                opened.append(address); relay.connected.set(); return True
        with patch.object(browser_cdp_relay, 'prepared', lambda: relay), patch.object(chrome_setup, 'WindowsChrome', Chrome):
            self.assertIs(s.link_own_chrome('https://www.youtube.com/watch?v=x', 'Opening.'), True)
            self.assertEqual(opened, ['https://www.youtube.com/watch?v=x'])
            m.speak_status.assert_called_with('Opening.')
            relay.connected.clear(); Chrome.state = None
            self.assertIsNone(s.link_own_chrome('https://www.youtube.com/'))   # no extension: never opened
            self.assertEqual(len(opened), 1)

    def test_your_own_chrome_opens_when_it_is_not_linked_yet(self):
        """Will, October 31, 2026: ads played and the video stopped after a minute. The real
        browser_cdp_relay.current() is None until Chrome links, so asking it meant your own
        Chrome never opened. Nothing in browser_cdp_relay is replaced here but the relay."""
        import threading
        import browser_cdp_relay
        import chrome_setup
        s, m = self.screen()
        relay = Mock(); relay.connected = threading.Event()
        opened = []

        class Chrome:
            def supported(self): return True
            def extension_state(self): return 'enabled'
            def open_chrome(self, address):
                opened.append(address); relay.connected.set(); return True
        with patch.object(browser_cdp_relay, '_RELAY', relay), patch.object(chrome_setup, 'WindowsChrome', Chrome):
            self.assertIsNone(browser_cdp_relay.current())   # not linked yet
            self.assertIs(s.link_own_chrome('https://www.youtube.com/', ys.OWN_CHROME_START), True)
        self.assertEqual(opened, ['https://www.youtube.com/'])


class AlwaysOwnChromeTests(unittest.TestCase):
    """Will, October 30, 2026: "it should just do that all the way around, I don't want playback
    interruptions at all". YouTube starts in your own Chrome whenever it has the extension."""

    def go(self, running, saved=None):
        m = Mock(PREFERENCES={}, APP=Path(tempfile.mkdtemp()), APP_WINDOW=None)
        s = ys.YouTubeScreen(m)
        web = Mock(injected_backend=False); web.running_browser.return_value = running
        web._saved_browser.return_value = saved
        web._navigate.return_value = ''
        s.web = lambda: web
        s.link_own_chrome = Mock(return_value=True)
        s.lists = Mock()
        s.go('https://www.youtube.com/')
        return s, web

    def test_youtube_starts_in_your_own_chrome(self):
        s, web = self.go('chrome')
        s.link_own_chrome.assert_called_once_with('https://www.youtube.com/', ys.OWN_CHROME_START)
        web.adopt_connected_browser.assert_called_once()

    def test_embedded_edge_as_the_default_gives_way_too(self):
        s, _ = self.go('webview2')
        s.link_own_chrome.assert_called_once()

    def test_a_chosen_embedded_edge_is_kept(self):
        s, _ = self.go('webview2', saved='webview2')
        s.link_own_chrome.assert_not_called()


class CloseOwnChromeTests(unittest.TestCase):
    """Will, October 31, 2026: "when YouTube or voice companion closes, chrome should close as well."
    Only a Chrome window Companion opened for YouTube closes; one you opened yourself stays."""

    class Desktop:
        def __init__(self, handles):
            self.handles, self.closed = set(handles), []
        def windows(self):
            from window_hiding import Window
            return [Window(h, 'Chrome_WidgetWin_1', 'YouTube - Google Chrome', False, False, False) for h in sorted(self.handles)]
        def exists(self, handle):
            return handle in self.handles
        def close(self, handle):
            self.closed.append(handle); self.handles.discard(handle); return True

    def screen(self, before, after):
        import threading
        import browser_cdp_relay
        import chrome_setup
        m = Mock(PREFERENCES={}, APP=Path(tempfile.mkdtemp()), APP_WINDOW=None, web_session=None)
        s = ys.YouTubeScreen(m)
        s.CHROME_WINDOW_WAIT = 0
        ys.SCREEN = s
        self.addCleanup(setattr, ys, 'SCREEN', None)
        desktop = self.Desktop(before)
        s.desktop = lambda: desktop
        relay = Mock(); relay.connected = threading.Event()

        class Chrome:
            def supported(self): return True
            def extension_state(self): return 'enabled'
            def open_chrome(self, address):
                desktop.handles |= set(after); relay.connected.set(); return True
        with patch.object(browser_cdp_relay, 'prepared', lambda: relay), patch.object(chrome_setup, 'WindowsChrome', Chrome), \
                patch('atexit.register') as register:
            self.assertIs(s.link_own_chrome('https://www.youtube.com/', ys.OWN_CHROME_START), True)
        return s, m, desktop, register

    def test_the_chrome_window_companion_opened_closes_when_youtube_closes(self):
        s, m, desktop, register = self.screen(before={7}, after={42})
        self.assertEqual(s.own_chrome_windows, {42})
        register.assert_called_once_with(ys.close_own_chrome_at_exit, s)   # and when Companion closes
        ys.main_menu(m)
        self.assertEqual(desktop.closed, [42])     # window 7 was yours: it stays open
        self.assertEqual(s.own_chrome_windows, set())

    def test_chrome_you_already_had_open_is_never_closed(self):
        s, m, desktop, register = self.screen(before={7}, after=set())   # the tab joined your window
        self.assertEqual(s.own_chrome_windows, set())
        register.assert_not_called()
        self.assertFalse(s.close_own_chrome())
        self.assertEqual(desktop.closed, [])

    def test_leaving_youtube_with_nothing_playing_closes_it_but_a_playing_video_keeps_it(self):
        s, m, desktop, _ = self.screen(before=set(), after={42})
        s.has_video = lambda: True
        ys.leave(m, s)
        self.assertEqual(desktop.closed, [])       # the video plays on, like radio
        s.has_video = lambda: False
        ys.leave(m, s)
        self.assertEqual(desktop.closed, [42])

    def test_companion_closing_closes_it(self):
        s, m, desktop, _ = self.screen(before=set(), after={42})
        ys.close_own_chrome_at_exit(s)
        self.assertEqual(desktop.closed, [42])
        ys.close_own_chrome_at_exit(s)             # twice is safe
        self.assertEqual(desktop.closed, [42])

    def test_a_window_already_closed_by_you_is_skipped(self):
        s, m, desktop, _ = self.screen(before=set(), after={42})
        desktop.handles.clear()
        self.assertFalse(s.close_own_chrome())
        self.assertEqual(desktop.closed, [])

    def test_with_audio_kept_playing_the_main_menu_keeps_the_video_and_chrome(self):
        s, m, desktop, _ = self.screen(before=set(), after={42})
        m.PREFERENCES['keep_audio_playing'] = True
        s.has_video = lambda: True; s.owner = True
        ys.main_menu(m)
        self.assertEqual(desktop.closed, [])
