"""Undo and Redo for many steps in documents and email drafts, and "Save." on its own."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
import document_editor
from document_editor import Paragraph, VoiceDocument
from email_draft import VoiceEmail
from keyboard_text import document_text, replace_keyboard_text


def typed(doc, characters):
    """Type characters one key at a time at the end, as the window sends them."""
    before = document_text(doc)
    for character in characters:
        after = before + character
        replace_keyboard_text(doc, before, after, len(after))
        before = after


def backspace(doc, times):
    before = document_text(doc)
    for _ in range(times):
        after = before[:-1]
        replace_keyboard_text(doc, before, after, len(after))
        before = after


class DocumentUndoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.doc = VoiceDocument(Path(self.folder.name), title='Letter')
        self.doc.dictating = True

    def texts(self):
        return [p.text for p in self.doc.paragraphs]

    def test_many_steps_back_and_forward_each_named(self):
        doc = self.doc
        doc.process('Dear Sam.')
        doc.process('new paragraph')
        doc.process('Thank you for the visit.')
        doc.process('bold paragraph')
        said = doc.process('undo')
        self.assertTrue(said.startswith('Undid bold paragraph.'), said)
        self.assertFalse(doc.paragraphs[1].bold or any(r.bold for r in doc.paragraphs[1].runs))
        self.assertTrue(doc.process('Undo that.').startswith('Undid typing: Thank you for the visit.'))
        self.assertTrue(doc.process('undo').startswith('Undid a new paragraph.'))
        self.assertEqual(self.texts(), ['Dear Sam.'])
        self.assertTrue(doc.process('undo').startswith('Undid typing: Dear Sam.'))
        self.assertEqual(self.texts(), [])
        self.assertEqual(doc.process('undo'), 'Nothing more to undo.')
        self.assertTrue(doc.process('Redo.').startswith('Redid typing: Dear Sam.'))
        self.assertTrue(doc.process('redo that').startswith('Redid a new paragraph.'))
        doc.process('redo'); doc.process('redo')
        self.assertEqual(self.texts(), ['Dear Sam.', 'Thank you for the visit.'])
        self.assertTrue(doc.paragraphs[1].bold or any(r.bold for r in doc.paragraphs[1].runs))
        self.assertEqual(doc.process('redo'), 'Nothing more to redo.')
        # The saved file follows Undo and Redo.
        reopened = VoiceDocument.open_existing(Path(self.folder.name), 'Letter')
        self.assertEqual([p.text for p in reopened.paragraphs], self.texts())

    def test_nothing_to_undo_or_redo_in_a_new_document(self):
        self.assertEqual(self.doc.process('undo'), 'There is nothing to undo.')
        self.assertEqual(self.doc.process('redo'), 'There is nothing to redo.')

    def test_a_new_change_clears_redo(self):
        self.doc.process('One.')
        self.doc.process('undo')
        self.doc.process('Two.')
        self.assertEqual(self.doc.process('redo'), 'Nothing more to redo.')
        self.assertEqual(self.texts(), ['Two.'])

    def test_redo_in_a_sentence_is_text(self):
        self.doc.process('Redo the kitchen floor.')
        self.doc.process('Undo the knot.')
        self.assertEqual(self.texts(), ['Redo the kitchen floor. Undo the knot.'])

    def test_tables_and_deletes_undo_in_order(self):
        doc = self.doc
        doc.process('Intro.')
        doc.process('create table with two rows and two columns')
        doc.process('add row')
        self.assertEqual(sum(1 for p in doc.paragraphs if p.table_id), 6)
        self.assertTrue(doc.process('undo').startswith('Undid add row.'))
        self.assertEqual(sum(1 for p in doc.paragraphs if p.table_id), 4)
        doc.process('delete table')
        self.assertEqual(self.texts(), ['Intro.'])
        self.assertTrue(doc.process('undo').startswith('Undid delete table.'))
        self.assertEqual(sum(1 for p in doc.paragraphs if p.table_id), 4)
        doc.process('undo')
        self.assertEqual(self.texts(), ['Intro.'])
        doc.cursor = 0
        doc.process('delete paragraph')
        self.assertEqual(self.texts(), [])
        self.assertTrue(doc.process('undo').startswith('Undid delete paragraph.'))
        self.assertEqual(self.texts(), ['Intro.'])

    def test_keyboard_typing_is_one_step_per_sentence(self):
        doc = self.doc
        typed(doc, 'Hi there. ')
        typed(doc, 'More words')
        backspace(doc, 3)
        self.assertEqual([s.label for s in doc.undo_steps],
                         ['typing: Hi there', 'typing: More words', 'deleting: rds'])
        self.assertTrue(doc.process('undo').startswith('Undid deleting: rds.'))
        self.assertEqual(document_text(doc), 'Hi there. More words')
        self.assertTrue(doc.process('undo').startswith('Undid typing: More words.'))
        self.assertEqual(document_text(doc), 'Hi there. ')
        doc.process('redo')
        self.assertEqual(document_text(doc), 'Hi there. More words')

    def test_typing_after_a_voice_command_starts_a_new_step(self):
        typed(self.doc, 'abc')
        self.doc.process('bold paragraph')
        typed(self.doc, 'def')
        self.assertEqual([s.label for s in self.doc.undo_steps], ['typing: abc', 'bold paragraph', 'typing: def'])

    def test_a_typed_period_is_named(self):
        typed(self.doc, '.')
        self.assertEqual(self.doc.undo_steps[-1].label, 'typing: period')

    def test_history_has_a_limit(self):
        with patch.object(document_editor, 'UNDO_LIMIT', 5):
            for number in range(8):
                self.doc.process('Line %d.' % number)
            self.assertEqual(len(self.doc.undo_steps), 5)
            for _ in range(5):
                self.assertTrue(self.doc.process('undo').startswith('Undid'))
            self.assertEqual(self.doc.process('undo'), 'Nothing more to undo.')
            self.assertEqual(self.texts(), ['Line 0. Line 1. Line 2.'])

    def test_rename_undo_and_redo_keep_one_file(self):
        doc = self.doc
        doc.process('Words.')
        doc.process('name document Second name')
        folder = Path(self.folder.name)
        self.assertTrue((folder / 'Second name.docx').exists())
        self.assertTrue(doc.process('undo').startswith('Undid the new name.'))
        self.assertEqual(sorted(p.name for p in folder.glob('*.docx')), ['Letter.docx'])
        doc.process('redo')
        self.assertEqual(sorted(p.name for p in folder.glob('*.docx')), ['Second name.docx'])

    def test_save_as_copy_is_never_renamed_back(self):
        doc = self.doc
        doc.process('Words.')
        other = Path(self.folder.name) / 'Other'
        doc.save_as(other, 'Copy')
        doc.process('undo')
        self.assertEqual(doc.path, other / 'Copy.docx')
        self.assertTrue((other / 'Copy.docx').exists())
        self.assertEqual(self.texts(), [])


class EmailUndoTests(unittest.TestCase):
    def test_undo_brings_back_addresses_and_subject_step_by_step(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), compose_step='recipient')
            draft.process('sam at example dot com')
            draft.process('Lunch')
            draft.process('See you at noon.')
            self.assertEqual((draft.recipient, draft.subject), ('sam@example.com', 'Lunch'))
            self.assertTrue(draft.process('undo').startswith('Undid typing: See you at noon.'))
            self.assertTrue(draft.process('undo').startswith('Undid the subject.'))
            self.assertEqual(draft.subject, '')
            self.assertTrue(draft.process('undo').startswith('Undid the To address.'))
            self.assertEqual(draft.recipient, '')
            self.assertTrue(draft.process('redo').startswith('Redid the To address.'))
            self.assertEqual(draft.recipient, 'sam@example.com')

    def test_redo_in_the_subject_field_is_a_command_but_redo_words_are_a_subject(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='sam@example.com', compose_step='subject')
            self.assertEqual(draft.process('Redo.'), 'There is nothing to redo.')
            draft.process('Redo the deck')
            self.assertEqual(draft.subject, 'Redo the deck')

    def test_save_alone_saves_the_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='sam@example.com', compose_step='body')
            # "Save my document" once said "Saved the Word document as ... .eml" in a draft.
            for phrase in ('Save.', 'Save it.', 'Save my document.'):
                self.assertIn('Email draft saved locally', draft.process(phrase))


class KeysTests(unittest.TestCase):
    def test_control_z_and_control_y(self):
        from input_parity import screen_command
        from task_navigation import SHORTCUTS, DOCUMENT_ACTIONS
        self.assertEqual((SHORTCUTS['Ctrl+Z'], SHORTCUTS['Ctrl+Y']), ('undo', 'redo'))
        self.assertEqual((screen_command('Ctrl+Z', 'email_draft'), screen_command('Ctrl+Y', 'email_draft')), ('undo', 'redo'))
        edit = [name for name, _, _ in DOCUMENT_ACTIONS['Edit']]
        # Redo is added last, so the menu letters people know stay the same.
        self.assertEqual((edit[0], edit[-1], edit[1]), ('Undo', 'Redo', 'Select all'))


class SaveCommandTests(unittest.TestCase):
    def test_save_alone_saves_a_named_document_and_stays_text_in_a_sentence(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder), title='Notes')
            doc.paragraphs = [Paragraph('Hello.')]
            doc.dictating = True
            with patch.object(companion, 'document', doc), patch.object(companion, 'speak') as said:
                self.assertEqual(companion.handle('Save.', 'document'), 'document')
                self.assertIn('Saved the Word document as Notes.docx', said.call_args.args[0])
                self.assertEqual(companion.handle('Save the date for Friday.', 'document'), 'document')
            self.assertEqual(doc.paragraphs[0].text, 'Hello. Save the date for Friday.')
            self.assertTrue((Path(folder) / 'Notes.docx').exists())

    def test_save_on_an_unnamed_document_asks_for_a_name(self):
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            doc.paragraphs = [Paragraph('Hello.')]
            with patch.object(companion, 'document', doc), patch.object(companion, 'speak') as said, \
                    patch.object(companion, 'checkpoint_document'):
                self.assertEqual(companion.handle('Save it.', 'document'), 'document_name')
                self.assertIn('no name yet', said.call_args.args[0])
                self.assertEqual(companion.handle('Shopping list', 'document_name', typed=True), 'document')
            self.assertEqual(doc.title, 'Shopping list')
            self.assertTrue((Path(folder) / 'Shopping list.docx').exists())


if __name__ == '__main__':
    unittest.main()
