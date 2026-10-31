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


if __name__ == '__main__':
    unittest.main()
