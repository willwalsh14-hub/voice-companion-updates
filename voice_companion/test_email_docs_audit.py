"""Bugs found in the October 2026 fine-comb review of email, documents, notes,
Document reader and File manager. Each test failed before its fix."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_yahoo_mail as yahoo_tests
import thunderbird_mailbox
from thunderbird_mailbox import ThunderbirdMailbox

ME = 'me@gmail.com'


def thunderbird(pages=None, context=None):
    """A Thunderbird mailbox whose add-on answers from these values."""
    def call(method, **params):
        if method == 'inbox':
            return {'id': 'account1://INBOX'}
        if method == 'list':
            return pages
        if method == 'context':
            return context
        raise AssertionError(method)
    patcher = mock.patch.object(thunderbird_mailbox, 'call', call)
    patcher.start()
    return ThunderbirdMailbox(ME, {'account_id': 'account1', 'identity_id': 'id1'}), patcher


class ThunderbirdAddressTests(unittest.TestCase):
    def use(self, pages=None, context=None):
        box, patcher = thunderbird(pages, context)
        self.addCleanup(patcher.stop)
        return box

    def test_a_sender_name_with_a_comma_does_not_hide_the_whole_list(self):
        # Thunderbird decodes "=?UTF-8?Q?Smith,_John?=" without quotes. Read as a list of
        # people, it was two, and the whole Inbox failed to open.
        box = self.use({'rows': [{'id': 7, 'from': 'Smith, John <john@example.com>', 'subject': 'Hi'},
                                 {'id': 6, 'from': 'Pat <pat@example.com>', 'subject': 'Two'},
                                 {'id': 5, 'from': '', 'subject': 'Three'}]})
        rows, _ = box.list_messages('inbox')
        self.assertEqual([(row['from'], row['sender_name']) for row in rows],
                         [('john@example.com', 'Smith, John'), ('pat@example.com', 'Pat'), ('Unknown sender', '')])

    def test_reply_all_never_adds_part_of_a_name_as_an_address(self):
        box = self.use(context={'author': 'Smith, John <john@example.com>', 'subject': 'Plans',
                                'recipients': [ME, 'Lee, Pat <pat@example.com>'], 'ccList': ['kim@example.com']})
        context = box.response_context(7, 'reply_all', ME)
        self.assertEqual((context['recipient'], context['cc']), ('john@example.com', ['pat@example.com', 'kim@example.com']))

    def test_reply_to_a_message_you_sent_goes_to_the_people_you_wrote_to(self):
        # In Sent, the sender is you. The reply went to nobody ("Recipient .").
        box = self.use(context={'author': 'Me <me@gmail.com>', 'subject': 'Plans',
                                'recipients': ['Sam <sam@example.com>'], 'ccList': ['kim@example.com']})
        self.assertEqual(box.response_context(7, 'reply', ME)['recipient'], 'sam@example.com')
        context = box.response_context(7, 'reply_all', ME)
        self.assertEqual((context['recipient'], context['cc']), ('sam@example.com', ['kim@example.com']))

    def test_reply_to_header_is_still_used(self):
        box = self.use(context={'author': 'Sam <sam@example.com>', 'replyTo': '"Help, Desk" <help@example.com>',
                                'subject': 'Ticket', 'recipients': [ME]})
        self.assertEqual(box.response_context(7, 'reply', ME)['recipient'], 'help@example.com')


class YahooReplyTests(yahoo_tests.YahooTestCase):
    def test_reply_to_a_message_you_sent_goes_to_the_people_you_wrote_to(self):
        raw = yahoo_tests.message(sender='Vet <' + yahoo_tests.ADDRESS + '>', to='Sam Lee <sam@example.com>',
                                  extra={'Cc': 'kim@example.com'})
        uid = self.server.add('Inbox', raw)
        self.assertEqual(self.mailbox().response_context(str(uid), 'reply', yahoo_tests.ADDRESS, 'inbox')['recipient'],
                         'sam@example.com')
        context = self.mailbox().response_context(str(uid), 'reply_all', yahoo_tests.ADDRESS, 'inbox')
        self.assertEqual((context['recipient'], context['cc']), ('sam@example.com', ['kim@example.com']))


if __name__ == '__main__':
    unittest.main()
