"""Web, download and media defects found in the October 2026 review.

Each test failed before its fix. They use real files and the app's own
command paths; fakes behave like the browser, player or feed they stand for.
"""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import downloads
import test_downloads
from downloads import edge_report


class DownloadScreenTests(unittest.TestCase):
    """The Downloads screen, through companion, as test_downloads.ScreenTests sets it up."""
    setUp = test_downloads.ScreenTests.setUp
    add = test_downloads.ScreenTests.add
    press = test_downloads.ScreenTests.press

    def test_cancel_it_never_runs_a_program_and_run_it_never_cancels_a_download(self):
        self.add('setup.exe', 'Completed')
        mode = self.app.handle('download manager', 'web')
        started = []
        with patch('os.startfile', side_effect=started.append, create=True):
            self.press('Enter', mode)
            self.assertIn('is a program. Run it?', self.said[-1])
            # "Cancel it" is a No to "Run it?".
            self.app.handle('cancel it', mode)
            self.assertEqual(started, [])
            self.assertTrue(self.said[-1].startswith('Not opened.'), self.said[-1])
            self.press('Enter', mode)
            self.app.handle('Run it.', mode)
            self.assertEqual(started, [str(self.root / 'setup.exe')])
        # "Run it" is not a Yes to "Cancel this download?".
        self.add('movie.mp4')
        self.press('Home', mode)
        self.press('Delete', mode)
        self.assertIn('Cancel movie.mp4?', self.said[-1])
        self.app.handle('run it', mode)
        self.assertNotIn(('movie.mp4', 'cancel'), self.edge.sent)
        self.app.handle('Cancel it.', mode)
        self.assertIn(('movie.mp4', 'cancel'), self.edge.sent)


class FileAddressTests(unittest.TestCase):
    def test_an_address_that_downloads_keeps_the_same_browser_and_its_download(self):
        from web_assistant import WebSession, BrowserBackend
        folder = tempfile.mkdtemp()
        backend = object.__new__(BrowserBackend)
        backend.browser = 'webview2'; backend.start = Mock(); backend.page = Mock(); backend.close = Mock()
        backend.page.goto.side_effect = Exception('page.goto: Download is starting')
        with patch('connected_browser.create_backend', return_value=backend):
            session = WebSession(folder)
        self.assertFalse(session.injected_backend)
        with patch('web_assistant.guided_backend') as other:
            said = session.open('https://files.test/report.zip')
        other.assert_not_called()
        backend.close.assert_not_called()
        self.assertIs(session.backend, backend)
        self.assertEqual(said, 'This address is a file, so it is downloading. Press Control+D to see your downloads.')


class Player:
    """Behaves like MediaPlayer: open() raises OSError when the stream will not start."""
    def __init__(self, fail=False):
        self.kind = None; self.fail = fail; self.opened = []
    def open(self, url, title, kind):
        self.opened.append((url, title, kind))
        if self.fail:
            self.kind = None
            raise OSError('This audio format could not start in the player.')
        self.kind = kind
    def control(self, action, seconds=15, position=None):
        return 0 if self.kind else None
    def close(self): self.kind = None


class MediaTests(unittest.TestCase):
    def setUp(self):
        from media_hub import MediaHub
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.notices = []
        self.hub = MediaHub(self.folder, fetch_json=Mock(), notify=self.notices.append, player=Player())

    def companion(self, section):
        import companion as app
        stack = ExitStack(); self.addCleanup(stack.close)
        said = []
        stack.enter_context(patch.object(app, 'MEDIA_HUB', self.hub))
        stack.enter_context(patch.object(app, 'MEDIA_NAV', None, create=True))
        stack.enter_context(patch.object(app, 'MEDIA_SECTION', section))
        stack.enter_context(patch.object(app, 'TEXT_MODE', True))
        stack.enter_context(patch.object(app, 'APP_WINDOW', None))
        for name in ('speak', 'speak_prompt', 'speak_status'):
            stack.enter_context(patch.object(app, name, side_effect=lambda text, *a, **k: said.append(str(text))))
        stack.enter_context(patch.object(app, 'interrupt_speech'))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return app, said

    def test_damaged_schedule_entries_list_and_cancel_without_stopping_the_app(self):
        good = {'station': {'name': 'WGN', 'url': 'https://wgn.example/live'}, 'when': '2026-10-28T08:00:00', 'minutes': 30, 'id': '1'}
        (self.folder / 'radio-schedule.json').write_text(json.dumps(['broken', {'station': 'WXYZ'}, good]), encoding='utf-8')
        from media_navigation import MediaNavigation
        nav = MediaNavigation(self.hub, 'radio')
        said = nav.command('list scheduled recordings')
        self.assertIn('WGN', said)
        names, index, _ = nav.state()
        self.assertEqual(names, ['A damaged schedule entry', 'A damaged schedule entry', 'WGN at 2026-10-28T08:00:00'])
        nav.keyboard('Down')
        self.assertIn('Remove A damaged schedule entry?', nav.keyboard('Delete'))
        self.assertEqual(nav.command('yes'), 'Removed.')
        kept = json.loads((self.folder / 'radio-schedule.json').read_text(encoding='utf-8'))
        self.assertEqual(kept, ['broken', good])
        # Only the chosen entry goes; WGN stays.
        nav.command('list scheduled recordings'); nav.keyboard('End'); nav.keyboard('Delete'); nav.command('yes')
        self.assertEqual(json.loads((self.folder / 'radio-schedule.json').read_text(encoding='utf-8')), ['broken'])
        # A schedule file that is not a list is shown as empty, not as an error.
        (self.folder / 'radio-schedule.json').write_text('{"station": 1}', encoding='utf-8')
        nav.command('list scheduled recordings')
        self.assertIsNone(nav.state())

    def test_podcast_search_offline_or_with_a_damaged_answer_says_so_plainly(self):
        from urllib.error import URLError
        app, said = self.companion('podcast')
        self.hub.fetch_json.side_effect = URLError('[Errno -2] Name or service not known')
        self.assertEqual(app.handle('search podcasts for news', 'media'), 'media')
        self.assertEqual(said[-1], 'Podcast search is not available right now. Check the internet connection, then try again.')
        self.hub.fetch_json.side_effect = None
        for answer in (['not', 'a', 'dict'], {'results': ['text', None, {'collectionName': 'Real show', 'feedUrl': 'https://pod.example/feed'}]}):
            self.hub.fetch_json.return_value = answer
            app.handle('search podcasts for news', 'media')
        self.assertTrue(said[-1].startswith('Found 1 podcasts. Podcast 1: Real show.'), said[-1])

    def test_damaged_subscriptions_are_skipped_not_fatal(self):
        subs = ['text', {'feed': 'https://a.example/feed'}, {'name': 'Good show', 'feed': 'https://b.example/feed', 'episodes': []}]
        (self.folder / 'podcast-subscriptions.json').write_text(json.dumps(subs), encoding='utf-8')
        app, said = self.companion('podcast')
        app.handle('list subscriptions', 'media')
        self.assertTrue(said[-1].startswith('Found 1 subscriptions. Subscription 1: Good show'), said[-1])
        with patch('media_hub._http', return_value=b'<rss><channel><item><title>   </title><enclosure url="https://b.example/1.mp3"/></item></channel></rss>'):
            self.assertIn('Checked 1 of 1', self.hub.refresh_subscriptions())
            said_open = self.hub.open_podcast(1)
        # A blank episode title is read as Untitled episode, not as nothing.
        self.assertIn('Episode 1: Untitled episode.', said_open)
        # The damaged entries stay in the file for a helper.
        self.assertEqual(len(json.loads((self.folder / 'podcast-subscriptions.json').read_text(encoding='utf-8'))), 3)


if __name__ == '__main__':
    unittest.main()
