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


class SleepWithTheMicrophoneOffTests(KeyTestCase):
    """With the microphone off (F9), Control+F12 said "Going to sleep. Say wake up when you
    need me." Nothing can hear "Wake up" then. Waking with Control+F12 said "I am listening.",
    which was not true. The tutorial also said its own words: "Say wake up to continue here."."""

    def test_sleep_and_wake_say_only_what_is_true_with_the_microphone_off(self):
        companion.MICROPHONE_OFF = True
        companion.SLEEP_RETURN_MODE = 'awake'
        self.open_document()
        mode = self.keys('document', 'Ctrl+F12')
        self.assertEqual(mode, 'sleep')
        self.assertEqual(self.said, ['Going to sleep. The microphone is off, so press Control+F12 to wake up.'])
        mode = self.keys(mode, 'Ctrl+F12')
        self.assertEqual(mode, 'document')
        self.assertEqual(self.said, ['Back to document.'])

    def test_with_the_microphone_on_the_words_are_the_same_on_every_screen(self):
        for screen in ('awake', 'tutorial'):
            with self.subTest(screen=screen):
                with patch.object(companion, 'tutorial_session', None):
                    self.assertEqual(companion.handle('Go to sleep.', screen), 'sleep')
                self.assertEqual(self.said[-1], 'Going to sleep. Say wake up when you need me.')
        companion.MICROPHONE_OFF = True
        self.assertEqual(companion.handle('go to sleep', 'tutorial'), 'sleep')
        self.assertEqual(self.said[-1], 'Going to sleep. The microphone is off, so press Control+F12 to wake up.')
        companion.MICROPHONE_OFF = False; companion.MICROPHONE_FAILED = True
        self.assertEqual(companion.handle('go to sleep', 'awake'), 'sleep')
        self.assertEqual(self.said[-1], 'Going to sleep. The microphone could not start, so press Control+F12 to wake up.')


class TutorialListWordsTests(KeyTestCase):
    """In the tutorial's lists, Down and Up said "File manager. 12 of 12. Press Enter or say
    that one to open. " (the hint every time, and a space at the end), while Home and End said
    "File manager (F), 12 of 12." The opening message named 10 of the 12 courses: Contacts and
    calendar, and YouTube, were left out."""

    def test_arrows_say_items_like_home_end_and_the_main_menu(self):
        with patch.object(companion, 'tutorial_session', None):
            mode = self.keys('awake', 'Ctrl+T')
            self.assertEqual(mode, 'tutorial')
            self.keys(mode, 'Down')
            self.assertEqual(self.said, ['Getting started (G), 1 of 12.'])
            self.keys(mode, 'Down')
            self.assertEqual(self.said, ['Word processor (W), 2 of 12.'])
            self.keys(mode, 'End')
            self.assertEqual(self.said, ['File manager (F), 12 of 12.'])
            self.keys(mode, 'Up')
            self.assertEqual(self.said, ['Help and updates (H), 11 of 12.'])
            # Opening a list still says how to choose, once.
            self.keys(mode, 'Enter', 'Ctrl+L')
            self.assertTrue(self.said[0].startswith('Help and updates lessons. '), self.said)
            self.assertTrue(self.said[0].endswith(', 1 of ' + str(len(companion.tutorial_session.course()['lessons'])) +
                                                  '. Press Enter or say that one to open.'), self.said)
            self.keys(mode, 'Down')
            self.assertRegex(self.said[0], r'^[^.]+, 2 of \d+\.$')

    def test_the_opening_message_names_every_course(self):
        from practice_tutorial import PracticeTutorial
        session = PracticeTutorial(companion.GUIDE)
        names = [course['name'] for course in session.courses]
        self.assertIn('Courses cover ' + ', '.join(names[:-1]) + ', and ' + names[-1] + '. ', session.start())
        guide = companion.GUIDE.read_text(encoding='utf-8')
        self.assertIn('Courses cover ' + ', '.join(names[:-1]) + ', and ' + names[-1] + '.', guide)


class HelpListWordsTests(KeyTestCase):
    """In Help, each Down and Up said the item and then "Say next or previous, then that one.
    Say help with followed by a topic name to jump there." again. Home and End said only the
    item. The hint is now said when a list opens, and each move says only the item."""

    def test_moves_say_the_item_and_opening_a_list_says_how_to_choose(self):
        mode = self.keys('awake', 'F1')
        self.assertEqual(mode, 'help')
        count = str(len(companion.help_session.topics))  # 33 on Windows, where What's new is built
        self.assertTrue(self.said[0].endswith('1 of ' + count + '. Say next or previous, then that one. Say help with followed by '
                                              'a topic name to jump there.'), self.said)
        self.keys(mode, 'Down')
        self.assertEqual(self.said, ['Getting Started (G), 2 of ' + count + '.'])
        companion.handle('next', mode)
        self.assertEqual(self.said[-1], 'Using Voice Commands (U), 3 of ' + count + '.')
        self.keys(mode, 'Up', 'Enter')
        self.assertTrue(self.said[0].startswith('Getting Started. Install Voice Companion (I), 1 of 5. Say next or previous'),
                        self.said)
        self.keys(mode, 'Down')
        self.assertEqual(self.said, ['Open the app and wake it (O), 2 of 5.'])
        self.keys(mode, 'End')
        self.assertEqual(self.said, ['Set up your microphone (S), 5 of 5.'])


class InputModeWordsTests(CompanionTestCase):
    """"Switch to dictation mode" and "Switch to command mode" worked, but "Switch to normal
    mode" was not understood. "Which mode?" worked, but "What mode am I in?" did not."""

    def test_switch_to_works_for_every_mode_name(self):
        for words, mode, name in (('Switch to normal mode.', 'mixed', 'Normal mode.'),
                                  ('Switch to commands only', 'commands', 'Commands only.'),
                                  ('switch to dictation only', 'dictation', 'Dictation only.'),
                                  ('Switch to normal mode', 'mixed', 'Normal mode.')):
            with self.subTest(words=words):
                self.said.clear()
                self.assertEqual(companion.handle(words, 'awake'), 'awake')
                self.assertEqual(companion.INPUT_MODE, mode)
                self.assertTrue(self.said[0].startswith(name), self.said)

    def test_questions_about_the_mode_say_it(self):
        for words in ('What mode am I in?', 'What mode is this?', 'Which mode am I in?', 'What mode is on?'):
            with self.subTest(words=words):
                self.said.clear()
                self.assertEqual(companion.handle(words, 'awake'), 'awake')
                self.assertEqual(self.said, ['Input mode: Normal mode.'])


class ClosedThenMainMenuTests(KeyTestCase):
    """Two messages in a row cut each other off; after a key only the last one is said. Escape at
    the exit question said "Canceled." and then "Main menu...", so "Canceled." was never heard.
    Control+F1 or "Main menu" in Help said only "Main menu...", while the tutorial and Options
    say "Tutorial closed." and "Options closed." first."""

    def test_canceling_the_exit_or_power_question_is_one_message(self):
        mode = self.keys('awake', 'Escape')
        self.assertEqual(mode, 'exit_confirm')
        mode = self.keys(mode, 'Escape')
        self.assertEqual(mode, 'awake')
        self.assertEqual(self.said, ['Canceled. ' + companion.MAIN_MENU_PROMPT])
        self.assertEqual(companion.handle('restart computer', 'awake'), 'power_confirm')
        self.said.clear()
        self.assertEqual(companion.handle('No.', 'power_confirm'), 'awake')
        self.assertEqual(self.said, ['Canceled. ' + companion.MAIN_MENU_PROMPT])

    def test_canceling_elsewhere_says_where_you_are(self):
        companion.handle('help', 'awake')
        self.assertEqual(companion.handle('exit companion', 'help'), 'exit_confirm')
        self.said.clear()
        self.assertEqual(companion.handle('no', 'exit_confirm'), 'help')
        self.assertEqual(self.said, ['Canceled. Back to help.'])

    def test_leaving_help_for_the_main_menu_says_help_closed(self):
        for how in ('Ctrl+F1', 'main menu'):
            with self.subTest(how=how):
                mode = self.keys('awake', 'F1')
                self.said.clear()
                mode = self.keys(mode, how) if how.startswith('Ctrl') else companion.handle(how, mode)
                self.assertEqual(mode, 'awake')
                self.assertEqual(self.said, ['Help closed. ' + companion.MAIN_MENU_PROMPT])


class UpdateOfferWhileAsleepTests(CompanionTestCase):
    """Asleep in a document, an update was offered. "No" took the user to the main menu, awake,
    with the document still open behind it. The place kept by sleep was lost."""

    def setUp(self):
        super().setUp()
        self.service = MagicMock(); self.service.events = queue.Queue(); self.service.busy = False
        for name, value in dict(UPDATES=self.service, UPDATE_OFFER=False, UPDATE_MANUAL=False, UPDATE_RETURN_MODE='awake',
                                STARTUP_UPDATE_PENDING=False, MAIN_MENU_LEAD='').items():
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'speech_busy', return_value=False); p.start(); self.addCleanup(p.stop)

    def test_no_goes_back_to_the_document(self):
        document = self.open_document()
        companion.handle('Hello there.', 'document')
        self.assertEqual(companion.handle('go to sleep', 'document'), 'sleep')
        self.service.events.put(('available', {'version': '9.9.9-test'}))
        self.assertEqual(companion.poll_app_updates('sleep'), 'update_offer')
        self.said.clear()
        self.assertEqual(companion.handle('No.', 'update_offer'), 'document')
        self.assertEqual(self.said, ['Okay. You can keep using this version. Back to document.'])
        self.assertIs(companion.document, document)

    def test_an_offer_waits_while_a_question_is_asked_behind_sleep(self):
        companion.SLEEP_RETURN_MODE = 'exit_confirm'
        self.service.events.put(('available', {'version': '9.9.9-test'}))
        self.assertEqual(companion.poll_app_updates('sleep'), 'sleep')
        companion.SLEEP_RETURN_MODE = 'awake'
        self.assertEqual(companion.poll_app_updates('sleep'), 'update_offer')
        self.said.clear()
        self.assertEqual(companion.handle('no', 'update_offer'), 'awake')
        self.assertEqual(self.said, ['Okay. You can keep using this version. ' + companion.MAIN_MENU_PROMPT])


class WhereAmIInHelpTests(CompanionTestCase):
    """"Where am I?" says the place on the main menu and most screens. In Help it said "I did
    not understand that.", at every level."""

    def test_where_am_i_names_the_help_level_and_item(self):
        companion.handle('help', 'awake')
        companion.handle('next', 'help')
        count = str(len(companion.help_session.topics))  # 33 on Windows, where What's new is built
        for words, expected in (('Where am I?', 'Help topics. Getting Started (G), 2 of ' + count + '.'),
                                ('that one', None),
                                ('where am i', 'Help. Getting Started. Install Voice Companion (I), 1 of 5.'),
                                ('next', None), ('that one', None),
                                ('Where am I?', 'Help. Getting Started, Open the app and wake it.')):
            self.said.clear()
            self.assertEqual(companion.handle(words, 'help'), 'help')
            if expected:self.assertEqual(self.said, [expected])


class GlobalCommandsInHelpTests(CompanionTestCase):
    """Help reads 'To change mode, say "Normal mode", "Commands only", or "Dictation only".'
    Saying them in Help gave "I did not understand that.", as did "What mode?" and
    "What version?". The time, the transcript and the microphone already worked there."""

    def test_mode_and_version_commands_work_in_help(self):
        companion.handle('help', 'awake')
        for words, mode, said in (('Commands only.', 'commands', 'Commands only. Ordinary speech will not be added as text.'),
                                  ('What mode?', 'commands', 'Input mode: Commands only.'),
                                  ('Switch to normal mode', 'mixed', 'Normal mode. Commands and ordinary dictation are available.'),
                                  ('What version is this?', 'mixed',
                                   'Voice Companion version ' + companion.APP_VERSION + '. The microphone is on.')):
            with self.subTest(words=words):
                self.said.clear()
                self.assertEqual(companion.handle(words, 'help'), 'help')
                self.assertEqual(companion.INPUT_MODE, mode)
                self.assertEqual(self.said, [said])
        self.assertIsNotNone(companion.help_session)


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
