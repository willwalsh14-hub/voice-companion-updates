"""One startup announcement on every startup path (Will, October 2026).

"I need startup prompts to be spoken consistently regardless of microphone state and
whether an update has just completed. They are not."

The keyboard voice stops what it is saying when new speech starts. Startup used to
speak the ready message, then the update result, then the microphone, each as its own
message, so each one cut off the one before. Now startup says ONE message, always in
this order: version and ready, update result, one-time notices, the microphone and how
to use voice or keys, then where you are (the main menu, or a question)."""
import json
import queue
import unittest
from unittest import mock

import companion
import startup_speech
from test_core_email_fixes import CompanionTestCase


class FakeUpdates:
    def __init__(self, event=None):
        self.events = queue.Queue()
        self.checked = False
        self.release = None
        if event:
            self.events.put(event)

    def check(self, manual=False):
        self.checked = True
        return not self.events.empty()

    def can_install(self):
        return False


class StartupAnnouncementTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(MICROPHONE_OFF=False, MICROPHONE_FAILED=False, STARTUP_UPDATE_PENDING=False,
                                STARTUP_QUESTION=None, STARTUP_NOTICES=[], MAIN_MENU_LEAD='',
                                UPDATE_RETURN_MODE='awake', PREFERENCES={'check_updates': True},
                                UPDATES=FakeUpdates()).items():
            p = mock.patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion.sys, 'argv', ['companion']); p.start(); self.addCleanup(p.stop)

    def start(self, microphone='on', update=None, result=None):
        companion.MICROPHONE_OFF = microphone == 'off'
        companion.MICROPHONE_FAILED = microphone == 'failed'
        if update:
            companion.UPDATES = FakeUpdates(('available', {'version': '9.9.9-test'}))
        if result is not None:
            (self.folder / 'update-result.json').write_text(json.dumps(result), encoding='utf-8')
        return companion.announce_startup()

    def assert_in_order(self, text, *pieces):
        positions = [text.index(piece) for piece in pieces]
        self.assertEqual(positions, sorted(positions), text)

    # Normal start, microphone off, microphone that failed to open.

    def test_normal_start_is_one_message_that_says_wake_up(self):
        self.assertEqual(self.start(), 'sleep')
        self.assertEqual(len(self.said), 1, self.said)
        text = self.said[0]
        self.assertTrue(text.startswith('Voice Companion ' + companion.APP_VERSION + ' is ready.'))
        self.assert_in_order(text, 'is ready.', 'The microphone is on.', 'Say wake up', 'You are on the main menu.')
        self.assertTrue(text.endswith('You are on the main menu.'))

    def test_microphone_off_never_says_wake_up_and_starts_on_the_main_menu(self):
        self.assertEqual(self.start('off'), 'awake')
        self.assertEqual(companion.SLEEP_RETURN_MODE, 'awake')
        self.assertEqual(len(self.said), 1, self.said)
        text = self.said[0]
        self.assertNotIn('wake up', text.lower())
        self.assert_in_order(text, 'is ready.', companion.MICROPHONE_STILL_OFF, 'arrow keys', 'You are on the main menu.')

    def test_microphone_that_failed_to_open_never_says_wake_up(self):
        self.assertEqual(self.start('failed'), 'awake')
        self.assertEqual(len(self.said), 1, self.said)
        text = self.said[0]
        self.assertNotIn('wake up', text.lower())
        self.assert_in_order(text, 'is ready.', 'could not start', 'arrow keys', 'You are on the main menu.')

    # After an update.

    def test_update_result_comes_after_the_version_and_before_the_microphone(self):
        for microphone, mode in (('on', 'sleep'), ('off', 'awake'), ('failed', 'awake')):
            self.said.clear()
            self.assertEqual(self.start(microphone, result={'succeeded': True}), mode)
            self.assertEqual(len(self.said), 1, (microphone, self.said))
            self.assert_in_order(self.said[0], 'is ready.', 'was updated successfully.',
                                 startup_speech.MICROPHONE[microphone], 'You are on the main menu.')
            self.assertFalse((self.folder / 'update-result.json').exists())

    def test_failed_and_restart_results_are_said(self):
        self.start(result={'succeeded': False})
        self.assertIn('The update did not finish.', self.said[-1])
        self.start(result={'succeeded': True, 'restart_required': True})
        self.assertIn('needs a Windows restart', self.said[-1])
        (self.folder / 'update-result.json').write_text('not json', encoding='utf-8')
        companion.announce_startup()
        self.assertTrue(self.said[-1].startswith('Voice Companion'))
        self.assertFalse((self.folder / 'update-result.json').exists())

    def test_resume_after_update_does_not_check_for_another_update(self):
        companion.UPDATES = FakeUpdates(('available', {'version': '9.9.9-test'}))
        with mock.patch.object(companion.sys, 'argv', ['companion', '--resume-after-update']):
            self.assertEqual(companion.announce_startup(), 'sleep')
        self.assertFalse(companion.UPDATES.checked)
        self.assertEqual(len(self.said), 1)

    # One-time notices.

    def test_notices_come_after_the_update_result_and_before_the_microphone(self):
        companion.startup_notice('The user guide files could not be copied to Documents.')
        with mock.patch('mail_accounts.retire_removed_accounts',
                        return_value=mock.Mock(removed=[('gmail', 'will@gmail.com')])), \
                mock.patch('account_picker.retired_message', return_value='Your Gmail account was removed.'):
            self.start('off', result={'succeeded': True})
        self.assertEqual(len(self.said), 1, self.said)
        self.assert_in_order(self.said[0], 'is ready.', 'updated successfully.', 'user guide files',
                             'Gmail account was removed.', companion.MICROPHONE_STILL_OFF, 'You are on the main menu.')
        # After startup, a notice is spoken at once.
        companion.startup_notice('Later notice.')
        self.assertEqual(self.said[-1], 'Later notice.')

    # An update offered at startup.

    def test_update_offer_is_asked_last_after_the_version_and_microphone(self):
        for microphone in ('on', 'off', 'failed'):
            self.said.clear()
            self.assertEqual(self.start(microphone, update=True), 'update_offer')
            self.assertEqual(len(self.said), 1, self.said)
            text = self.said[0]
            self.assert_in_order(text, 'is ready.', startup_speech.MICROPHONE[microphone], 'A new update is available.')
            self.assertNotIn('wake up', text.lower())
            # Nobody can say yes or no with the microphone off.
            self.assertEqual('say yes or no' in text, microphone == 'on', (microphone, text))

    def test_no_to_the_startup_update_with_the_microphone_on_goes_to_sleep_with_one_message(self):
        self.start('on', update=True)
        self.said.clear()
        self.assertEqual(companion.handle('no', 'update_offer'), 'sleep')
        self.assertEqual(self.said, [startup_speech.followup('on')])

    def test_no_to_the_startup_update_with_the_microphone_off_says_it_with_the_main_menu(self):
        for microphone in ('off', 'failed'):
            self.start(microphone, update=True)
            self.said.clear()
            self.assertEqual(companion.handle('no', 'update_offer'), 'awake')
            self.assertEqual(companion.SLEEP_RETURN_MODE, 'awake')
            # One message: the second one would stop the first.
            self.assertEqual(len(self.said), 1, self.said)
            self.assert_in_order(self.said[0], startup_speech.MICROPHONE[microphone], 'Main menu.')
            self.assertNotIn('wake up', self.said[0].lower())

    def test_a_later_update_answer_is_said_together_with_the_main_menu(self):
        companion.UPDATE_RETURN_MODE = 'awake'
        self.assertEqual(companion.handle('no', 'update_offer'), 'awake')
        self.assertEqual(len(self.said), 1, self.said)
        self.assertTrue(self.said[0].startswith('Okay. You can keep using this version. Main menu.'), self.said)

    # Chrome setup asked for by the installer.

    def test_chrome_setup_question_is_said_last_in_the_same_message(self):
        def request(app, mode):
            app.speak('Chrome is not installed. Install it now? Press Y or N.')
            return 'chrome_setup'
        for microphone in ('on', 'off'):
            self.said.clear()
            with mock.patch('chrome_setup.startup_request', request):
                self.assertEqual(self.start(microphone), 'chrome_setup')
            self.assertEqual(len(self.said), 1, self.said)
            self.assert_in_order(self.said[0], 'is ready.', startup_speech.MICROPHONE[microphone], 'Install it now?')
            self.assertNotIn('wake up', self.said[0].lower())

    # Heard again after an early key press stopped the announcement.

    def test_what_version_repeats_the_version_and_the_microphone(self):
        for microphone in ('on', 'off', 'failed'):
            companion.MICROPHONE_OFF = microphone == 'off'
            companion.MICROPHONE_FAILED = microphone == 'failed'
            companion.handle('what version', 'awake')
            self.assertEqual(self.said[-1], 'Voice Companion version ' + companion.APP_VERSION + '. '
                             + startup_speech.MICROPHONE[microphone])


class StartupAnnouncementWordingTests(unittest.TestCase):
    def test_every_state_and_result_has_words(self):
        for state in ('on', 'off', 'failed'):
            text = startup_speech.announcement('1.0', state)
            self.assertEqual('wake up' in text.lower(), state == 'on')
        for result in ('succeeded', 'restart', 'failed'):
            self.assertIn(startup_speech.UPDATE_RESULTS[result], startup_speech.announcement('1.0', result=result))

    def test_a_notice_is_said_once(self):
        text = startup_speech.announcement('1.0', notices=['Same notice.', 'Same notice.', '', None])
        self.assertEqual(text.count('Same notice.'), 1)


if __name__ == '__main__':
    unittest.main()
