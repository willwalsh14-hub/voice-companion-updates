"""Print Screen goes to the main menu from every screen (Will, October 2026).

It is the same as saying Main menu, so a document still asks to save first."""
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class PrintScreenTests(CompanionTestCase):
    def test_print_screen_asks_for_the_main_menu_on_every_screen(self):
        for mode in ('awake', 'document', 'email_draft', 'note', 'mailbox', 'web', 'files', 'local_media',
                     'media_menu', 'word_processor', 'file_reader', 'settings', 'help', 'media', 'tutorial'):
            with self.subTest(mode=mode):
                self.assertEqual(companion.navigation_key('Print', mode), '@main_menu')

    def test_it_reaches_the_main_menu_and_says_so_once(self):
        for mode in ('awake', 'note', 'files', 'local_media', 'media_menu', 'word_processor', 'settings', 'help'):
            with self.subTest(mode=mode):
                self.said.clear()
                self.assertEqual(companion.handle('@main_menu', mode, typed=True), 'awake')
                self.assertEqual(self.said.count(companion.MAIN_MENU_PROMPT), 1, self.said)

    def test_a_document_asks_to_save_first(self):
        document = self.open_document()
        companion.INPUT_MODE = 'dictation'
        companion.handle('Dinner is at six.', 'document')
        self.assertEqual(companion.handle('@main_menu', 'document', typed=True), 'document_save')
        self.assertIn('Dinner is at six.', companion.document_text(document))

    def test_it_works_in_an_open_alt_menu(self):
        mode = companion.handle(companion.navigation_key('Alt', 'note'), 'note')
        self.assertTrue(companion.screen_menu().is_open('note'))
        self.assertEqual(companion.handle(companion.navigation_key('Print', mode), mode, typed=True), 'awake')
        self.assertFalse(companion.screen_menu().is_open('awake'))

    def test_it_cancels_account_setup(self):
        with mock.patch.object(companion, 'ACCOUNT_ENTRY', {'step': 'email'}):
            self.assertEqual(companion.navigation_key('Print', 'awake'), '@main_menu')


if __name__ == '__main__':
    unittest.main()
