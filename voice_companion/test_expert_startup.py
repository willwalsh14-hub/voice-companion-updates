"""Startup, sleep and wake, Help and the transcript, as an expert screen reader user uses them.

An expert expects every message to be true for the state the app is in, short, and worded
the same way on every screen (sweep, October 30, 2026)."""
import queue
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

import companion
from test_core_email_fixes import CompanionTestCase
from test_startup_announcement import FakeUpdates


class FakeWindow:
    """Just enough of the app window for process_keyboard_batch."""

    def __init__(self):
        self.commands = queue.Queue()
        self.closed = MagicMock()
        self.closed.is_set.return_value = False

    def feedback(self, text):
        pass

    def __getattr__(self, name):
        return MagicMock()


class KeyTestCase(CompanionTestCase):
    def setUp(self):
        super().setUp()
        self.window = FakeWindow()
        for name, value in dict(APP_WINDOW=self.window, MICROPHONE_OFF=False, MICROPHONE_FAILED=False,
                                MAIN_MENU_INDEX=None, POWER_ACTION=None, CONFIRM_CHOICE='no').items():
            p = patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)

    def keys(self, mode, *names):
        for name in names:
            self.said.clear()
            self.window.commands.put(('keyboard', name))
            mode = companion.process_keyboard_batch(mode)
        return mode


class WakeDuringExitOrPowerQuestionTests(KeyTestCase):
    """Keys work in the screen behind sleep. Escape on the main menu while asleep asks
    "Exit Voice Companion?". Control+F12 then woke with "I am listening. Back to exit
    confirm.", an internal name, and the question was never asked again. The same for
    Shut down the computer: "Back to power confirm."."""

    def test_waking_at_the_exit_question_asks_it_again(self):
        mode = self.keys('awake', 'Ctrl+F12', 'Escape')
        self.assertEqual(mode, 'sleep')
        self.assertEqual(companion.SLEEP_RETURN_MODE, 'exit_confirm')
        mode = self.keys(mode, 'Ctrl+F12')
        self.assertEqual(mode, 'exit_confirm')
        self.assertEqual(self.said, ['I am listening. Exit Voice Companion? Yes or no.'])

    def test_waking_at_the_shut_down_question_names_the_action(self):
        for action, words, question in (('shutdown', 'shut down computer', 'Shut down the computer? Yes or no.'),
                                        ('restart', 'restart computer', 'Restart the computer? Yes or no.')):
            with self.subTest(action=action):
                self.assertEqual(companion.handle(words, 'awake'), 'power_confirm')
                self.assertEqual(companion.handle('Go to sleep.', 'power_confirm'), 'power_confirm')
                # Asleep on the main menu, the power choice is opened with the keys.
                companion.POWER_ACTION = action; companion.SLEEP_RETURN_MODE = 'power_confirm'
                self.said.clear()
                self.assertEqual(companion.resume_from_sleep(), 'power_confirm')
                self.assertEqual(self.said, ['I am listening. ' + question])
                companion.handle('no', 'power_confirm')


class TranscriptStartupNoticeTests(CompanionTestCase):
    """With the transcript on, startup said "Say stop transcript to turn it off." But the app
    starts asleep, when only "Wake up" is heard, and with the microphone off nothing is heard."""

    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=False, MICROPHONE_FAILED=False, STARTUP_UPDATE_PENDING=False,
                                STARTUP_QUESTION=None, STARTUP_NOTICES=[], MAIN_MENU_LEAD='',
                                UPDATE_RETURN_MODE='awake', PREFERENCES={'check_updates': True},
                                UPDATES=FakeUpdates()).items():
            p = mock.patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion.sys, 'argv', ['companion']); p.start(); self.addCleanup(p.stop)

    def start(self, microphone='on'):
        companion.MICROPHONE_OFF = microphone == 'off'
        companion.MICROPHONE_FAILED = microphone == 'failed'
        return companion.announce_startup()

    def test_the_notice_says_how_to_turn_it_off_in_the_state_the_app_starts_in(self):
        transcript = MagicMock()
        with patch.object(companion, 'TRANSCRIPT', transcript):
            companion.PREFERENCES['speech_transcript'] = True
            self.assertEqual(self.start(), 'sleep')
            self.assertIn('The speech transcript is on. To turn it off, wake Companion, then say stop transcript.',
                          self.said[-1])
            for microphone in ('off', 'failed'):
                with self.subTest(microphone=microphone):
                    self.said.clear(); companion.STARTUP_NOTICES = []
                    self.assertEqual(self.start(microphone), 'awake')
                    self.assertNotIn('say stop transcript', self.said[-1].lower())
                    self.assertIn('The speech transcript is on. To turn it off, press Alt+O, then choose '
                                  'Startup and exit options.', self.said[-1])

if __name__ == '__main__':
    unittest.main()
