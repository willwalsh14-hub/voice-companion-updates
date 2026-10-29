"""Email as a new veteran uses it (sweep, October 29, 2026).

Someone who just installed the app says natural things in the inbox and in a
draft. Each test here failed before its fix."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import companion
import thunderbird_mailbox
import window_hiding
from email_draft import VoiceEmail
from mail_voice import MailSession


class FakeClient:
    """A small inbox, with the calls MailSession makes."""
    def __init__(self, *args): pass
    def folders(self):
        return [('Inbox', 'inbox'), ('Sent', 'sent'), ('Trash', 'trash'), ('Receipts', 'receipts')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': str(n), 'from': 'Person %d <p%d@example.com>' % (n, n), 'subject': 'Subject %d' % n,
                  'date': 'October 2, 2026'} for n in (1, 2, 3)], None)
    def read_message(self, ident, folder=None):
        return 'This is message ' + ident + '.'
    def attachments(self, ident, folder=None): return []


class CompanionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.said = []
        for name, value in dict(TEXT_MODE=True, APP=self.folder, APP_WINDOW=None, MEDIA_HUB=None, INPUT_MODE='mixed',
                                document=None, email_draft=None, mail_session=None, pending_send=None,
                                help_session=None, SLEEP_RETURN_MODE='awake', THUNDERBIRD_WINDOWS=None,
                                THUNDERBIRD_SETUP=None).items():
            p = patch.object(companion, name, value, create=True); p.start(); self.addCleanup(p.stop)
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            p = patch.object(companion, name, lambda text, *a, **k: self.said.append(str(text))); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'save_speech_settings'); p.start(); self.addCleanup(p.stop)
        p = patch('mail_voice.account_token', return_value=('me@example.com', 'token')); p.start(); self.addCleanup(p.stop)

    def last(self):
        return self.said[-1] if self.said else ''


class HideThunderbirdTests(CompanionCase):
    def test_hide_thunderbird_when_it_is_not_running_does_not_start_it(self):
        # Before: "Hide Thunderbird" started Thunderbird (or left a hidden setup question
        # waiting for a yes), then said it was not open.
        starter = Mock(side_effect=AssertionError('Thunderbird was started'))
        with patch.object(thunderbird_mailbox, 'STARTER', starter), patch.object(thunderbird_mailbox, 'current', return_value=None):
            mode = companion.handle('hide Thunderbird', 'awake')
        self.assertEqual(mode, 'awake')
        starter.assert_not_called()
        self.assertIsNone(companion.THUNDERBIRD_SETUP)
        self.assertEqual(self.last(), window_hiding.NOT_RUNNING)

    def test_hide_thunderbird_minimizes_a_running_thunderbird(self):
        bridge = Mock(); bridge.connected.is_set.return_value = True
        with patch.object(thunderbird_mailbox, 'current', return_value=bridge):
            companion.handle('hide Thunderbird', 'awake')
        bridge.request.assert_called_with('minimize', {}, timeout=10)
        self.assertEqual(self.last(), window_hiding.MINIMIZED_NOW)


class SendWithoutAccountTests(CompanionCase):
    """An email written before any account was set up."""
    def store(self, selected):
        store = Mock(); store.load.return_value = {'address': 'me@gmail.com'}
        store.selected.return_value = selected
        store.accounts.return_value = [selected] if selected else []
        return Mock(return_value=store)

    def draft(self):
        companion.email_draft = VoiceEmail(self.folder / 'Email Drafts', recipient='bob@example.com', subject='Hi',
                                           provider='', compose_step='body')
        companion.email_draft.process('See you soon.')
        return companion.email_draft

    def test_no_account_says_how_to_add_one_once(self):
        # Before: "Choose a connected email account first. The draft has not been sent.
        # The draft is saved locally. It has not been sent." There was none to choose.
        self.draft()
        with patch.object(companion, 'ProtectedStore', self.store(None)):
            companion.handle('send it', 'email_draft')
        self.assertEqual(self.last(), 'No email account is set up yet. Say add account to set one up. '
                                      'The draft is saved locally. It has not been sent.')
        self.assertIsNone(companion.pending_send)

    def test_an_account_added_later_sends_the_draft(self):
        # Before: the draft could never be sent: no command chooses an account for it.
        draft = self.draft()
        with patch.object(companion, 'ProtectedStore', self.store(('thunderbird', 'me@gmail.com'))):
            companion.handle('send it', 'email_draft')
        self.assertTrue(self.last().startswith('Review before sending. From Thunderbird, me@gmail.com. To bob@example.com.'), self.last())
        self.assertEqual(draft.provider, 'thunderbird')
        self.assertEqual(companion.pending_send, (draft.send_hash(), 'thunderbird', 'me@gmail.com'))


class AccountTypeQuestionTests(CompanionCase):
    """'Add account' asks Thunderbird or Yahoo, as a list."""
    def setUp(self):
        super().setUp()
        for name, value in (('account_setup', False), ('ACCOUNT_MENU_INDEX', None), ('ACCOUNT_ENTRY', None)):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)

    def press(self, key, mode):
        request = companion.navigation_key(key, mode)
        return companion.handle(request, mode) if isinstance(request, str) else mode

    def test_escape_cancels_the_question_on_every_screen(self):
        # Before: Escape on the main menu asked "Exit Voice Companion?", and in the inbox it
        # left the question waiting, so later commands heard "Say Thunderbird, Yahoo, or cancel."
        for mode in ('awake', 'mailbox'):
            companion.mail_session = MailSession('thunderbird', self.folder, FakeClient) if mode == 'mailbox' else None
            companion.handle('add account', mode)
            self.assertEqual(self.press('Escape', mode), mode)
            self.assertEqual(self.last(), 'Account setup canceled.')
            self.assertFalse(companion.account_setup)

    def test_next_previous_and_that_one_by_voice(self):
        # Before: "Next" said "Say Thunderbird, Yahoo, or cancel" while Down Arrow moved.
        companion.handle('add account', 'awake')
        companion.handle('next', 'awake')
        self.assertTrue(self.last().startswith('Thunderbird'), self.last())
        companion.handle('next', 'awake')
        self.assertTrue(self.last().startswith('Yahoo'), self.last())
        companion.handle('next', 'awake')
        self.assertEqual(self.last(), 'End of list.')
        companion.handle('previous', 'awake')
        self.assertTrue(self.last().startswith('Thunderbird'), self.last())
        companion.handle('cancel', 'awake')
        # A new question starts at the top again.
        companion.handle('add account', 'awake')
        companion.handle('next', 'awake')
        companion.handle('next', 'awake')
        companion.handle('that one', 'awake')
        self.assertFalse(companion.account_setup)
        self.assertEqual(companion.ACCOUNT_ENTRY, {'provider': 'yahoo', 'step': 'address'})


class DraftFieldTests(CompanionCase):
    def new_email(self):
        companion.email_draft = VoiceEmail(self.folder / 'Email Drafts', provider='thunderbird')
        companion.email_draft.compose_step = 'recipient'
        return companion.email_draft

    def test_changing_the_address_keeps_the_subject(self):
        # Before: after a new address, the next sentence replaced the subject and was lost.
        draft = self.new_email()
        for words in ('bob at example dot com.', 'Lunch plans', 'Hello Bob.', 'go to to', 'sam at example dot com.'):
            companion.handle(words, 'email_draft')
        self.assertNotIn('What is the subject', self.last())
        self.assertIn('message body', self.last())
        companion.handle('I will be late.', 'email_draft')
        self.assertEqual(draft.recipient, 'sam@example.com')
        self.assertEqual(draft.subject, 'Lunch plans')
        self.assertEqual([p.text for p in draft.paragraphs if p.text], ['Hello Bob. I will be late.'])

    def test_a_new_address_without_a_subject_still_asks_for_one(self):
        draft = self.new_email()
        companion.handle('bob at example dot com.', 'email_draft')
        self.assertIn('What is the subject?', self.last())
        self.assertEqual(draft.compose_step, 'subject')

    def test_spoken_subject_drops_the_period_speech_adds(self):
        draft = self.new_email()
        companion.handle('bob at example dot com.', 'email_draft')
        companion.handle('Lunch plans.', 'email_draft')
        self.assertEqual(draft.subject, 'Lunch plans')
        self.assertIn('Subject recorded as Lunch plans. ', self.last())
        companion.handle('subject is Dinner, tonight?', 'email_draft')
        self.assertEqual(draft.subject, 'Dinner, tonight?')
        companion.handle('subject is More to come...', 'email_draft')
        self.assertEqual(draft.subject, 'More to come...')

    def reply(self):
        companion.email_draft = VoiceEmail(self.folder / 'Email Drafts', provider='thunderbird', recipient='sam@example.com',
                                           subject='Re: Lunch', compose_step='body',
                                           response_context={'action': 'reply', 'recipient': 'sam@example.com'})
        return companion.email_draft

    def test_reply_subject_field_says_how_to_get_back_to_the_message(self):
        # Before: in a reply's subject, each sentence heard only "This subject comes from
        # the original message", with no way back named, and the words were lost.
        draft = self.reply()
        companion.handle('go to subject', 'email_draft')
        self.assertIn('go to body', self.last())
        companion.handle('I will be there.', 'email_draft')
        self.assertIn('go to body', self.last())
        companion.handle('go to body', 'email_draft')
        companion.handle('I will be there.', 'email_draft')
        self.assertEqual(draft.subject, 'Re: Lunch')
        self.assertEqual([p.text for p in draft.paragraphs if p.text], ['I will be there.'])

    def test_reply_body_sentence_that_starts_with_subject_is_is_written(self):
        # A reply keeps its subject, so "Subject is ..." there is never a command: it is text.
        draft = self.reply()
        companion.handle('Sounds good.', 'email_draft')
        companion.handle('Subject is on my mind too.', 'email_draft')
        self.assertEqual(draft.subject, 'Re: Lunch')
        self.assertEqual([p.text for p in draft.paragraphs if p.text], ['Sounds good. Subject is on my mind too.'])


class ShrinkingInbox(FakeClient):
    ids = ['1', '2', '3']
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': n, 'from': 'Person %s <p%s@example.com>' % (n, n), 'subject': 'Subject ' + n,
                  'date': 'October 2, 2026'} for n in self.ids], None)
    def trash(self, ids, folder=None):
        type(self).ids = [n for n in self.ids if n not in ids]
    def move(self, ids, source, destination):
        self.trash(ids)


class DeleteKeepsYourPlaceTests(unittest.TestCase):
    def session(self):
        ShrinkingInbox.ids = ['1', '2', '3']
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        p = patch('mail_voice.account_token', return_value=('me@example.com', 'token')); p.start(); self.addCleanup(p.stop)
        session = MailSession('thunderbird', folder.name, ShrinkingInbox)
        session.process('list messages')
        return session

    def test_deleting_the_message_you_are_on_goes_to_the_next_one(self):
        # Before: after deleting message 2 you were back on message 1.
        session = self.session()
        session.process('next message')
        session.process('open current message')
        session.process('delete')
        said = session.process('yes')
        self.assertEqual(said, '1 message moved to Trash or Deleted Items. Inbox. Person 3. Subject 3. October 2, 2026. 2 of 2.')
        self.assertEqual(session.current, 2)
        session.process('delete')
        said = session.process('yes')
        self.assertTrue(said.endswith('Inbox. Person 1. Subject 1. October 2, 2026. 1 of 1.'), said)

    def test_moving_the_message_you_are_on_goes_to_the_next_one(self):
        session = self.session()
        session.process('next message')
        session.process('move to Receipts')
        self.assertTrue(session.process('yes').endswith('Person 3. Subject 3. October 2, 2026. 2 of 2.'))

    def test_deleting_marked_messages_starts_at_the_top(self):
        session = self.session()
        session.process('mark')
        session.process('last message')
        session.process('delete')
        self.assertTrue(session.process('yes').endswith('Person 2. Subject 2. October 2, 2026. 1 of 2.'))


class InboxWordsTests(CompanionCase):
    def setUp(self):
        super().setUp()
        companion.mail_session = MailSession('thunderbird', self.folder, FakeClient)
        companion.mail_session.process('list messages')

    def say(self, words):
        self.assertEqual(companion.handle(words, 'mailbox'), 'mailbox', words)
        return self.last()

    def test_folder_names_with_the_my_and_folder(self):
        # Before: "I could not find that folder."
        for words, folder in (('go to the trash', 'Trash'), ('go to my sent folder', 'Sent'), ('go to the receipts folder', 'Receipts'),
                              ('open the inbox', 'Inbox'), ('go to my inbox', 'Inbox'), ('open folder the receipts', 'Receipts')):
            self.assertTrue(self.say(words).startswith(folder + '. '), words + ': ' + self.last())
            self.assertEqual(companion.mail_session.folder_name, folder, words)

    def test_go_to_a_message(self):
        # Before: "go to message 2" was taken as a folder name.
        self.assertTrue(self.say('go to message 2').startswith('Person 2. '), self.last())
        self.assertTrue(self.say('go to the next message').startswith('Person 3. '), self.last())
        self.assertTrue(self.say('go to the first message').startswith('Person 1. '), self.last())
        self.assertTrue(self.say('go to last message').startswith('Person 3. '), self.last())
        self.assertEqual(companion.mail_session.view, 'folder')

    def test_open_messages_in_everyday_words(self):
        self.assertEqual(self.say('open the first message'), 'Message opened. This is message 1. End of message.')
        self.assertEqual(self.say('read the next message'), 'Message opened. This is message 2. End of message.')
        self.assertEqual(self.say('open the last message'), 'Message opened. This is message 3. End of message.')
        self.assertTrue(self.say('read the next message').startswith('End of list. '), self.last())
        self.assertEqual(self.say('read this message'), 'Message opened. This is message 3. End of message.')
        self.assertTrue(self.say('close message').startswith('Back to Inbox. '), self.last())
        self.assertTrue(self.say('next email').startswith('End of list. '), self.last())
        self.assertTrue(self.say('previous email').startswith('Person 2. '), self.last())

    def test_check_email_in_the_inbox_checks_for_new_mail(self):
        # The guide says "Check email" opens email; said again in the inbox it checks again.
        for words in ('check email', 'Check my email.', 'check for new mail', 'refresh'):
            self.assertTrue(self.say(words).startswith('Inbox. Person 1. '), words + ': ' + self.last())

    def test_unknown_words_say_they_were_not_understood(self):
        self.assertTrue(self.say('zebra quantum lantern').startswith('I did not understand that. '), self.last())

    def test_one_of_a_kind_is_said_without_a_plural(self):
        # Before: "Marked 1 messages", "1 messages on this page selected", "Found 1 folders".
        self.say('mark')
        self.assertEqual(self.say('what is selected'), 'Marked 1 message across this folder.')
        self.say('clear selection')
        self.say('mark')
        self.say('next message')
        self.say('mark')
        self.assertEqual(self.say('what is selected'), 'Marked 2 messages across this folder.')
        session = companion.mail_session
        session.rows = session.rows[:1]
        session.current = 1
        self.assertEqual(self.say('select all messages'), '1 message on this page selected.')
        with patch.object(FakeClient, 'folders', lambda self: [('Inbox', 'inbox')]):
            self.assertTrue(self.say('go to folder').startswith('Found 1 folder. '), self.last())

    def test_all_messages_says_how_more_are_reached(self):
        # Before: "I load 10 at a time as you say next message." Next message stops at the
        # end of the page; more come with Page Down.
        said = self.say('set list size to all')
        self.assertTrue(said.startswith('All messages, 10 at a time. For more, press Page Down, or say next page. Inbox. '), said)
        self.assertEqual(self.say('email list size'), 'All messages, 10 at a time. For more, press Page Down, or say next page. '
                                                      'Say set to 20, set list to 1000, or set to all.')

    def test_reply_in_the_list_says_how_to_open_the_message(self):
        self.say('reply')
        self.assertIn('Press Enter', self.last())
        self.assertNotIn('numbered', self.last())


if __name__ == '__main__':
    unittest.main()
