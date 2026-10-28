"""Will's answers to the consumer test (October 2026), through the real app.

- "What's playing?" and F3 say what plays now, on every screen.
- A radio stream that stops after it started is reported once, in plain words.
- "Play WGN" finds and plays a station from screens where words are commands.
- Deleting a web favorite asks Yes or No first, with No chosen.
- A favorite saved from a page returns you to the page and your place.
- "Microphone off" while writing: text in Command and dictation mode and in
  Dictation only mode; the command in Commands only mode.
"""
import unittest
from unittest.mock import patch

import companion
from test_core_email_fixes import CompanionTestCase
from web_assistant import WebSession


class _Backend:
    """The browser behind a WebSession; these tests never load a page."""
    regular = False


def web_text_box(folder, input_mode):
    """A real WebSession editing a message box on a page. Typing into the page
    is recorded instead of sent to a browser."""
    session = WebSession(folder, _Backend())
    session.snapshot = {'url': 'https://example.com/contact', 'title': 'Contact us', 'elements': []}
    session.keyboard_field = {'tag': 'textarea', 'key': 'message', 'label': 'Message', 'value': ''}
    session.input_mode = input_mode
    session.typed = []
    session.dictate_to_focus = lambda text, literal=False: (session.typed.append(text), 'Typed.')[1]
    return session


class MicrophoneOffWhileWritingTests(CompanionTestCase):
    """Will: in Command and dictation mode, "Microphone off" in a writing field is
    typed, and F9 turns the microphone off. In Dictation only mode it is dictated
    as said. In Commands only mode it turns the microphone off."""

    def setUp(self):
        super().setUp()
        for name, value in (('MICROPHONE_OFF', False), ('MICROPHONE_ANNOUNCED', False), ('note_buffer', []), ('web_session', None)):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(companion.PREFERENCES); p.start(); self.addCleanup(p.stop)

    def written(self, screen, input_mode):
        """Say "Microphone off." on a writing screen; return the text written there."""
        companion.INPUT_MODE = input_mode
        companion.MICROPHONE_OFF = False
        if screen == 'document':
            self.open_document().dictating = input_mode != 'commands'
        elif screen == 'email_draft':
            self.draft().dictating = input_mode != 'commands'
        elif screen == 'note':
            companion.note_buffer.clear()
        elif screen == 'web':
            companion.web_session = web_text_box(self.folder, input_mode)
        self.assertEqual(companion.handle('Microphone off.', screen), screen)
        if screen == 'document':
            return companion.document_text(companion.document)
        if screen == 'email_draft':
            return companion.document_text(companion.email_draft)
        if screen == 'note':
            return ' '.join(companion.note_buffer)
        return ' '.join(companion.web_session.typed)

    def test_the_words_are_written_in_command_and_dictation_mode_and_dictation_only(self):
        for input_mode in ('mixed', 'dictation'):
            for screen in ('document', 'email_draft', 'note', 'web'):
                with self.subTest(input_mode=input_mode, screen=screen):
                    self.assertIn('Microphone off', self.written(screen, input_mode))
                    self.assertFalse(companion.MICROPHONE_OFF)

    def test_commands_only_turns_the_microphone_off_in_every_writing_field(self):
        for screen in ('document', 'email_draft', 'note', 'web'):
            with self.subTest(screen=screen):
                self.said.clear()
                self.assertNotIn('Microphone off', self.written(screen, 'commands'))
                self.assertTrue(companion.MICROPHONE_OFF)
                self.assertEqual(self.said[-1], 'Microphone off. Voice Companion is not listening at all. Press F9 to turn it back on.')

    def test_f9_still_turns_it_off_while_writing(self):
        for screen in ('document', 'email_draft', 'note', 'web'):
            with self.subTest(screen=screen):
                companion.MICROPHONE_OFF = False
                self.assertEqual(companion.navigation_key('F9', screen), '@microphone')
                companion.handle('@microphone', screen, typed=True)
                self.assertTrue(companion.MICROPHONE_OFF)


if __name__ == '__main__':
    unittest.main()
