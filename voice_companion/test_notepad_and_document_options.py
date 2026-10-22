"""Word processor options and saved notes, checked through the real app."""
import io
import re
import tempfile
import unittest
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import companion as app
from field_selection import FieldSelection
from menu_navigation import next_match
from reading_navigation import ReadingCursor
from settings_model import DEFAULTS, SettingsSession, fields

SOURCE = Path(__file__).resolve().parent


class WordProcessorOptionsTests(unittest.TestCase):
    def test_documents_folder_choice_is_gone_from_word_processor_options(self):
        from app_settings import ACTIONS
        options = fields('Word processor options', {})
        self.assertNotIn('open_documents', [f.key for f in options])
        self.assertFalse([f for f in options if 'folder' in f.label.lower()])
        self.assertNotIn('open_documents', ACTIONS)
        self.assertNotIn('open documents folder', ACTIONS.values())
        # The other choices stay.
        self.assertEqual([f.key for f in options],
                         ['document_font', 'document_size', 'document_spacing', 'document_alignment'])

    def test_guide_no_longer_lists_the_documents_folder_choice(self):
        for name in ('START HERE - Veteran.txt', 'START HERE - Veteran.html'):
            text = (SOURCE / name).read_text(encoding='utf-8')
            self.assertNotIn('Choose Open Documents folder', text)
            # The spoken command for the document folder is still documented.
            self.assertIn('Open documents', text)

    def test_font_for_new_documents_is_a_list_of_common_windows_fonts(self):
        font = next(f for f in fields('Word processor options', {}) if f.key == 'document_font')
        self.assertEqual(font.kind, 'choice')
        for name in ('Aptos', 'Arial', 'Calibri', 'Cambria', 'Courier New', 'Georgia',
                     'Segoe UI', 'Tahoma', 'Times New Roman', 'Verdana'):
            self.assertIn(name, font.choices)
        self.assertEqual(list(font.choices), sorted(font.choices, key=str.casefold))
        self.assertEqual(len(font.choices), len(set(font.choices)))
        # First-letter keys move through the list like other choices.
        start = font.choices.index('Calibri')
        tahoma = next_match(font.choices, start, 't')
        self.assertEqual(font.choices[tahoma], 'Tahoma')
        self.assertEqual(font.choices[next_match(font.choices, tahoma, 't')], 'Times New Roman')

    def test_a_saved_font_outside_the_list_stays_available(self):
        session = SettingsSession(DEFAULTS | {'document_font': 'Garamond'}, {})
        font = next(f for f in fields('Word processor options', session.context) if f.key == 'document_font')
        self.assertIn('Garamond', font.choices)
        self.assertIn('Arial', font.choices)
        self.assertEqual(session.set('document_font', 'Garamond'), 'Font for new documents Garamond')
        plain = next(f for f in fields('Word processor options', {}) if f.key == 'document_font')
        self.assertNotIn('Garamond', plain.choices)

    def test_spoken_font_choice_tolerates_speech_punctuation_and_case(self):
        session = SettingsSession(DEFAULTS, {})
        for spoken, expected in [('font Arial', 'Arial'), ('Font, Arial.', 'Arial'), ('font: times new roman', 'Times New Roman'),
                                 ('set font to Verdana', 'Verdana'), ('Choose font Segoe UI.', 'Segoe UI'),
                                 ('font comic sans', 'Comic Sans MS'), ('default font Times New Roman', 'Times New Roman'),
                                 ('Font for new documents Georgia', 'Georgia'), ('font courier', 'Courier New')]:
            with self.subTest(spoken=spoken):
                key, value = session.voice_setting(spoken)
                self.assertEqual(key, 'document_font')
                session.set(key, value)
                self.assertEqual(session.values['document_font'], expected)
        self.assertEqual(session.voice_setting('font size 14'), ('document_size', '14'))
        with self.assertRaises(ValueError) as error:
            session.set('document_font', 'banana')
        self.assertIn('Arial', str(error.exception))

    def test_chosen_font_is_used_in_the_new_word_file(self):
        from app_settings import apply_document_defaults
        from document_editor import VoiceDocument
        for name in ('Times New Roman', 'Comic Sans MS', 'Segoe UI'):
            with self.subTest(font=name), tempfile.TemporaryDirectory() as folder:
                doc = VoiceDocument(Path(folder))
                apply_document_defaults(Mock(PREFERENCES=DEFAULTS | {'document_font': name}), doc)
                doc.append_text('Hello there.')
                path = doc.save()
                xml = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8')
                self.assertIn('w:ascii="' + name + '"', xml)
                self.assertNotIn('Calibri', xml)
                # A later paragraph and table cells use the same font.
                doc.process('new paragraph');doc.append_text('Second line.')
                doc.process('create table with one row and two columns');doc.append_text('Cell.')
                xml = zipfile.ZipFile(doc.save()).read('word/document.xml').decode('utf-8')
                self.assertEqual(xml.count('w:ascii="' + name + '"'), xml.count('<w:r>'))
                self.assertEqual(doc.process('say font name'), 'Font ' + name + '.')
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            apply_document_defaults(Mock(PREFERENCES=DEFAULTS | {'document_size': '16', 'document_spacing': 'double',
                                                                 'document_alignment': 'justify'}), doc)
            self.assertEqual(doc.paragraphs, [])  # Nothing is written until the user writes.
            doc.append_text('Hello.');doc.process('new paragraph');doc.append_text('Again.')
            xml = zipfile.ZipFile(doc.save()).read('word/document.xml').decode('utf-8')
            self.assertEqual(xml.count('<w:sz w:val="32"/>'), 2)
            self.assertEqual(xml.count('<w:jc w:val="both"/>'), 2)
            self.assertEqual(xml.count('w:line="480"'), 2)

    def test_font_choice_is_saved_atomically_with_other_preferences(self):
        from app_settings import save_preferences
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'preferences.json'
            target.write_text('{"document_font": "Calibri"}', encoding='utf-8')
            replaced = []
            real_replace = Path.replace
            def watch(self, other):
                replaced.append((self.name, Path(other).name));return real_replace(self, other)
            with patch.object(Path, 'replace', watch):
                save_preferences(Mock(APP=Path(folder), PREFERENCES={'document_font': 'Arial'}))
            self.assertEqual(replaced, [('preferences.writing', 'preferences.json')])
            self.assertIn('"Arial"', target.read_text(encoding='utf-8'))


class SavedNotesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack();self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'VOICE_PICK_ORIGINAL': None,
                    'help_session': None, 'tutorial_session': None, 'MEDIA_HUB': None, 'note_buffer': [],
                    'note_selection': FieldSelection(), 'note_reading': ReadingCursor(), 'OPEN_NOTE': None,
                    'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'PREFERENCES': dict(app.PREFERENCES),
                    'SLEEP_RETURN_MODE': 'awake', 'SEARCH_RETURN_MODE': 'awake'}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=(name == 'OPEN_NOTE')))
        self.speech = self.stack.enter_context(patch.object(app, 'speak'))
        self.stack.enter_context(patch.object(app, 'speak_prompt', side_effect=lambda text, *a, **k: app.speak(text)))

    @property
    def notes_file(self):
        return self.root / 'notes.txt'

    def spoken(self):
        return ' '.join(str(call.args[0]) for call in self.speech.call_args_list)

    def last(self):
        return str(self.speech.call_args.args[0])

    def write(self, *texts):
        for text in texts:
            mode = app.handle('write a note', 'awake')
            app.handle(text, mode)
            self.assertEqual(app.handle('save note', mode), 'awake')

    def test_empty_notes_list_says_how_to_start_one(self):
        for phrase in ('list notes', 'Open a note.', 'My notes', 'open notes', 'Show my notes.'):
            with self.subTest(phrase=phrase):
                self.assertEqual(app.handle(phrase, 'awake'), 'awake')
                self.assertIn('no saved notes', self.last())
                self.assertIn('Write a note', self.last())
                self.assertIsNone(app.NAMED_LIST)
                self.assertFalse(app.note_buffer)

    def test_starting_a_note_says_how_to_open_a_saved_one(self):
        self.assertEqual(app.handle('write a note', 'awake'), 'note')
        self.assertIn('Say list notes to open a saved note.', self.last())

    def test_list_and_open_a_note_by_voice_then_keep_writing_in_the_same_note(self):
        self.write('Buy milk.', 'Call the clinic on Monday.')
        before = self.notes_file.read_text(encoding='utf-8')
        self.assertEqual(app.handle('Open a note.', 'awake'), 'awake')
        self.assertIn('Notes list', self.last())
        self.assertIn('Call the clinic on Monday', self.last())  # Newest first.
        self.assertIn('1 of 2', self.last())
        app.handle('next', 'awake')
        self.assertIn('Buy milk', self.last());self.assertIn('2 of 2', self.last())
        app.handle('next', 'awake')
        self.assertEqual(app.NAMED_LIST['index'], 1)  # Stays on the last item.
        self.assertTrue('2 of 2' in self.last() or 'End of list' in self.last(), self.last())
        mode = app.handle('okay', 'awake')
        self.assertEqual(mode, 'note')
        self.assertIn('Buy milk.', self.spoken())
        self.assertEqual(' '.join(app.note_buffer), 'Buy milk.')
        app.handle('Also eggs.', mode)
        self.assertEqual(app.handle('save note', mode), 'awake')
        after = self.notes_file.read_text(encoding='utf-8')
        self.assertEqual(after.count('Buy milk.'), 1)
        self.assertIn('Buy milk. Also eggs.', after)
        self.assertEqual(after.count('Call the clinic on Monday.'), 1)
        self.assertEqual(len(re.findall(r'^\[\d{4}-\d\d-\d\d \d\d:\d\d\] ', after, re.M)), 2)
        # Only the opened note changed; everything else is byte for byte the same.
        self.assertEqual(after.replace('Buy milk. Also eggs.', 'Buy milk.'), before)
        self.assertTrue((self.root / 'notes.txt.backup1').exists())
        self.assertIsNone(app.OPEN_NOTE)

    def test_open_note_by_number_and_go_back_saves_the_opened_note(self):
        self.write('First reminder.', 'Second reminder.')
        for phrase in ('open note 2', 'Open note two.', 'open note number 2', 'Open note to.'):
            with self.subTest(phrase=phrase):
                mode = app.handle(phrase, 'awake')
                self.assertEqual(mode, 'note')
                self.assertEqual(' '.join(app.note_buffer), 'First reminder.')
                self.assertEqual(app.handle('go back', mode), 'awake')
        app.handle('open note 1', 'awake');app.handle('Done today.', 'note');app.handle('go back', 'note')
        text = self.notes_file.read_text(encoding='utf-8')
        self.assertIn('Second reminder. Done today.', text);self.assertEqual(text.count('First reminder.'), 1)
        self.assertEqual(app.handle('open note 7', 'awake'), 'awake')
        self.assertIn('There are 2 notes', self.last())

    def test_list_notes_from_the_notes_screen_saves_the_current_note_first(self):
        self.write('Old note.')
        mode = app.handle('write a note', 'awake')
        app.handle('My notes are on the desk.', mode)  # Writing comes first.
        self.assertEqual(' '.join(app.note_buffer), 'My notes are on the desk.')
        self.assertEqual(app.handle('list notes', mode), 'note')
        self.assertIn('Notes list', self.last())
        self.assertIn('My notes are on the desk.', self.notes_file.read_text(encoding='utf-8'))
        self.assertFalse(app.note_buffer)
        self.assertEqual(app.handle('okay', mode), 'note')
        self.assertEqual(' '.join(app.note_buffer), 'My notes are on the desk.')

    def test_ctrl_o_lists_notes_and_arrows_move_through_the_list(self):
        self.write('Keyboard note.')
        self.assertEqual(app.navigation_key('Ctrl+O', 'note'), 'list notes')
        app.handle_keyboard(app.navigation_key('Ctrl+O', 'note'), 'note')
        self.assertIn('Keyboard note', self.last())
        self.assertEqual(app.navigation_key('Down', 'note'), 'next')
        self.assertEqual(app.navigation_key('Enter', 'note'), 'okay')
        self.assertEqual(app.navigation_key('Escape', 'note'), 'close list')

    def test_new_note_after_an_opened_note_is_a_separate_note(self):
        self.write('Original.')
        app.handle('open note 1', 'awake');app.handle('go back', 'note')
        self.write('Brand new.')
        text = self.notes_file.read_text(encoding='utf-8')
        self.assertEqual(len(re.findall(r'^\[', text, re.M)), 2)
        self.assertIn('Original.', text);self.assertIn('Brand new.', text)

    def test_clearing_an_opened_note_does_not_erase_the_saved_note(self):
        self.write('Keep me safe.')
        mode = app.handle('open note 1', 'awake')
        app.handle('select all', mode);app.handle('scratch', mode)
        self.assertEqual(app.handle('save note', mode), 'awake')
        self.assertIn('Keep me safe.', self.notes_file.read_text(encoding='utf-8'))
        self.assertIn('your saved note was not changed', self.spoken())

    def test_save_failure_keeps_the_note_text_and_says_what_to_do(self):
        mode = app.handle('write a note', 'awake');app.handle('Do not lose this.', mode)
        with patch('notepad.write_text_atomic', side_effect=PermissionError('locked')):
            self.assertEqual(app.handle('save note', mode), 'note')
        self.assertEqual(' '.join(app.note_buffer), 'Do not lose this.')
        self.assertIn('could not be saved', self.last())
        self.assertEqual(app.handle('save note', mode), 'awake')
        self.assertIn('Do not lose this.', self.notes_file.read_text(encoding='utf-8'))

    def test_damaged_notes_file_keeps_its_bytes_and_still_lists(self):
        self.notes_file.write_bytes(b'\n[2024-01-02 03:04] Old \xff text\n')
        self.write('Fresh note.')
        data = self.notes_file.read_bytes().replace(b'\r\n', b'\n')  # Windows writes CR LF line ends.
        self.assertTrue(data.startswith(b'\n[2024-01-02 03:04] Old \xff text\n'))
        app.handle('list notes', 'awake')
        self.assertEqual(len(app.NAMED_LIST['items']), 2)
        self.assertIn('January 2, 2024', app.NAMED_LIST['items'][1][0])

    def test_guide_documents_listing_and_opening_notes(self):
        for name in ('START HERE - Veteran.txt', 'START HERE - Veteran.html'):
            text = (SOURCE / name).read_text(encoding='utf-8')
            self.assertIn('List notes', text)
            self.assertRegex(text, r'(?:Ctrl|Control)\+O')  # The guide spells keys as Control+.


if __name__ == '__main__':
    unittest.main()
