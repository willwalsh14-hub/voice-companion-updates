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


if __name__ == '__main__':
    unittest.main()
