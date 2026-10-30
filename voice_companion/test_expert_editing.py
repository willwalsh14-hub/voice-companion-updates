"""Word processor, Notepad, reader and File manager: what an expert screen reader user met (sweep, October 30, 2026)."""
import tempfile
import unittest
from pathlib import Path

import companion as app
from document_editor import VoiceDocument
from test_document_save_as import DocumentFlowCase


class EditorCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.doc = VoiceDocument(Path(folder.name), title='Letter')
        self.doc.dictating = True

    def write(self, *paragraphs):
        for index, text in enumerate(paragraphs):
            if index: self.doc.process('new paragraph')
            self.doc.process(text)

    def texts(self):
        return [p.text for p in self.doc.paragraphs]


class ReadingTheParagraphKeepsYourPlace(EditorCase):
    """Reading the current line or paragraph is a look, not a move, as NVDA+Up is.

    Before: "next sentence", then "read paragraph", then "delete sentence" deleted the
    first sentence instead of the one the user had moved to."""
    def setUp(self):
        super().setUp()
        self.write('The quick brown fox jumps. Over the lazy dog. A third sentence.')

    def test_delete_sentence_after_reading_the_paragraph(self):
        for look in ('read paragraph', 'read current paragraph', 'read current line', 'read line'):
            with self.subTest(look=look):
                self.doc.process('beginning of document')
                self.assertEqual(self.doc.process('next sentence'), 'Over the lazy dog.')
                self.doc.process(look)
                self.assertEqual(self.doc.process('read current sentence'), 'Over the lazy dog.')
                self.assertIn('Over the lazy dog', self.doc.process('delete sentence'))
                self.assertEqual(self.texts(), ['The quick brown fox jumps. A third sentence.'])
                self.doc.process('undo')

    def test_delete_word_after_reading_the_paragraph(self):
        for look in ('read paragraph', 'read current line'):
            with self.subTest(look=look):
                self.doc.process('beginning of document')
                self.doc.process('next word'); self.assertEqual(self.doc.process('next word'), 'brown')
                self.doc.process(look)
                self.assertEqual(self.doc.process('read current word'), 'brown')
                self.doc.process('delete word')
                self.assertEqual(self.texts(), ['The quick fox jumps. Over the lazy dog. A third sentence.'])
                self.doc.process('undo')

    def test_delete_word_acts_on_the_word_read_current_word_says(self):
        self.doc.process('beginning of document')
        for _ in range(4): self.doc.process('next word')
        self.doc.process('read paragraph')
        self.assertEqual(self.doc.process('read current word'), 'jumps.')
        self.assertEqual(self.doc.process('delete word'), 'Deleted: jumps. Say undo to restore it.')

    def test_next_word_continues_from_where_you_were(self):
        self.doc.process('beginning of document')
        self.doc.process('next word'); self.doc.process('next word')
        self.doc.process('read paragraph')
        self.assertEqual(self.doc.process('next word'), 'fox')


class DeletingLeavesTidyText(EditorCase):
    """Deleting a word or sentence takes one space with it, as Word does.

    Before: "delete sentence" in the middle left two spaces, and "cut" at the start of a
    paragraph left a space in front of the next word."""
    def setUp(self):
        super().setUp()
        self.write('Dear Bob. Thanks for the call. See you soon.')
        self.doc.process('beginning of document')

    def test_delete_a_middle_sentence(self):
        self.doc.process('next sentence')
        self.doc.process('delete sentence')
        self.assertEqual(self.texts(), ['Dear Bob. See you soon.'])

    def test_delete_the_last_sentence(self):
        self.doc.process('next sentence'); self.doc.process('next sentence')
        self.doc.process('delete sentence')
        self.assertEqual(self.texts(), ['Dear Bob. Thanks for the call.'])

    def test_cut_the_first_sentence_then_paste_it_at_the_end(self):
        self.doc.process('cut sentence')
        self.assertEqual(self.texts(), ['Thanks for the call. See you soon.'])

    def test_delete_a_word_by_name(self):
        self.doc.process('delete the call')
        self.assertEqual(self.texts(), ['Dear Bob. Thanks for. See you soon.'])
        self.doc.process('undo')
        self.doc.process('delete Thanks')
        self.assertEqual(self.texts(), ['Dear Bob. for the call. See you soon.'])


class NoDoubledPeriods(EditorCase):
    """Before: "Deleted: Dear Bob.. Say undo to restore it." The screen reader paused twice."""
    def setUp(self):
        super().setUp()
        self.write('Dear Bob. Thanks for the call.')
        self.doc.process('beginning of document')

    def test_cut_delete_paste_and_replace(self):
        from unittest.mock import patch
        import document_editor
        clipboard = []
        with patch.object(document_editor, 'copy_to_clipboard', clipboard.append), \
             patch.object(document_editor, 'paste_from_clipboard', lambda: clipboard[-1]):
            self.assertEqual(self.doc.process('cut sentence'), 'Cut: Dear Bob. Say undo to restore it.')
            self.assertEqual(self.doc.process('paste'), 'Pasted: Dear Bob. Say undo to remove it.')
            self.doc.process('undo'); self.doc.process('undo')
            self.assertEqual(self.doc.process('delete sentence'), 'Deleted: Dear Bob. Say undo to restore it.')
            self.doc.process('undo')
            self.assertEqual(self.doc.process('replace Bob. with Sam.'), 'Replaced Bob. with Sam.')
            self.assertEqual(self.doc.process('delete word'), 'Deleted: Dear. Say undo to restore it.')


class FormattingIsConfirmedInPlainWords(EditorCase):
    """Before: "Formatted the current paragraph. italic: True." and "style: Heading 2.",
    program words the user should never hear."""
    def setUp(self):
        super().setUp()
        self.write('Dear Bob.')

    def test_paragraph_formatting(self):
        for command, said in (('make this italic', 'Formatted the current paragraph. Italic.'),
                              ('remove italic', 'Formatted the current paragraph. Not italic.'),
                              ('make this bold', 'Formatted the current paragraph. Bold.'),
                              ('heading 2', 'Formatted the current paragraph. Heading 2.'),
                              ('normal text', 'Formatted the current paragraph. Normal text.'),
                              ('bulleted list', 'Formatted the current paragraph. Bulleted list.'),
                              ('align center', 'Formatted the current paragraph. Alignment center.'),
                              ('justify justified', 'Formatted the current paragraph. Alignment justified.'),
                              ('set font size to 14', 'Formatted the current paragraph. Font size 14 points.'),
                              ('change font to Arial', 'Formatted the current paragraph. Font Arial.'),
                              ('set font Verdana size 12', 'Formatted the current paragraph. Font Verdana. Font size 12 points.')):
            with self.subTest(command=command):
                self.assertEqual(self.doc.process(command), said)

    def test_selection_and_word_formatting(self):
        self.doc.process('select all')
        self.assertEqual(self.doc.process('set font size to 16'), 'Formatted the selection. Font size 16 points.')
        self.doc.process('unselect')
        self.doc.process('beginning of document')
        self.assertEqual(self.doc.process('set word size 12'), 'Formatted word Dear. Font size 12 points.')


class MakeItIsOnlyAFontSize(EditorCase):
    """Before: dictating "Make it quick." said "Please say a whole font size" and the words were lost."""
    def test_a_sentence_that_starts_with_make_it_is_written(self):
        for sentence in ('Make it quick.', 'make it a good one', 'Make it happen!'):
            with self.subTest(sentence=sentence):
                self.assertNotIn('font size', self.doc.process(sentence))
                self.assertIn(sentence.lower().rstrip('.!'), ' '.join(self.texts()).lower())

    def test_make_it_a_size_still_sets_the_size(self):
        self.write('Dear Bob.')
        self.assertEqual(self.doc.process('make it 14 points'), 'Formatted the current paragraph. Font size 14 points.')
        self.assertEqual(self.doc.process('make it twelve'), 'Formatted the current paragraph. Font size 12 points.')
        self.assertEqual(self.doc.process('make it 100 points'), 'Choose a font size from 6 to 72 points.')
        self.assertEqual(self.texts(), ['Dear Bob.'])


class RedoLandsOnTheChange(EditorCase):
    """Before: Redo of typing in paragraph 2 said "Paragraph 1 of 2" and put you in paragraph 1,
    so the next words went to the wrong place."""
    def test_redo_typing_in_a_later_paragraph(self):
        self.write('Opening words.', 'Closing words.')
        self.doc.process('beginning of document')
        self.assertTrue(self.doc.process('undo').startswith('Undid typing: Closing words. Paragraph 2 of 2.'))
        self.doc.process('beginning of document')
        self.assertEqual(self.doc.process('redo'), 'Redid typing: Closing words. Paragraph 2 of 2. Closing words.')
        self.assertEqual(self.doc.cursor, 1)

    def test_redo_a_new_paragraph(self):
        self.write('Opening words.')
        self.doc.process('new paragraph')
        self.doc.process('undo')
        self.assertEqual(self.doc.process('redo'), 'Redid a new paragraph. Paragraph 2 of 2. Empty paragraph.')

    def test_redo_a_deleted_last_paragraph(self):
        self.write('Opening words.', 'Closing words.')
        self.doc.process('delete paragraph')
        self.doc.process('undo'); self.doc.process('beginning of document')
        self.assertTrue(self.doc.process('redo').startswith('Redid delete paragraph. Paragraph 1 of 1.'))


class ReaderFindToleratesSpeech(DocumentFlowCase):
    """Before: in the Document reader, "Find milk." said "Text not found." because of the period
    the recognizer added."""
    def setUp(self):
        super().setUp()
        from document_reader import open_document
        path = self.elsewhere / 'notes.txt'
        path.write_text('Shopping list.\nMilk and eggs.\n\nCall the doctor soon. Then rest.\n')
        app.FILE_READER = open_document(path)

    def test_a_spoken_period_or_question_mark_is_left_off(self):
        for heard, line in (('Find milk.', 'Milk and eggs.'), ('find the doctor?', 'Call the doctor soon. Then rest.'),
                            ('Find, shopping.', 'Shopping list.')):
            with self.subTest(heard=heard):
                app.handle(heard, 'file_reader')
                self.assertEqual(self.last(), line)

    def test_find_with_no_words_asks_for_them(self):
        app.handle('find.', 'file_reader')
        self.assertEqual(self.last(), 'Please say the words to find after find.')


class ReaderWarningsAreReadOnce(unittest.TestCase):
    """Before: a scanned PDF said "...check against the original image.. " with a doubled period."""
    def test_no_doubled_period(self):
        from document_reader import DocumentReader
        from file_manager import FileManager
        reader = DocumentReader('scan.pdf', 'Dear Bob.')
        reader.warnings = ['Page 1: offline OCR text.', '3 OCR lines on page 1 have low confidence. Check against the original image.']
        said = FileManager.__new__(FileManager).finish_open(reader)
        self.assertNotIn('..', said)
        self.assertIn('original image. Dear Bob.', said)


class MissingFolderSuggestsAnother(unittest.TestCase):
    """Before: "Your Desktop folder was not found. Say another folder, such as Desktop."."""
    def test_the_suggestion_is_a_different_folder(self):
        import attachment_screen
        from unittest.mock import patch
        with patch.object(attachment_screen, 'place', lambda key: Path('/no/such/folder/' + key)):
            for heard, other in (('Desktop.', 'Documents'), ('documents', 'Desktop')):
                with self.subTest(heard=heard):
                    with self.assertRaises(attachment_screen.AttachmentError) as caught:
                        attachment_screen.spoken_folder(heard)
                    said = str(caught.exception)
                    self.assertTrue(said.endswith('Say another folder, such as ' + other + '.'), said)


class DeleteParagraphStartsAtTheNextOne(EditorCase):
    """Before: after "delete paragraph", "delete word" removed a word in the next paragraph
    at the place you had been in the deleted one."""
    def test_the_next_paragraph_starts_from_its_first_word(self):
        self.write('One two three.', 'Four five six.')
        self.doc.process('beginning of document')
        self.doc.process('next word'); self.doc.process('next word')
        self.assertEqual(self.doc.process('delete paragraph'), 'Deleted paragraph: One two three. Say undo to restore it.')
        self.assertEqual(self.doc.process('read current word'), 'Four')
        self.doc.process('delete word')
        self.assertEqual(self.texts(), ['five six.'])

    def test_a_paragraph_without_a_period(self):
        self.write('Shopping list', 'Milk')
        self.doc.process('beginning of document')
        self.assertEqual(self.doc.process('delete paragraph'), 'Deleted paragraph: Shopping list. Say undo to restore it.')


class FileManagerCountsAndSelection(unittest.TestCase):
    """Before: "1 items copied.", and "What is selected" said "No item is named what is selected"."""
    def setUp(self):
        from file_manager import FileManager
        from test_file_manager import FakeFiles, settle
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.disk = Path(folder.name)
        for name in ('apple.txt', 'Banana.docx', 'cherry.txt'):
            (self.disk / name).write_text('x')
        self.settle = settle
        self.fm = FileManager(FakeFiles(self.disk)); self.fm.refresh(str(self.disk)); settle(self.fm)

    def run_command(self, text):
        said = self.fm.command(text)
        return ' '.join(part for part in (said, self.settle(self.fm)) if part)

    def test_one_item_is_one_item(self):
        self.assertIn('1 item copied.', self.run_command('copy'))
        self.assertIn('1 item cut.', self.run_command('cut'))
        self.assertIn('1 item pasted.', self.run_command('paste'))
        self.assertIn('3 items copied.', (self.run_command('select all'), self.run_command('copy'))[1])

    def test_what_is_selected(self):
        for heard in ('what is selected', "What's selected?", 'say selection'):
            with self.subTest(heard=heard):
                self.fm.selected.clear()
                self.assertEqual(self.fm.command(heard), 'Nothing is selected. apple.txt, file. 1 of 3.')
        self.fm.command('toggle selection'); self.fm.command('end'); self.fm.command('toggle selection')
        self.assertEqual(self.fm.command('what is selected'), '2 items selected: apple.txt, cherry.txt.')
        self.fm.command('select all')
        self.assertEqual(self.fm.command('what is selected'), '3 items selected: apple.txt, Banana.docx, cherry.txt.')


if __name__ == '__main__':
    unittest.main()
