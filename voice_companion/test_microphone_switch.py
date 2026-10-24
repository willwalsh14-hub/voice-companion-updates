"""F9 turns the microphone fully off and on (Will, October 2026).

Control+F12 only puts listening to sleep; the microphone still hears "wake up". F9 closes
it, so nothing listens, and opens it again. It works on every screen, asleep or awake."""
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class MicrophoneSwitchTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(companion, 'MICROPHONE_OFF', False); patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.object(companion, 'MICROPHONE_ANNOUNCED', False); patcher.start(); self.addCleanup(patcher.stop)
        # F9 saves its choice in the preferences; keep that inside this test.
        patcher = mock.patch.dict(companion.PREFERENCES); patcher.start(); self.addCleanup(patcher.stop)

    def test_f9_turns_the_microphone_off_and_on_on_any_screen(self):
        for mode in ('awake', 'document', 'mailbox', 'web', 'local_media'):
            with self.subTest(mode=mode):
                self.assertEqual(companion.navigation_key('F9', mode), '@microphone')
        self.assertEqual(companion.handle('@microphone', 'awake', typed=True), 'awake')
        self.assertTrue(companion.MICROPHONE_OFF)
        self.assertEqual(self.said[-1], 'Microphone off. Voice Companion is not listening at all. Press F9 to turn it back on.')
        companion.handle('@microphone', 'awake', typed=True)
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertEqual(self.said[-1], 'Microphone on. Voice commands are on.')

    def test_microphone_off_is_remembered_after_a_restart(self):
        import json
        companion.handle('@microphone', 'awake', typed=True)
        saved = json.loads((self.folder / 'preferences.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['microphone_off'])
        companion.handle('@microphone', 'awake', typed=True)
        saved = json.loads((self.folder / 'preferences.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['microphone_off'])
        self.assertIn('Press F9 to turn it on', companion.MICROPHONE_STILL_OFF)

    def test_f9_works_while_asleep_and_says_how_to_wake(self):
        companion.MICROPHONE_OFF = True
        self.assertEqual(companion.handle('@microphone', 'sleep', typed=True), 'sleep')
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertIn('Say wake up, or press Control+F12', self.said[-1])

    def test_spoken_off_works_but_writing_keeps_the_words(self):
        self.assertEqual(companion.handle('Turn off the microphone.', 'awake'), 'awake')
        self.assertTrue(companion.MICROPHONE_OFF)
        companion.MICROPHONE_OFF = False
        # Dictating in a document, the same words are text, not a command.
        self.open_document()
        companion.INPUT_MODE = 'dictation'
        companion.handle('Turn off the microphone.', 'document')
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertIn('Turn off the microphone', companion.document_text(companion.document))


if __name__ == '__main__':
    unittest.main()
