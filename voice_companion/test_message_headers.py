"""Alt+1 to Alt+6 and spoken questions say one line of an email's header (Will, October 2026)."""
import unittest
from unittest.mock import Mock, patch

import companion
from input_parity import MAIL_HEADER_KEYS, SCREEN_KEYS, mail_header_key
from key_state import ALT_STATE
from mail_voice import MailSession, header_request


def session(rows, client=None):
    mail = MailSession.__new__(MailSession)
    mail.rows, mail.current, mail.view, mail.folder_id = rows, 1 if rows else None, 'folder', 'inbox'
    mail.attachments, mail.attachments_for = None, None
    mail.client = lambda: client
    return mail


class HeaderKeyTests(unittest.TestCase):
    def test_alt_and_a_number_is_a_header_key_in_email_only(self):
        for number, command in zip('123456', ('who is it from', 'who is it to', 'read the cc',
                                               'what is the subject', 'when was it sent', 'how many attachments')):
            self.assertEqual(mail_header_key(number, ALT_STATE), 'Alt+' + number)
            with patch.object(companion, 'speak'):
                self.assertEqual(companion.navigation_key('Alt+' + number, 'mailbox'), command)
            self.assertEqual(header_request(command), ('from', 'to', 'cc', 'subject', 'date', 'attachments')[int(number) - 1])
        # Without Alt, or with Control or Shift too, the number keeps its usual job.
        self.assertIsNone(mail_header_key('1', 0))
        self.assertIsNone(mail_header_key('1', ALT_STATE | 4))
        self.assertIsNone(mail_header_key('1', ALT_STATE | 1))
        self.assertIsNone(mail_header_key('7', ALT_STATE))

    def test_header_keys_do_not_clash_with_other_keys(self):
        from screen_menus import MENUS
        from task_navigation import SHORTCUTS
        taken = set(SHORTCUTS) | {key for keys in SCREEN_KEYS.values() for key in keys}
        self.assertFalse(set(MAIL_HEADER_KEYS) & taken)
        menu_keys = [key for _, (_, groups) in MENUS.items() for _, entries in groups for _, key, _ in entries]
        for key in MAIL_HEADER_KEYS:
            self.assertLessEqual(menu_keys.count(key), 1, key)

    def test_spoken_questions_tolerate_recognizer_punctuation(self):
        for phrase, wanted in (('Who sent this?', 'from'), ('Read the CC.', 'cc'), ("What's the subject?", 'subject'),
                               ('Read the date.', 'date'), ('When was it sent?', 'date'), ('Who is it to?', 'to')):
            self.assertEqual(header_request(phrase), wanted, phrase)
        self.assertIsNone(header_request('Who is it for the party tonight?'))


class EmptyHeaderTests(unittest.TestCase):
    def test_empty_lines_say_so(self):
        client = Mock()
        client.message_headers.return_value = {'to': [], 'cc': []}
        client.attachments.return_value = []
        mail = session([{'id': '1', 'from': 'Unknown sender', 'subject': '(no subject)', 'date': 'unavailable'}], client)
        self.assertEqual(mail.header('cc'), 'No CC.')
        self.assertEqual(mail.header('to'), 'No To address.')
        self.assertEqual(mail.header('subject'), 'No subject.')
        self.assertEqual(mail.header('date'), 'The date it was sent is not known.')
        self.assertEqual(mail.header('attachments'), 'No attachments.')
        self.assertEqual(mail.header('from'), 'From: Unknown sender.')
        client.message_headers.assert_called_once()   # To and CC come from one request

    def test_an_empty_list_and_no_message(self):
        self.assertEqual(session([]).header('from'), 'Empty list. No messages on this page.')
        mail = session([{'id': '1', 'from': 'a@example.com'}]); mail.current = None
        self.assertEqual(mail.header('from'), 'Open a message first.')


if __name__ == '__main__':
    unittest.main()
