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


if __name__ == '__main__':
    unittest.main()
