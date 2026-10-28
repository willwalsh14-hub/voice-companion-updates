"""Problems found by using the app as a customer would (October 28, 2026).

Each test drives the real app with the words or keys a person would use, and
checks what they hear."""
import unittest
from unittest.mock import Mock, patch

import companion
from document_editor import Paragraph
from test_core_email_fixes import CompanionTestCase


class SendReviewTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        store = Mock(); store.load.return_value = {'address': 'me@gmail.com'}
        store.selected.return_value = ('thunderbird', 'me@gmail.com'); store.accounts.return_value = [('thunderbird', 'me@gmail.com')]
        for target, value in (('ProtectedStore', Mock(return_value=store)),
                              ('account_token', Mock(return_value=('me@gmail.com', 'tok'))),
                              ('MailSession', Mock()), ('finish_mail_announcement', Mock())):
            p = patch.object(companion, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'submit'); self.submit = p.start(); self.addCleanup(p.stop)

    def review(self, body, subject='Hi', **fields):
        draft = self.draft(subject=subject, **fields)
        draft.paragraphs = [Paragraph(body)]
        companion.handle('send email', 'email_draft', typed=True)
        return self.said[-1]

    def test_review_never_doubles_the_message_punctuation(self):
        # Heard: "Message: Sounds good.. Is that right?" A screen reader can say "dot dot".
        review = self.review('Sounds good.')
        self.assertIn('Message: Sounds good. Is that right?', review)
        self.assertNotIn('..', review)

    def test_review_keeps_a_question_or_exclamation_as_written(self):
        review = self.review('Are you free?', subject='Lunch?')
        self.assertIn('Subject Lunch? Message: Are you free? Is that right?', review)
        self.assertNotIn('?.', review)

    def test_review_of_a_reply_adds_one_period_before_the_note(self):
        review = self.review('Sounds good.', response_context={'action': 'reply', 'source_id': '1'})
        self.assertIn('Message: Sounds good. This reply uses the original message in your mailbox. Is that right?', review)

    def test_review_of_a_body_without_punctuation_still_ends_the_sentence(self):
        self.assertIn('Message: See you soon. Is that right?', self.review('See you soon'))


if __name__ == '__main__':
    unittest.main()
