"""The window's hint line fits where the user is (Will, October 2026)."""
import unittest
from unittest.mock import patch

import companion
import display_screen


def hint(mode, **state):
    with patch.multiple(companion, **{'MICROPHONE_OFF': False, 'MICROPHONE_FAILED': False, **state}):
        return display_screen.context(companion, mode, companion.app_context(mode))['display_hint']


class HintLineTests(unittest.TestCase):
    def test_asleep_says_how_to_wake_up(self):
        self.assertEqual(hint('sleep'), 'Asleep. Say Wake up, or press Control+F12.')
        self.assertEqual(hint('sleep', MICROPHONE_OFF=True), 'Microphone off. Press Control+F12 to wake up.')

    def test_microphone_off_or_broken_says_use_the_keys(self):
        self.assertEqual(hint('mailbox', MICROPHONE_OFF=True), 'Microphone off. Use the keys. F1 for help.')
        self.assertEqual(hint('awake', MICROPHONE_FAILED=True), 'Microphone not working. Use the keys. F1 for help.')

    def test_each_screen_names_itself_f1_and_its_main_keys(self):
        self.assertEqual(hint('awake'), 'Main menu. F1 for help. Arrows move. Enter opens.')
        self.assertEqual(hint('mailbox'), 'Email. F1 for help. Enter opens. R replies. N writes new.')
        self.assertEqual(hint('help'), 'Help. Arrows move. Escape goes back.')
        self.assertEqual(hint('tutorial'), 'Tutorial. F1 lists commands.')
        self.assertTrue(hint('word_processor').endswith('Arrows move. Enter chooses.'))

    def test_a_question_says_how_to_answer(self):
        self.assertEqual(hint('document_save'), 'Save the document. Arrows choose. Enter answers.')

    def test_every_hint_is_short_enough_for_large_text(self):
        modes = ['sleep'] + list(__import__('display_options').SCREEN_NAMES)
        for mode in modes:
            for state in ({}, {'MICROPHONE_OFF': True}):
                with self.subTest(mode=mode, state=state):
                    text = hint(mode, **state)
                    self.assertTrue(text)
                    self.assertLessEqual(len(text), 75, text)
                    self.assertNotIn('Wake up to begin', text)


if __name__ == '__main__':
    unittest.main()
