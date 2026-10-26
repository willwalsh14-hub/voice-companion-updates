"""Email works only through Thunderbird and Yahoo (Will, October 2026).

Gmail and Microsoft accounts that Voice Companion connected by itself are
removed when the app starts. Thunderbird and Yahoo accounts, drafts, contacts
and notes are never touched. Real account files in a temporary folder, sealed
as plain JSON as in test_email_accounts.plain_store.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
import mail_accounts
from mail_accounts import ProtectedStore, retire_removed_accounts
from test_core_email_fixes import CompanionTestCase


def plain_store():
    return [patch.object(mail_accounts.ProtectedStore, '_seal', staticmethod(lambda data: json.dumps(data).encode())),
            patch.object(mail_accounts.ProtectedStore, '_unseal', staticmethod(lambda path: plain_unseal(path)))]


def plain_unseal(path):
    try:
        return json.loads(path.read_bytes())
    except ValueError as exc:
        # Like the real DPAPI store: a damaged file is an AccountError.
        raise mail_accounts.AccountError('The saved account cannot be opened. Reconnect it.') from exc


GMAIL = ('gmail', 'will@gmail.com')
OUTLOOK = ('outlook', 'sam@outlook.com')
BIRD = ('thunderbird', 'will@gmail.com')
YAHOO = ('yahoo', 'pat@yahoo.com')


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for item in plain_store():
            item.start(); self.addCleanup(item.stop)
        self.store = ProtectedStore(self.folder)

    def put(self, provider, address, name=None):
        """An account file as an older Voice Companion saved it."""
        path = self.folder / (name or self.store._account_path(provider, address).name)
        path.write_bytes(json.dumps({'address': address, 'credentials': 'secret'}).encode())
        return path

    def select(self, provider, address):
        (self.folder / 'mail-selection.account').write_bytes(
            json.dumps({'provider': provider, 'address': address}).encode())

    def keep(self):
        """Things that must never be removed: drafts, contacts, notes."""
        drafts = self.folder / 'Email Drafts'; drafts.mkdir()
        (drafts / 'Letter to Sam.json').write_text('{"recipient": "sam@example.com"}', encoding='utf-8')
        (self.folder / 'contacts-will@gmail.com.json').write_text('{}', encoding='utf-8')
        (self.folder / 'Notes').mkdir()
        (self.folder / 'Notes' / 'Shopping.txt').write_text('Milk', encoding='utf-8')
        return sorted(str(p.relative_to(self.folder)) for p in self.folder.rglob('*')
                      if 'account' not in p.name)


class RetireRemovedAccountsTests(StoreTestCase):
    def test_only_kinds_left_are_thunderbird_and_yahoo(self):
        self.assertEqual(mail_accounts.PROVIDERS, ('thunderbird', 'yahoo'))
        self.assertFalse(hasattr(mail_accounts, 'gmail_connect'))
        self.assertFalse(hasattr(mail_accounts, 'microsoft_connect'))

    def test_a_removed_kind_is_never_listed_even_before_it_is_deleted(self):
        self.put(*GMAIL); self.put(*BIRD)
        self.assertEqual(self.store.accounts(), [BIRD])

    def test_will_gmail_is_removed_and_his_thunderbird_account_is_selected(self):
        # Will's folder: gmail-<digest>.account, a selection naming it, and Thunderbird.
        gmail = self.put(*GMAIL); bird = self.put(*BIRD); yahoo = self.put(*YAHOO)
        self.select(*GMAIL)
        kept = self.keep()
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.removed, [GMAIL])
        self.assertFalse(gmail.exists())
        self.assertTrue(bird.exists() and yahoo.exists())
        self.assertEqual(self.store.selected(), BIRD)
        self.assertEqual(result.selected, BIRD)
        self.assertTrue(result.changed_selection)
        self.assertEqual(sorted(str(p.relative_to(self.folder)) for p in self.folder.rglob('*')
                                if 'account' not in p.name), kept)

    def test_thunderbird_is_chosen_before_yahoo(self):
        self.put(*YAHOO); self.put(*GMAIL); self.put(*BIRD)
        self.select(*GMAIL)
        self.assertEqual(retire_removed_accounts(self.folder).selected, BIRD)

    def test_yahoo_is_chosen_when_there_is_no_thunderbird_account(self):
        self.put(*GMAIL); self.put(*YAHOO)
        self.select(*GMAIL)
        self.assertEqual(retire_removed_accounts(self.folder).selected, YAHOO)
        self.assertEqual(self.store.selected(), YAHOO)

    def test_a_chosen_yahoo_account_stays_chosen(self):
        self.put(*GMAIL); self.put(*BIRD); self.put(*YAHOO)
        self.select(*YAHOO)
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.selected, YAHOO)
        self.assertFalse(result.changed_selection)

    def test_only_a_removed_account_leaves_nothing_selected(self):
        self.put(*GMAIL); self.select(*GMAIL)
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.removed, [GMAIL])
        self.assertIsNone(result.selected)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [])
        self.assertIsNone(self.store.selected())

    def test_microsoft_files_old_and_new_are_removed(self):
        self.put(*OUTLOOK)
        self.put('outlook', 'old@hotmail.com', name='outlook.account')
        self.put('gmail', 'first@gmail.com', name='gmail.account')
        (self.folder / 'gmail-abc.writing').write_bytes(b'half written')
        result = retire_removed_accounts(self.folder)
        self.assertEqual(sorted(result.removed), [('gmail', 'first@gmail.com'), ('outlook', 'old@hotmail.com'), OUTLOOK])
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_damaged_removed_file_is_still_removed(self):
        (self.folder / 'gmail-0123456789abcdef01234567.account').write_bytes(b'\x00 not readable')
        bird = self.put(*BIRD)
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.removed, [('gmail', '')])
        self.assertTrue(bird.exists())
        self.assertEqual(self.store.selected(), BIRD)

    def test_damaged_selection_file_does_not_stop_the_app(self):
        self.put(*GMAIL); self.put(*BIRD)
        (self.folder / 'mail-selection.account').write_bytes(b'\x00 damaged')
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.selected, BIRD)
        self.assertEqual(self.store.selected(), BIRD)

    def test_damaged_selection_alone_is_read_as_no_choice(self):
        self.put(*BIRD); self.put(*YAHOO)
        (self.folder / 'mail-selection.account').write_bytes(b'\x00 damaged')
        self.assertIsNone(self.store.selected())

    def test_nothing_to_remove_changes_nothing(self):
        bird = self.put(*BIRD); self.select(*BIRD)
        before = sorted((p.name, p.read_bytes()) for p in self.folder.iterdir())
        result = retire_removed_accounts(self.folder)
        self.assertEqual(result.removed, [])
        self.assertEqual(sorted((p.name, p.read_bytes()) for p in self.folder.iterdir()), before)
        self.assertTrue(bird.exists())

    def test_empty_or_missing_folder(self):
        self.assertEqual(retire_removed_accounts(self.folder).removed, [])
        self.assertEqual(retire_removed_accounts(self.folder / 'not there').removed, [])

    def test_runs_once(self):
        self.put(*GMAIL); self.put(*BIRD); self.select(*GMAIL)
        self.assertEqual(len(retire_removed_accounts(self.folder).removed), 1)
        self.assertEqual(retire_removed_accounts(self.folder).removed, [])

    def test_a_file_that_cannot_be_deleted_is_tried_again_next_time(self):
        gmail = self.put(*GMAIL); self.put(*BIRD)
        real_unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == gmail: raise PermissionError('in use')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', locked):
            self.assertEqual(retire_removed_accounts(self.folder).removed, [])
        self.assertTrue(gmail.exists())
        self.assertEqual(self.store.accounts(), [BIRD])
        self.assertEqual(retire_removed_accounts(self.folder).removed, [GMAIL])


class RemovedAccountsMessageTests(unittest.TestCase):
    def message(self, removed, selected=None, changed=True, remaining=()):
        result = mail_accounts.Retired(list(removed), selected, changed, list(remaining))
        import account_picker
        return account_picker.retired_message(result)

    def test_gmail_only_says_what_happened_and_what_to_do(self):
        said = self.message([GMAIL])
        self.assertEqual(said, 'Your Gmail account will@gmail.com was removed from Voice Companion. '
                               'Email now works through Thunderbird. Your drafts and contacts are kept. '
                               'Say add account to set it up in Thunderbird.')

    def test_thunderbird_account_now_in_use(self):
        said = self.message([GMAIL], BIRD, True, [BIRD])
        self.assertIn('Your Gmail account will@gmail.com was removed from Voice Companion.', said)
        self.assertIn('Now using the Thunderbird account will@gmail.com.', said)
        self.assertNotIn('add account', said)

    def test_address_not_yet_in_thunderbird_gets_the_add_hint(self):
        said = self.message([GMAIL], YAHOO, True, [YAHOO])
        self.assertIn('Now using the Yahoo account pat@yahoo.com.', said)
        self.assertIn('To use will@gmail.com again, say add account and choose Thunderbird.', said)

    def test_two_accounts_and_a_damaged_one(self):
        said = self.message([GMAIL, OUTLOOK, ('outlook', '')])
        self.assertIn('Your Gmail account will@gmail.com, your Outlook account sam@outlook.com, '
                      'and an Outlook account were removed from Voice Companion.', said)
        self.assertIn('Gmail and Outlook email now work through Thunderbird.', said)

    def test_nothing_removed_says_nothing(self):
        self.assertEqual(self.message([]), '')

    def test_reading_level(self):
        said = self.message([GMAIL, OUTLOOK], YAHOO, True, [YAHOO])
        for sentence in said.split('. '):
            self.assertLessEqual(len(sentence.split()), 20, sentence)


class StartupAnnouncementTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for item in plain_store():
            item.start(); self.addCleanup(item.stop)

    def test_startup_removes_and_tells_once(self):
        store = ProtectedStore(self.folder)
        (self.folder / store._account_path(*GMAIL).name).write_bytes(json.dumps({'address': GMAIL[1]}).encode())
        companion.retire_old_mail_accounts()
        self.assertEqual(len(self.said), 1)
        self.assertIn('Your Gmail account will@gmail.com was removed', self.said[0])
        companion.retire_old_mail_accounts()
        self.assertEqual(len(self.said), 1)

    def test_open_mail_session_of_removed_kind_is_closed(self):
        store = ProtectedStore(self.folder)
        (self.folder / store._account_path(*GMAIL).name).write_bytes(json.dumps({'address': GMAIL[1]}).encode())
        companion.mail_session = companion.MailSession('gmail', self.folder)
        companion.retire_old_mail_accounts()
        self.assertIsNone(companion.mail_session)

    def test_a_problem_never_stops_the_app(self):
        with patch.object(mail_accounts, 'retire_removed_accounts', side_effect=RuntimeError('boom')):
            companion.retire_old_mail_accounts()
        self.assertEqual(self.said, [])

    def test_main_runs_it_at_startup(self):
        import inspect
        source = inspect.getsource(companion.main)
        self.assertIn('retire_old_mail_accounts()', source)
        # After the update result, before the listening loop starts.
        self.assertLess(source.index("result_file = APP / 'update-result.json'"), source.index('retire_old_mail_accounts()'))
        self.assertLess(source.index('retire_old_mail_accounts()'), source.index('while True:'))


class OldDraftTests(CompanionTestCase):
    """Drafts saved with a removed Gmail or Outlook connection are never lost."""

    def setUp(self):
        super().setUp()
        for item in plain_store():
            item.start(); self.addCleanup(item.stop)
        for target in ('MailSession', 'finish_mail_announcement'):
            p = patch.object(companion, target); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'submit', return_value='accepted'); self.submit = p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'account_token', return_value=('will@gmail.com', {'address': 'will@gmail.com'}))
        p.start(); self.addCleanup(p.stop)
        self.store = ProtectedStore(self.folder)

    def connect_thunderbird(self):
        self.store.save('thunderbird', {'address': 'will@gmail.com', 'account_id': 'a', 'identity_id': 'i'})

    def reopened(self, draft):
        from email_draft import VoiceEmail
        return VoiceEmail.open_existing(self.folder / 'Email Drafts', draft.title)

    def test_new_email_draft_is_sent_from_the_thunderbird_account(self):
        self.connect_thunderbird()
        draft = self.draft(provider='gmail'); draft.save()
        companion.handle('send email', 'email_draft', typed=True)
        self.assertTrue(self.said[-1].startswith('Review before sending. From Thunderbird, will@gmail.com. To friend@example.com. '
                                                 'Subject Hi. Message: See you soon.'), self.said[-1])
        self.assertEqual(self.reopened(draft).provider, 'thunderbird')
        companion.handle('yes', 'email_draft')
        self.submit.assert_called_once()
        self.assertEqual(self.submit.call_args.args[0], 'thunderbird')

    def test_reply_becomes_a_new_email_to_the_same_person(self):
        self.connect_thunderbird()
        draft = self.draft(provider='gmail', subject='Re: Lunch', response_context={
            'action': 'reply', 'source_id': 'gmail-id', 'thread_id': 'thread', 'message_id': '<a@b>',
            'recipient': 'friend@example.com', 'cc': [], 'subject': 'Re: Lunch', 'source_address': 'will@gmail.com'})
        draft.save()
        companion.handle('send email', 'email_draft', typed=True)
        self.assertIn('From Thunderbird, will@gmail.com. To friend@example.com. Subject Re: Lunch.', self.said[-1])
        self.assertNotIn('original message', self.said[-1])
        saved = self.reopened(draft)
        self.assertEqual((saved.provider, saved.response_context, saved.recipient), ('thunderbird', {}, 'friend@example.com'))

    def test_forward_keeps_the_forwarded_text(self):
        self.connect_thunderbird()
        draft = self.draft(provider='gmail', subject='Fwd: Plans', response_context={
            'action': 'forward', 'source_id': 'gmail-id', 'original_from': 'Sam <sam@example.com>',
            'original_subject': 'Plans', 'original_body': 'Meet at nine.\n\nBring maps.'})
        draft.save()
        companion.handle('send email', 'email_draft', typed=True)
        self.assertIn('Message: See you soon.', self.said[-1])
        self.assertIn('Forwarded message:', self.said[-1])
        self.assertEqual([p.text for p in self.reopened(draft).paragraphs],
                         ['See you soon.', 'Forwarded message:', 'From: Sam <sam@example.com>', 'Subject: Plans',
                          'Meet at nine.', 'Bring maps.'])

    def test_an_old_draft_already_sent_is_not_sent_again(self):
        self.connect_thunderbird()
        draft = self.draft(provider='gmail')
        draft.last_accepted_hash = draft.send_hash(); draft.save()
        companion.handle('send email', 'email_draft', typed=True)
        self.assertIn('This same draft was already attempted.', self.said[-1])
        self.assertEqual(self.reopened(draft).provider, 'gmail')
        companion.handle('yes', 'email_draft')
        self.submit.assert_not_called()

    def test_without_an_account_the_draft_is_kept_and_nothing_is_sent(self):
        draft = self.draft(provider='outlook'); draft.save()
        companion.handle('send email', 'email_draft', typed=True)
        self.assertEqual(self.said[-1], 'No email account is connected. Say add account to connect one in Thunderbird. '
                                        'The draft is saved locally. It has not been sent.')
        saved = self.reopened(draft)
        self.assertEqual((saved.provider, [p.text for p in saved.paragraphs]), ('outlook', ['See you soon.']))
        companion.handle('yes', 'email_draft')
        self.submit.assert_not_called()

    def test_canceling_an_old_draft_opens_the_chosen_inbox(self):
        self.connect_thunderbird()
        draft = self.draft(provider='gmail')
        self.assertEqual(companion.handle('cancel', 'email_draft'), 'mailbox')
        companion.MailSession.assert_called_once_with('thunderbird', self.folder, progress=companion.mail_progress)

    def test_switch_account_on_an_old_reply_is_allowed(self):
        self.store.save('yahoo', {'address': 'pat@yahoo.com', 'app_password': 'x'})
        draft = self.draft(provider='gmail', response_context={'action': 'reply', 'source_id': 'gmail-id'})
        companion.handle('switch to pat at yahoo dot com', 'email_draft', typed=True)
        self.assertEqual(self.said[-1], 'Switched to Yahoo, pat@yahoo.com.')
        self.assertEqual((draft.provider, draft.response_context), ('yahoo', {}))


if __name__ == '__main__':
    unittest.main()
