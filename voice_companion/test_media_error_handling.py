"""Night 3 sweep (October 31, 2026): media errors are said in plain words and never stop the app.

The servers here are real sockets on this computer, so the errors are the ones
Python's own HTTP code raises (an old SHOUTcast station answers "ICY 200 OK"; a
connection cut off in the middle of a reply)."""
import contextlib
import io
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import media_hub
from media_hub import MediaHub


def serve(reply, keep=None):
    """A one-reply web server on this computer. Returns its address."""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(8)
    server.settimeout(10)

    def run():
        while True:
            try: client, _ = server.accept()
            except OSError: return
            with client:
                try:
                    client.recv(65536)
                    client.sendall(reply)
                except OSError:
                    pass
    threading.Thread(target=run, daemon=True).start()
    if keep is not None: keep.addCleanup(server.close)
    return 'http://127.0.0.1:%d/stream.mp3' % server.getsockname()[1]


# An old SHOUTcast station: its first line is not HTTP.
SHOUTCAST = b'ICY 200 OK\r\nicy-name: Test\r\nContent-Type: audio/mpeg\r\n\r\n' + b'\xff\xfb' * 50
# A reply cut off in the middle of its first chunk.
CUT_OFF_JSON = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n100\r\n[{"name":'
CUT_OFF_AUDIO = (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: audio/mpeg\r\n\r\n'
                 b'4000\r\n' + b'\xff' * 100)


class NoPlayer:
    kind = None
    def open(self, *args): raise AssertionError('nothing should play')


class MediaErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.notices = []
        self.hub = MediaHub(self.temp.name, notify=self.notices.append, player=NoPlayer(),
                            clock=lambda: datetime(2026, 10, 31, 20, 0))

    def wait_for_notice(self, seconds=10):
        end = time.monotonic() + seconds
        while not self.notices and time.monotonic() < end: time.sleep(0.02)
        return self.notices

    # A broken reply from a directory or feed is a network problem, said as one.
    def test_a_reply_cut_off_says_the_directory_is_unavailable(self):
        address = serve(CUT_OFF_JSON, self)
        with patch.object(media_hub, 'RADIO_ROOTS', (address.rsplit('/', 1)[0],)):
            answer = self.hub.command('search jazz', section='radio')
        self.assertIn('The live directory is unavailable.', answer)

    def test_a_podcast_feed_cut_off_says_the_feed_is_unavailable(self):
        self.hub.shows = [{'name': 'Rome', 'feed': serve(CUT_OFF_JSON, self)}]
        self.hub.show_index = 0
        self.assertEqual(self.hub.command('that one', section='podcast'),
                         'The podcast feed is unavailable. Try again later.')

    def test_a_podcast_search_on_a_station_that_is_not_http(self):
        address = serve(SHOUTCAST, self)
        with patch.object(media_hub, 'APPLE_API', address):
            answer = self.hub.command('search podcasts for history', section='podcast')
        self.assertIn('Podcast search is not available right now.', answer)

    # Recording an old SHOUTcast station: before, the recorder stopped without a word.
    def test_recording_a_station_that_is_not_http_says_it_stopped(self):
        station = {'name': 'Old Station', 'url': serve(SHOUTCAST, self)}
        self.assertIn('Recording Old Station', self.hub.record_now(station, 1))
        self.assertTrue(self.wait_for_notice(), 'the recorder stopped without a word')
        self.assertIn('Radio recording stopped because the stream failed.', self.notices[-1])
        self.assertEqual(list((Path(self.temp.name) / 'Radio Recordings').glob('*')), [])

    # A download cut off: before, it stopped without a word and left a .part file.
    def test_a_download_cut_off_says_so_and_leaves_no_part_file(self):
        self.hub.episodes = [{'title': 'Rome', 'url': serve(CUT_OFF_AUDIO, self), 'show': 'History'}]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIn('Downloading Rome', self.hub.download_episode(1))
            self.assertTrue(self.wait_for_notice(), 'the download stopped without a word')
        self.assertIn('The episode download did not finish.', self.notices[-1])
        self.assertEqual(list((Path(self.temp.name) / 'Podcast Downloads').glob('*')), [])

    # A directory that answers with something other than a station list.
    def test_a_directory_reply_that_is_not_a_station_list_is_said_plainly(self):
        for reply in ({'error': 'rate limited'}, 'busy', None, 7):
            self.hub.fetch_json = lambda url, reply=reply: reply
            answer = self.hub.command('search jazz', section='radio')
            self.assertIn('The live directory is unavailable.', answer, reply)
            self.assertIn('No matching stations.', answer, reply)

    def test_damaged_station_entries_are_skipped(self):
        self.hub.fetch_json = lambda url: [
            'WGN', None, 5, {'name': 12, 'url': 'https://radio.example/12'},
            {'name': 'No address', 'url': None, 'url_resolved': 3},
            {'name': ['list'], 'url': 'https://radio.example/list'},
            {'name': 'Jazz 24', 'url': 'https://radio.example/jazz', 'country': None, 'tags': 5}]
        answer = self.hub.command('search jazz', section='radio')
        self.assertIn('Station 1: ', answer)
        names = [station['name'] for station in self.hub.stations]
        self.assertIn('Jazz 24', names)
        self.assertTrue(all(isinstance(name, str) for name in names), names)
        self.assertNotIn('No address', names)

    def test_a_damaged_iprd_catalog_is_refused_plainly(self):
        self.hub.fetch_json = lambda url: {'stations': [
            {'name': None, 'genres': 5, 'streams': 'https://radio.example/a'},
            {'name': 'Good', 'genres': None, 'streams': [{'url': 'https://radio.example/good', 'format': None}]},
            {'name': 7, 'genres': ['jazz', 3], 'streams': [None, {'url': 'https://radio.example/seven'}]}]}
        answer = self.hub.command('refresh stations', section='radio')
        self.assertIn('IPRD station catalog refreshed with 2 stations.', answer)
        saved = self.hub._stations_file('radio-iprd-stations.json')
        self.assertTrue(all(isinstance(entry['name'], str) and isinstance(entry['tags'], str) for entry in saved), saved)
        self.assertIn('Good', [entry['name'] for entry in saved])

    # The recordings folder cannot be made: before, the recorder stopped without a word.
    def test_a_recording_that_cannot_be_saved_says_so_and_keeps_the_station(self):
        (Path(self.temp.name) / 'Radio Recordings').write_text('not a folder', encoding='utf-8')
        station = {'name': 'Jazz 24', 'url': serve(SHOUTCAST, self)}
        self.hub.record_now(station, 1)
        recorder = self.hub.recording
        recorder.join(10)
        self.assertTrue(self.notices, 'the recorder stopped without a word')
        self.assertIn('could not be saved', self.notices[-1])
        self.assertIn('then try again', self.notices[-1])
        # The station is fine: it must not be hidden from searches for a day.
        self.assertTrue(self.hub._usable(station))
        self.assertIsNone(self.hub.recording)

    # The player's browser closed under it (Playwright raises its own Error).
    def test_chapters_with_a_player_that_does_not_answer(self):
        class Gone:
            kind = 'podcast'
            def control(self, *args, **kwargs): raise RuntimeError('Target page, context or browser has been closed')
        self.hub.player = Gone()
        self.hub.chapters = [{'time': 0, 'title': 'Start'}, {'time': 600, 'title': 'Middle'}]
        for spoken in ('next chapter', 'previous chapter'):
            self.assertEqual(self.hub.command(spoken, section='podcast'),
                             'The audio player could not respond. Try opening that audio again.')

    # A subscription whose saved episode list is damaged: the other shows still refresh.
    def test_one_damaged_subscription_does_not_stop_the_others_refreshing(self):
        import json
        shows = [{'name': 'Damaged', 'feed': 'https://pod.example/damaged', 'episodes': 'not a list'},
                 {'name': 'Fine', 'feed': 'https://pod.example/fine', 'episodes': [None, 5]}]
        (Path(self.temp.name) / 'podcast-subscriptions.json').write_text(json.dumps(shows), encoding='utf-8')
        episodes = [{'title': 'New', 'url': 'https://pod.example/new.mp3'}]
        self.hub._feed_episodes = lambda show: episodes
        self.assertEqual(self.hub.refresh_subscriptions(), 'Checked 2 of 2 subscribed podcast feeds.')
        saved = json.loads((Path(self.temp.name) / 'podcast-subscriptions.json').read_text(encoding='utf-8'))
        self.assertEqual([show['episodes'] for show in saved], [episodes, episodes])
        # Offline, a damaged saved list reads as no episodes, never an error.
        self.hub._feed_episodes = lambda show: (_ for _ in ()).throw(OSError('offline'))
        (Path(self.temp.name) / 'podcast-subscriptions.json').write_text(json.dumps(shows), encoding='utf-8')
        self.hub.shows = self.hub.saved_subscriptions()
        self.assertEqual(self.hub.open_podcast(1), 'The podcast feed is unavailable. Try again later.')
        self.assertEqual(self.hub.open_podcast(2), 'The podcast feed is unavailable. Try again later.')

    # Background podcast news: "1 new episode", never "1 new episodes".
    def test_podcast_news_counts_one_episode_and_one_feed_correctly(self):
        import json
        shows = [{'name': 'History', 'feed': 'https://pod.example/history',
                  'episodes': [{'title': 'Old', 'url': 'https://pod.example/old.mp3'}]},
                 {'name': 'Down', 'feed': 'https://pod.example/down', 'episodes': []}]
        (Path(self.temp.name) / 'podcast-subscriptions.json').write_text(json.dumps(shows), encoding='utf-8')
        def feed(show):
            if show['name'] == 'Down': raise OSError('offline')
            return [{'title': 'New', 'url': 'https://pod.example/new.mp3'}, {'title': 'Old', 'url': 'https://pod.example/old.mp3'}]
        self.hub._feed_episodes = feed
        self.hub.refresh_subscriptions()
        self.assertIn('1 new episode in History.', self.notices)
        self.assertIn('1 subscribed podcast feed could not refresh. Its saved list is still there.', self.notices)

    # A scheduled recording whose saved station has no address: reported as damaged, never a silent recorder.
    def test_a_scheduled_station_without_an_address_is_reported_as_damaged(self):
        import json
        job = {'station': {'name': 'WGN'}, 'when': '2026-10-31T20:00:00', 'minutes': 30, 'repeat': False, 'id': '1'}
        (Path(self.temp.name) / 'radio-schedule.json').write_text(json.dumps([job]), encoding='utf-8')
        self.hub._last_refresh_attempt = self.hub._last_podcast_refresh_attempt = time.time()
        self.hub.tick()
        self.assertIsNone(self.hub.recording)
        self.assertEqual(len(self.notices), 1, self.notices)
        self.assertIn('One scheduled radio recording is damaged and will be skipped.', self.notices[0])


class AppPathTests(unittest.TestCase):
    """Through the app, by key and by voice: the error is said in plain words."""
    def setUp(self):
        import companion
        self.companion = companion
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hub = MediaHub(self.temp.name, player=NoPlayer())
        self.hub.shows = [{'name': 'History', 'feed': 'https://pod.example/feed'}]
        self.hub.show_index = 0
        self.hub.episodes = [{'title': 'Rome', 'url': 'https://pod.example/rome.mp3', 'show': 'History'}]
        self.hub.browsing_episodes = True
        self.hub.episode_index = 0
        # The downloads folder cannot be made: a file has its name (a real FileExistsError).
        (Path(self.temp.name) / 'Podcast Downloads').write_text('not a folder', encoding='utf-8')
        for name, value in (('MEDIA_HUB', self.hub), ('MEDIA_NAV', None), ('MEDIA_SECTION', 'podcast')):
            patcher = patch.object(companion, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.said = []
        patcher = patch.object(companion, 'speak', lambda text, *args, **kwargs: self.said.append(text))
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_plain(self, text):
        self.assertNotIn('Errno', text)
        self.assertNotIn(self.temp.name, text)
        self.assertIn('then try again', text)

    def test_a_key_that_fails_is_said_and_the_app_keeps_going(self):
        # Control+D downloads the selected episode. Before, the error stopped the app.
        self.assertIsNone(self.companion.navigation_key('Ctrl+D', 'media'))
        self.assertTrue(self.said, 'nothing was said')
        self.assert_plain(self.said[-1])

    def test_a_spoken_command_that_fails_is_said_in_plain_words(self):
        self.assertEqual(self.companion.handle('download this episode', 'media'), 'media')
        self.assertTrue(self.said, 'nothing was said')
        self.assertTrue(self.said[-1].startswith('I could not complete that media request. '), self.said[-1])
        self.assert_plain(self.said[-1])

    def test_a_message_written_for_people_is_said_as_it_is(self):
        from media_navigation import spoken_error
        self.assertEqual(spoken_error(ValueError('That station number is unavailable. Search radio again.')),
                         'That station number is unavailable. Search radio again.')
        self.assertEqual(spoken_error(OSError('Radio Browser mirrors are unavailable.')),
                         'Radio Browser mirrors are unavailable.')
        self.assert_plain(spoken_error(IndexError('list index out of range')))
        self.assertEqual(spoken_error(OSError(28, 'No space left on device')), 'The disk is full. Make some room, then try again.')
        self.assert_plain(spoken_error(__import__('urllib.error').error.URLError('timed out')))

    def test_a_radiosure_file_that_is_not_there_is_named_plainly(self):
        self.companion.MEDIA_SECTION = 'radio'
        missing = str(Path(self.temp.name) / 'stations.rsd')
        self.companion.handle('import radiosure ' + missing, 'media')
        self.assertEqual(self.said[-1], 'I could not complete that media request. '
                         'That file was not found. Check its name and folder, then try again.')


class StuckPage:
    """The player page of a file that stopped answering: the page script throws, as
    the real page does when play() is refused ("This audio could not resume")."""
    url = ''
    def is_closed(self): return False
    def goto(self, url): self.url = url
    def evaluate(self, script, args=None):
        from playwright.sync_api import Error
        if isinstance(args, dict) and 'url' in args: return True   # open()
        raise Error('Error: This audio could not resume. Choose the station or episode again.')


class MediaPlayerScreenTests(unittest.TestCase):
    """The Media player screen (your own files) when the player stops answering."""
    def setUp(self):
        import companion
        from media_player import MediaPlayer
        self.companion = companion
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        player = MediaPlayer(Path(self.temp.name) / 'Audio Player')
        player.page = StuckPage()
        player.start = lambda: None
        player.open('file:///C:/Music/song.mp3', 'song.mp3', 'local')
        self.hub = MediaHub(self.temp.name, player=player)
        for name, value in (('MEDIA_HUB', self.hub),):
            patcher = patch.object(companion, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.said = []
        patcher = patch.object(companion, 'speak', lambda text, *args, **kwargs: self.said.append(text))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_player_commands_say_the_player_did_not_answer(self):
        for command in ('play or pause', 'pause', 'stop media', 'volume up', 'speed 1.5', 'media status',
                        'start of file', 'back 15 seconds'):
            with self.subTest(command=command):
                self.said.clear()
                self.assertEqual(self.companion.handle(command, 'local_media'), 'local_media')
                self.assertTrue(self.said, 'nothing was said')
                self.assertEqual(self.said[-1], 'The media player did not answer. Choose Open a media file to play the file again.')


class WindowErrorTests(unittest.TestCase):
    """The window's update loop runs every 15 ms and records each error it survives.
    An error that repeats on every update must not fill memory or the error log."""
    def test_a_repeating_window_error_is_kept_once(self):
        from app_window import keep_window_error
        errors = []
        said = [keep_window_error(errors, 'Traceback: the same problem') for _ in range(1000)]
        self.assertEqual(errors, ['Traceback: the same problem'])
        self.assertEqual(said.count(True), 1)

    def test_only_the_latest_window_errors_are_kept(self):
        from app_window import keep_window_error, WINDOW_ERROR_LIMIT
        errors = []
        for number in range(500): keep_window_error(errors, 'problem %d' % number)
        self.assertEqual(len(errors), WINDOW_ERROR_LIMIT)
        self.assertEqual(errors[-1], 'problem 499')
        # The first one is kept too: it is usually the cause of the rest.
        self.assertEqual(errors[0], 'problem 0')


if __name__ == '__main__':
    unittest.main()
