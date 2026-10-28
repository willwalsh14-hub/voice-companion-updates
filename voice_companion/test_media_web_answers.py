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


class WebFavoriteTests(unittest.TestCase):
    """Deleting asks Yes or No first, with No chosen, like cancelling a download.
    Saving from a page goes back to the page and your place on it."""

    def setUp(self):
        import tempfile
        from test_web_assistant import FakeBackend
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.session = WebSession(temp.name, FakeBackend())
        self.session.auto_read = False
        self.said = []
        for name, value in (('web_session', self.session), ('APP_WINDOW', None), ('pending_website', None),
                            ('PREFERENCES', {'web_keep_open': False}), ('CONFIRM_CHOICE', 'yes'), ('NAMED_LIST', None),
                            ('MAIN_MENU_LEAD', ''), ('TEXT_MODE', True), ('INPUT_MODE', 'mixed')):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        for name in ('speak', 'speak_prompt', 'speak_status'):
            p = patch.object(companion, name, lambda text, *a, **k: self.said.append(str(text))); p.start(); self.addCleanup(p.stop)
        self.session.favorites.add('News', 'https://example.com/news')
        self.session.favorites.add('Weather', 'https://example.com/weather')

    def key(self, key, mode):
        """Press a key the way the window sends it, and handle what it asks for."""
        request = companion.navigation_key(key, mode)
        return companion.handle(request, mode, typed=True) if request else mode

    def ask_to_delete_news(self):
        mode = companion.handle('Delete favorite.', 'browser_menu')
        self.assertEqual(mode, 'browser_menu')
        self.said.clear()
        return companion.handle('News.', mode)

    def test_delete_asks_first_and_no_is_chosen(self):
        mode = self.ask_to_delete_news()
        self.assertEqual(self.said[-1], 'Delete favorite News? Say Yes or No. Escape keeps it.')
        self.assertEqual(companion.CONFIRM_CHOICE, 'no')
        self.assertEqual(companion.app_context(mode)['confirmation'], 'no')
        self.assertIsNotNone(self.session.favorites.get('News'))
        # Enter answers with the chosen No; the favorite stays.
        self.assertEqual(self.key('Enter', mode), 'browser_menu')
        self.assertTrue(self.said[-1].startswith('Not deleted. News'), self.said[-1])
        self.assertIsNotNone(self.session.favorites.get('News'))

    def test_up_arrow_then_enter_deletes(self):
        mode = self.ask_to_delete_news()
        self.key('Up', mode)
        self.assertEqual(self.said[-1], 'Yes.')
        self.key('Enter', mode)
        self.assertTrue(self.said[-1].startswith('Deleted favorite News. Weather'), self.said[-1])
        self.assertIsNone(self.session.favorites.get('News'))

    def test_spoken_and_typed_answers(self):
        for answer, kept in (('Yes, please.', False), ('No.', True), ('Escape', True)):
            with self.subTest(answer=answer):
                self.session.favorites.add('News', 'https://example.com/news') if self.session.favorites.get('News') is None else None
                mode = self.ask_to_delete_news()
                companion.handle(answer, mode)
                self.assertEqual(self.session.favorites.get('News') is not None, kept)
        for key, kept in (('Escape', True), ('Letter:n', True), ('Letter:y', False)):
            with self.subTest(key=key):
                self.session.favorites.add('News', 'https://example.com/news') if self.session.favorites.get('News') is None else None
                mode = self.ask_to_delete_news()
                self.key(key, mode)
                self.assertEqual(self.session.favorites.get('News') is not None, kept)

    def test_other_words_ask_again(self):
        mode = self.ask_to_delete_news()
        companion.handle('Weather', mode)
        self.assertEqual(self.said[-1], 'Delete favorite News? Say Yes or No. Escape keeps it.')
        self.assertIsNotNone(self.session.favorites.get('News'))
        self.assertIsNotNone(self.session.favorites.get('Weather'))

    def test_new_favorite_from_a_page_returns_to_the_page_and_place(self):
        self.session.open('https://example.com/story')
        self.session.reading.position = 42
        mode = companion.handle('New favorite.', 'web')
        self.assertEqual(mode, 'browser_menu')
        self.assertEqual(self.said[-1], 'New favorite. Say or type the favorite name, then press Enter. Escape cancels.')
        self.assertEqual(companion.handle('Story.', mode), 'web')
        self.assertEqual(self.said[-1], 'Saved favorite Story. Back to web page.')
        self.assertEqual(self.session.favorites.get('Story')['url'], 'https://example.com/story')
        self.assertEqual(self.session.reading.position, 42)
        # The Alt menu's Add favorite does the same.
        self.assertEqual(companion.handle('add favorite', 'web'), 'browser_menu')
        self.assertEqual(companion.handle('Story two', 'browser_menu'), 'web')

    def test_escape_while_naming_from_a_page_goes_back_to_the_page(self):
        self.session.open('https://example.com/story')
        mode = companion.handle('New favorite', 'web')
        self.assertEqual(self.key('Escape', mode), 'web')
        self.assertEqual(self.said[-1], 'Not saved. Back to web page.')
        self.assertIsNone(self.session.favorites.get('Story'))

    def test_new_favorite_from_the_browser_menu_stays_there(self):
        self.session.open('https://example.com/story')
        self.assertEqual(companion.handle('New favorite', 'browser_menu'), 'browser_menu')
        self.assertEqual(companion.handle('Story', 'browser_menu'), 'browser_menu')
        self.assertEqual(self.said[-1], 'Saved favorite Story. Web browser menu.')

    def test_save_favorite_as_on_a_page_stays_on_the_page(self):
        self.session.open('https://example.com/story')
        self.assertEqual(companion.handle('Save favorite as Story.', 'web'), 'web')
        self.assertEqual(self.said[-1], 'Saved favorite Story.')


if __name__ == '__main__':
    unittest.main()
