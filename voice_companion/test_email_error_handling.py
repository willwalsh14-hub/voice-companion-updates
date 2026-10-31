"""Email when something goes wrong (sweep, October 31, 2026: error handling).

A full disk, a draft file held by another program, a mail service that stops
halfway, a mailbox that says more than Voice Companion read. Each test here
failed before its fix: the app stopped, said the wrong thing, or said nothing."""
import unittest
from unittest.mock import patch

import companion
from mail_voice import MailSession
from mailbox_access import MailboxError
from test_email_new_veteran import CompanionCase
from test_email_expert_sweep import Mailbox


class DraftCannotBeSavedTests(CompanionCase):
    """The Email Drafts folder is full or locked. Before: the app stopped (the error went
    past every handler) or said "Open and read a message first."""
    def setUp(self):
        super().setUp()
        companion.mail_session = MailSession('thunderbird', self.folder, Mailbox)
        p = patch('companion.account_token', return_value=('me@example.com', 'token'), create=True)
        p.start(); self.addCleanup(p.stop)
        companion.mail_session.process('list messages')

    def failing_save(self):
        return patch('email_draft.VoiceEmail.save', side_effect=PermissionError('locked'))

    def test_a_reply_that_cannot_be_saved_says_so_and_stays_on_the_message(self):
        companion.mail_session.process('open message')
        with self.failing_save():
            self.assertEqual(companion.handle('reply', 'mailbox'), 'mailbox')
        self.assertNotIn('Open and read a message first', self.last())
        self.assertIn('could not be saved', self.last())
        self.assertIsNone(companion.email_draft)

    def test_a_new_email_that_cannot_be_saved_says_so(self):
        with self.failing_save():
            self.assertEqual(companion.handle('new email', 'mailbox'), 'mailbox')
        self.assertIn('could not be saved', self.last())
        self.assertIsNone(companion.email_draft)

    def start_draft(self):
        self.assertEqual(companion.handle('new email', 'mailbox'), 'email_draft')
        companion.handle('bob@example.com', 'email_draft')
        companion.handle('Lunch', 'email_draft')
        companion.handle('See you at noon.', 'email_draft')
        return companion.email_draft

    def test_send_it_when_the_draft_cannot_be_saved_does_not_stop_the_app(self):
        draft = self.start_draft()
        with self.failing_save():
            self.assertEqual(companion.handle('send it', 'email_draft'), 'email_draft')
        self.assertIn('could not be saved', self.last())
        self.assertIn('not sent', self.last())
        self.assertIs(companion.email_draft, draft)
        self.assertIsNone(companion.pending_send)

    def test_leaving_a_draft_that_cannot_be_saved_keeps_it_open(self):
        for command in ('go to sleep', 'file manager', 'new email'):
            draft = self.start_draft()
            with self.subTest(command=command), self.failing_save():
                self.assertEqual(companion.handle(command, 'email_draft'), 'email_draft')
                self.assertEqual(self.last(), companion.UNSAVED_DRAFT)
                self.assertIs(companion.email_draft, draft)
                self.assertIn('See you at noon.', ' '.join(p.text for p in draft.paragraphs))
            companion.handle('cancel', 'email_draft')

    def test_choosing_another_account_for_a_draft_that_cannot_be_saved(self):
        from unittest.mock import Mock
        draft = self.start_draft()
        store = Mock(); store.return_value.select.return_value = ('yahoo', 'me@yahoo.com')
        with patch.object(companion, 'ProtectedStore', store), self.failing_save():
            self.assertEqual(companion.switch_mail_account('yahoo', 'me@yahoo.com', 'email_draft'), 'email_draft')
        self.assertEqual(self.last(), companion.UNSAVED_DRAFT)
        self.assertIs(companion.email_draft, draft)


class DamagedDraftTests(CompanionCase):
    def test_a_damaged_or_locked_draft_says_what_is_wrong(self):
        # Before: "I could not open that draft. Say list email drafts to hear the names." for
        # every problem, though the name was right.
        drafts = self.folder / 'Email Drafts'; drafts.mkdir()
        (drafts / 'Broken.json').write_text('{not json', encoding='utf-8')
        self.assertEqual(companion.handle('open email draft Broken', 'awake'), 'awake')
        self.assertIn('That draft is damaged', self.last())
        (drafts / 'Broken.json').write_text('{"paragraphs": 5}', encoding='utf-8')
        companion.handle('open email draft Broken', 'awake')
        self.assertIn('That draft is damaged', self.last())
        with patch('pathlib.Path.read_text', side_effect=PermissionError('in use')):
            companion.handle('open email draft Broken', 'awake')
        self.assertIn('open in another program', self.last())
        companion.handle('open email draft Nothing', 'awake')
        self.assertIn('Say list email drafts to hear the names', self.last())


class HalfFinishedMoveTests(CompanionCase):
    """The mail service stopped while messages were moving. Before: the open message stayed
    "open" with no list under it, so Reply said "Open and read a message first" and Down said
    "That number is not on this page."""
    def test_after_an_uncertain_move_every_key_says_to_list_messages(self):
        class Stops(Mailbox):
            def trash(self, ids, source_id=None):
                raise MailboxError('Thunderbird stopped answering.')
        mail = MailSession('thunderbird', self.folder, Stops)
        companion.mail_session = mail
        p = patch('companion.account_token', return_value=('me@example.com', 'token'), create=True)
        p.start(); self.addCleanup(p.stop)
        mail.process('list messages'); mail.process('next message'); mail.process('open message')
        mail.process('delete')
        said = mail.process('yes')
        self.assertIn('Say list messages', said)
        self.assertEqual((mail.view, mail.current, mail.rows), ('folder', None, []))
        for command in ('reply', 'next message', 'message status', 'delete'):
            with self.subTest(command=command):
                companion.handle(command, 'mailbox')
                self.assertEqual(self.last(), mail._empty_list())


class AfterAFolderChangeTests(CompanionCase):
    def test_the_inbox_is_listed_not_said_to_be_empty(self):
        # Before: "Folder change completed. Back to Inbox." with no list loaded, so the next key
        # said "Empty list. No messages on this page of Inbox." for a full Inbox.
        class Folders(Mailbox):
            made = []
            def folders(self):
                return Mailbox.folders(self) + [(name, name.lower()) for name in self.made]
            def create_folder(self, name, parent_id=None, parent_path=''):
                self.made.append(name)
        mail = MailSession('thunderbird', self.folder, Folders)
        mail.process('go to folder work'); mail.process('next message'); mail.process('open message')
        mail.process('create folder Bills')
        said = mail.process('yes')
        self.assertTrue(said.startswith('Folder change completed. Say list folders to check it. Inbox. Person 1.'), said)
        self.assertEqual((mail.folder_name, mail.view, mail.current), ('Inbox', 'folder', 1))
        self.assertTrue(mail.process('next message').startswith('Person 2.'))


class YahooFlagsTests(unittest.TestCase):
    """Before: Yahoo read only the Seen flag, so "Is it flagged?" on a starred message
    said "Read, not flagged." and a reply you sent was never said."""
    def test_flagged_replied_and_forwarded_come_from_yahoo(self):
        import tempfile
        from test_yahoo_mail import YahooIMAPServer, ADDRESS, APP_PASSWORD, message
        from yahoo_mailbox import YahooMailbox
        server = YahooIMAPServer(); self.addCleanup(server.close)
        server.add('Inbox', message(subject='Plain'), flags=('\\Seen',))
        server.add('Inbox', message(subject='Starred'), flags=('\\Seen', '\\Flagged', '\\Answered', '$Forwarded'))
        rows, _ = YahooMailbox(ADDRESS, APP_PASSWORD, server.factory).list_messages('Inbox')
        self.assertEqual([(r['subject'], r['flagged'], r['replied'], r['forwarded']) for r in rows],
                         [('Starred', True, True, True), ('Plain', False, False, False)])
        factory = lambda *args: YahooMailbox(ADDRESS, APP_PASSWORD, server.factory)
        with patch('mail_voice.account_token', return_value=(ADDRESS, APP_PASSWORD)), \
                tempfile.TemporaryDirectory() as folder:
            session = MailSession('yahoo', folder, factory)
            self.assertTrue(session.process('open inbox').startswith('Inbox. Flagged, replied, forwarded.'))
            self.assertEqual(session.process('Is it flagged?'), 'Read, flagged, replied, forwarded.')
            session.process('next message')
            self.assertEqual(session.process('message status'), 'Read, not flagged.')


class AddonFileLockedTests(unittest.TestCase):
    """The add-on file in Thunderbird's profile cannot be replaced (held by a virus scanner or a
    sync program). Before: the error was dropped, Thunderbird came back with the old add-on,
    Voice Companion said "Thunderbird has not started again yet" while it was open, and then
    restarted Thunderbird again on every new connection."""
    def test_a_locked_add_on_restarts_thunderbird_once_and_says_why(self):
        import queue
        from test_thunderbird_addon_refresh import StaleCase
        import thunderbird_setup
        case = StaleCase('run'); case.setUp(); self.addCleanup(case.doCleanups)
        system, relay, _ = case.make()
        said = []
        for patcher in (patch.object(companion, 'speak_status', said.append),
                        patch.object(companion, 'mail_log', lambda text: None),
                        patch.object(companion, 'addon_package', lambda: case.package),
                        patch.object(companion, 'ADDON_REFRESH', type(companion.ADDON_REFRESH)(
                            connection=None, retry_at=0.0, thread=None, results=queue.Queue())),
                        patch.object(companion, 'THUNDERBIRD_STARTED', True),
                        patch.object(companion, 'THUNDERBIRD_WATCH', None),
                        patch.object(companion, 'THUNDERBIRD_SETUP', None),
                        patch.object(companion, 'THUNDERBIRD_SIGN_IN', None),
                        patch.object(companion, 'THUNDERBIRD_STARTING', False),
                        patch.object(companion, 'ACCOUNT_ENTRY', None),
                        patch.object(companion, 'quiet_thunderbird', lambda relay: None),
                        patch('thunderbird_bridge.current', lambda: relay),
                        patch.object(thunderbird_setup, 'write_atomic', side_effect=PermissionError('in use'))):
            patcher.start(); self.addCleanup(patcher.stop)
        for _ in range(3):
            companion.poll_thunderbird_addon(system)
            if companion.ADDON_REFRESH.thread is not None:
                companion.ADDON_REFRESH.thread.join(10)
            companion.poll_thunderbird_addon(system)
        self.assertEqual(relay.asked.count('quit'), 1)
        self.assertTrue(system.is_running and relay.connected.is_set())
        self.assertEqual(said, [thunderbird_setup.ADDON_UPDATING, thunderbird_setup.ADDON_NOT_REPLACED])


if __name__ == '__main__':
    unittest.main()
