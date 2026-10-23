"""Several email accounts: choose one before its folders, switch, and remove one.

Real ProtectedStore files in a temporary folder (sealed as plain JSON, as in
test_thunderbird_mail.plain_store) and the real MailSession; only the mail
server is a stand-in, chosen by the account the store has selected.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import companion
import mail_accounts
import mail_voice
from mail_accounts import AccountError, ProtectedStore
from mail_voice import MailSession
from test_core_email_fixes import CompanionTestCase
from document_editor import Paragraph


def plain_store():
    return [patch.object(mail_accounts.ProtectedStore, '_seal', staticmethod(lambda data: json.dumps(data).encode())),
            patch.object(mail_accounts.ProtectedStore, '_unseal', staticmethod(lambda path: json.loads(path.read_bytes())))]


WILL, SAM, YAHOO, BIRD = (('gmail', 'will@gmail.com'), ('gmail', 'sam@gmail.com'),
                          ('yahoo', 'sam@yahoo.com'), ('thunderbird', 'pat@example.com'))


class FakeMailbox:
    """One mailbox per address: its own folders and one message naming the address."""
    def __init__(self, address):
        self.address = address
    def folders(self):
        user = self.address.split('@')[0].title()
        return [('Inbox', 'inbox'), (user + ' receipts', 'r-' + self.address)]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': folder + '1', 'from': 'friend@example.com', 'subject': 'For ' + self.address + ' in ' + folder.lower()}], None)
    def folder_info(self, ident):
        return {'totalItemCount': 1, 'childFolderCount': 0}


def store_client(self):
    # Which mailbox a session reads depends on the saved selection, as with account_token.
    return FakeMailbox(ProtectedStore(self.folder).load(self.provider)['address'])


class StoreRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for item in plain_store():
            item.start(); self.addCleanup(item.stop)
        self.store = ProtectedStore(self.folder)
        for provider, address in (WILL, SAM, YAHOO, BIRD):
            self.store.save(provider, {'address': address, 'secret': address + '-token'}, select=False)
        self.store.select(*WILL)

    def test_removes_only_that_address_and_moves_the_selection_to_the_next(self):
        files = sorted(p.name for p in self.folder.glob('*.account'))
        self.assertEqual(self.store.remove('GMAIL', 'Will@Gmail.com'), SAM)
        self.assertEqual(self.store.accounts(), [SAM, YAHOO, BIRD])
        self.assertEqual(self.store.selected(), SAM)
        self.assertEqual(self.store.load('gmail')['secret'], 'sam@gmail.com-token')
        self.assertEqual(len(list(self.folder.glob('*.account'))), len(files) - 1)
        self.assertFalse(list(self.folder.glob('*.writing')))

    def test_removing_another_account_keeps_the_selection(self):
        self.assertEqual(self.store.remove(*YAHOO), WILL)
        self.assertEqual(self.store.accounts(), [WILL, SAM, BIRD])
        self.assertEqual(self.store.selected(), WILL)

    def test_last_account_in_the_list_selects_the_one_before(self):
        self.store.select(*BIRD)
        self.assertEqual(self.store.remove(*BIRD), YAHOO)
        self.assertEqual(self.store.selected(), YAHOO)

    def test_removing_every_account_leaves_nothing_selected(self):
        for account in (WILL, SAM, YAHOO):
            self.store.remove(*account)
        self.assertIsNone(self.store.remove(*BIRD))
        self.assertEqual(self.store.accounts(), [])
        self.assertIsNone(self.store.selected())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_old_single_file_is_removed_only_when_it_holds_that_address(self):
        legacy = self.folder / 'yahoo.account'
        legacy.write_bytes(json.dumps({'address': 'sam@yahoo.com'}).encode())
        (self.folder / 'outlook.account').write_bytes(json.dumps({'address': 'old@outlook.com'}).encode())
        self.store.remove(*YAHOO)
        self.assertFalse(legacy.exists())
        self.assertNotIn(YAHOO, self.store.accounts())
        self.assertIn(('outlook', 'old@outlook.com'), self.store.accounts())

    def test_unknown_account_changes_nothing(self):
        before = sorted(p.read_bytes() for p in self.folder.iterdir())
        with self.assertRaises(AccountError):
            self.store.remove('gmail', 'nobody@gmail.com')
        self.assertEqual(sorted(p.read_bytes() for p in self.folder.iterdir()), before)


class AccountScreenTestCase(CompanionTestCase):
    ACCOUNTS = (WILL, SAM, YAHOO, BIRD)

    def setUp(self):
        super().setUp()
        for item in plain_store() + [
                patch.object(mail_voice.MailSession, 'client', store_client),
                patch.object(companion, 'finish_mail_announcement', lambda *a, **k: None),
                patch.object(companion, 'focus_email_entry', lambda *a, **k: None, create=True),
                patch.object(companion, 'sync_app_context', lambda *a, **k: None),
                patch.object(companion, 'interrupt_speech', lambda *a, **k: None),
                patch.object(companion, 'ACCOUNT_PICKER', None),
                patch.object(companion, 'FOLDER_ACCOUNT_RETURN', None),
                patch.object(companion, 'NAMED_LIST', None),
                patch.object(companion, 'account_setup', False),
                patch.object(companion, 'CONFIRM_CHOICE', 'yes'),
                patch.object(companion, 'SYNTH_PICK_INDEX', None),
                patch.object(companion, 'VOICE_PICK_INDEX', None)]:
            item.start(); self.addCleanup(item.stop)
        companion.screen_menu().close()
        self.addCleanup(companion.screen_menu().close)
        self.store = ProtectedStore(self.folder)
        for provider, address in self.ACCOUNTS:
            self.store.save(provider, {'address': address}, select=False)
        self.store.select(*self.ACCOUNTS[0])

    def open_inbox(self):
        companion.mail_session = MailSession(self.store.selected()[0], self.folder)
        companion.mail_session.process('open inbox')
        return companion.mail_session

    def key(self, key, mode='mailbox'):
        request = companion.navigation_key(key, mode)
        return mode if request is None else companion.handle(request, mode, typed=True)

    def last(self):
        return self.said[-1]


class FolderAccountTests(AccountScreenTestCase):
    def test_control_y_lists_accounts_then_that_accounts_folders(self):
        self.open_inbox()
        self.assertEqual(companion.navigation_key('Ctrl+Y', 'mailbox'), 'go to folder')
        self.key('Ctrl+Y')
        self.assertEqual(self.last(), 'Choose an account. Gmail, will@gmail.com, current, 1 of 4. '
                                      'Arrows move, Enter chooses, and Escape cancels. You can also say the account number or name.')
        self.key('Down')
        self.assertEqual(self.last(), 'Gmail, sam@gmail.com, 2 of 4.')
        self.key('End')
        self.assertEqual(self.last(), 'Thunderbird, pat@example.com, 4 of 4.')
        self.key('Down')
        self.assertEqual(self.last(), 'End of list. Thunderbird, pat@example.com, 4 of 4.')
        self.key('Home')
        self.key('Down')
        self.key('Enter')
        self.assertEqual(self.store.selected(), SAM)
        self.assertTrue(self.last().startswith('Switched to Gmail, sam@gmail.com. Found 2 folders. Folder 1: Inbox'), self.last())
        self.assertIsNone(companion.ACCOUNT_PICKER)
        self.key('Down')
        self.assertIn('Sam receipts', self.last())
        self.key('Enter')
        self.assertIn('For sam@gmail.com in r-sam@gmail.com', self.last())
        self.assertEqual(companion.mail_session.provider, 'gmail')
        self.assertIsNone(companion.mail_session.folder_picker)

    def test_choosing_the_current_account_keeps_its_session(self):
        session = self.open_inbox()
        self.key('Ctrl+Y')
        self.key('Enter')
        self.assertIs(companion.mail_session, session)
        self.assertTrue(self.last().startswith('Found 2 folders. Folder 1: Inbox'), self.last())

    def test_escape_goes_back_one_level_then_cancels_and_keeps_the_first_account(self):
        session = self.open_inbox()
        self.key('Ctrl+Y')
        self.key('Down')
        self.key('Down')
        self.key('Enter')
        self.assertEqual(self.store.selected(), YAHOO)
        self.assertIsNotNone(companion.mail_session.folder_picker)
        self.assertEqual(companion.navigation_key('Escape', 'mailbox'), 'cancel')
        self.key('Escape')
        self.assertTrue(self.last().startswith('Choose an account. Yahoo, sam@yahoo.com, current, 1 of 4.'), self.last())
        self.assertIsNone(companion.mail_session.folder_picker)
        self.key('Escape')
        self.assertEqual(self.last(), 'Folder list canceled. Back to Gmail, will@gmail.com, Inbox.')
        self.assertIsNone(companion.ACCOUNT_PICKER)
        self.assertEqual(self.store.selected(), WILL)
        self.assertIs(companion.mail_session, session)

    def test_escape_in_the_account_list_cancels(self):
        self.open_inbox()
        self.key('Ctrl+Y')
        self.key('Escape')
        self.assertEqual(self.last(), 'Folder list canceled.')
        self.assertIsNone(companion.ACCOUNT_PICKER)
        self.assertEqual(self.store.selected(), WILL)

    def test_voice_choices_tolerate_speech_punctuation(self):
        self.open_inbox()
        companion.handle('Go to folder.', 'mailbox')
        self.assertTrue(self.last().startswith('Choose an account.'))
        companion.handle('Gmail.', 'mailbox')
        self.assertEqual(self.last(), 'There are 2 Gmail accounts. Say the account number or the address.')
        self.assertIsNotNone(companion.ACCOUNT_PICKER)
        companion.handle('Banana bread', 'mailbox')
        self.assertEqual(self.last(), 'Say next or previous, then that one. Or say the account number or name. Say cancel to close the list.')
        companion.handle('next', 'mailbox')
        self.assertEqual(self.last(), 'Gmail, sam@gmail.com, 2 of 4.')
        companion.handle('That one.', 'mailbox')
        self.assertEqual(self.store.selected(), SAM)
        companion.handle('cancel', 'mailbox')
        self.assertTrue(self.last().startswith('Choose an account. Gmail, sam@gmail.com, current, 1 of 4.'))
        companion.handle('Yahoo.', 'mailbox')
        self.assertEqual(self.store.selected(), YAHOO)
        companion.handle('go back', 'mailbox')
        companion.handle('Sam at Gmail, dot com.', 'mailbox')
        self.assertEqual(self.store.selected(), SAM)
        companion.handle('back', 'mailbox')
        companion.handle('Number four.', 'mailbox')
        self.assertEqual(self.store.selected(), BIRD)
        companion.handle('cancel', 'mailbox')
        # The current account is listed first: Thunderbird, then Will, Sam and Yahoo.
        companion.handle('the third one', 'mailbox')
        self.assertEqual(self.store.selected(), SAM)

    def test_named_folder_stays_in_the_current_account(self):
        self.open_inbox()
        companion.handle('go to folder Will receipts', 'mailbox')
        self.assertIsNone(companion.ACCOUNT_PICKER)
        self.assertIn('For will@gmail.com in r-will@gmail.com', self.last())

    def test_folder_menu_item_lists_accounts(self):
        self.open_inbox()
        self.key('Alt')
        companion.handle('3', 'mailbox')  # Go to folder group
        self.key('End')
        for _ in range(3):
            self.key('Up')
        self.assertIn('Choose a folder', self.last())
        self.key('Enter')
        self.assertTrue(self.last().startswith('Choose an account.'), self.last())
        self.assertIn('Close this list first', companion.screen_menu().available('mailbox'))


class SingleAccountTests(AccountScreenTestCase):
    ACCOUNTS = (WILL,)

    def test_one_account_goes_straight_to_folders(self):
        self.open_inbox()
        self.key('Ctrl+Y')
        self.assertTrue(self.last().startswith('Found 2 folders. Folder 1: Inbox'), self.last())
        self.assertIsNone(companion.ACCOUNT_PICKER)
        companion.handle('go to folder', 'mailbox')
        companion.handle('cancel', 'mailbox')
        self.assertEqual(self.last(), 'Folder choice canceled. Nothing was changed.')

    def test_escape_cancels_the_folder_list(self):
        self.open_inbox()
        self.key('Ctrl+Y')
        self.key('Escape')
        self.assertEqual(self.last(), 'Folder choice canceled. Nothing was changed.')
        self.assertIsNone(companion.mail_session.folder_picker)

    def test_switch_account_with_one_account(self):
        companion.handle('switch account', 'awake')
        self.assertEqual(self.last(), 'Only one email account is connected: Gmail, will@gmail.com. Say add account to connect another.')


class SwitchAccountTests(AccountScreenTestCase):
    def test_switch_account_from_the_main_menu_opens_that_inbox(self):
        self.assertEqual(companion.handle('Switch account.', 'awake'), 'awake')
        self.assertTrue(self.last().startswith('Choose an account to open. Gmail, will@gmail.com, current, 1 of 4.'))
        self.assertEqual(self.key('Down', 'awake'), 'awake')
        self.assertEqual(self.key('Down', 'awake'), 'awake')
        self.assertEqual(self.key('Enter', 'awake'), 'mailbox')
        self.assertEqual(self.store.selected(), YAHOO)
        self.assertIn('Switched to Yahoo, sam@yahoo.com', self.said[-1])
        self.assertIn('For sam@yahoo.com in inbox', ' '.join(self.said))

    def test_email_menu_switch_and_remove_items(self):
        from screen_menus import MENUS
        accounts = dict(MENUS['mailbox'][1])['Accounts']
        self.assertEqual([(label, command) for label, _, command in accounts],
                         [('Add account', 'add account'), ('Switch account', 'switch account'),
                          ('Which account', 'which email account'), ('Remove account', 'remove email account')])
        self.open_inbox()
        companion.handle('email menu', 'mailbox')
        companion.handle('6', 'mailbox')
        companion.handle('2', 'mailbox')
        self.assertTrue(self.last().startswith('Choose an account to open.'), self.last())
        self.key('Down')
        self.key('Enter')
        self.assertEqual(self.store.selected(), SAM)
        self.assertIn('For sam@gmail.com in inbox', self.last())

    def test_cancel_switch(self):
        companion.handle('switch account', 'awake')
        self.key('Escape', 'awake')
        self.assertEqual(self.last(), 'Account list closed. Nothing was changed.')
        self.assertEqual(self.store.selected(), WILL)


class RemoveAccountTests(AccountScreenTestCase):
    def test_no_keeps_the_account(self):
        companion.handle('Remove email account.', 'awake')
        self.assertTrue(self.last().startswith('Choose the account to remove. Gmail, will@gmail.com, current, 1 of 4.'))
        companion.handle('Yahoo', 'awake')
        self.assertEqual(self.last(), 'Remove the Yahoo account sam@yahoo.com from Voice Companion? '
                                      'Your email stays with Yahoo. Say yes or no.')
        self.assertEqual(companion.CONFIRM_CHOICE, 'no')
        companion.handle('what', 'awake')
        self.assertEqual(self.last(), 'Remove the Yahoo account sam@yahoo.com from Voice Companion? '
                                      'Your email stays with Yahoo. Say yes or no.')
        companion.handle('No, thanks.', 'awake')
        self.assertEqual(self.last(), 'Nothing was removed.')
        self.assertIn(YAHOO, self.store.accounts())
        self.assertIsNone(companion.ACCOUNT_PICKER)

    def test_enter_on_the_question_is_no_unless_yes_is_chosen(self):
        companion.handle('delete email account', 'awake')
        self.key('Down', 'awake')
        self.key('Down', 'awake')
        self.key('Enter', 'awake')
        self.assertIn('Say yes or no.', self.last())
        self.key('Enter', 'awake')
        self.assertEqual(self.last(), 'Nothing was removed.')
        self.assertIn(YAHOO, self.store.accounts())
        companion.handle('remove account', 'awake')
        companion.handle('3', 'awake')
        self.key('Up', 'awake')
        self.key('Enter', 'awake')
        self.assertNotIn(YAHOO, self.store.accounts())

    def test_yes_removes_an_account_that_is_not_selected(self):
        companion.handle('disconnect account', 'awake')
        companion.handle('sam at yahoo dot com', 'awake')
        companion.handle('Yes, please.', 'awake')
        self.assertEqual(self.last(), 'The Yahoo account sam@yahoo.com is removed from Voice Companion. '
                                      'Your email is still with Yahoo. The app password still works until you delete it in Yahoo Account security. '
                                      'You are still using the Gmail account will@gmail.com.')
        self.assertEqual(self.store.accounts(), [WILL, SAM, BIRD])
        self.assertEqual(self.store.selected(), WILL)

    def test_removing_the_open_account_moves_to_the_next_one(self):
        self.open_inbox()
        self.assertEqual(companion.handle('remove email account', 'mailbox', typed=True), 'mailbox')
        companion.handle('that one', 'mailbox')
        self.assertIn('Remove the Gmail account will@gmail.com from Voice Companion? Your email stays with Gmail.', self.last())
        self.assertEqual(companion.handle('yes', 'mailbox'), 'mailbox')
        said = ' '.join(self.said[-2:])
        self.assertIn('The Gmail account will@gmail.com is removed from Voice Companion.', said)
        self.assertIn('Google Account', said)
        self.assertIn('Now using the Gmail account sam@gmail.com.', said)
        self.assertIn('For sam@gmail.com in inbox', said)
        self.assertEqual(self.store.selected(), SAM)
        self.assertEqual(self.store.accounts(), [SAM, YAHOO, BIRD])

    def test_thunderbird_is_only_disconnected(self):
        companion.handle('remove account', 'awake')
        companion.handle('thunderbird', 'awake')
        self.assertIn('Your email stays in Thunderbird.', self.last())
        companion.handle('yes', 'awake')
        self.assertIn('Thunderbird and its email are not changed.', self.last())
        self.assertNotIn(BIRD, self.store.accounts())

    def test_alt_menu_remove_account(self):
        self.open_inbox()
        companion.handle('email menu', 'mailbox')
        companion.handle('6', 'mailbox')
        companion.handle('4', 'mailbox')
        self.assertTrue(self.last().startswith('Choose the account to remove.'), self.last())


class LastAccountTests(AccountScreenTestCase):
    ACCOUNTS = (('outlook', 'me@outlook.com'),)

    def test_last_account_asks_at_once_and_leaves_email(self):
        self.open_inbox()
        companion.handle('remove email account', 'mailbox', typed=True)
        self.assertEqual(self.last(), 'Remove the Outlook account me@outlook.com from Voice Companion? '
                                      'Your email stays with Outlook. Say yes or no.')
        self.assertEqual(companion.handle('yes', 'mailbox'), 'awake')
        said = ' '.join(self.said)
        self.assertIn('The Outlook account me@outlook.com is removed from Voice Companion.', said)
        self.assertIn('Microsoft account', said)
        self.assertIn('No email account is connected now. Say add account to connect one.', said)
        self.assertIsNone(companion.mail_session)
        self.assertEqual(self.store.accounts(), [])

    def test_nothing_to_remove(self):
        self.store.remove('outlook', 'me@outlook.com')
        companion.handle('remove email account', 'awake')
        self.assertEqual(self.last(), 'No email account is connected. Say add account.')
        self.assertIsNone(companion.ACCOUNT_PICKER)


class WritingComesFirstTests(AccountScreenTestCase):
    def test_account_phrases_are_text_while_writing(self):
        for phrase in ('Remove account', 'Remove email account.', 'Switch account', 'Disconnect account'):
            document = self.open_document()
            self.assertEqual(companion.handle(phrase, 'document'), 'document')
            self.assertEqual(companion.document_text(document), phrase)
            self.assertIsNone(companion.ACCOUNT_PICKER)
            draft = self.draft()
            draft.dictating = True
            self.assertEqual(companion.handle(phrase, 'email_draft'), 'email_draft')
            self.assertIn(phrase.rstrip('.'), ' '.join(p.text for p in draft.paragraphs))
            self.assertIsNone(companion.ACCOUNT_PICKER)
        # "Delete ..." while writing is the editor's delete-words command, never account removal.
        self.open_document()
        companion.handle('Delete email account.', 'document')
        self.assertIsNone(companion.ACCOUNT_PICKER)
        self.assertEqual(len(self.store.accounts()), 4)

    def test_typed_command_in_a_document_still_works(self):
        self.open_document()
        companion.handle('remove email account', 'document', typed=True)
        self.assertTrue(self.last().startswith('Choose the account to remove.'), self.last())
        companion.handle('cancel', 'document', typed=True)
        self.assertEqual(self.last(), 'Nothing was removed.')


if __name__ == '__main__':
    unittest.main()
