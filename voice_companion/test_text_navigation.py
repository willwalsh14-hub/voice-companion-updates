"""Will, October 31, 2026: reading and moving through text must work the same on every screen, as in
JAWS and NVDA. Left and Right by character, Control with them by word, Up and Down by line, Control with
them by paragraph, Insert+Down to say all. Blank lines always say Blank, in email above all. Text can
be selected by keyboard and by voice, by character, word, line, sentence and paragraph, and all of it."""
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import companion as app
import text_review
from field_selection import FieldSelection
from keyboard_text import (BLANK, caret_feedback, is_blank, selection_feedback, spoken_line, word_at,
                           word_destination)
from onboard_help import HelpReadingCursor
from reading_navigation import ReadingCursor, reading_request


class BlankLineTests(unittest.TestCase):
    def test_lines_that_show_nothing_are_blank(self):
        for line in ('', ' ', '\t', ' ', '‌  ', '͏​', '﻿'):
            self.assertTrue(is_blank(line), repr(line))
            self.assertEqual(spoken_line(line), BLANK)
        for line in ('a', ' . ', ' x'):
            self.assertFalse(is_blank(line), repr(line))

    def test_up_and_down_say_blank_on_a_line_with_only_spaces_or_hidden_characters(self):
        # The kind of empty line an HTML email has: a no-break space and a zero-width joiner.
        text = 'Hi Will,\n ‌\nSee you soon.'
        self.assertEqual(caret_feedback(text, 10, 'Down')[0], BLANK)
        self.assertEqual(caret_feedback(text, 0, 'Down')[0], 'Hi Will,')
        self.assertEqual(caret_feedback('First\n\nLast', 6, 'Down')[0], BLANK)
        self.assertEqual(caret_feedback('First\n   \nLast', 7, 'Up')[0], BLANK)

    def test_an_email_line_read_with_its_formatting_says_blank_too(self):
        from email_reader import MessageBody, spoken_range
        body = MessageBody('Hello\n \nBye', spans=[(0, 5, 'bold', '')])
        self.assertEqual(spoken_line(spoken_range(body, 6, 7)), BLANK)

    def test_left_and_right_say_blank_at_a_line_end_and_the_end_of_the_text(self):
        self.assertEqual(caret_feedback('ab\ncd', 2, 'Right')[0], BLANK)
        self.assertEqual(caret_feedback('ab\n\ncd', 3, 'Right')[0], BLANK)
        self.assertEqual(caret_feedback('ab', 2, 'Right')[0], BLANK)
        self.assertEqual(caret_feedback('ab', 1, 'Right'), ('b', 'b'))
        self.assertEqual(caret_feedback('a\n \nb', 2, 'Right')[0], BLANK)

    def test_voice_reading_says_blank_on_an_empty_line(self):
        cursor = ReadingCursor(); cursor.set_text('One\n \nTwo')
        self.assertEqual(cursor.move('line', 1), BLANK)
        self.assertEqual(cursor.move('line', 1), 'Two')
        cursor.position = 2
        self.assertEqual(cursor.move('character', 1), BLANK)


class WordMovementTests(unittest.TestCase):
    TEXT = 'Dear Will,\n\nThe plan works.'

    def test_control_right_stops_at_each_word_line_end_and_empty_line(self):
        caret, said = 0, []
        for _ in range(7):
            caret = word_destination(self.TEXT, caret, 1)
            said.append(word_at(self.TEXT, caret))
        self.assertEqual(said, ['Will,', BLANK, BLANK, 'The', 'plan', 'works.', BLANK])

    def test_control_left_comes_back_the_same_way(self):
        caret, said = len(self.TEXT), []
        for _ in range(7):
            caret = word_destination(self.TEXT, caret, -1)
            said.append(word_at(self.TEXT, caret))
        self.assertEqual(said, ['works.', 'plan', 'The', BLANK, BLANK, 'Will,', 'Dear'])
        self.assertEqual(word_destination(self.TEXT, 0, -1), 0)

    def test_control_arrows_in_the_window_say_the_word(self):
        self.assertEqual(caret_feedback('One two.', 4, 'Right', True), ('two.', ''))
        self.assertEqual(caret_feedback('One\n\ntwo', 4, 'Right', True), (BLANK, ''))


class SelectionFeedbackTests(unittest.TestCase):
    def test_shift_and_an_arrow_say_only_what_changed(self):
        self.assertEqual(selection_feedback('hello', None, (0, 1)), 'Selected h')
        self.assertEqual(selection_feedback('hello', (0, 1), (0, 2)), 'Selected e')
        self.assertEqual(selection_feedback('hello', (0, 2), (0, 1)), 'Unselected e')
        self.assertEqual(selection_feedback('hello', (3, 5), (2, 5)), 'Selected l')
        self.assertEqual(selection_feedback('one two', (0, 3), (0, 7)), 'Selected two')
        self.assertEqual(selection_feedback('a\nb', (0, 1), (0, 2)), 'Selected blank')
        self.assertEqual(selection_feedback('hello world', None, (0, 11)), 'All text selected.')

    def test_window_feedback_uses_the_change(self):
        self.assertEqual(caret_feedback('One two', 3, 'Right', False, (0, 3)), ('Selected One', ''))
        self.assertEqual(caret_feedback('One two', 4, 'Right', False, (0, 4), (0, 3)), ('Selected space', ''))


class SayAllTests(unittest.TestCase):
    def test_say_all_by_voice_with_speech_punctuation(self):
        for said in ('Say all.', 'say all', 'Read from here.', 'read to the end', 'Read the rest', 'read all'):
            self.assertEqual(reading_request(said), ('read', 'all'), said)

    def test_say_all_reads_from_the_reading_place_to_the_end(self):
        text = 'Intro. ' + 'Word ' * 600 + 'Last line.'
        cursor = ReadingCursor(); cursor.set_text(text)
        cursor.position = cursor.continuation = 7
        result = cursor.read('all')
        self.assertTrue(result.startswith('Word'))
        self.assertTrue(result.endswith('Last line. End of text.'))
        self.assertEqual(cursor.read('continue'), 'End of text.')

    def test_a_part_and_the_top_still_work(self):
        cursor = ReadingCursor(); cursor.set_text('A. ' * 800)
        self.assertIn('Say continue reading for more.', cursor.read('top'))
        self.assertIn('Say continue reading for more.', cursor.read(True))

    def test_help_says_every_block_to_the_end_of_the_topic(self):
        cursor = HelpReadingCursor(); cursor.set_text('First.\nSecond.\nThird.')
        self.assertEqual(cursor.read('top'), 'First.')
        self.assertEqual(cursor.read('all'), 'Second. Third.')
        self.assertEqual(cursor.read('continue'), 'End of topic.')

    def test_insert_down_says_all_and_insert_up_reads_the_line_on_every_text_screen(self):
        for mode in app.TEXT_SCREENS:
            with patch.object(app, 'app_context', return_value={'mode': mode, 'source': 'x'}), \
                    patch.object(app, 'interrupt_speech'):
                self.assertEqual(app._navigation_key('Insert+Down', mode), 'say all', mode)
                self.assertEqual(app._navigation_key('CapsLock+Down', mode), 'say all', mode)
                self.assertEqual(app._navigation_key('Insert+Up', mode), 'read current line', mode)
                self.assertEqual(app._navigation_key('Insert+Shift+Down', mode), 'what is selected', mode)
        self.assertEqual(reading_request('read current line'), ('line', 0))


class VoiceSelectionRequestTests(unittest.TestCase):
    def test_spoken_commands_tolerate_capitals_and_punctuation(self):
        cases = {'Select word.': ('unit', 'word', 0), 'select, sentence': ('unit', 'sentence', 0),
                 'Select the current line.': ('unit', 'line', 0), 'select paragraph': ('unit', 'paragraph', 0),
                 'Select letter': ('unit', 'character', 0), 'select character': ('unit', 'character', 0),
                 'select next word': ('unit', 'word', 1), 'Select previous line.': ('unit', 'line', -1),
                 'Select all.': ('all',), 'Extend selection by word.': ('extend', 'word', 1),
                 'extend selection to previous sentence': ('extend', 'sentence', -1),
                 'Select to end of line.': ('to', 1, 'line'), 'select from here to the end': ('to', 1, 'all'),
                 'select to the beginning of the paragraph': ('to', -1, 'paragraph'),
                 'What is selected?': ('say',), 'Unselect.': ('clear',), 'Copy that.': ('copy',)}
        for said, want in cases.items():
            self.assertEqual(text_review.request(said), want, said)
        for sentence in ('Select a time that works', 'copy the files tomorrow', 'selected', 'select'):
            self.assertIsNone(text_review.request(sentence), sentence)


class VoiceSelectionTests(unittest.TestCase):
    TEXT = 'Hello there. How are you?\n\nSecond paragraph here.'

    def reader(self, position=0):
        reader = ReadingCursor(); reader.set_text(self.TEXT); reader.position = position
        return reader

    def run_command(self, reader, selection, said):
        return text_review.apply(reader, selection, text_review.request(said))

    def test_each_unit_at_the_reading_place(self):
        expected = {'select character': ((6, 7), 'Selected t.'), 'select word': ((6, 12), 'Selected there.'),
                    'select sentence': ((0, 12), 'Selected Hello there.'),
                    'select line': ((0, 25), 'Selected Hello there. How are you?'),
                    'select paragraph': ((0, 25), 'Selected Hello there. How are you?'),
                    'select all': ((0, len(self.TEXT)), 'All text selected.')}
        for said, want in expected.items():
            self.assertEqual(self.run_command(self.reader(6), None, said), want, said)

    def test_next_and_previous_and_growing_the_selection(self):
        reader = self.reader(6)
        selection, said = self.run_command(reader, None, 'select word')
        selection, said = self.run_command(reader, selection, 'extend selection by word')
        self.assertEqual((selection, said), ((6, 16), 'Selected How.'))
        selection, said = self.run_command(reader, selection, 'extend selection to previous word')
        self.assertEqual((selection, said), ((0, 16), 'Selected Hello.'))
        selection, said = self.run_command(self.reader(0), None, 'select next paragraph')
        self.assertEqual((selection, said), ((27, len(self.TEXT)), 'Selected Second paragraph here.'))

    def test_to_the_end_and_to_the_start(self):
        self.assertEqual(self.run_command(self.reader(13), None, 'select to end of line')[0], (13, 25))
        self.assertEqual(self.run_command(self.reader(13), None, 'select to the beginning of the line')[0], (0, 13))
        self.assertEqual(self.run_command(self.reader(27), None, 'select from here to the end')[0], (27, len(self.TEXT)))

    def test_say_clear_and_copy(self):
        copied = []
        reader = self.reader()
        self.assertEqual(text_review.apply(reader, (0, 5), ('say',)), ((0, 5), 'Selected: Hello.'))
        self.assertEqual(text_review.apply(reader, (0, 5), ('copy',), clipboard=copied.append), ((0, 5), 'Copied.'))
        self.assertEqual(copied, ['Hello'])
        self.assertEqual(text_review.apply(reader, (0, 5), ('clear',)), (None, 'Selection cleared.'))
        self.assertEqual(text_review.apply(reader, None, ('say',)), (None, 'No text is selected.'))

    def test_empty_text_says_so_and_never_fails(self):
        reader = ReadingCursor(); reader.set_text('')
        for want in (('all',), ('unit', 'word', 0), ('extend', 'line', 1), ('to', 1, 'all')):
            self.assertEqual(text_review.apply(reader, None, want), (None, 'There is no text to select.'))


class ScreenTests(unittest.TestCase):
    """Through the real app: the Document reader, an open email, and a note."""

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'help_session': None,
                    'tutorial_session': None, 'MEDIA_HUB': None, 'note_buffer': app.NoteBuffer(),
                    'note_selection': FieldSelection(), 'note_reading': ReadingCursor(), 'OPEN_NOTE': None,
                    'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'PREFERENCES': dict(app.PREFERENCES), 'FILE_READER': None,
                    'SLEEP_RETURN_MODE': 'awake', 'SEARCH_RETURN_MODE': 'awake'}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=(name == 'OPEN_NOTE')))
        self.speech = self.stack.enter_context(patch.object(app, 'speak'))
        self.stack.enter_context(patch.object(app, 'speak_prompt', side_effect=lambda text, *a, **k: app.speak(text)))

    def last(self):
        return str(self.speech.call_args.args[0])

    def test_document_reader_selects_and_copies_by_voice(self):
        reading = ReadingCursor(); reading.set_text('Alpha beta.\nGamma delta.')
        reader = SimpleNamespace(text=reading.text, reading=reading, selection=None, spans=[])
        copied = []
        with patch.object(app, 'FILE_READER', reader), patch('document_editor.copy_to_clipboard', copied.append):
            self.assertEqual(app.review_text_command('Select line.', 'file_reader'), 'Selected Alpha beta.')
            self.assertEqual(reader.selection, (0, 11))
            self.assertEqual(app.review_text_command('Copy.', 'file_reader'), 'Copied.')
            self.assertEqual(copied, ['Alpha beta.'])
            self.assertEqual(app._navigation_key('Insert+Down', 'file_reader'), 'say all')

    def test_email_keeps_its_own_meaning_when_no_text_is_selected(self):
        reading = ReadingCursor()
        mail = SimpleNamespace(view='message', pending=None, body_text='Line one.\n \nLine two.', reading=reading)
        with patch.object(app, 'mail_session', mail):
            # "Clear selection" and "What is selected" are about marked messages until text is selected.
            self.assertIsNone(app.review_text_command('clear selection', 'mailbox'))
            self.assertIsNone(app.review_text_command('what is selected', 'mailbox'))
            self.assertEqual(app.review_text_command('select word', 'mailbox'), 'Selected Line.')
            self.assertEqual(reading.selection, (0, 4))
            self.assertEqual(app.review_text_command('what is selected', 'mailbox'), 'Selected: Line.')
            mail.view = 'folder'
            self.assertIsNone(app.review_text_command('select word', 'mailbox'))   # the list, not a message

    def test_the_arrows_read_a_note_when_nothing_is_being_typed(self):
        from input_parity import screen_key
        self.assertEqual(screen_key('note', 'Down', 0, {}), 'Down')
        self.assertIsNone(screen_key('note', 'Down', 0, {}, typed_text='more words'))
        self.assertEqual(app.navigation_key('Down', 'note'), 'next line')
        self.assertEqual(app.navigation_key('Ctrl+Right', 'note'), 'next word')
        self.assertEqual(app.navigation_key('Insert+Down', 'note'), 'say all')
        app.note_buffer[:] = ['First line\n \nlast line']
        app.note_reading.set_text(' '.join(app.note_buffer))
        mode = 'note'
        app.handle('next line', mode)
        self.assertEqual(self.last(), BLANK)
        app.handle('next line', mode)
        self.assertEqual(self.last(), 'last line')


if __name__ == '__main__':
    unittest.main()
