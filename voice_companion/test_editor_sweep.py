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


class ComplexWordFileOpensInTheReader(DocumentFlowCase):
    def test_the_reason_is_said_in_plain_words(self):
        from docx import Document
        made = Document(); made.add_paragraph('Body text.'); made.sections[0].header.paragraphs[0].text = 'Top'
        path = self.elsewhere / 'Complex.docx'; made.save(path)
        self.assertEqual(app.task_navigation().open_document(path), 'file_reader')
        self.assertIn('Opened Complex.docx. This Word file has features the editor cannot keep, '
                      'so it opened in the Document reader. You can read it, but not change it.', self.last())


class OpenDocumentByName(DocumentFlowCase):
    """"Open document" followed by a name does what choosing the file in Open a document does."""
    def setUp(self):
        super().setUp(); self.default.mkdir(parents=True, exist_ok=True)

    def test_a_complex_word_file_opens_in_the_reader(self):
        from docx import Document
        made = Document(); made.add_paragraph('Body text.'); made.sections[0].header.paragraphs[0].text = 'Top'
        made.save(self.default / 'Complex.docx')
        self.assertEqual(app.handle('Open document Complex.', 'word_processor'), 'file_reader')
        self.assertIn('features the editor cannot keep', self.last())
        self.assertNotIn('test editor', ' '.join(self.said))
        self.assertEqual(self.press('Escape', 'file_reader'), 'word_processor')
        self.assertTrue(self.last().startswith('Word processor menu.'))

    def test_a_damaged_file_is_named_in_plain_words(self):
        (self.default / 'Broken.docx').write_bytes(b'PK not really')
        self.assertEqual(app.handle('open document Broken', 'word_processor'), 'word_processor')
        self.assertEqual(self.last(), 'This document is empty or damaged, so it could not be opened.')


class ListsOverAMenu(DocumentFlowCase):
    """A list opened over the Word processor or Notepad menu owns the keys, as its prompt says."""
    def setUp(self):
        super().setUp(); self.default.mkdir(parents=True, exist_ok=True)
        for name in ('Alpha', 'Bravo'):
            made = VoiceDocument(self.default, title=name); made.paragraphs = [Paragraph(name + ' text.')]; made.save()

    def test_enter_on_list_documents_opens_the_document(self):
        mode = app.handle('list documents', 'word_processor')
        mode = self.press('Down', mode)
        self.assertIn('Bravo', self.last())
        self.assertEqual(self.press('Enter', mode), 'document')
        self.assertEqual(app.document.title, 'Bravo')

    def test_home_and_end_move_in_the_list(self):
        mode = app.handle('list documents', 'word_processor')
        mode = self.press('End', mode)
        self.assertIn('Bravo', self.last())
        mode = self.press('Home', mode)
        self.assertIn('Alpha', self.last())
        self.assertEqual(self.press('Enter', mode), 'document')
        self.assertEqual(app.document.title, 'Alpha')

    def test_escape_closes_the_list_and_stays_on_the_menu(self):
        mode = app.handle('list documents', 'word_processor')
        self.assertEqual(self.press('Escape', mode), 'word_processor')
        self.assertIsNone(app.NAMED_LIST)

    def test_enter_on_the_notes_list_opens_the_note(self):
        import notepad
        notepad.add(app.APP, 'Buy milk.')
        mode = self.press('Ctrl+O', app.handle('notepad', 'awake'))
        self.assertIn('Buy milk', self.last())
        self.assertEqual(self.press('Enter', mode), 'note')
        self.assertIn('Buy milk.', ' '.join(self.said[-2:]))


if __name__ == '__main__':
    unittest.main()
