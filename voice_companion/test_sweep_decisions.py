"""Will's answers to the sweep questions (October 31, 2026). Each test failed before its change."""
import unittest
from unittest.mock import patch

import companion
from mail_voice import MailSession
from test_email_new_veteran import CompanionCase
from test_email_expert_sweep import Mailbox


class InboxOpenEmailTests(CompanionCase):
    """In the inbox, "Open email" opens the message you are on. Before: the inbox loaded again."""
    def test_open_email_in_the_inbox_opens_the_current_message(self):
        companion.mail_session = MailSession('thunderbird', self.folder, Mailbox)
        companion.mail_session.process('list messages')
        companion.mail_session.process('next')
        with patch('companion.MailSession', side_effect=AssertionError('the inbox was opened again')):
            self.assertEqual(companion.handle('Open email.', 'mailbox'), 'mailbox')
        self.assertIn('This is message inbox2.', self.last())



class SendPhraseTests(CompanionCase):
    """"Send my email" and "Send now" send, but only as the whole sentence. Before: written as text."""
    def setUp(self):
        super().setUp()
        companion.mail_session = MailSession('thunderbird', self.folder, Mailbox)
        p = patch('companion.account_token', return_value=('me@example.com', 'token'), create=True)
        p.start(); self.addCleanup(p.stop)
        companion.pending_send = None
        self.addCleanup(setattr, companion, 'pending_send', None)

    def start_draft(self):
        self.assertEqual(companion.handle('new email', 'mailbox'), 'email_draft')
        companion.handle('bob@example.com', 'email_draft')
        companion.handle('Lunch', 'email_draft')
        companion.handle('See you at noon.', 'email_draft')
        return companion.email_draft

    def test_send_my_email_and_send_now_start_the_review(self):
        for phrase in ('Send my email.', 'Send now.', 'send it now'):
            with self.subTest(phrase):
                draft = self.start_draft()
                companion.handle(phrase, 'email_draft')
                # It reaches sending (here: no account is set up), and is not written.
                self.assertIn('has not been sent', self.last())
                self.assertEqual([p.text for p in draft.paragraphs if p.text], ['See you at noon.'])
                companion.handle('cancel send', 'email_draft')

    def test_a_longer_sentence_is_still_text(self):
        draft = self.start_draft()
        companion.handle('Send my email to the team too.', 'email_draft')
        self.assertIsNone(companion.pending_send)
        self.assertIn('Send my email to the team too.', ' '.join(p.text for p in draft.paragraphs))



class MailErrorWordsTests(CompanionCase):
    """A request that fails for a reason that is not the account does not blame the account."""
    def test_unexpected_value_error_does_not_name_the_account(self):
        import mail_voice
        mail = MailSession('thunderbird', self.folder, Mailbox)
        mail.process('list messages')
        with patch.object(Mailbox, 'read_message', side_effect=ValueError('bad date')):
            answer = mail.process('open message')
        self.assertEqual(answer, mail_voice.MAIL_REQUEST_FAILED)
        self.assertNotIn('account', answer)



class DraftFlushTests(unittest.TestCase):
    """Email drafts reach the disk before they replace the saved copy, as documents do."""
    def test_draft_files_are_flushed_and_failures_leave_no_half_copy(self):
        import os, tempfile
        from pathlib import Path
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            draft = VoiceEmail(Path(folder), recipient='sam@example.com', subject='Lunch')
            with patch('email_draft.os.fsync', wraps=os.fsync) as sync:
                draft.save()
            self.assertEqual(sync.call_count, 2, 'the saved state and the message file')
            with patch('email_draft.os.fsync', side_effect=OSError(28, 'No space left on device')):
                with self.assertRaises(OSError):
                    draft.save()
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir() if '.writing' in p.name), [])



class GeneralAnswerTests(CompanionCase):
    def test_pause_with_nothing_playing_stops_speech(self):
        with patch('companion.control_speech') as control:
            self.assertEqual(companion.handle('Pause.', 'awake'), 'awake')
        control.assert_called_once_with('pause')
        self.assertNotIn('did not understand', self.last() or '')

    def test_pause_in_a_document_is_still_text(self):
        from document_editor import VoiceDocument
        companion.document = VoiceDocument(self.folder / 'Documents')
        self.addCleanup(setattr, companion, 'document', None)
        with patch('companion.control_speech') as control:
            companion.handle('pause', 'document')
        control.assert_not_called()

    def test_f1_in_a_yes_or_no_question_says_it_again(self):
        def heard(text, *args, **kwargs):
            companion.remember_question(text); self.said.append(str(text))
        p = patch.object(companion, 'speak', heard); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'LAST_YES_NO', ''); p.start(); self.addCleanup(p.stop)
        self.assertEqual(companion.handle('exit companion', 'awake'), 'exit_confirm')
        question = self.last()
        self.assertIn('Yes or no', question)
        companion.navigation_key('Down', 'exit_confirm')
        self.assertIn(self.last(), ('No.', 'End of list.'))
        self.assertIsNone(companion.navigation_key('F1', 'exit_confirm'))
        self.assertEqual(self.last(), question)
        companion.handle('no', 'exit_confirm')

    def test_text_size_out_of_range_says_the_sizes(self):
        before = dict(companion.PREFERENCES)
        companion.handle('Text size 1000.', 'awake')
        self.assertIn('100 to 400 percent', self.last())
        self.assertEqual(companion.PREFERENCES.get('display_text_size'), before.get('display_text_size'))

    def test_main_menu_understands_everyday_help_and_go_back(self):
        for phrase in ('Help me.', 'What can I say?'):
            with self.subTest(phrase):
                self.assertEqual(companion.handle(phrase, 'awake'), 'help')
                self.assertIn('User guide', self.last())
                companion.handle('main menu', 'help')
        self.assertEqual(companion.handle('Go back.', 'awake'), 'awake')
        self.assertIn('first screen', self.last())



class CalendarWordsTests(unittest.TestCase):
    def test_next_weekday_means_next_week(self):
        from datetime import date
        from spoken_dates import find_day
        monday = date(2026, 11, 2)
        self.assertEqual(find_day('next Tuesday', monday, True)[0], date(2026, 11, 10))
        self.assertEqual(find_day('Tuesday', monday, True)[0], date(2026, 11, 3))
        self.assertEqual(find_day('this coming Tuesday', monday, True)[0], date(2026, 11, 3))
        saturday = date(2026, 10, 31)
        self.assertEqual(find_day('next Tuesday', saturday, True)[0], date(2026, 11, 3), 'already next week')

    def test_half_past_and_quarter_to_are_times(self):
        from datetime import datetime, time
        from spoken_dates import parse_when
        now = datetime(2026, 11, 2, 9, 0)
        for words, start, rest in (('Dentist at half past two', time(14, 30), 'Dentist'),
                                   ('quarter to three meeting', time(14, 45), 'meeting'),
                                   ('a quarter after 4 PM lunch', time(16, 15), 'lunch'),
                                   ('tomorrow at quarter past nine in the morning call Mom', time(9, 15), 'call Mom'),
                                   ('quarter to one', time(12, 45), '')):
            with self.subTest(words):
                when = parse_when(words, now)
                self.assertEqual((when.start, when.rest), (start, rest))


class NoSecondTryForChangesTests(unittest.TestCase):
    """A change sent before Thunderbird closed is not sent again: it could make a second copy."""
    def bridge(self, *answers):
        from unittest.mock import Mock
        bridge = Mock()
        bridge.connected.is_set.return_value = True
        bridge.request.side_effect = list(answers)
        return bridge

    def test_a_dropped_change_says_to_check_and_is_not_repeated(self):
        import thunderbird_mailbox
        from mailbox_access import MailboxError
        from thunderbird_bridge import Dropped
        bridge = self.bridge(Dropped('gone'), {'id': 'twice'})
        with patch.object(thunderbird_mailbox, 'current', return_value=bridge), \
             patch.object(thunderbird_mailbox, 'STARTER', lambda: None), \
             patch.object(thunderbird_mailbox, 'connected', side_effect=[True, False, True]):
            with self.assertRaises(MailboxError) as caught:
                thunderbird_mailbox.call('addEvent', title='Lunch')
        self.assertEqual(str(caught.exception), thunderbird_mailbox.CHANGE_UNSURE)
        self.assertEqual(bridge.request.call_count, 1)

    def test_a_dropped_read_is_asked_again(self):
        import thunderbird_mailbox
        from thunderbird_bridge import Dropped
        bridge = self.bridge(Dropped('gone'), ['events'])
        with patch.object(thunderbird_mailbox, 'current', return_value=bridge), \
             patch.object(thunderbird_mailbox, 'STARTER', lambda: None), \
             patch.object(thunderbird_mailbox, 'connected', side_effect=[True, False, True]):
            self.assertEqual(thunderbird_mailbox.call('events', start=0, end=1), ['events'])
        self.assertEqual(bridge.request.call_count, 2)



from test_calendar_screen import CalendarHarness  # noqa: E402


class FailedNewEventTests(CalendarHarness):
    """A new event Thunderbird could not save keeps its name and time; Yes tries again."""
    def test_yes_after_a_failed_save_tries_again_with_the_same_event(self):
        import calendar_screen
        import thunderbird_pim
        from unittest import mock
        mode = self.open()
        real = thunderbird_pim.add_event
        with mock.patch.object(thunderbird_pim, 'add_event', side_effect=thunderbird_pim.PimError('Thunderbird could not save the event.')):
            app_mode = __import__('companion').handle('add event tomorrow at 3 PM called Lunch', mode)
            __import__('companion').handle('yes', app_mode)
        self.assertIn('Its name and time are kept', self.said())
        with mock.patch.object(thunderbird_pim, 'add_event', wraps=real) as add:
            __import__('companion').handle('yes', mode)
        self.assertEqual(add.call_args.args[1], 'Lunch')
        self.assertTrue(self.said().startswith('Saved Lunch'), self.said())
        self.assertIsNone(calendar_screen.PENDING)

    def test_a_day_that_could_not_be_read_does_not_say_no_events(self):
        import thunderbird_pim
        from unittest import mock
        with mock.patch.object(thunderbird_pim, 'events', side_effect=thunderbird_pim.PimError('Thunderbird did not answer in time. Wait a minute, then try again.')):
            self.open()
        self.assertNotIn('No events', self.said())
        self.assertIn('did not answer in time', self.said())


if __name__ == '__main__':
    unittest.main()
