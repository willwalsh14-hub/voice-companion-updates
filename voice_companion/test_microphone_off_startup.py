"""Starting with the microphone off (Will, October 2026).

0.2.166 started asleep, saying "Say wake up", with no microphone to hear it, and the
keyboard could be left in Thunderbird, which Companion starts for mail. Now it starts
on the main menu, takes the keyboard, and a stall writes freeze-report.txt."""
import queue
import time
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class MicrophoneOffStartupTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=True, STARTUP_UPDATE_PENDING=False, SLEEP_RETURN_MODE='sleep').items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def test_ready_message_says_how_to_use_keys_and_turn_the_microphone_on(self):
        prompt = companion.startup_prompt()
        self.assertNotIn('Say wake up', prompt)
        self.assertIn('Press F9 to turn it on', prompt)
        self.assertIn('arrow keys, Enter and Escape', prompt)
        companion.MICROPHONE_OFF = False
        self.assertIn('Say wake up', companion.startup_prompt())

    def test_answering_no_to_the_startup_update_goes_to_the_main_menu(self):
        service = mock.Mock(); service.check.return_value = True; service.events = queue.Queue()
        service.events.put(('available', {'version': '0.2.168-test'}))
        with mock.patch.object(companion, 'UPDATES', service), mock.patch.object(companion, 'PREFERENCES', {'check_updates': True}):
            self.assertEqual(companion.startup_update_mode(), 'update_offer')
            self.assertEqual(companion.handle('no', 'update_offer'), 'awake')
        self.assertEqual(companion.SLEEP_RETURN_MODE, 'awake')
        # One message: a second one would stop the first (test_startup_announcement.py).
        self.assertTrue(self.said[-1].startswith(companion.MICROPHONE_STILL_OFF), self.said)
        self.assertTrue(self.said[-1].endswith(companion.MAIN_MENU_PROMPT), self.said)

    def test_a_stalled_main_loop_writes_a_freeze_report_once(self):
        with mock.patch.dict(companion.FREEZE_WATCH, {'beat': time.monotonic() - 100, 'reported': False}):
            companion.start_freeze_watch(limit=1, interval=.05)
            report = self.folder / 'freeze-report.txt'
            end = time.monotonic() + 5
            while not report.exists() and time.monotonic() < end: time.sleep(.05)
            time.sleep(.2)
            text = report.read_text(encoding='utf-8')
            self.assertIn('stopped responding', text)
            self.assertIn('File', text)   # the stacks of every thread follow
            report.unlink(); time.sleep(.2)
            self.assertFalse(report.exists())     # once per freeze, not every few seconds
            companion.FREEZE_WATCH['beat'] = time.monotonic() + 3600   # the loop is moving again: stop watching
            time.sleep(.2)


if __name__ == '__main__':
    unittest.main()
