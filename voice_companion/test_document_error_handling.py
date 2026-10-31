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


if __name__ == '__main__':
    unittest.main()
