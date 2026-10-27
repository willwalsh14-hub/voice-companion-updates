"""Core fixes found in the October 2026 audit: sleep and wake, the microphone switch,
the main menu, updates and the menus' names. Each test failed before its fix."""
import queue
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class FakeWindow:
    def __init__(self, *commands):
        self.commands = queue.Queue()
        for command in commands:
            self.commands.put(command)
        self.spoken = []
        self.closed = mock.Mock(is_set=mock.Mock(return_value=False))

    def feedback(self, text):
        self.spoken.append(text)

    def set_context(self, context):
        pass

    def show(self, text):
        pass


class MicrophoneKeyWhileAsleepTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=True, MICROPHONE_FAILED=False, MICROPHONE_ANNOUNCED=False,
                                SLEEP_RETURN_MODE='awake').items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(companion.PREFERENCES); p.start(); self.addCleanup(p.stop)

    def test_f9_pressed_while_asleep_says_how_to_wake_and_stays_asleep(self):
        # The key arrives the real way: through the keyboard path, which works on the
        # screen behind sleep. It used to say "Voice commands are on." while still asleep.
        self.assertEqual(companion.handle_keyboard('@microphone', 'sleep'), 'sleep')
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertIn('Say wake up, or press Control+F12', self.said[-1])
        self.assertEqual(companion.SLEEP_RETURN_MODE, 'awake')

    def test_typed_microphone_on_while_asleep_says_how_to_wake(self):
        self.assertEqual(companion.handle_keyboard('microphone on', 'sleep'), 'sleep')
        self.assertIn('Say wake up', self.said[-1])


if __name__ == '__main__':
    unittest.main()
