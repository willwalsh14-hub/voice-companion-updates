"""Documents when something goes wrong: a file open in Word, a full disk (sweep, October 31, 2026).

The change must stay in the document, the app must keep running, and the person
must hear what happened and what to do next, never Python's own error text.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

import companion as app
from document_editor import VoiceDocument
from email_draft import VoiceEmail
from test_document_save_as import DocumentFlowCase

LOCKED = PermissionError(13, 'The process cannot access the file because it is being used by another process')


class LockedFileWhileEditing(DocumentFlowCase):
    """Every kind of change, said or pressed, while Word has the file open."""
    def open_letter(self):
        app.document = VoiceDocument(self.default, title='Letter')
        app.document.append_text('Dear Bob. How are you?')
        app.document.save()
        self.said.clear()

    def test_no_change_stops_the_app_or_reads_out_the_raw_error(self):
        ways = ('make it 14 points', 'type literally hello', 'new line', 'scratch that', 'delete paragraph',
                'replace Dear with Hi', 'delete word', 'cut paragraph', 'change font to Arial', 'delete sentence',
                'Ctrl+B', 'Ctrl+I', 'Ctrl+U', 'Enter')
        for way in ways:
            with self.subTest(way=way):
                self.open_letter()
                with patch.object(Path, 'replace', side_effect=LOCKED):
                    mode = self.press(way, 'document') if way.startswith('Ctrl+') or way == 'Enter' else app.handle(way, 'document')
                self.assertEqual(mode, 'document')
                heard = ' '.join(self.said)
                self.assertIn(app.UNSAVED_DOCUMENT, heard)
                self.assertNotIn('process cannot access', heard)


class LockedDraftWhileEditing(DocumentFlowCase):
    def test_a_change_in_an_email_draft_says_the_draft_was_not_saved(self):
        for way in ('replace Dear with Hi', 'delete word', 'Ctrl+B'):
            with self.subTest(way=way):
                app.email_draft = VoiceEmail(self.root / 'Email Drafts', recipient='sam@example.com', subject='Hi',
                                             compose_step='body')
                app.email_draft.append_text('Dear Sam. See you soon.')
                self.said.clear()
                with patch.object(Path, 'replace', side_effect=LOCKED):
                    mode = self.press(way, 'email_draft') if way.startswith('Ctrl+') else app.handle(way, 'email_draft')
                self.assertEqual(mode, 'email_draft')
                heard = ' '.join(self.said)
                self.assertIn(app.UNSAVED_DRAFT, heard)
                self.assertNotIn('process cannot access', heard)

    def test_word_processor_from_a_draft_that_cannot_be_saved_stays_in_the_draft(self):
        app.email_draft = VoiceEmail(self.root / 'Email Drafts', recipient='sam@example.com', subject='Hi',
                                     compose_step='body')
        app.email_draft.append_text('Dear Sam.')
        self.said.clear()
        with patch.object(Path, 'replace', side_effect=LOCKED):
            mode = app.handle('word processor', 'email_draft')
        self.assertEqual(mode, 'email_draft')
        self.assertIn(app.UNSAVED_DRAFT, ' '.join(self.said))


class UndoWhileTheFileIsLocked(DocumentFlowCase):
    """Undo with Word holding the file: Redo must still bring the words back."""
    def test_redo_brings_back_what_a_locked_undo_took_away(self):
        app.document = VoiceDocument(self.default, title='Letter')
        app.handle('Dear Bob.', 'document')
        app.handle('new paragraph', 'document')
        app.handle('See you on Friday.', 'document')
        with patch.object(Path, 'replace', side_effect=LOCKED):
            app.handle('undo', 'document')
        self.assertIn(app.UNSAVED_DOCUMENT, self.last())
        self.assertNotIn('See you on Friday.', [p.text for p in app.document.paragraphs])
        app.handle('redo', 'document')
        self.assertIn('Redid', self.last())
        self.assertIn('See you on Friday.', [p.text for p in app.document.paragraphs])
        # The redone words reach the file once Word lets go.
        from document_reader import open_document
        self.assertIn('See you on Friday.', open_document(app.document.path).text)

    def test_undo_twice_while_locked_undoes_two_steps(self):
        app.document = VoiceDocument(self.default, title='Letter')
        for words in ('One.', 'Two.', 'Three.'):
            app.handle('new paragraph', 'document'); app.handle(words, 'document')
        with patch.object(Path, 'replace', side_effect=LOCKED):
            app.handle('undo', 'document')
            app.handle('undo', 'document')
        app.handle('redo', 'document'); app.handle('redo', 'document')
        self.assertEqual([p.text for p in app.document.paragraphs if p.text], ['One.', 'Two.', 'Three.'])


class SavedWordsReachTheDisk(DocumentFlowCase):
    """A power cut or a laptop battery that dies just after a save must not leave an empty file."""
    def test_the_new_copy_is_on_the_disk_before_it_replaces_the_old_file(self):
        import os
        events = []
        real_fsync, real_replace = os.fsync, Path.replace
        def fsync(fd):
            events.append('fsync'); return real_fsync(fd)
        def replace(path, target):
            events.append('replace ' + Path(path).name); return real_replace(path, target)
        made = VoiceDocument(self.default, title='Letter'); made.append_text('Dear Bob.')
        with patch('os.fsync', side_effect=fsync), patch.object(Path, 'replace', replace):
            made.save()
        self.assertIn('replace Letter.writing.docx', events)
        self.assertEqual(events[events.index('replace Letter.writing.docx') - 1], 'fsync')


class DocumentsFromTheOldFolder(unittest.TestCase):
    """Before October 19, 2026, documents were kept in the app's own folder. They are copied to
    Documents, Voice Companion once. A document deleted or renamed after that must stay gone."""
    def setUp(self):
        import os, tempfile
        from contextlib import ExitStack
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        environment = {k: v for k, v in os.environ.items() if k != 'VOICE_COMPANION_DATA_DIR'}
        self.stack.enter_context(patch.dict(os.environ, environment, clear=True))
        app_folder = self.root / 'AppData'
        self.stack.enter_context(patch.object(app, 'APP', app_folder))
        self.stack.enter_context(patch.object(app, 'DEFAULT_APP', app_folder))
        self.stack.enter_context(patch.object(app.os, 'name', 'posix'))
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.root / 'Home'))
        self.legacy = app_folder / 'Documents'; self.legacy.mkdir(parents=True)
        (self.legacy / 'Letter.docx').write_bytes(b'old letter')
        (self.legacy / 'List.docx').write_bytes(b'old list')

    def test_old_documents_are_copied_once(self):
        folder = app.documents_folder()
        self.assertEqual(folder, self.root / 'Home' / 'Documents' / 'Voice Companion')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['Letter.docx', 'List.docx'])
        (folder / 'Letter.docx').unlink()           # deleted in File manager, or renamed
        self.assertEqual(sorted(p.name for p in app.documents_folder().iterdir()), ['List.docx'])
        self.assertTrue((self.legacy / 'Letter.docx').exists())   # the old copy is never removed

    def test_a_copy_that_fails_does_not_stop_the_app_and_is_tried_again(self):
        import shutil
        real = shutil.copy2
        def copy(source, target, *a, **k):
            if Path(source).name == 'List.docx': raise PermissionError(13, 'in use')
            return real(source, target, *a, **k)
        with patch('shutil.copy2', side_effect=copy):
            folder = app.documents_folder()
        self.assertFalse(any(folder.glob('*.docx')))   # no half-moved folder
        self.assertEqual(sorted(p.name for p in app.documents_folder().iterdir()), ['Letter.docx', 'List.docx'])
        self.assertEqual((folder / 'Letter.docx').read_bytes(), b'old letter')


if __name__ == '__main__':
    unittest.main()
