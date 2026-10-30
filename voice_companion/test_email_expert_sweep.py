"""Email as an expert screen reader user drives it (sweep, October 30, 2026).

Terse, consistent speech; Escape goes back to where you were. Each test here
failed before its fix."""
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
from mail_voice import MailSession
from test_email_new_veteran import CompanionCase


class Body(str):
    attachments = []


class Mailbox:
    """Two folders with messages; the calls MailSession and the reply flow make."""
    def __init__(self, *args): pass
    def folders(self):
        return [('Inbox', 'inbox'), ('Work', 'work'), ('Trash', 'trash')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': folder + str(n), 'from': 'Person %d <p%d@example.com>' % (n, n),
                  'subject': 'Lunch on Friday.' if n == 1 else '  ' if n == 2 else '(no subject)' if n == 3 else 'Plans',
                  'date': 'October 2, 2026'} for n in (1, 2, 3, 4)], None)
    def read_message(self, ident, folder=None):
        body = Body('This is message ' + ident + '.')
        body.attachments = [{'name': 'notes.txt', 'size': 20}] if ident.endswith('2') else []
        return body
    def attachments(self, ident, folder=None):
        return [{'name': 'notes.txt', 'size': 20}] if ident.endswith('2') else []
    def response_context(self, ident, action, address, folder=None):
        return {'recipient': 'p1@example.com', 'cc': [], 'subject': 'Re: Lunch', 'action': action,
                'source_id': ident}


class SummaryWordsTests(CompanionCase):
    def test_a_subject_that_ends_in_a_period_is_said_with_one_period(self):
        # Heard: "Person 1. Lunch on Friday.. October 2, 2026. 1 of 4."
        mail = MailSession('thunderbird', self.folder, Mailbox)
        self.assertEqual(mail.process('list messages'),
                         'Inbox. Person 1. Lunch on Friday. October 2, 2026. 1 of 4.')

    def test_a_blank_subject_says_no_subject_as_alt_4_does(self):
        # Heard: "Person 2. . October 2, 2026." and "Person 3. (no subject). ..."
        mail = MailSession('thunderbird', self.folder, Mailbox)
        mail.process('list messages')
        self.assertTrue(mail.process('next message').startswith('Person 2. No subject. October 2, 2026. 2 of 4.'))
        self.assertEqual(mail.process('what is the subject'), 'No subject.')
        self.assertTrue(mail.process('next message').startswith('Person 3. No subject. October 2, 2026. 3 of 4.'))


class EscapeReturnsToYourPlaceTests(CompanionCase):
    def setUp(self):
        super().setUp()
        companion.mail_session = MailSession('thunderbird', self.folder, Mailbox)
        patch.object(companion, 'finish_mail_announcement').start()
        self.addCleanup(patch.stopall)
        p = patch('companion.account_token', return_value=('me@example.com', 'token'), create=True); p.start()

    def press(self, key, mode):
        command = companion.navigation_key(key, mode)
        return companion.handle_keyboard(command, mode) if command else mode

    def test_escape_from_the_attachments_list_says_the_message_you_are_on(self):
        # Heard: "Back to Inbox." and nothing more; Escape from a message says the message.
        companion.mail_session.process('list messages')
        companion.mail_session.process('next message')
        mode = self.press('L', 'mailbox')
        self.assertEqual(mode, 'attachments')
        self.assertEqual(self.press('Escape', mode), 'mailbox')
        self.assertEqual(self.last(), 'Back to Inbox. Person 2. No subject. October 2, 2026. 2 of 4.')

    def test_canceling_a_reply_goes_back_to_the_message_in_its_folder(self):
        # Before: canceling a reply to a message in Work opened the Inbox at message 1.
        mail = companion.mail_session
        mail.process('go to folder work')
        mail.process('next message')
        mail.process('open message')
        self.assertEqual(companion.handle('reply', 'mailbox'), 'email_draft')
        self.said.clear()
        self.assertEqual(self.press('Escape', 'email_draft'), 'mailbox')
        self.assertEqual(self.said, ['Draft canceled without saving.', 'Back to message.'])
        self.assertEqual((mail.folder_name, mail.current, mail.view), ('Work', 2, 'message'))

    def test_canceling_a_new_email_goes_back_to_the_message_you_were_on(self):
        mail = companion.mail_session
        mail.process('go to folder work')
        mail.process('last message')
        self.assertEqual(companion.handle('new email', 'mailbox'), 'email_draft')
        self.said.clear()
        self.assertEqual(companion.handle('cancel', 'email_draft'), 'mailbox')
        self.assertEqual(self.said[-1], 'Back to Work. Person 4. Plans. October 2, 2026. 4 of 4.')
        self.assertEqual((mail.folder_name, mail.current), ('Work', 4))


if __name__ == '__main__':
    unittest.main()
