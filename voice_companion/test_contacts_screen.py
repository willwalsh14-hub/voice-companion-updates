"""The Contacts screen, end to end.

The real add-on (background.js and pim.js) runs in Node against the simulated
Thunderbird (fake-messenger.cjs), through the real host code and relay, and
Companion's real command routing and keys drive the screen.
"""
import io
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

import companion as app
import contacts_book
import contacts_screen
import mail_accounts
import regular_browser
import thunderbird_bridge
import thunderbird_mailbox
import thunderbird_pim
from address_book import AddressBook
from browser_bridge_host import read_message
from field_selection import FieldSelection
from reading_navigation import ReadingCursor
from test_thunderbird_mail import plain_store

HERE = Path(__file__).parent


class CompanionHarness(unittest.TestCase):
    """Companion's state, patched like the other screen tests, with speech recorded."""
    thunderbird = True        # start the simulated Thunderbird
    old_addon = False         # a copy of the add-on from before contacts and calendars
    thunderbird_env = {}      # other switches for fake-thunderbird.cjs

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'VOICE_PICK_ORIGINAL': None,
                    'help_session': None, 'tutorial_session': None, 'MEDIA_HUB': None, 'note_buffer': [],
                    'note_selection': FieldSelection(), 'note_reading': ReadingCursor(), 'OPEN_NOTE': None,
                    'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'PREFERENCES': dict(app.PREFERENCES), 'TASK_NAV': None,
                    'SCREEN_MENU': None, 'SLEEP_RETURN_MODE': 'awake', 'SEARCH_RETURN_MODE': 'awake',
                    'FILE_READER': None, 'EMAIL_LINK_RECIPIENT': None, 'THUNDERBIRD_SETUP': None, 'ACCOUNT_ENTRY': None,
                    'ACCOUNT_PICKER': None}
        for name, value in defaults.items():
            self.stack.enter_context(mock.patch.object(app, name, value, create=(name == 'OPEN_NOTE')))
        self.speech = self.stack.enter_context(mock.patch.object(app, 'speak'))
        self.stack.enter_context(mock.patch.object(app, 'speak_prompt', side_effect=lambda text, *a, **k: app.speak(text)))
        self.stack.enter_context(mock.patch.object(app, 'sync_app_context'))
        self.stack.enter_context(mock.patch.object(app, 'focus_email_entry', create=True))
        for patcher in plain_store():
            self.stack.enter_context(patcher)
        # Never start a real Thunderbird.
        self.stack.enter_context(mock.patch.object(thunderbird_mailbox, 'STARTER', None))
        self.stack.enter_context(mock.patch.dict(contacts_book.CACHE, {'at': None, 'contacts': [], 'books': []}))
        contacts_screen.reset()
        self.addCleanup(contacts_screen.reset)
        self.relay = None
        if self.thunderbird:
            self.start_thunderbird()
            mail_accounts.connect('thunderbird', self.root)
        else:
            self.stack.enter_context(mock.patch.object(thunderbird_bridge, '_RELAY', None))

    def start_thunderbird(self):
        if not shutil.which('node'):
            self.skipTest('Node.js runs the simulated Thunderbird')
        if os.name == 'nt':
            endpoint, family = None, 'AF_PIPE'
        else:
            endpoint, family = str(self.root / 'mail.sock'), 'AF_UNIX'
        self.relay = thunderbird_bridge.MailRelay(self.root, endpoint, family)
        self.addCleanup(self.relay.close)
        self.stack.enter_context(mock.patch.object(thunderbird_bridge, '_RELAY', self.relay))
        self.stack.enter_context(mock.patch.object(regular_browser, 'data_folder', lambda: self.root))
        env = dict(os.environ)
        if self.old_addon:
            env['FAKE_THUNDERBIRD_OLD_ADDON'] = '1'
        env.update(self.thunderbird_env)
        process = subprocess.Popen(['node', str(HERE / 'thunderbird_mail_extension' / 'fake-thunderbird.cjs')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        def stop():
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.addCleanup(stop)
        first = read_message(process.stdout)
        threading.Thread(target=thunderbird_bridge.native_stream,
                         args=(first, process.stdout, process.stdin, endpoint, family), daemon=True).start()
        self.assertTrue(self.relay.connected.wait(10), 'Thunderbird did not connect')

    def said(self):
        return str(self.speech.call_args.args[0])

    def everything_said(self):
        return ' '.join(str(call.args[0]) for call in self.speech.call_args_list)

    def say(self, text, mode):
        return app.handle(text, mode)

    def press(self, key, mode):
        request = app.navigation_key(key, mode)
        return app.handle_keyboard(request, mode) if request else mode


class ContactsScreenTests(CompanionHarness):
    def open(self):
        mode = self.say('Contacts', 'awake')
        self.assertEqual(mode, 'contacts')
        return mode

    def test_main_menu_has_contacts_and_calendar_after_email(self):
        names = [name for name, _ in app.MAIN_MENU_CHOICES]
        self.assertEqual(names[names.index('Email') + 1:names.index('Email') + 3], ['Contacts', 'Calendar'])
        self.assertEqual(len(names), 15)

    def test_opens_on_a_sorted_merged_list(self):
        # A contact saved here and in Thunderbird is said once; one only here is added.
        AddressBook(self.root, 'me@gmail.com').save('Johnny', 'JOHN@example.com')
        AddressBook(self.root, 'me@gmail.com').save('Zed Local', 'zed@example.com', '555-0177')
        self.open()
        self.assertEqual(self.said(), 'Contacts. 6 contacts. Ann Brown, ann@example.com, 1 of 6.')
        self.assertEqual([c.name for c in contacts_screen.CONTACTS],
                         ['Ann Brown', 'Help Desk', 'John Smith', 'Mary Jones', 'Plumber', 'Zed Local'])
        self.assertEqual(contacts_screen.CONTACTS[-1].source, 'local')

    def test_arrows_letters_home_and_end_with_list_edges(self):
        mode = self.open()
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Start of list. Ann Brown, ann@example.com, 1 of 5.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Help Desk, help@work.example, 555-0111, 2 of 5.')
        self.press('Letter:m', mode)
        self.assertEqual(self.said(), 'Mary Jones, mary@example.com, 3 of 5.'.replace('3 of 5', '4 of 5'))
        self.press('Letter:q', mode)
        self.assertEqual(self.said(), 'No contact starts with Q.')
        self.press('End', mode)
        self.assertEqual(self.said(), 'Plumber, 555-0199, 5 of 5.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'End of list. Plumber, 555-0199, 5 of 5.')
        self.press('Home', mode)
        self.assertEqual(self.said(), 'Ann Brown, ann@example.com, 1 of 5.')
        # The same by voice, with recognizer punctuation.
        self.say('Next contact.', mode)
        self.assertEqual(self.said(), 'Help Desk, help@work.example, 555-0111, 2 of 5.')
        self.say('Last contact', mode)
        self.assertEqual(contacts_screen.current().name, 'Plumber')

    def test_enter_offers_email_this_person_first_then_the_details(self):
        mode = self.open()
        self.press('Letter:j', mode)
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'John Smith. Email this person, 1 of 7. Escape goes back to the list.')
        labels = [label for label, _ in contacts_screen.DETAILS]
        self.assertEqual(labels, ['Email this person', 'Name: John Smith', 'Email: john@example.com', 'Phone: 555-0100',
                                  'Saved in: Thunderbird address book me@gmail.com', 'Edit contact', 'Delete contact'])
        self.press('Down', mode); self.press('Down', mode); self.press('Down', mode)
        self.assertEqual(self.said(), 'Phone: 555-0100, 4 of 7.')
        self.press('Escape', mode)
        self.assertEqual(self.said(), 'Contacts. John Smith, john@example.com, 555-0100, 3 of 5.')
        self.press('Enter', mode)
        mode = self.press('Enter', mode)
        self.assertEqual(mode, 'email_draft')
        self.assertEqual((app.email_draft.recipient, app.email_draft.compose_step), ('john@example.com', 'subject'))
        self.assertIn('Email to John Smith, john@example.com.', self.everything_said())

    def test_control_e_emails_at_once_and_asks_which_address_when_there_are_several(self):
        mode = self.open()
        self.press('Letter:m', mode)
        self.assertEqual(app.navigation_key('Ctrl+E', mode), 'email contact')
        self.press('Ctrl+E', mode)
        self.assertIn('Which email address for Mary Jones?', self.said())
        self.assertEqual([label for label, _ in app.NAMED_LIST['items']], ['mary@example.com', 'mary.work@example.org'])
        self.press('Down', mode)
        mode = self.press('Enter', mode)
        self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'mary.work@example.org'))

    def test_a_contact_with_no_email_says_so_plainly(self):
        mode = self.open()
        self.press('Letter:p', mode)
        self.press('Ctrl+E', mode)
        self.assertEqual(self.said(), 'Plumber has no email address saved.')
        self.say('call details', mode)
        self.assertEqual(self.said(), 'Plumber, phone 555-0199.')
        self.press('Enter', mode)
        self.assertEqual([label for label, _ in contacts_screen.DETAILS][:2], ['Name: Plumber', 'No email address'])
        self.say('read contact', mode)
        self.assertEqual(self.said(), 'Plumber. No email address. Phone 555-0199. Thunderbird address book Personal Address Book.')

    def test_control_n_adds_into_thunderbird_with_simple_prompts(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+N', mode), 'add contact')
        mode = self.press('Ctrl+N', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'New contact. Type or say the name, then press Enter. Escape cancels.'))
        mode = app.handle('Pat Doe.', mode)
        self.assertEqual(mode, 'task_input')
        self.assertIn('Email address for Pat Doe?', self.said())
        mode = app.handle('pat at example dot com.', mode)
        self.assertIn('Phone number?', self.said())
        # Enter with nothing typed leaves the phone out.
        self.assertEqual(app.navigation_key('Enter', mode), '@task:skip')
        mode = self.press('Enter', mode)
        self.assertEqual(mode, 'contacts')
        self.assertEqual(self.said(), 'Saved Pat Doe in Thunderbird, me@gmail.com. Pat Doe, pat@example.com, 5 of 6.')
        # The address book Thunderbird links to the selected account (named for its address) gets it.
        pat = contacts_screen.current()
        self.assertEqual((pat.source, pat.emails, pat.book), ('thunderbird', ['pat@example.com'], 'me@gmail.com'))

    def test_a_slow_thunderbird_is_never_called_not_saved_or_saved_twice(self):
        # Thunderbird got the new contact but answered too late. It saved it, so "The contact was
        # not saved." was wrong, and nothing is saved on this computer as well.
        from test_calendar_screen import slow_thunderbird
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('Pat Doe.', mode)
        mode = app.handle('pat at example dot com.', mode)
        with slow_thunderbird('addContact'):
            mode = self.press('Enter', mode)
        self.assertEqual((mode, self.said()), ('contacts', thunderbird_pim.UNSURE))
        self.assertIsNone(AddressBook(self.root, 'me@gmail.com').get('Pat Doe'))
        self.assertIn('Pat Doe', [c.name for c in contacts_screen.BOOK.load()])

    def test_adding_needs_a_name_and_an_email_or_phone_and_escape_cancels(self):
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('Sam', mode)
        mode = app.handle('not an address', mode)
        self.assertIn('I could not understand one complete email address.', self.said())
        self.assertEqual(mode, 'task_input')
        mode = app.handle('skip', mode)
        mode = app.handle('skip', mode)
        self.assertEqual(mode, 'task_input')
        self.assertIn('A contact needs an email address or a phone number.', self.said())
        mode = self.press('Escape', mode)
        self.assertEqual((mode, self.said()), ('contacts', 'Canceled. Back to contacts.'))
        self.assertNotIn('Sam', [c.name for c in contacts_screen.CONTACTS])

    def test_a_change_thunderbird_could_not_make_says_so_and_what_to_do(self):
        # Thunderbird's general answer ("could not complete that request") said neither that nothing
        # changed nor what to do next.
        from mailbox_access import MailboxError
        real = thunderbird_mailbox.call

        def failing(method, **params):
            if method in ('deleteContact', 'updateContact', 'addContact'):
                raise MailboxError('Thunderbird could not complete that request.')
            return real(method, **params)
        mode = self.open()
        self.press('Letter:p', mode)
        with mock.patch.object(thunderbird_mailbox, 'call', failing):
            self.press('Delete', mode)
            mode = app.handle('yes', mode)
            self.assertEqual(self.said(), 'Thunderbird could not complete that request. Close Thunderbird, then try again. '
                                          'Plumber was not deleted.')
            self.assertEqual(contacts_screen.current().name, 'Plumber')
            mode = self.press('Ctrl+N', mode)
            mode = app.handle('Pat Doe', mode)
            mode = app.handle('skip', mode)
            mode = app.handle('555-0100', mode)
            self.assertEqual(self.said(), 'Thunderbird could not complete that request. Close Thunderbird, then try again. '
                                          'The contact was not saved.')

    def test_damaged_answers_from_thunderbird_never_stop_the_screen(self):
        # An answer with missing or wrong parts (an add-on from another version, a damaged profile).
        damaged = {'contacts': [{'id': 'c1', 'name': 'Bo Lee', 'emails': 'bo@example.com', 'phones': None, 'bookId': 'b'},
                                'not a contact', {'id': 'c2', 'emails': [None, 'kim@example.com']}],
                   'books': None, 'failed': 0, 'failedBooks': 'b'}
        with mock.patch.object(thunderbird_mailbox, 'call', lambda method, **params: damaged):
            mode = self.open()
        self.assertEqual(mode, 'contacts')
        self.assertEqual([(c.name, c.emails) for c in contacts_screen.CONTACTS],
                         [('Bo Lee', ['bo@example.com']), ('kim@example.com', ['kim@example.com'])])

    def test_delete_asks_first_and_read_only_books_are_protected(self):
        mode = self.open()
        self.press('Letter:p', mode)
        self.press('Delete', mode)
        self.assertEqual(self.said(), 'Delete Plumber from your contacts? It is also removed from Thunderbird. Say Yes or No. Escape keeps it.')
        self.assertEqual(app.app_context(mode)['confirmation'], 'no')
        self.press('Escape', mode)
        self.assertTrue(self.said().startswith('Not deleted.'))
        self.press('Delete', mode)
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Yes.')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Deleted Plumber. Mary Jones, mary@example.com, 4 of 4.')
        # From the details view: Delete contact is the last line; afterwards the list is back.
        self.press('Letter:a', mode); self.press('Enter', mode); self.press('End', mode)
        self.assertEqual(self.said(), 'Delete contact, 6 of 6.')
        self.press('Enter', mode); self.press('Up', mode); self.press('Enter', mode)
        self.assertEqual(self.said(), 'Deleted Ann Brown. Help Desk, help@work.example, 555-0111, 1 of 3.')
        self.assertEqual(contacts_screen.VIEW, 'list')
        self.say('first contact', mode)
        self.press('Delete', mode)
        self.assertEqual(self.said(), 'Help Desk is in a Thunderbird address book that cannot be changed, so it cannot be deleted.')

    def test_where_am_i_and_repeat_keep_your_place_and_list_words_move(self):
        mode = self.open()
        self.press('Down', mode)
        for words in ('repeat', 'Say that again.', 'where am i'):
            with self.subTest(words=words):
                self.say(words, mode)
                self.assertEqual(self.said(), 'Contacts. 5 contacts. Help Desk, help@work.example, 555-0111, 2 of 5.')
        self.say('next item', mode)
        self.assertEqual(self.said(), 'John Smith, john@example.com, 555-0100, 3 of 5.')
        self.say('previous item', mode)
        self.assertEqual(self.said(), 'Help Desk, help@work.example, 555-0111, 2 of 5.')
        # In a contact's details, Where am I says them and stays there.
        self.press('Enter', mode)
        self.press('Down', mode)
        self.say('Where am I?', mode)
        self.assertEqual(self.said(), 'Contacts. Help Desk. Name: Help Desk, 2 of 5.')
        self.assertEqual(contacts_screen.VIEW, 'details')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Email: help@work.example, 3 of 5.')

    def test_a_contact_removed_in_thunderbird_meanwhile_leaves_the_list(self):
        # Deleted in Thunderbird while Contacts was open: the list is read again, so Down and Up
        # never reach a contact that is gone.
        mode = self.open()
        self.press('Letter:j', mode)
        contacts_screen.BOOK.delete(contacts_screen.current())
        self.press('Delete', mode); self.press('Up', mode); self.press('Enter', mode)
        self.assertEqual(self.said(), 'That contact is no longer in Thunderbird. Mary Jones, mary@example.com, 3 of 4.')
        self.assertNotIn('John Smith', [c.name for c in contacts_screen.CONTACTS])
        # Editing one that is gone: the same.
        contacts_screen.BOOK.delete(contacts_screen.current())
        self.say('rename contact to Mary Smith', mode)
        self.say('yes', mode)
        self.assertEqual(self.said(), 'That contact is no longer in Thunderbird. The change was not saved. Plumber, 555-0199, 3 of 3.')
        self.assertNotIn('Mary Jones', [c.name for c in contacts_screen.CONTACTS])

    def test_f2_edits_a_contact_with_a_question(self):
        mode = self.open()
        self.press('Letter:j', mode)
        self.assertEqual(app.navigation_key('F2', mode), 'edit contact')
        self.press('F2', mode)
        self.assertEqual(self.said(), 'Edit John Smith. What do you want to change? Name: John Smith, 1 of 3. '
                                      'Use the arrows and Enter, or say what to change. Escape cancels.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Email: john@example.com, 2 of 3.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Phone: 555-0100, 3 of 3.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'End of list.')
        mode = self.press('Enter', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'New phone number for John Smith? Type or say it, then press Enter. '
                                                             'To remove it, press Enter or say none. Escape cancels.'))
        mode = app.handle('555-0200', mode)
        self.assertEqual((mode, self.said()), ('contacts', 'Change the phone number for John Smith to 555-0200? '
                                                           'Say Yes to save or No to cancel.'))
        self.assertEqual(app.app_context(mode)['confirmation'], 'yes')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Saved John Smith. John Smith, john@example.com, 555-0200, 3 of 5.')
        # By voice, in one step, with recognizer punctuation.
        app.handle('Rename contact to John Smyth.', mode)
        self.assertEqual(self.said(), 'Rename John Smith to John Smyth? Say Yes to save or No to cancel.')
        app.handle('Yes.', mode)
        self.assertEqual(self.said(), 'Saved John Smyth. John Smyth, john@example.com, 555-0200, 3 of 5.')
        app.handle('change the email to john at new dot com', mode)
        self.assertEqual(self.said(), 'Change the email address for John Smyth to john@new.com? Say Yes to save or No to cancel.')
        app.handle('no', mode)
        self.assertEqual(self.said(), 'Not changed. John Smyth, john@example.com, 555-0200, 3 of 5.')
        # The details have Edit contact too; it is the same list.
        self.press('Enter', mode)
        self.assertIn(('Edit contact', '@contacts:edit'), contacts_screen.DETAILS)
        self.say('edit contact', mode)
        self.press('Escape', mode)
        self.assertEqual(self.said(), 'Not changed. John Smyth, john@example.com, 555-0200, 3 of 5.')
        self.assertEqual(contacts_screen.VIEW, 'list')

    def test_editing_keeps_an_email_or_phone_and_refuses_read_only_books(self):
        mode = self.open()
        self.press('Letter:p', mode)
        mode = app.handle('change the phone number', mode)
        mode = self.press('Enter', mode)        # nothing typed: remove it
        self.assertEqual((mode, self.said()), ('task_input', 'A contact needs an email address or a phone number. '
                                                             'Type a phone number, or press Escape to cancel.'))
        mode = self.press('Escape', mode)
        self.assertEqual(mode, 'contacts')
        self.assertEqual(contacts_screen.current().phones, ['555-0199'])
        self.press('Letter:j', mode)
        mode = app.handle('change the email', mode)
        mode = app.handle('not an address', mode)
        self.assertEqual(mode, 'task_input')
        self.assertTrue(self.said().startswith('I could not understand one complete email address.'))
        self.assertTrue(self.said().endswith('Type it again, or press Escape to cancel.'))
        mode = app.handle('none', mode)
        self.assertEqual(self.said(), 'Remove the email address john@example.com from John Smith? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved John Smith. John Smith, 555-0100, 3 of 5.')
        # The same name again changes nothing.
        app.handle('rename contact to John Smith', mode)
        self.assertEqual(self.said(), 'That is the same as before, so nothing was changed. John Smith, 555-0100, 3 of 5.')
        app.handle('first contact', mode)
        app.handle('next contact', mode)
        self.press('F2', mode)
        self.assertEqual(self.said(), 'Help Desk is in a Thunderbird address book that cannot be changed, so it cannot be edited.')
        self.assertIsNone(contacts_screen.PENDING)

    def test_find_by_control_f_and_by_voice(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+F', mode), '@prompt:find contact {value}')
        mode = self.press('Ctrl+F', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'Find contact. Type or say a name, then press Enter. Escape cancels.'))
        mode = app.handle('mary', mode)
        self.assertEqual((mode, contacts_screen.current().name), ('contacts', 'Mary Jones'))
        self.say('Find John.', mode)
        self.assertEqual(contacts_screen.current().name, 'John Smith')
        self.say('find nobody', mode)
        self.assertTrue(self.said().startswith('No contact named nobody.'))

    def test_an_unknown_command_names_what_to_say_including_edit(self):
        mode = self.open()
        app.handle('zebra quantum lantern', mode)
        self.assertEqual(self.said(), 'Contacts. Say next contact, find and a name, email contact, add contact, edit contact, '
                                      'or delete contact. Press F1 for help, or Escape for the main menu.')

    def test_escape_goes_to_the_main_menu(self):
        mode = self.open()
        self.assertEqual(self.press('Escape', mode), 'awake')

    def test_email_by_name_says_why_when_thunderbird_contacts_cannot_be_read(self):
        # Not "No contact named John": John may be in Thunderbird, which did not answer.
        reason = 'Thunderbird did not let Voice Companion read its address books. Close Thunderbird, then try again.'
        with mock.patch.object(thunderbird_pim, 'contacts', side_effect=thunderbird_pim.PimError(reason)):
            for words in ('Email John.', 'Send an email to John Smith'):
                with self.subTest(words=words):
                    contacts_book.CACHE['at'] = None
                    self.assertEqual(app.handle(words, 'awake'), 'awake')
                    self.assertEqual(self.said(), reason)
                    self.assertIsNone(app.email_draft)

    def test_email_by_name_uses_the_last_list_when_thunderbird_does_not_answer(self):
        # The list read a while ago is old, and Thunderbird is slow this time. Before, "Email John
        # Smith" said "No contact named John Smith", though he is in Thunderbird.
        import time
        app.handle('Email John.', 'awake')
        app.email_draft = None
        contacts_book.CACHE['at'] = time.monotonic() - contacts_book.KEEP_SECONDS - 1
        reason = 'Thunderbird did not answer in time. Wait a minute, then try again.'
        with mock.patch.object(thunderbird_pim, 'contacts', side_effect=thunderbird_pim.PimError(reason)):
            mode = app.handle('Send an email to John Smith', 'awake')
            self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'john@example.com'))
            app.email_draft = None
            # A name that is not in the old list: the reason, not "No contact named".
            app.handle('Email Zelda.', 'awake')
            self.assertEqual(self.said(), reason)

    def test_email_a_contact_by_name_from_the_main_menu(self):
        mode = app.handle('Email John.', 'awake')
        self.assertEqual((mode, app.email_draft.recipient, app.email_draft.compose_step), ('email_draft', 'john@example.com', 'subject'))
        app.email_draft = None
        mode = app.handle('Send an email to John Smith', 'awake')
        self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'john@example.com'))
        app.email_draft = None
        mode = app.handle('write to Mary', 'awake')
        self.assertEqual(mode, 'awake')
        self.assertIn('Which email address for Mary Jones?', self.said())
        mode = app.handle('send an email to Bob', 'awake')
        self.assertEqual(self.said(), 'No contact named bob. Say open contacts to hear your contacts, or add contact to save one.')
        # A sentence is not a name.
        app.handle('Write to the council about the road.', 'awake')
        self.assertNotIn('No contact named', self.said())
        mode = app.handle('email the plumber', 'awake')
        self.assertNotEqual(mode, 'email_draft')
        app.handle('email plumber', 'awake')
        self.assertEqual(self.said(), 'Plumber has no email address saved. Open Contacts to add one.')

    def test_two_contacts_with_the_same_first_name_ask_which_one(self):
        AddressBook(self.root, 'me@gmail.com').save('John Brown', 'jb@example.com')
        mode = app.handle('email john', 'awake')
        self.assertEqual(mode, 'awake')
        self.assertTrue(self.said().startswith('Which john? Contacts list. John Brown, jb@example.com'))
        self.press('Down', mode)
        mode = self.press('Enter', mode)
        self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'john@example.com'))

    def test_email_commands_on_the_contacts_screen(self):
        mode = self.open()
        mode = app.handle('email John', mode)
        self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'john@example.com'))

    def test_a_contact_name_in_an_email_draft_comes_from_thunderbird(self):
        self.open()
        mode = app.handle('new email', 'awake')
        self.assertEqual(mode, 'email_draft')
        app.handle('email to John Smith', mode)
        self.assertEqual(app.email_draft.recipient, 'john@example.com')

    def test_writing_comes_first_in_writing_fields(self):
        self.open()
        mode = app.handle('write a note', 'awake')
        self.assertEqual(app.handle('Email John about the trip.', mode), 'note')
        self.assertEqual(app.email_draft, None)

    def test_natural_words_for_adding_and_deleting_a_contact(self):
        # A new user says "add a new contact", answers No to the email question, and says "delete it".
        mode = app.handle('Add a new contact.', 'awake')
        self.assertEqual((mode, self.said()), ('task_input', 'New contact. Type or say the name, then press Enter. Escape cancels.'))
        mode = app.handle('Sue', mode)
        mode = app.handle('No.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'Phone number? Type or say it, then press Enter. '
                                                             'To leave it out, press Enter or say skip.'))
        mode = app.handle('555 0123', mode)
        self.assertEqual((mode, contacts_screen.current().name), ('contacts', 'Sue'))
        app.handle('Delete it.', mode)
        self.assertEqual(self.said(), 'Delete Sue from your contacts? It is also removed from Thunderbird. Say Yes or No. Escape keeps it.')
        app.handle('yes', mode)
        self.assertNotIn('Sue', [c.name for c in contacts_screen.CONTACTS])
        mode = app.handle('add a new contact', mode)
        self.assertEqual(mode, 'task_input')
        # "No" is not a name: ask again.
        mode = app.handle('No.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'A contact needs a name. Type or say the name, then press Enter. '
                                                             'Escape cancels.'))
        mode = app.handle('Tim', mode)
        mode = app.handle('tim at example dot com', mode)
        mode = app.handle('no thanks', mode)
        self.assertEqual(self.said(), 'Saved Tim in Thunderbird, me@gmail.com. Tim, tim@example.com, 6 of 6.')

    def test_everyday_words_open_contacts_from_the_main_menu(self):
        for words in ('Go to contacts.', 'Show my address book', 'open my address book', 'View my contacts', 'open the contacts',
                      'Phone book'):
            with self.subTest(words=words):
                self.assertEqual(app.handle(words, 'awake'), 'contacts')
                self.assertTrue(self.said().startswith('Contacts. 5 contacts.'), self.said())
                contacts_screen.reset()

    def test_old_google_contacts_command_points_to_thunderbird(self):
        app.handle('connect google contacts', 'awake')
        self.assertIn('Google contacts now come from Thunderbird.', self.said())


class NamelessContactTests(unittest.TestCase):
    def test_a_thunderbird_contact_with_only_a_phone_number_has_a_name_to_say(self):
        # Google keeps contacts with only a phone number; every message names the contact.
        contact = contacts_book.from_thunderbird({'id': 'c9', 'phones': ['555-0142'], 'book': 'Google'})
        self.assertEqual(contact.name, 'No name')
        self.assertEqual(contacts_screen.question(contact), 'Delete No name from your contacts? It is also removed from Thunderbird. '
                                                            'Say Yes or No. Escape keeps it.')
        self.assertEqual(contacts_book.from_thunderbird({'id': 'c8', 'emails': ['a@example.com']}).name, 'a@example.com')


class NotStartedContactsTests(CompanionHarness):
    thunderbird = False

    def test_email_by_name_reads_thunderbird_contacts_when_thunderbird_is_not_running_yet(self):
        # Thunderbird is the user's mail program but not started: its contacts are read, which starts it.
        AddressBook(self.root).save('Zed Local', 'zed@example.com')     # contacts here too
        john = {'id': 'c1', 'name': 'John Smith', 'emails': ['john@example.com'], 'phones': [], 'book': 'Google'}
        with mock.patch.object(thunderbird_pim, 'connected', return_value=False), \
                mock.patch.object(thunderbird_pim, 'has_thunderbird_account', return_value=True), \
                mock.patch.object(thunderbird_pim, 'contacts', return_value={'books': [], 'contacts': [john]}) as read:
            mode = app.handle('email John', 'awake')
        self.assertEqual((mode, app.email_draft.recipient), ('email_draft', 'john@example.com'))
        read.assert_called_once()


class OldAddonContactsTests(CompanionHarness):
    old_addon = True

    def test_an_old_add_on_copy_asks_for_an_update_and_keeps_local_contacts(self):
        AddressBook(self.root, 'me@gmail.com').save('Zed Local', 'zed@example.com')
        self.assertEqual(app.handle('contacts', 'awake'), 'contacts')
        from thunderbird_pim import UPDATE_NEEDED
        self.assertEqual(self.said(), 'Contacts. 1 contact. Zed Local, zed@example.com, 1 of 1. ' + UPDATE_NEEDED)
        self.assertNotIn('Close Thunderbird', UPDATE_NEEDED)   # it updates by itself (Will, October 30, 2026)


class NoThunderbirdContactsTests(CompanionHarness):
    thunderbird = False

    def test_without_thunderbird_contacts_are_kept_on_this_computer(self):
        mode = app.handle('open contacts', 'awake')
        self.assertEqual(self.said(), 'Contacts. No contacts yet. Press Control+N to add one, or say add contact. '
                         'Only contacts saved on this computer are here. To use your Google or Outlook contacts, say add Thunderbird account.')
        mode = app.handle('add contact', mode)
        mode = app.handle('Lee', mode)
        mode = app.handle('lee@example.com', mode)
        mode = app.handle('skip', mode)
        self.assertEqual(self.said(), 'Saved Lee on this computer. Lee, lee@example.com, 1 of 1.')
        self.assertEqual(AddressBook(self.root).get('lee')['address'], 'lee@example.com')
        # The details say where it is in plain words, not "Saved in: Saved on this computer".
        self.press('Enter', mode)
        self.assertIn('Saved on this computer', [label for label, _ in contacts_screen.DETAILS])
        self.press('Escape', mode)
        # A phone number alone is enough.
        mode = app.handle('add contact', mode)
        for answer in ('Plumber', '', '555 0199'):
            mode = app.handle(answer, mode) if answer else self.press('Enter', mode)
        self.assertEqual(AddressBook(self.root).get('plumber'), {'name': 'Plumber', 'address': '', 'phone': '555 0199'})
        self.press('Letter:p', mode)
        mode = self.press('Delete', mode)
        self.press('Up', mode); self.press('Enter', mode)
        self.assertIsNone(AddressBook(self.root).get('plumber'))

    def test_contacts_on_this_computer_can_be_edited_without_losing_any(self):
        book = AddressBook(self.root)
        book.save('Lee', 'lee@example.com', '555-0101')
        book.save('Bob', 'bob@example.com')
        mode = app.handle('open contacts', 'awake')
        self.press('Letter:l', mode)
        app.handle('rename contact to Lee Ann', mode)
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved Lee Ann. Lee Ann, lee@example.com, 555-0101, 2 of 2.')
        self.assertIsNone(AddressBook(self.root).get('lee'))
        self.assertEqual(AddressBook(self.root).get('lee ann'), {'name': 'Lee Ann', 'address': 'lee@example.com', 'phone': '555-0101'})
        # Another contact's name is never taken over.
        app.handle('rename contact to bob', mode)
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'There is already a contact named Bob. The change was not saved.')
        self.assertEqual(AddressBook(self.root).get('bob')['address'], 'bob@example.com')
        app.handle('change the phone to 555 0102', mode)
        app.handle('yes', mode)
        self.assertEqual(AddressBook(self.root).get('lee ann')['phone'], '555 0102')

    def test_adding_a_name_already_saved_here_does_not_replace_it(self):
        AddressBook(self.root).save('Bob', 'bob@example.com')
        mode = app.handle('open contacts', 'awake')
        mode = app.handle('add contact', mode)
        for answer in ('Bob', 'skip', '555 0100'):
            mode = app.handle(answer, mode)
        self.assertEqual(self.said(), 'There is already a contact named Bob. To change it, choose Bob, then press F2. '
                                      'The contact was not saved.')
        self.assertEqual(AddressBook(self.root).get('bob'), {'name': 'Bob', 'address': 'bob@example.com'})

    def test_add_contact_as_address_still_works_everywhere(self):
        app.handle('add contact Bob as bob at example dot com', 'awake')
        self.assertEqual(self.said(), 'Saved contact Bob at bob@example.com.')
        app.handle('find contact bob', 'awake')
        self.assertEqual(self.said(), 'Bob, bob@example.com')


if __name__ == '__main__':
    unittest.main()
