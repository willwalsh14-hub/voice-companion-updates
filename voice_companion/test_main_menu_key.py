"""Control+F1 goes to the main menu from every screen (Will, October 2026). Print Screen stays
with Windows for screenshots.

It is the same as saying Main menu, so a document still asks to save first."""
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class MainMenuKeyTests(CompanionTestCase):
    def test_control_f1_asks_for_the_main_menu_on_every_screen(self):
        for mode in ('awake', 'document', 'email_draft', 'note', 'mailbox', 'web', 'files', 'local_media',
                     'media_menu', 'word_processor', 'file_reader', 'settings', 'help', 'media', 'tutorial'):
            with self.subTest(mode=mode):
                self.assertEqual(companion.navigation_key('Ctrl+F1', mode), '@main_menu')

    def test_it_reaches_the_main_menu_and_says_so_once(self):
        for mode in ('awake', 'note', 'files', 'local_media', 'media_menu', 'word_processor', 'settings', 'help'):
            with self.subTest(mode=mode):
                self.said.clear()
                self.assertEqual(companion.handle('@main_menu', mode, typed=True), 'awake')
                # Once, alone or after what just happened ("Note saved. Main menu..."), in one message.
                self.assertEqual(sum(said.count(companion.MAIN_MENU_PROMPT) for said in self.said), 1, self.said)

    def test_a_document_asks_to_save_first(self):
        document = self.open_document()
        companion.INPUT_MODE = 'dictation'
        companion.handle('Dinner is at six.', 'document')
        self.assertEqual(companion.handle('@main_menu', 'document', typed=True), 'document_save')
        self.assertIn('Dinner is at six.', companion.document_text(document))

    def test_it_works_in_an_open_alt_menu(self):
        mode = companion.handle(companion.navigation_key('Alt', 'note'), 'note')
        self.assertTrue(companion.screen_menu().is_open('note'))
        self.assertEqual(companion.handle(companion.navigation_key('Ctrl+F1', mode), mode, typed=True), 'awake')
        self.assertFalse(companion.screen_menu().is_open('awake'))

    def test_print_screen_is_left_for_screenshots(self):
        self.assertNotEqual(companion.navigation_key('Print', 'awake'), '@main_menu')

    def test_main_menu_keys_use_one_modifier(self):
        import input_parity
        self.assertEqual({key: input_parity.screen_command(key, 'awake') for key in ('Ctrl+V', 'Ctrl+M', 'Ctrl+G', 'Ctrl+T')},
                         {'Ctrl+V': 'what version', 'Ctrl+M': 'what mode', 'Ctrl+G': 'open user guides', 'Ctrl+T': 'start tutorial'})
        # The old keys still work, and Control+V still pastes into a typed command.
        self.assertEqual(input_parity.screen_command('Ctrl+Shift+V', 'awake'), 'what version')
        self.assertEqual(input_parity.screen_key('awake', 'v', 4, {}), 'Ctrl+V')
        self.assertIsNone(input_parity.screen_key('awake', 'v', 4, {}, typed_text='open'))

    def test_it_cancels_account_setup(self):
        with mock.patch.object(companion, 'ACCOUNT_ENTRY', {'step': 'email'}):
            self.assertEqual(companion.navigation_key('Ctrl+F1', 'awake'), '@main_menu')


if __name__ == '__main__':
    unittest.main()
