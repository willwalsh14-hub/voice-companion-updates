"""Regression checks for document editor and reader bugs found in the October 2026 review."""
import re
import shutil
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

import document_editor
from document_editor import Paragraph, VoiceDocument
from reading_navigation import ReadingCursor
from rich_documents import RichText
from task_navigation import TaskNavigation

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


class EditorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def document(self, *texts, title='Untitled'):
        document = VoiceDocument(self.folder, title=title)
        document.paragraphs = [Paragraph(text) for text in texts]
        return document

    def texts(self, document):
        return [p.text for p in document.paragraphs]

    def write_docx(self, name, body):
        xml = f'<?xml version="1.0"?><w:document {W}><w:body>{body}<w:sectPr/></w:body></w:document>'
        with zipfile.ZipFile(self.folder / name, 'w') as archive:
            archive.writestr('word/document.xml', xml)


class StaleSelectionTests(EditorTestCase):
    def test_selection_is_dropped_after_its_paragraph_is_deleted(self):
        document = self.document('Draft', 'Keep this paragraph plain', 'Other')
        document.process('select first paragraph')
        document.process('delete paragraph')
        document.process('delete selection')
        self.assertEqual(self.texts(document), ['Keep this paragraph plain', 'Other'])

    def test_selection_past_the_end_does_not_crash(self):
        document = self.document('alpha', 'beta', 'gamma')
        document.process('select last paragraph')
        document.process('delete paragraph')
        self.assertIn('No text is selected', document.process('read selection'))
        document.process('make this bold')
        self.assertEqual(self.texts(document), ['alpha', 'beta'])

    def test_formatting_keeps_a_selection_on_unchanged_text(self):
        document = self.document('Hello world')
        document.process('select first paragraph')
        document.process('bold selection')
        self.assertIsNotNone(document.selection)
        self.assertIn('Hello world', document.process('read selection'))


class EmptyDocumentTests(EditorTestCase):
    def test_new_paragraph_in_an_empty_document_then_read_and_dictate(self):
        document = self.document()
        self.assertEqual(document.process('new paragraph'), 'New paragraph.')
        self.assertEqual((len(document.paragraphs), document.cursor), (1, 0))
        self.assertEqual(document.process('read paragraph'), 'Empty paragraph.')
        document.append_text('hello', literal=True)
        self.assertEqual(self.texts(document), ['hello'])


class CommandOrderTests(EditorTestCase):
    def test_formatting_commands_do_not_rewrite_body_text(self):
        body = 'The font on this page is nice. Double spacing and line spacing matter.'
        document = self.document(body)
        document.process('change font to Arial')
        self.assertEqual(document.paragraphs[0].text, body)
        self.assertEqual(document.paragraphs[0].font, 'Arial')
        document.process('change line spacing to double')
        self.assertEqual(document.paragraphs[0].text, body)
        self.assertEqual(document.paragraphs[0].line_spacing_twips, 480)
        document.process('change font size to 14')
        self.assertEqual(document.paragraphs[0].text, body)
        self.assertEqual(document.paragraphs[0].size, 14)

    def test_replace_paragraph_replaces_the_whole_paragraph(self):
        for command in ('replace paragraph with Brand new text', 'replace current paragraph with Brand new text'):
            document = self.document('An old paragraph here.')
            document.process(command)
            self.assertEqual(self.texts(document), ['Brand new text'])

    def test_ordinary_replacement_still_works(self):
        document = self.document('The quick brown fox.')
        document.process('change quick to slow')
        self.assertEqual(self.texts(document), ['The slow brown fox.'])
        document = self.document('Pick a new font for the letter.')
        document.process('replace font with typeface')
        self.assertEqual(self.texts(document), ['Pick a new typeface for the letter.'])


class BlankParagraphReadingTests(EditorTestCase):
    def test_reading_a_blank_paragraph_keeps_the_cursor_there(self):
        document = self.document('First paragraph.')
        document.process('new paragraph')
        self.assertEqual(document.process('read paragraph'), 'Empty paragraph.')
        self.assertEqual(document.cursor, 1)
        document.append_text('Second paragraph text', literal=True)
        self.assertEqual(self.texts(document), ['First paragraph.', 'Second paragraph text'])

    def test_next_paragraph_onto_a_blank_paragraph(self):
        document = self.document('Only text', '')
        self.assertEqual(document.process('next paragraph'), 'Empty paragraph.')
        self.assertEqual(document.cursor, 1)

    def test_next_word_from_leading_blank_space_reads_the_first_word(self):
        cursor = ReadingCursor()
        cursor.set_text('  Hello world')
        self.assertEqual(cursor.move('word', 1), 'Hello')
        cursor.set_text('\nFirst para\nSecond para', reset=True)
        self.assertEqual(cursor.move('paragraph', 1), 'First para')


class TableSelectionTests(EditorTestCase):
    def table_document(self):
        document = self.document('Intro')
        document.process('create table with two rows and two columns')
        for paragraph, text in zip(document.paragraphs[1:], ('apple', 'banana', 'c', 'd')):
            paragraph.text = text
        document.save()
        return document

    def cells(self, document):
        return [(p.text, p.table_id, p.row, p.column) for p in document.paragraphs]

    def test_change_across_cells_is_refused_and_table_still_saves(self):
        document = self.table_document()
        before = self.cells(document)
        self.assertIn('crosses table cells', document.process('change apple banana to fruit'))
        self.assertEqual(self.cells(document), before)
        document.save()

    def test_paste_across_cells_is_refused(self):
        document = self.table_document()
        document.process('select from apple to banana')
        document_editor.copy_to_clipboard('X')
        before = self.cells(document)
        self.assertIn('crosses table cells', document.process('paste'))
        self.assertEqual(self.cells(document), before)
        document.save()


class DeleteLineTests(EditorTestCase):
    def test_delete_line_removes_only_the_current_line(self):
        document = self.document('Line one\nLine two')
        document.navigation_position = (0, 0)
        self.assertEqual(document.process('delete line'), 'Deleted line: Line one. Say undo to restore it.')
        self.assertEqual(self.texts(document), ['Line two'])
        document = self.document('Line one\nLine two')
        document.navigation_position = (0, 12)
        document.process('delete line')
        self.assertEqual(self.texts(document), ['Line one'])
        document = self.document('Only line')
        document.process('delete line')
        self.assertEqual(self.texts(document), [''])


class OpeningFilesTests(EditorTestCase):
    def test_doubled_extension_opens_and_saves_its_own_file(self):
        original = self.document('ORIGINAL', title='Letter'); original.save()
        other = self.document('Other contents', title='tmp'); other.save()
        shutil.move(self.folder / 'tmp.docx', self.folder / 'Letter.docx.docx')
        opened = VoiceDocument.open_existing(self.folder, 'Letter.docx')
        self.assertEqual(opened.paragraphs[0].text, 'Other contents')
        self.assertEqual(opened.path.name, 'Letter.docx.docx')
        opened.append_text('edit', literal=True)
        self.assertEqual(VoiceDocument.open_existing(self.folder, 'Letter').paragraphs[0].text, 'ORIGINAL')

    def test_renaming_an_opened_file_moves_it(self):
        self.document('Body', title='Report').save()
        opened = VoiceDocument.open_existing(self.folder, 'Report')
        opened.process('rename document to Summary')
        self.assertEqual(sorted(p.name for p in self.folder.glob('*.docx')), ['Summary.docx'])

    def test_empty_or_damaged_word_files_are_reported_not_raised(self):
        (self.folder / 'New Microsoft Word Document.docx').write_bytes(b'')
        (self.folder / 'Notes.docx').write_bytes(b'PK\x05\x06' + b'\0' * 18)
        app = types.SimpleNamespace(speak=lambda text: None, APP=self.folder, document=None,
                                    FILE_MANAGER=None, checkpoint_document=lambda: None)
        for name in ('New Microsoft Word Document.docx', 'Notes.docx'):
            with self.assertRaisesRegex(ValueError, 'empty or damaged'):
                TaskNavigation(app).open_document(self.folder / name)
            with self.assertRaisesRegex(ValueError, 'empty or damaged'):
                VoiceDocument.open_existing(self.folder, Path(name).stem)


class WordFormattingRoundTripTests(EditorTestCase):
    def saved_runs(self, name):
        xml = zipfile.ZipFile(self.folder / name).read('word/document.xml').decode()
        return re.findall(r'<w:rPr>(.*?)</w:rPr>', xml)

    def test_switched_off_formatting_stays_off(self):
        self.write_docx('Off.docx', '<w:p><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>Not bold</w:t></w:r></w:p>'
                                    '<w:p><w:r><w:rPr><w:u w:val="none"/></w:rPr><w:t>No underline</w:t></w:r></w:p>'
                                    '<w:p><w:r><w:rPr><w:i w:val="false"/></w:rPr><w:t>Not italic</w:t></w:r></w:p>'
                                    '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r></w:p>')
        document = VoiceDocument.open_existing(self.folder, 'Off')
        self.assertEqual([(p.bold, p.italic, p.underline) for p in document.paragraphs],
                         [(False, False, False)] * 3 + [(True, False, False)])
        document.save()
        self.assertEqual(self.saved_runs('Off.docx'), ['', '', '', '<w:b/>'])

    def test_half_point_font_sizes_survive(self):
        self.write_docx('Size.docx', '<w:p><w:r><w:rPr><w:sz w:val="21"/></w:rPr><w:t>Ten and a half</w:t></w:r></w:p>')
        document = VoiceDocument.open_existing(self.folder, 'Size')
        self.assertEqual(document.paragraphs[0].size, 10.5)
        self.assertIn('10.5 points', document.formatting_report())
        document.save()
        self.assertEqual(self.saved_runs('Size.docx'), ['<w:sz w:val="21"/>'])

    def test_formatting_the_editor_would_drop_is_refused(self):
        self.write_docx('Spaced.docx', '<w:p><w:pPr><w:spacing w:before="480" w:after="480"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>')
        self.write_docx('Titled.docx', '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>')
        for name in ('Spaced', 'Titled'):
            with self.assertRaisesRegex(ValueError, 'cannot preserve'):
                VoiceDocument.open_existing(self.folder, name)
        self.write_docx('Line.docx', '<w:p><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>')
        self.assertEqual(VoiceDocument.open_existing(self.folder, 'Line').paragraphs[0].line_spacing_twips, 360)


class HtmlReaderTests(unittest.TestCase):
    def read(self, html):
        parser = RichText(); parser.feed(html); parser.finish()
        return parser.text

    def test_hidden_void_elements_do_not_hide_the_rest(self):
        self.assertEqual(self.read('<p>Intro</p><img aria-hidden="true"><p>Important body text</p>'),
                         'Intro\nImportant body text')
        self.assertEqual(self.read('<p>Intro</p><br hidden><p>Important body text</p>'),
                         'Intro\nImportant body text')

    def test_unclosed_tags_inside_a_hidden_block(self):
        self.assertEqual(self.read('<p>Intro</p><div hidden><p>secret</div><p>Important body text</p>'),
                         'Intro\nImportant body text')
        self.assertEqual(self.read('<p>Intro</p><div aria-hidden="true"><ul><li>one<li>two</ul></div><p>Shown</p>'),
                         'Intro\nShown')

    def test_bare_attributes(self):
        self.assertEqual(self.read('<p>Intro <img alt> after</p>'), 'Intro after')
        self.assertEqual(self.read('<ol start><li>one</li></ol>'), '1. one')


if __name__ == '__main__':
    unittest.main()
