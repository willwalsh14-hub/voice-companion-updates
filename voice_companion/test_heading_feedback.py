"""Headings say their level; other formatting waits until asked (Will, October 30, 2026)."""
import tempfile
import unittest
from pathlib import Path

from document_editor import Paragraph, TextRun, VoiceDocument, heading_spans
from keyboard_text import caret_feedback, document_text, heading_notice, replace_keyboard_text


class HeadingFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.doc = VoiceDocument(Path(self.temp.name))
        self.doc.paragraphs = [Paragraph('Plans', kind='Heading 1'), Paragraph('We meet on Friday.', bold=True,
                               runs=[TextRun('We meet on Friday.', bold=True)]),
                               Paragraph('Food', kind='Heading 2'), Paragraph('', kind='Heading 3')]

    def test_reading_and_moving_by_voice_says_the_heading_level(self):
        self.doc.cursor = 0
        self.assertEqual(self.doc.process('read paragraph'), 'Heading level 1. Plans')
        self.assertEqual(self.doc.process('next paragraph'), 'We meet on Friday.')   # bold is not said
        self.assertEqual(self.doc.process('next heading'), 'Heading level 2. Food')
        self.assertEqual(self.doc.process('next heading'), 'Heading level 3. Empty heading.')
        self.assertEqual(self.doc.process('previous heading'), 'Heading level 2. Food')
        self.assertEqual(self.doc.process('where am i'), 'Paragraph 3 of 4. Heading level 2. Food')

    def test_setting_a_heading_says_its_level(self):
        self.doc.cursor = 1
        self.assertEqual(self.doc.process('heading 1'), 'Formatted the current paragraph. Heading level 1.')
        self.assertEqual(self.doc.process('heading level two'), 'Formatted the current paragraph. Heading level 2.')
        self.assertEqual(self.doc.process('normal text'), 'Formatted the current paragraph. Normal text.')

    def test_arrows_say_the_heading_level_and_nothing_else(self):
        text = document_text(self.doc)
        spans = heading_spans(self.doc.paragraphs)
        self.assertEqual(spans, [(0, 5, 1), (25, 29, 2), (30, 30, 3)])
        for caret, expected in ((0, 'Heading level 1. Plans'), (8, 'We meet on Friday.'), (27, 'Heading level 2. Food'),
                                (30, 'Heading level 3. Empty heading.')):
            line, _ = caret_feedback(text, caret, 'Down')
            self.assertEqual(heading_notice(spans, caret, line), expected)
        self.assertEqual(heading_notice([], 3, 'Plans'), 'Plans')

    def test_enter_at_the_end_of_a_heading_starts_normal_text(self):
        doc = VoiceDocument(Path(self.temp.name))
        doc.paragraphs = [Paragraph('Plans', kind='Heading 1')]
        replace_keyboard_text(doc, 'Plans', 'Plans\n', 6)
        self.assertEqual([p.kind for p in doc.paragraphs], ['Heading 1', 'Normal'])
        replace_keyboard_text(doc, 'Plans\n', 'Plans\nToday', 11)
        self.assertEqual([p.kind for p in doc.paragraphs], ['Heading 1', 'Normal'])
        # Enter inside a heading keeps both parts headings, as in Word.
        doc.paragraphs = [Paragraph('Big plans', kind='Heading 2')]
        replace_keyboard_text(doc, 'Big plans', 'Big\n plans', 4)
        self.assertEqual([p.kind for p in doc.paragraphs], ['Heading 2', 'Heading 2'])

    def test_the_window_gets_the_heading_places(self):
        import companion as app
        from unittest import mock
        with mock.patch.object(app, 'document', self.doc), mock.patch.object(app, 'email_draft', None):
            context = app.app_context('document')
        self.assertEqual(context['document_headings'], [(0, 5, 1), (25, 29, 2), (30, 30, 3)])


if __name__ == '__main__':
    unittest.main()
