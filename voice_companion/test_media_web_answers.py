"""Will's answers to the consumer test (October 2026), through the real app.

- "What's playing?" and F3 say what plays now, on every screen.
- A radio stream that stops after it started is reported once, in plain words.
- "Play WGN" finds and plays a station from screens where words are commands.
- Deleting a web favorite asks Yes or No first, with No chosen.
- A favorite saved from a page returns you to the page and your place.
- "Microphone off" while writing: text in Command and dictation mode and in
  Dictation only mode; the command in Commands only mode.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
from media_hub import MediaHub
from media_player import STALL_SECONDS
from test_core_email_fixes import CompanionTestCase
from web_assistant import WebSession


class StreamPlayer:
    """Behaves like MediaPlayer and its audio element (media_player.py), without a browser.

    The element's state follows the player page's script: Pause, Stop and Control+P
    mark a user pause; Play clears it, and loads a failed or ended stream again; the
    position moves only while the network delivers sound."""

    def __init__(self):
        self.kind = self.title = self.url = None
        self.session = 0
        self.failed = False
        self.opened = []
        self.element = {}

    # The network, as the tests see it.
    def deliver(self, seconds):
        if not self.element['paused']: self.element['position'] += seconds

    def error(self, code=2):
        self.element['error'] = code

    def end(self):
        self.element.update(ended=True, paused=True)

    # The MediaPlayer methods the app uses.
    def open(self, url, title, kind):
        self.opened.append((url, title, kind))
        self.element = {'paused': False, 'ended': False, 'error': None, 'position': 0.0, 'user_paused': False,
                        'duration': None if url.startswith('http') and kind == 'radio' else 600.0}
        self.kind, self.title, self.url, self.failed = kind, title, url, False
        self.session += 1

    def streaming(self):
        return self.kind == 'radio' and str(self.url or '').startswith(('http://', 'https://'))

    def health(self):
        if not self.kind: return None
        e = self.element
        return {'paused': e['paused'], 'ended': e['ended'], 'error': e['error'], 'position': e['position'], 'user_paused': e['user_paused']}

    def status(self):
        if not self.kind: raise ValueError('Open a media file first.')
        return {'paused': self.element['paused'], 'position': self.element['position'], 'duration': self.element['duration'], 'failed': self.failed}

    def mark_failed(self):
        self.failed = True
        self.element.update(paused=True, user_paused=False)

    def control(self, action, seconds=15, position=None):
        if not self.kind: return None
        e = self.element
        if action == 'toggle':
            if not e['paused']:
                e.update(paused=True, user_paused=True); return True
            action = 'play'
        if action in ('pause', 'stop'):
            e.update(paused=True, user_paused=True)
            if action == 'stop': e['position'] = 0.0
            return e['position']
        if action == 'play':
            if self.failed or e['error'] is not None or e['ended']:
                e.update(error=None, ended=False, position=0.0)
            e.update(paused=False, user_paused=False)
            self.failed = False; self.session += 1
            return False
        return e['position']

    def set_ducked(self, ducked, factor=0.18):
        pass

    def close(self):
        self.kind = None


STATIONS = [{'name': 'WGN Radio 720', 'url': 'https://radio.test/wgn', 'country': 'US'},
            {'name': 'WGN', 'url': 'https://radio.test/wgn-am', 'country': 'US'},
            {'name': 'WGNB Christian Radio', 'url': 'https://radio.test/wgnb', 'country': 'US'},
            {'name': 'Jazz 24', 'url': 'https://radio.test/jazz', 'country': 'US'}]


TWO_WGN = [STATIONS[0], {'name': 'WGN Chicago', 'url': 'https://radio.test/wgn-chicago', 'country': 'US'}, STATIONS[3]]


def radio_hub(folder, stations=STATIONS):
    """A real MediaHub whose directory search answers from `stations`, as Radio Browser
    does: every station whose name contains the words searched for."""
    def directory(url):
        from urllib.parse import parse_qs, urlsplit
        name = parse_qs(urlsplit(url).query).get('name', [''])[0].casefold()
        return [dict(station) for station in stations if name in station['name'].casefold()]
    hub = MediaHub(folder, player=StreamPlayer(), fetch_json=directory)
    hub.choose_source('radio browser')
    hub.start_scheduler = lambda: None
    return hub


class _Backend:
    """The browser behind a WebSession; these tests never load a page."""
    regular = False


def web_text_box(folder, input_mode):
    """A real WebSession editing a message box on a page. Typing into the page
    is recorded instead of sent to a browser."""
    session = WebSession(folder, _Backend())
    session.snapshot = {'url': 'https://example.com/contact', 'title': 'Contact us', 'elements': []}
    session.keyboard_field = {'tag': 'textarea', 'key': 'message', 'label': 'Message', 'value': ''}
    session.input_mode = input_mode
    session.typed = []
    session.dictate_to_focus = lambda text, literal=False: (session.typed.append(text), 'Typed.')[1]
    return session


class MicrophoneOffWhileWritingTests(CompanionTestCase):
    """Will: in Command and dictation mode, "Microphone off" in a writing field is
    typed, and F9 turns the microphone off. In Dictation only mode it is dictated
    as said. In Commands only mode it turns the microphone off."""

    def setUp(self):
        super().setUp()
        for name, value in (('MICROPHONE_OFF', False), ('MICROPHONE_ANNOUNCED', False), ('note_buffer', []), ('web_session', None)):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(companion.PREFERENCES); p.start(); self.addCleanup(p.stop)

    def written(self, screen, input_mode):
        """Say "Microphone off." on a writing screen; return the text written there."""
        companion.INPUT_MODE = input_mode
        companion.MICROPHONE_OFF = False
        if screen == 'document':
            self.open_document().dictating = input_mode != 'commands'
        elif screen == 'email_draft':
            self.draft().dictating = input_mode != 'commands'
        elif screen == 'note':
            companion.note_buffer.clear()
        elif screen == 'web':
            companion.web_session = web_text_box(self.folder, input_mode)
        self.assertEqual(companion.handle('Microphone off.', screen), screen)
        if screen == 'document':
            return companion.document_text(companion.document)
        if screen == 'email_draft':
            return companion.document_text(companion.email_draft)
        if screen == 'note':
            return ' '.join(companion.note_buffer)
        return ' '.join(companion.web_session.typed)

    def test_the_words_are_written_in_command_and_dictation_mode_and_dictation_only(self):
        for input_mode in ('mixed', 'dictation'):
            for screen in ('document', 'email_draft', 'note', 'web'):
                with self.subTest(input_mode=input_mode, screen=screen):
                    self.assertIn('Microphone off', self.written(screen, input_mode))
                    self.assertFalse(companion.MICROPHONE_OFF)

    def test_commands_only_turns_the_microphone_off_in_every_writing_field(self):
        for screen in ('document', 'email_draft', 'note', 'web'):
            with self.subTest(screen=screen):
                self.said.clear()
                self.assertNotIn('Microphone off', self.written(screen, 'commands'))
                self.assertTrue(companion.MICROPHONE_OFF)
                self.assertEqual(self.said[-1], 'Microphone off. Voice Companion is not listening at all. Press F9 to turn it back on.')

    def test_f9_still_turns_it_off_while_writing(self):
        for screen in ('document', 'email_draft', 'note', 'web'):
            with self.subTest(screen=screen):
                companion.MICROPHONE_OFF = False
                self.assertEqual(companion.navigation_key('F9', screen), '@microphone')
                companion.handle('@microphone', screen, typed=True)
                self.assertTrue(companion.MICROPHONE_OFF)


class WebFavoriteTests(unittest.TestCase):
    """Deleting asks Yes or No first, with No chosen, like cancelling a download.
    Saving from a page goes back to the page and your place on it."""

    def setUp(self):
        import tempfile
        from test_web_assistant import FakeBackend
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.session = WebSession(temp.name, FakeBackend())
        self.session.auto_read = False
        self.said = []
        for name, value in (('web_session', self.session), ('APP_WINDOW', None), ('pending_website', None),
                            ('PREFERENCES', {'web_keep_open': False}), ('CONFIRM_CHOICE', 'yes'), ('NAMED_LIST', None),
                            ('MAIN_MENU_LEAD', ''), ('TEXT_MODE', True), ('INPUT_MODE', 'mixed')):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        for name in ('speak', 'speak_prompt', 'speak_status'):
            p = patch.object(companion, name, lambda text, *a, **k: self.said.append(str(text))); p.start(); self.addCleanup(p.stop)
        self.session.favorites.add('News', 'https://example.com/news')
        self.session.favorites.add('Weather', 'https://example.com/weather')

    def key(self, key, mode):
        """Press a key the way the window sends it, and handle what it asks for."""
        request = companion.navigation_key(key, mode)
        return companion.handle(request, mode, typed=True) if request else mode

    def ask_to_delete_news(self):
        mode = companion.handle('Delete favorite.', 'browser_menu')
        self.assertEqual(mode, 'browser_menu')
        self.said.clear()
        return companion.handle('News.', mode)

    def test_delete_asks_first_and_no_is_chosen(self):
        mode = self.ask_to_delete_news()
        self.assertEqual(self.said[-1], 'Delete favorite News? Say Yes or No. Escape keeps it.')
        self.assertEqual(companion.CONFIRM_CHOICE, 'no')
        self.assertEqual(companion.app_context(mode)['confirmation'], 'no')
        self.assertIsNotNone(self.session.favorites.get('News'))
        # Enter answers with the chosen No; the favorite stays.
        self.assertEqual(self.key('Enter', mode), 'browser_menu')
        self.assertTrue(self.said[-1].startswith('Not deleted. News'), self.said[-1])
        self.assertIsNotNone(self.session.favorites.get('News'))

    def test_up_arrow_then_enter_deletes(self):
        mode = self.ask_to_delete_news()
        self.key('Up', mode)
        self.assertEqual(self.said[-1], 'Yes.')
        self.key('Enter', mode)
        self.assertTrue(self.said[-1].startswith('Deleted favorite News. Weather'), self.said[-1])
        self.assertIsNone(self.session.favorites.get('News'))

    def test_spoken_and_typed_answers(self):
        for answer, kept in (('Yes, please.', False), ('No.', True), ('Escape', True)):
            with self.subTest(answer=answer):
                self.session.favorites.add('News', 'https://example.com/news') if self.session.favorites.get('News') is None else None
                mode = self.ask_to_delete_news()
                companion.handle(answer, mode)
                self.assertEqual(self.session.favorites.get('News') is not None, kept)
        for key, kept in (('Escape', True), ('Letter:n', True), ('Letter:y', False)):
            with self.subTest(key=key):
                self.session.favorites.add('News', 'https://example.com/news') if self.session.favorites.get('News') is None else None
                mode = self.ask_to_delete_news()
                self.key(key, mode)
                self.assertEqual(self.session.favorites.get('News') is not None, kept)

    def test_other_words_ask_again(self):
        mode = self.ask_to_delete_news()
        companion.handle('Weather', mode)
        self.assertEqual(self.said[-1], 'Delete favorite News? Say Yes or No. Escape keeps it.')
        self.assertIsNotNone(self.session.favorites.get('News'))
        self.assertIsNotNone(self.session.favorites.get('Weather'))

    def test_new_favorite_from_a_page_returns_to_the_page_and_place(self):
        self.session.open('https://example.com/story')
        self.session.reading.position = 42
        mode = companion.handle('New favorite.', 'web')
        self.assertEqual(mode, 'browser_menu')
        self.assertEqual(self.said[-1], 'New favorite. Say or type the favorite name, then press Enter. Escape cancels.')
        self.assertEqual(companion.handle('Story.', mode), 'web')
        self.assertEqual(self.said[-1], 'Saved favorite Story. Back to web page.')
        self.assertEqual(self.session.favorites.get('Story')['url'], 'https://example.com/story')
        self.assertEqual(self.session.reading.position, 42)
        # The Alt menu's Add favorite does the same.
        self.assertEqual(companion.handle('add favorite', 'web'), 'browser_menu')
        self.assertEqual(companion.handle('Story two', 'browser_menu'), 'web')

    def test_escape_while_naming_from_a_page_goes_back_to_the_page(self):
        self.session.open('https://example.com/story')
        mode = companion.handle('New favorite', 'web')
        self.assertEqual(self.key('Escape', mode), 'web')
        self.assertEqual(self.said[-1], 'Not saved. Back to web page.')
        self.assertIsNone(self.session.favorites.get('Story'))

    def test_new_favorite_from_the_browser_menu_stays_there(self):
        self.session.open('https://example.com/story')
        self.assertEqual(companion.handle('New favorite', 'browser_menu'), 'browser_menu')
        self.assertEqual(companion.handle('Story', 'browser_menu'), 'browser_menu')
        self.assertEqual(self.said[-1], 'Saved favorite Story. Web browser menu.')

    def test_save_favorite_as_on_a_page_stays_on_the_page(self):
        self.session.open('https://example.com/story')
        self.assertEqual(companion.handle('Save favorite as Story.', 'web'), 'web')
        self.assertEqual(self.said[-1], 'Saved favorite Story.')


class MediaTestCase(CompanionTestCase):
    def setUp(self):
        super().setUp()
        self.hub = radio_hub(self.folder)
        for name, value in (('MEDIA_HUB', self.hub), ('MEDIA_NAV', None), ('MEDIA_SECTION', 'radio'), ('web_session', None),
                            ('note_buffer', []), ('MEDIA_STREAM_ALERT', None), ('MEDIA_STREAM_NEXT_CHECK', 0.0),
                            ('NAMED_LIST', None), ('MAIN_MENU_LEAD', ''), ('pending_website', None)):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'sync_app_context'); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'speech_busy', lambda: self.busy); p.start(); self.addCleanup(p.stop)
        self.busy = False

    def play_station(self, name='WGN Radio 720', url='https://radio.test/wgn'):
        self.hub.stations = [{'name': name, 'url': url}]
        self.hub.station_index = 0
        return self.hub.play_station(1)


class WhatIsPlayingTests(MediaTestCase):
    def ask(self, words='What\'s playing?', mode='awake', typed=False):
        self.said.clear()
        self.assertEqual(companion.handle(words, mode, typed), mode)
        return self.said[-1]

    def test_nothing_playing_says_how_to_start(self):
        self.assertEqual(self.ask(), 'Nothing is playing. To start, say Play and a station name, such as Play WGN. '
                                     'Or choose Media on the main menu for radio, podcasts, and your own files.')
        with patch.object(companion, 'MEDIA_HUB', None):
            self.assertTrue(self.ask().startswith('Nothing is playing.'))

    def test_a_station_says_its_name_and_whether_it_is_paused(self):
        self.play_station()
        self.assertEqual(self.ask(), 'Station: WGN Radio 720. Playing.')
        self.hub.command('pause')
        self.assertEqual(self.ask('What station is this?'), 'Station: WGN Radio 720. Paused.')

    def test_a_podcast_episode_says_the_show_episode_place_and_length(self):
        self.hub.episodes = [{'title': 'The Big Short', 'url': 'https://feeds.test/1.mp3', 'show': 'Planet Money'}]
        self.hub.play_episode(1)
        self.hub.player.deliver(75)
        self.assertEqual(self.ask('What episode is this?'),
                         'Podcast: Planet Money. Episode: The Big Short. Playing. Position 1 minute 15 seconds of 10 minutes.')

    def test_a_downloaded_episode_a_recording_and_a_file(self):
        downloads = self.folder / 'Podcast Downloads'; downloads.mkdir()
        (downloads / 'Planet Money - The Big Short.mp3').write_bytes(b'x')
        self.hub.play_download(1)
        self.hub.command('pause')
        self.assertEqual(self.ask(), 'Podcast episode: Planet Money - The Big Short. Paused. Position 0 seconds of 10 minutes.')
        recordings = self.folder / 'Radio Recordings'; recordings.mkdir()
        (recordings / 'WGN 2026-10-28.mp3').write_bytes(b'x')
        self.hub.play_recording(1)
        self.assertEqual(self.ask(), 'Radio recording: WGN 2026-10-28. Playing. Position 0 seconds of 10 minutes.')
        song = self.folder / 'song.mp3'; song.write_bytes(b'x')
        self.hub.player.open(song.as_uri(), 'song.mp3', 'local')   # as Open a media file does
        self.assertEqual(self.ask(), 'File: song.mp3. Playing. Position 0 seconds of 10 minutes.')

    def test_recognizer_punctuation_and_other_ways_to_ask(self):
        self.play_station()
        self.assertIsNone(__import__('media_hub').now_playing_request("What's on?"))   # the Calendar's words
        for words in ('What is playing.', 'Whats playing', 'WHAT\'S PLAYING NOW?', 'What is this?', 'What am I listening to?',
                      'What station is this.', 'What episode is this?'):
            with self.subTest(words=words):
                self.assertEqual(self.ask(words), 'Station: WGN Radio 720. Playing.')

    def test_works_on_every_screen_where_words_are_commands(self):
        self.play_station()
        companion.mail_session = None
        for mode in ('awake', 'media', 'word_processor', 'media_menu', 'local_media', 'notepad_menu', 'files'):
            with self.subTest(mode=mode):
                self.assertEqual(self.ask(mode=mode), 'Station: WGN Radio 720. Playing.')

    def test_f3_works_on_every_screen_even_while_writing(self):
        self.play_station()
        self.open_document()
        for mode in ('awake', 'document', 'email_draft', 'note', 'web', 'mailbox', 'media', 'local_media', 'exit_confirm', 'sleep'):
            with self.subTest(mode=mode):
                self.said.clear()
                self.assertIsNone(companion.navigation_key('F3', mode))
                self.assertEqual(self.said, ['Station: WGN Radio 720. Playing.'])

    def test_f3_with_nothing_playing(self):
        self.said.clear()
        self.assertIsNone(companion.navigation_key('F3', 'awake'))
        self.assertTrue(self.said[-1].startswith('Nothing is playing.'))

    def test_writing_fields_keep_the_words(self):
        self.play_station()
        for input_mode in ('mixed', 'dictation'):
            companion.INPUT_MODE = input_mode
            document = self.open_document()
            companion.handle('What\'s playing?', 'document')
            self.assertEqual(companion.document_text(document), 'What\'s playing?', input_mode)
            companion.note_buffer.clear()
            companion.handle('What is this?', 'note')
            self.assertEqual(companion.note_buffer, ['What is this?'], input_mode)

    def test_commands_only_answers_in_a_document(self):
        self.play_station()
        companion.INPUT_MODE = 'commands'
        self.open_document()
        self.assertEqual(self.ask(mode='document'), 'Station: WGN Radio 720. Playing.')

    def test_what_is_this_is_left_alone_when_nothing_plays_and_on_the_calendar(self):
        self.ask('What is this?')
        self.assertFalse(any(text.startswith('Nothing is playing') for text in self.said), self.said)
        self.play_station()
        import calendar_screen
        with patch.object(calendar_screen, 'handle', return_value='calendar') as calendar:
            companion.handle('What is this?', 'calendar')
        calendar.assert_called_once()


class StreamFailureTests(MediaTestCase):
    """A stream that stops, errors or stalls after it started is reported once, at a
    quiet moment. A user pause or stop, the end of an episode or a file, and short
    buffering are not failures."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        # The window, as far as the main loop's quiet check sees it: when a key was last pressed.
        self.window = type('Window', (), {'last_navigation': 0.0})()

    def run_for(self, seconds, delivering=True, hearing=None):
        """The main loop for a while: the network delivers sound (or not), and the
        stream is checked every 2 seconds, as in the app."""
        self.said.clear()
        with patch.object(companion, 'APP_WINDOW', self.window):
            for _ in range(int(seconds / 0.5)):
                self.now += 0.5
                if delivering and self.hub.player.kind: self.hub.player.deliver(0.5)
                companion.poll_media_stream(self.now, hearing)
        return list(self.said)

    def test_steady_playback_and_short_buffering_say_nothing(self):
        self.play_station()
        self.assertEqual(self.run_for(60), [])
        self.assertEqual(self.run_for(STALL_SECONDS - 3, delivering=False), [])
        self.assertEqual(self.run_for(30), [])

    def test_a_stalled_stream_is_reported_once_and_play_tries_again(self):
        self.play_station()
        self.run_for(10)
        said = self.run_for(STALL_SECONDS + 4, delivering=False)
        self.assertEqual(said, ['WGN Radio 720 stopped playing. The station or the internet connection may be down. '
                                'Say Play or press Control+P to try again, or choose another station.'])
        self.assertTrue(self.hub.player.element['paused'])
        self.assertEqual(self.run_for(60, delivering=False), [])
        self.said.clear()
        self.assertEqual(companion.handle('What\'s playing?', 'awake'), 'awake')
        self.assertEqual(self.said[-1], 'Station: WGN Radio 720. Stopped, because the stream failed. '
                                        'Say Play or press Control+P to try again, or choose another station.')
        # Control+P tries again; a later failure is reported again.
        self.assertEqual(companion.handle(companion.navigation_key('Ctrl+P', 'awake'), 'awake', typed=True), 'awake')
        self.assertEqual(self.said[-1], 'Playing audio.')
        self.assertEqual(self.run_for(20), [])
        self.assertEqual(len(self.run_for(STALL_SECONDS + 4, delivering=False)), 1)

    def test_a_stream_error_or_end_is_reported(self):
        for fail in ('error', 'end'):
            with self.subTest(fail=fail):
                self.play_station()
                self.run_for(6)
                getattr(self.hub.player, fail)()
                self.assertEqual(len(self.run_for(4, delivering=False)), 1)

    def test_a_user_pause_or_stop_is_not_a_failure(self):
        for words in ('Pause.', 'Stop audio.', 'toggle audio'):
            with self.subTest(words=words):
                self.play_station()
                self.run_for(4)
                companion.handle(words, 'awake')
                self.assertEqual(self.run_for(60, delivering=False), [])

    def test_the_end_of_an_episode_a_recording_or_a_file_is_not_a_failure(self):
        self.hub.episodes = [{'title': 'The Big Short', 'url': 'https://feeds.test/1.mp3', 'show': 'Planet Money'}]
        self.hub.play_episode(1)
        self.run_for(4); self.hub.player.end()
        self.assertEqual(self.run_for(30, delivering=False), [])
        recordings = self.folder / 'Radio Recordings'; recordings.mkdir()
        (recordings / 'WGN.mp3').write_bytes(b'x')
        self.hub.play_recording(1)
        self.run_for(4); self.hub.player.end()
        self.assertEqual(self.run_for(30, delivering=False), [])

    def test_the_notice_waits_while_companion_speaks_keys_are_pressed_or_someone_speaks(self):
        self.play_station()
        self.run_for(4)
        self.busy = True
        self.assertEqual(self.run_for(STALL_SECONDS + 10, delivering=False), [])
        self.busy = False
        self.window.last_navigation = self.now + 0.5     # typing
        self.assertEqual(self.run_for(2, delivering=False), [])
        speaking = [True]
        self.assertEqual(self.run_for(10, delivering=False, hearing=lambda: speaking[0]), [])
        speaking[0] = False
        self.assertEqual(len(self.run_for(1, delivering=False, hearing=lambda: speaking[0])), 1)

    def test_old_news_is_dropped_when_something_else_starts(self):
        self.play_station()
        self.run_for(4)
        self.busy = True
        self.run_for(STALL_SECONDS + 4, delivering=False)
        self.play_station('Jazz 24', 'https://radio.test/jazz')
        self.busy = False
        self.assertEqual(self.run_for(4), [])

    def test_a_player_that_stops_answering_is_reported(self):
        self.play_station()
        self.run_for(4)
        def broken(): raise RuntimeError('Target page, context or browser has been closed')
        self.hub.player.health = broken
        self.assertEqual(self.run_for(3), ['WGN Radio 720 stopped, because the audio player closed. Choose the station again to play it.'])


class PlayStationByNameTests(MediaTestCase):
    def test_one_exact_name_plays_and_you_stay_where_you_are(self):
        for mode in ('awake', 'mailbox', 'word_processor', 'local_media', 'files'):
            with self.subTest(mode=mode):
                self.hub.player.opened.clear()
                self.said.clear()
                companion.mail_session = None
                self.assertEqual(companion.handle('Play WGN.', mode), mode)
                self.assertEqual(self.hub.player.opened, [('https://radio.test/wgn-am', 'WGN', 'radio')])
                self.assertEqual(self.said[-1], 'Playing WGN. Say pause or stop.')

    def test_other_ways_to_say_it(self):
        for words in ('Play station WGN.', 'Play WGN radio.', 'play, WGN', 'Please play WGN radio station.'):
            with self.subTest(words=words):
                self.hub.player.opened.clear()
                companion.handle(words, 'awake')
                self.assertEqual(self.hub.player.opened[-1][1], 'WGN')

    def test_several_matches_offer_a_short_list_on_the_radio_screen(self):
        hub = radio_hub(self.folder / 'two', TWO_WGN)
        companion.MEDIA_HUB = hub
        self.assertEqual(companion.handle('Play WGN.', 'awake'), 'media')
        self.assertEqual(self.said[-1], 'I found 2 stations for WGN. Choice 1 of 2: WGN Radio 720. '
                                        'Say next or previous to hear another, then that one to play it. Say cancel to stop.')
        self.assertEqual(hub.player.opened, [])
        # Arrows and Enter choose from the short list, as Radio search does.
        companion.navigation_key('Down', 'media')
        self.assertTrue(self.said[-1].startswith('WGN Chicago'), self.said[-1])
        companion.navigation_key('Enter', 'media')
        self.assertEqual(hub.player.opened, [('https://radio.test/wgn-chicago', 'WGN Chicago', 'radio')])

    def test_several_matches_by_voice(self):
        hub = radio_hub(self.folder / 'two', TWO_WGN)
        companion.MEDIA_HUB = hub
        mode = companion.handle('Play WGN.', 'awake')
        companion.handle('Next.', mode)
        self.assertTrue(self.said[-1].startswith('WGN Chicago'), self.said[-1])
        companion.handle('That one.', mode)
        self.assertEqual(hub.player.opened[-1][1], 'WGN Chicago')

    def test_a_close_match_plays_and_a_different_name_does_not_count(self):
        # WGNB is another station; WGN Radio 720 is the only close match.
        hub = radio_hub(self.folder / 'close', [STATIONS[0], STATIONS[2]])
        companion.MEDIA_HUB = hub
        self.assertEqual(companion.handle('Play WGN.', 'awake'), 'awake')
        self.assertEqual(hub.player.opened[-1][1], 'WGN Radio 720')

    def test_no_match_says_so_plainly(self):
        self.assertEqual(companion.handle('Play Radio Nowhere.', 'awake'), 'awake')
        self.assertEqual(self.said[-1], 'I could not find a station called Nowhere. Check the name, or say Radio to search for stations.')
        self.assertEqual(self.hub.player.opened, [])

    def test_the_radio_screen_searches_when_it_has_no_results_yet(self):
        mode = companion.handle('Radio', 'awake')
        self.assertEqual(mode, 'media')
        companion.handle('Play WGN', mode)
        self.assertEqual(self.hub.player.opened[-1][1], 'WGN')

    def test_play_keeps_its_other_meanings(self):
        downloads = self.folder / 'Podcast Downloads'; downloads.mkdir()
        (downloads / 'Show - Episode.mp3').write_bytes(b'x')
        companion.handle('Play download 1', 'awake')
        self.assertEqual(self.hub.player.opened[-1][2], 'podcast')
        self.hub.player.opened.clear()
        for words in ('Play.', 'Play radio.', 'Play audio.', 'Play podcast.', 'Play episode 2', 'Play station 3',
                      'Play recording 1', 'Play preset News', 'Play station.', 'Play it.', 'Play next episode'):
            with self.subTest(words=words):
                companion.handle(words, 'awake')
                self.assertEqual([o for o in self.hub.player.opened if o[1] in ('WGN', 'WGN Radio 720')], [], words)

    def test_writing_fields_and_web_pages_keep_the_words(self):
        for input_mode in ('mixed', 'dictation'):
            companion.INPUT_MODE = input_mode
            document = self.open_document()
            companion.handle('Play WGN radio.', 'document')
            self.assertEqual(companion.document_text(document), 'Play WGN radio.')
        companion.INPUT_MODE = 'mixed'
        companion.web_session = web_text_box(self.folder, 'mixed')
        companion.handle('Play WGN.', 'web')
        self.assertEqual(companion.web_session.typed, ['Play WGN.'])
        self.assertEqual(self.hub.player.opened, [])

    def test_list_screens_play_but_their_questions_keep_the_words(self):
        import calendar_screen, contacts_screen
        for mode in ('contacts', 'calendar', 'downloads', 'attachments'):
            with self.subTest(mode=mode):
                self.hub.player.opened.clear()
                self.assertEqual(companion.handle('Play WGN.', mode), mode)
                self.assertEqual(self.hub.player.opened[-1][1], 'WGN')
        # Naming a new event: "Play WGN radio show" is the name, not a station.
        self.hub.player.opened.clear()
        with patch.object(calendar_screen, 'PENDING', ('add', {})), patch.object(calendar_screen, 'handle', return_value='calendar') as calendar:
            companion.handle('Play WGN radio show', 'calendar')
        calendar.assert_called_once()
        with patch.object(contacts_screen, 'ADDING', {'step': 'name'}), patch.object(contacts_screen, 'handle', return_value='contacts') as contacts:
            companion.handle('Play WGN', 'contacts')
        contacts.assert_called_once()
        self.assertEqual(self.hub.player.opened, [])

    def test_the_tutorial_and_yes_or_no_questions_keep_the_words(self):
        for mode in ('tutorial', 'exit_confirm', 'power_confirm', 'update_offer'):
            with self.subTest(mode=mode):
                self.assertIsNone(companion.station_asked('Play WGN.', mode))
        self.assertEqual(companion.station_asked('Play WGN.', 'awake'), 'WGN')

    def test_commands_only_plays_from_a_document(self):
        companion.INPUT_MODE = 'commands'
        self.open_document()
        self.assertEqual(companion.handle('Play WGN', 'document'), 'document')
        self.assertEqual(self.hub.player.opened[-1][1], 'WGN')


if __name__ == '__main__':
    unittest.main()
