"""Settings files are written in one step, so a crash never leaves them half written."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import storage
from storage import write_text_atomic


class AtomicWriteTests(unittest.TestCase):
    def test_writes_new_file_and_replaces_old_one(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sub' / 'speech-settings.json'
            write_text_atomic(path, json.dumps({'rate': 1}))
            write_text_atomic(path, json.dumps({'rate': 2}))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'rate': 2})
            self.assertEqual([p.name for p in path.parent.iterdir()], ['speech-settings.json'])

    def test_failed_write_keeps_the_old_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'browser-choice.txt'
            write_text_atomic(path, 'firefox')
            with mock.patch.object(storage.os, 'fsync', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    write_text_atomic(path, 'chrome')
            self.assertEqual(path.read_text(encoding='utf-8'), 'firefox')


if __name__ == '__main__':
    unittest.main()
