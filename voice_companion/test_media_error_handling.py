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
        self.assertTrue(self.wait_for_notice(), 'the recorder stopped without a word')
        self.assertIn('could not be saved', self.notices[-1])
        self.assertIn('then try again', self.notices[-1])
        # The station is fine: it must not be hidden from searches for a day.
        self.assertTrue(self.hub._usable(station))
        self.assertIsNone(self.hub.recording)


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
        self.assert_plain(spoken_error(OSError(28, 'No space left on device')))


if __name__ == '__main__':
    unittest.main()
