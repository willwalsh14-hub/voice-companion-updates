"""A link chosen in an email opens in the guided browser from Web browser options, and
an email address link starts a new email (Will, October 2026)."""
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class FakeWeb:
    def __init__(self, *a, **k):
        self.opened = []; self.snapshot = None; self.page_revision = 0
    def open(self, url):
        self.opened.append(url); self.snapshot = {'url': url}; return 'Page loaded.'


class EmailLinkTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(companion, 'web_session', None); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'WebSession', FakeWeb); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'speak_web_result'); p.start(); self.addCleanup(p.stop)

    def test_web_link_opens_in_the_guided_browser(self):
        with mock.patch('webbrowser.open') as outside:
            self.assertEqual(companion.open_email_link('https://example.com/give'), 'web')
        outside.assert_not_called()
        self.assertEqual(companion.web_session.opened, ['https://example.com/give'])

    def test_mailbox_hands_the_chosen_link_to_the_browser(self):
        session = mock.Mock(); session.process.return_value = 'Opening Donate now.'
        session.link_request = 'https://example.com/give'
        with mock.patch.object(companion, 'mail_session', session):
            self.assertEqual(companion.handle('activate current element', 'mailbox'), 'web')
        self.assertIsNone(session.link_request)

    def test_email_address_link_starts_a_new_email(self):
        with mock.patch.object(companion, 'mail_session', None):
            mode = companion.open_email_link('mailto:friend%40example.com?subject=Hi')
        self.assertEqual(mode, 'email_draft')
        self.assertEqual(companion.email_draft.recipient, 'friend@example.com')


if __name__ == '__main__':
    unittest.main()
