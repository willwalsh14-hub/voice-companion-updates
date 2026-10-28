"""New documents, Save As, and the folder new documents go to (Will, October 2026).

- Every way of creating a document asks for its name first, in the same words.
- Save As asks for the new name, then the folder. Enter or "confirm" keeps the
  default folder. It never replaces a file without a Yes or No question.
- After Save As you are editing the new file. The folder for new documents
  never changes, even after saving or opening a file somewhere else.
- F12 is Save As in a document. Insert+F12 and Caps Lock+F12 still say the time
  there, and F12 says the time on every other screen.
"""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import companion as app
import time_date
from document_editor import Paragraph, VoiceDocument

NEW_PROMPT = 'Please say or type the name for your document, then press Enter or say confirm.'


class DocumentFlowCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {'APP': self.root, 'APP_WINDOW': None, 'TASK_NAV': None, 'document': None, 'FILE_MANAGER': None,
                            'FILE_READER': None, 'MEDIA_HUB': None, 'SETTINGS_PANEL': None, 'NAMED_LIST': None,
                            'DOCUMENT_CLOSE_TARGET': 'awake', 'DOCUMENT_NAME_RETURN': 'awake', 'DOCUMENT_BASELINE': None,
                            'DOCUMENT_NAMING_ACTION': 'name document', 'PENDING_DOCUMENT_NAME': None,
                            'NEW_DOCUMENT_RETURN': None, 'SAVE_AS_TITLE': None, 'SAVE_AS_FOLDER': None,
                            'SETTINGS_RETURN_MODE': 'awake', 'CONFIRM_CHOICE': 'yes', 'email_draft': None,
                            'mail_session': None, 'note_buffer': [], 'MAIN_MENU_INDEX': None,
                            'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'INPUT_MODE': 'mixed', 'PREFERENCES': dict(app.PREFERENCES)}.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        for name in ('speak', 'speak_prompt'):
            self.stack.enter_context(patch.object(app, name, side_effect=lambda text, *a, **k: self.said.append(str(text))))
        self.stack.enter_context(patch.object(app, 'sync_app_context'))
        self.default = app.documents_folder()
        self.elsewhere = self.root / 'Elsewhere'; self.elsewhere.mkdir()

    def last(self):
        return self.said[-1]

    def press(self, key, mode):
        request = app.navigation_key(key, mode)
        return app.handle_keyboard(request, mode) if request else mode

    def open_elsewhere(self, title='Away', text='Away text.'):
        made = VoiceDocument(self.elsewhere, title=title); made.paragraphs = [Paragraph(text)]; made.save()
        mode = app.task_navigation().open_document(self.elsewhere / (title + '.docx'))
        self.assertEqual(mode, 'document')
        return made.path


class NewDocumentPromptTests(DocumentFlowCase):
    def test_every_way_of_creating_a_document_asks_for_the_name_in_the_same_words(self):
        ways = {
            'Enter on Create a document': lambda: self.press('Enter', 'word_processor'),
            'Control+N in the Word processor menu': lambda: self.press('Ctrl+N', 'word_processor'),
            'voice on the main menu': lambda: app.handle('Create a document.', 'awake'),
            'voice: new document': lambda: app.handle('new document', 'awake'),
            'voice: write a document': lambda: app.handle('write a document', 'awake'),
            'voice in the Word processor menu': lambda: app.handle('create a document', 'word_processor'),
            'voice from email': lambda: app.handle('create a document', 'mailbox'),
            'voice: start a word document': lambda: app.handle('start a word document', 'awake'),
        }
        for name, way in ways.items():
            with self.subTest(way=name):
                app.task_navigation().indices.clear()
                self.assertEqual(way(), 'document_name')
                self.assertEqual(self.last(), NEW_PROMPT)
                app.handle('cancel', 'document_name')

    def test_creating_from_an_open_document_asks_too(self):
        app.document = VoiceDocument(self.default, title='First'); app.document.append_text('First text.')
        self.assertEqual(self.press('Ctrl+N', 'document'), 'document_name')
        self.assertEqual(self.last(), NEW_PROMPT)
        self.assertTrue((self.default / 'First.docx').exists())   # the open one was saved first
        app.handle('cancel', 'document_name')
        # New on the document's Alt menu runs the same command.
        self.assertEqual(app.handle('create a document', 'document'), 'document_name')
        self.assertEqual(self.last(), NEW_PROMPT)

    def test_spoken_name_then_confirm_by_voice_creates_the_document(self):
        mode = app.handle('create a document', 'awake')
        self.assertEqual(app.handle('My Letter.', mode), 'document_name')
        self.assertEqual(self.last(), 'My Letter. Press Enter or say confirm to use this name.')
        self.assertEqual(app.app_context('document_name')['naming_request'][1], 'My Letter')
        self.assertIsNone(app.document)
        self.assertEqual(app.handle('Confirm.', 'document_name'), 'document')
        self.assertEqual(app.document.title, 'My Letter')
        self.assertEqual(app.document.folder, self.default)
        self.assertTrue(self.last().startswith('New document, My Letter.'), self.last())
        app.handle('Dear Sam.', 'document')
        app.handle('save document', 'document')
        self.assertTrue((self.default / 'My Letter.docx').exists())

    def test_typed_name_and_enter_creates_the_document(self):
        mode = self.press('Enter', 'word_processor')
        self.assertEqual(app.handle('name document Shopping list', mode, typed=True), 'document')
        self.assertEqual(app.document.path, self.default / 'Shopping list.docx')

    def test_a_name_already_used_is_refused_and_nothing_is_replaced(self):
        old = VoiceDocument(self.default, title='Taken'); old.append_text('Keep me.'); old.save()
        before = old.path.read_bytes()
        mode = app.handle('create a document', 'awake')
        self.assertEqual(app.handle('Taken', mode, typed=True), 'document_name')
        self.assertIn('already', self.last())
        self.assertEqual(old.path.read_bytes(), before)

    def test_cancel_creates_nothing(self):
        mode = app.handle('create a document', 'word_processor')
        self.assertEqual(app.handle('cancel', mode), 'word_processor')
        self.assertEqual(self.last(), 'Canceled. No document was created. Back to Word processor menu.')
        self.assertIsNone(app.document)
        self.assertEqual(list(self.default.glob('*.docx')), [])
        # From an open document, Escape goes back to it.
        app.document = VoiceDocument(self.default, title='Open one'); app.document.append_text('Text.')
        mode = app.handle('create a document', 'document')
        self.assertEqual(self.press('Escape', mode), 'document')
        self.assertEqual(app.document.title, 'Open one')
        self.assertEqual(self.last(), 'Canceled. No document was created. Back to document.')

    def test_leaving_a_new_document_without_saving_leaves_no_file(self):
        mode = app.handle('create a document', 'awake')
        mode = app.handle('Scratch pad', mode, typed=True)
        app.handle('Some words.', mode)
        self.assertEqual(app.handle('leave document', mode), 'document_save')
        app.handle('no', 'document_save')
        self.assertFalse((self.default / 'Scratch pad.docx').exists())


class SaveAsTests(DocumentFlowCase):
    def start(self, title='Original', text='Keep both files.'):
        app.document = VoiceDocument(self.default, title=title); app.document.append_text(text); app.document.save()
        app.checkpoint_document()
        return app.document.path

    def test_save_as_asks_for_a_name_then_a_folder_and_keeps_the_first_file(self):
        original = self.start(); before = original.read_bytes()
        self.assertEqual(self.press('Doc:SaveAs', 'document'), 'document_name')
        self.assertEqual(self.last(), 'Save as. Please say or type the new name for your document, then press Enter or say confirm.')
        self.assertEqual(app.handle('name document New copy', 'document_name', typed=True), 'document_name')
        self.assertEqual(self.last(), 'Now choose a folder. Press Enter or say confirm to save in Documents, Voice Companion. '
                                      'Or say or type another folder, such as Desktop.')
        # The box holds the default folder, so Enter saves there.
        self.assertEqual(app.app_context('document_name')['naming_request'][1], str(self.default))
        self.assertEqual(app.handle('name document ' + str(self.default), 'document_name', typed=True), 'document')
        self.assertEqual(self.last(), 'Saved as New copy.docx in Documents, Voice Companion. You are now working in New copy.')
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(app.document.path, self.default / 'New copy.docx')
        self.assertTrue(app.document.path.exists())
        # Later saves go to the new file, not the first one.
        app.handle('More text.', 'document'); app.handle('save document', 'document')
        self.assertEqual(original.read_bytes(), before)

    def test_every_save_as_key_and_command_reaches_the_same_prompt(self):
        self.start()
        prompt = 'Save as. Please say or type the new name for your document, then press Enter or say confirm.'
        for way in (lambda: self.press('Doc:SaveAs', 'document'), lambda: self.press('Ctrl+Shift+S', 'document'),
                    lambda: app.handle('Save as.', 'document'), lambda: app.handle('save document as', 'document')):
            self.assertEqual(way(), 'document_name')
            self.assertEqual(self.last(), prompt)
            self.assertEqual(app.handle('cancel', 'document_name'), 'document')
            self.assertEqual(self.last(), 'Save as canceled. Your document is still open.')

    def test_spoken_save_as_with_a_name_goes_to_the_folder_question(self):
        self.start()
        self.assertEqual(app.handle('Save as My Letter Copy.', 'document'), 'document_name')
        self.assertTrue(self.last().startswith('Save as My Letter Copy. Now choose a folder.'), self.last())
        self.assertEqual(app.handle('confirm', 'document_name'), 'document')
        self.assertTrue((self.default / 'My Letter Copy.docx').exists())

    def test_spoken_name_waits_for_confirm(self):
        self.start()
        app.handle('save as', 'document')
        self.assertEqual(app.handle('Budget', 'document_name'), 'document_name')
        self.assertEqual(self.last(), 'Budget. Press Enter or say confirm to use this name.')
        self.assertEqual(app.handle('okay', 'document_name'), 'document_name')
        self.assertTrue(self.last().startswith('Now choose a folder.'))

    def test_another_folder_by_typing_its_path(self):
        self.start()
        app.handle('save as', 'document')
        app.handle('Trip notes', 'document_name', typed=True)
        self.assertEqual(app.handle('name document ' + str(self.elsewhere), 'document_name', typed=True), 'document')
        self.assertEqual(app.document.path, self.elsewhere / 'Trip notes.docx')
        self.assertTrue(app.document.path.exists())
        self.assertIn('Saved as Trip notes.docx in', self.last())

    def test_a_folder_that_does_not_exist_asks_again(self):
        self.start()
        app.handle('save as', 'document'); app.handle('Trip notes', 'document_name', typed=True)
        self.assertEqual(app.handle(str(self.root / 'Missing'), 'document_name', typed=True), 'document_name')
        self.assertIn('That folder does not exist.', self.last())
        self.assertEqual(app.handle('confirm', 'document_name'), 'document')
        self.assertTrue((self.default / 'Trip notes.docx').exists())

    def test_replacing_a_file_needs_a_yes(self):
        self.start()
        other = VoiceDocument(self.elsewhere, title='Report'); other.append_text('Other report.'); other.save()
        before = other.path.read_bytes()
        app.handle('save as', 'document'); app.handle('Report', 'document_name', typed=True)
        self.assertEqual(app.handle(str(self.elsewhere), 'document_name', typed=True), 'document_replace')
        self.assertTrue(self.last().startswith('Report.docx is already in that folder. Replace it? Yes or no.'), self.last())
        self.assertEqual(app.app_context('document_replace')['confirmation'], 'no')
        # Enter on the first choice (No), then the name question again; nothing changed.
        self.assertEqual(self.press('Enter', 'document_replace'), 'document_name')
        self.assertEqual(self.last(), 'Not replaced. Please say or type the new name for your document, then press Enter or say confirm.')
        self.assertEqual(other.path.read_bytes(), before)
        self.assertEqual(app.document.title, 'Original')
        app.handle('Report', 'document_name', typed=True)
        self.assertEqual(app.handle(str(self.elsewhere), 'document_name', typed=True), 'document_replace')
        self.assertEqual(self.press('Y', 'document_replace'), 'document')
        self.assertNotEqual(other.path.read_bytes(), before)
        self.assertEqual(app.document.path, other.path)
        self.assertIn('Keep both files.', VoiceDocument.open_existing(self.elsewhere, 'Report').process('read document'))

    def test_escape_on_the_replace_question_keeps_the_file(self):
        self.start()
        other = VoiceDocument(self.default, title='Report'); other.append_text('Other.'); other.save()
        before = other.path.read_bytes()
        app.handle('save as', 'document'); app.handle('Report', 'document_name', typed=True)
        self.assertEqual(app.handle('confirm', 'document_name'), 'document_replace')
        self.assertEqual(self.press('Escape', 'document_replace'), 'document_name')
        self.assertEqual(other.path.read_bytes(), before)

    def test_save_as_the_same_name_and_folder_just_saves(self):
        original = self.start()
        app.handle('save as', 'document'); app.handle('Original', 'document_name', typed=True)
        self.assertEqual(app.handle('confirm', 'document_name'), 'document')
        self.assertEqual(app.document.path, original)

    def test_save_as_failure_keeps_the_document_as_it_was(self):
        original = self.start()
        app.handle('save as', 'document'); app.handle('Locked', 'document_name', typed=True)
        with patch.object(VoiceDocument, 'save', side_effect=PermissionError('locked')):
            self.assertEqual(app.handle('confirm', 'document_name'), 'document')
        self.assertIn('could not save', self.last())
        self.assertEqual(app.document.path, original)
        self.assertFalse((self.default / 'Locked.docx').exists())

    def test_names_windows_cannot_use_are_refused(self):
        self.start()
        app.handle('save as', 'document')
        self.assertEqual(app.handle('a/b', 'document_name', typed=True), 'document_name')
        self.assertIn('Windows cannot use', self.last())
        self.assertEqual(app.handle('CON', 'document_name', typed=True), 'document_name')
        self.assertIn('Windows reserves', self.last())


class DefaultFolderTests(DocumentFlowCase):
    def test_save_as_from_a_document_opened_elsewhere_offers_the_default_folder(self):
        # Before 0.2.172, Save As put the copy next to the opened file, so new copies
        # left the folder for new documents behind.
        self.open_elsewhere()
        app.handle('save as', 'document')
        app.handle('Copy', 'document_name', typed=True)
        self.assertIn('Documents, Voice Companion', self.last())
        self.assertIn('Say same folder to save it next to the file you opened.', self.last())
        self.assertEqual(app.handle('confirm', 'document_name'), 'document')
        self.assertEqual(app.document.path, self.default / 'Copy.docx')
        self.assertFalse((self.elsewhere / 'Copy.docx').exists())

    def test_same_folder_saves_next_to_the_opened_file(self):
        self.open_elsewhere()
        app.handle('save as', 'document'); app.handle('Copy', 'document_name', typed=True)
        self.assertEqual(app.handle('Same folder.', 'document_name'), 'document')
        self.assertEqual(app.document.path, self.elsewhere / 'Copy.docx')

    def test_typed_same_folder_works_like_the_spoken_words(self):
        # The window sends typed names as "name document <text>".
        self.open_elsewhere()
        app.handle('save as', 'document'); app.handle('Copy', 'document_name', typed=True)
        self.assertEqual(app.handle('name document same folder', 'document_name', typed=True), 'document')
        self.assertEqual(app.document.path, self.elsewhere / 'Copy.docx')

    def test_the_folder_label_is_only_shown_while_save_as_asks_for_the_folder(self):
        self.open_elsewhere()
        app.handle('save as', 'document'); app.handle('Copy', 'document_name', typed=True)
        self.assertEqual(app.app_context('document_name').get('naming_label'), 'Folder; press Enter to save there:')
        app.handle('@main_menu', 'document_name')          # Control+F1 leaves half-way
        app.handle('name document', 'document')            # later, F2 renames
        self.assertIsNone(app.app_context('document_name').get('naming_label'))
        self.assertEqual(app.handle('Renamed', 'document_name', typed=True), 'document')
        self.assertEqual(app.document.path, self.elsewhere / 'Renamed.docx')

    def test_new_documents_stay_in_the_default_folder_after_opening_or_saving_elsewhere(self):
        self.open_elsewhere()
        app.handle('save as', 'document'); app.handle('Copy', 'document_name', typed=True)
        app.handle(str(self.elsewhere), 'document_name', typed=True)
        self.assertEqual(app.document.folder, self.elsewhere)
        mode = app.handle('create a document', 'document')
        self.assertEqual(app.handle('Fresh', mode, typed=True), 'document')
        self.assertEqual(app.document.folder, self.default)
        app.handle('Fresh words.', 'document'); app.handle('save document', 'document')
        self.assertTrue((self.default / 'Fresh.docx').exists())
        self.said.clear(); app.handle('where are my documents', 'awake')
        self.assertIn(str(self.default), self.last())

    def test_save_as_to_another_folder_then_rename_stays_with_the_file(self):
        self.open_elsewhere()
        app.handle('save as', 'document'); app.handle('Copy', 'document_name', typed=True)
        app.handle('Same folder', 'document_name')
        app.handle('name document', 'document'); app.handle('Copy two', 'document_name', typed=True)
        self.assertEqual(app.document.path, self.elsewhere / 'Copy two.docx')


class F12Tests(DocumentFlowCase):
    def test_f12_is_save_as_only_in_an_open_document(self):
        self.assertTrue(time_date.save_as_key('document', prefix_held=False, command_entry=False))
        self.assertFalse(time_date.save_as_key('document', prefix_held=True, command_entry=False))   # Insert or Caps Lock
        self.assertFalse(time_date.save_as_key('document', prefix_held=False, command_entry=True))   # Alt+T box
        for mode in ('awake', 'document_menu', 'document_name', 'word_processor', 'email_draft', 'note', 'files', 'sleep'):
            self.assertFalse(time_date.save_as_key(mode, prefix_held=False, command_entry=False), mode)

    def test_the_save_as_request_runs_save_as(self):
        app.document = VoiceDocument(self.default, title='Doc'); app.document.append_text('Words.')
        self.assertEqual(app.navigation_key('Doc:SaveAs', 'document'), 'save as')

    def test_document_menu_shows_f12_for_save_as(self):
        from task_navigation import DOCUMENT_ACTIONS
        self.assertIn(('Save as', 'F12', 'save as'), DOCUMENT_ACTIONS['File'])


if __name__ == '__main__':
    unittest.main()
