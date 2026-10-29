"""Word processor, Notepad, reader and File manager: bugs a new user met (sweep, October 29, 2026)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion as app
from document_editor import Paragraph, VoiceDocument
from test_document_save_as import DocumentFlowCase


class FailedSaveLeavesNoStrayFile(DocumentFlowCase):
    def test_a_locked_file_leaves_no_extra_document_in_the_list(self):
        made = VoiceDocument(app.documents_folder(), title='Letter')
        made.paragraphs = [Paragraph('Dear Bob.')]; made.save()
        with patch.object(Path, 'replace', side_effect=PermissionError('open in Word')):
            with self.assertRaises(OSError):
                made.save()
        names = sorted(p.name for p in app.documents_folder().glob('*.docx'))
        self.assertEqual(names, ['Letter.docx'])
        app.handle('list documents', 'word_processor')
        self.assertIn('1 of 1', self.last())


class SpokenRenameInFileManager(DocumentFlowCase):
    """Rename by voice: recognizers add a period, and people say "dot" for the dot."""
    def setUp(self):
        super().setUp()
        from file_manager import FileManager
        from test_file_manager import FakeFiles, settle
        self.settle = settle
        self.disk = self.root / 'Disk'; self.disk.mkdir()
        (self.disk / 'notes.txt').write_text('Milk.')
        self.fm = FileManager(FakeFiles(self.disk))
        self.stack.enter_context(patch.object(app, 'FILE_MANAGER', self.fm))
        self.fm.refresh(str(self.disk)); settle(self.fm)
        self.fm.choose('notes.txt')
        self.assertIn('Rename notes.txt', self.fm.command('rename'))

    def renamed_to(self, heard, typed=False):
        app.handle(heard, 'files', typed=typed); self.settle(self.fm)
        actions = self.fm.backend.actions
        return actions[-1][3] if actions and actions[-1][0] == 'rename' else None

    def test_a_period_the_recognizer_added_is_left_off(self):
        self.assertEqual(self.renamed_to('Shopping.txt.'), 'Shopping.txt')

    def test_dot_and_the_file_type_said_as_words(self):
        for heard, name in (('shopping dot txt', 'shopping.txt'), ('shopping dot t x t', 'shopping.txt'),
                            ('Shopping list dot docx.', 'Shopping list.docx')):
            with self.subTest(heard=heard):
                self.fm.backend.actions.clear()
                self.fm.pending = ('rename', [str(self.disk / 'notes.txt')])
                self.assertEqual(self.renamed_to(heard), name)

    def test_a_typed_name_is_used_as_typed(self):
        self.assertEqual(self.renamed_to('shopping dot txt', typed=True), 'shopping dot txt')

    def test_a_typed_name_ending_in_a_period_says_why_it_cannot_be_used(self):
        self.assertIsNone(self.renamed_to('Shopping.', typed=True))
        self.assertIn('cannot end with a period or a space', self.last())


if __name__ == '__main__':
    unittest.main()
