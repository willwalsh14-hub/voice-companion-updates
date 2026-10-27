"""Document reader is an app with its own menu, and File manager can start in a chosen place
(Will, October 2026)."""
import json
import unittest
from pathlib import Path
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class DocumentReaderMenuTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, FILE_READER=None, FILE_MANAGER=None,
                                PREFERENCES=dict(companion.PREFERENCES)).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        self.text = self.folder / 'Orders.txt'; self.text.write_text('Report to the front desk at nine.', encoding='utf-8')

    def key(self, key, mode):
        request = companion.navigation_key(key, mode)
        return mode if request is None else companion.handle(request, mode)

    def test_main_menu_has_document_reader_and_it_opens_its_menu(self):
        self.assertEqual(dict(companion.MAIN_MENU_CHOICES)['Document reader'], 'document reader')
        self.assertEqual(companion.handle('document reader', 'awake'), 'reader_menu')
        self.assertTrue(self.said[-1].startswith('Document reader menu. Open a document'), self.said[-1])
        names = [name for name, _, _ in companion.task_navigation().items('reader_menu')]
        self.assertEqual(names, ['Open a document', 'Recent documents', 'Options', 'Help', 'Main menu'])

    def test_open_reads_any_file_without_editing_and_escape_returns_to_the_menu(self):
        nav = companion.task_navigation()
        mode = companion.handle('document reader', 'awake')
        with mock.patch('file_manager.FileManager.refresh', return_value='Drive list.'):
            mode = self.key('Ctrl+O', mode)
        self.assertEqual((mode, nav.picker), ('files', 'reader'))
        self.assertIn('.pdf', companion.FILE_MANAGER.pick_extensions)
        mode = nav.selected_file(self.text)
        self.assertEqual(mode, 'file_reader')
        self.assertIn('Report to the front desk', self.said[-1])
        self.assertEqual(self.key('Escape', mode), 'reader_menu')
        self.assertEqual(json.loads((self.folder / 'recent-reader.json').read_text()), [str(self.text.resolve())])

    def test_recent_documents_reopen_and_handle_a_missing_file(self):
        nav = companion.task_navigation()
        nav.remember_read(self.text)
        mode = self.key('Ctrl+R', companion.handle('document reader', 'awake'))
        self.assertEqual(mode, 'reader_recent')
        self.assertTrue(self.said[-1].startswith('Recent documents. Orders.txt'), self.said[-1])
        self.assertEqual(self.key('Enter', mode), 'file_reader')
        self.text.unlink()
        mode = self.key('Ctrl+R', companion.handle('document reader', 'awake'))
        self.assertEqual(self.key('Enter', mode), 'reader_menu')
        self.assertTrue(any('no longer there' in line for line in self.said[-3:]), self.said[-3:])
        self.assertEqual(self.key('Escape', 'reader_recent'), 'reader_menu')

    def test_auto_read_option_can_be_turned_off(self):
        companion.PREFERENCES['reader_auto_read'] = False
        self.assertEqual(companion.task_navigation().read_file(self.text), 'file_reader')
        self.assertIn('Press Control+R to read it', self.said[-1])
        self.assertNotIn('front desk', self.said[-1])

    def test_escape_and_control_f1_leave_the_menu(self):
        self.assertEqual(self.key('Escape', companion.handle('document reader', 'awake')), 'awake')
        self.assertEqual(self.key('Ctrl+F1', companion.handle('document reader', 'awake')), 'awake')


class FileManagerStartTests(unittest.TestCase):
    def test_named_places_and_folders(self):
        from file_manager import start_setting, start_location
        self.assertEqual(start_setting(''), 'drive list')
        self.assertEqual(start_setting('C drive.'), 'C drive')
        self.assertEqual(start_setting('my documents folder'), 'Documents')
        folder = Path(__file__).parent
        self.assertEqual(start_setting(str(folder)), str(folder))
        with self.assertRaises(ValueError):start_setting(str(folder / 'no such folder'))
        self.assertIsNone(start_location('drive list'))
        self.assertEqual(start_location(str(folder)), str(folder))
        self.assertIsNone(start_location(str(folder / 'unplugged')))   # a missing folder falls back to the drive list

    def test_file_manager_opens_at_the_chosen_folder(self):
        folder = str(Path(__file__).parent)
        with mock.patch.object(companion, 'FILE_MANAGER', None), mock.patch.object(companion, 'PREFERENCES', {'file_manager_start': folder}), \
                mock.patch.object(companion, 'speak'), mock.patch('file_manager.FileManager.refresh', return_value='') as refresh:
            self.assertEqual(companion.handle('file manager', 'awake'), 'files')
        refresh.assert_called_once_with(folder)


if __name__ == '__main__':
    unittest.main()
