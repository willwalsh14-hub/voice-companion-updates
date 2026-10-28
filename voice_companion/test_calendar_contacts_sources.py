"""Choosing which calendar and which address book you see (pim_sources.py).

The simulated Thunderbird (fake-messenger.cjs) has a Gmail account and, with
FAKE_THUNDERBIRD_TWO_ACCOUNTS, an Outlook account too. Gmail has two calendars
(me@gmail.com and Family) and an address book named by its address; Outlook has a
calendar called Calendar and an address book called Contacts, the plain names
Thunderbird gives them. Thunderbird's own Home calendar, Personal Address Book and
Collected Addresses belong to no account. The clock and time zone are those of
test_calendar_screen: Monday, October 26, 2026, five hours behind UTC.
"""
import json
import unittest
from datetime import date
from unittest import mock

import calendar_screen
import companion as app
import contacts_screen
import display_screen
import pim_sources
from pim_sources import Sources
from screen_menus import MENUS
from test_calendar_screen import CalendarHarness
from test_contacts_screen import CompanionHarness

TWO = {'FAKE_THUNDERBIRD_TWO_ACCOUNTS': '1'}


class SourcesTests(unittest.TestCase):
    """Names, choices and spoken matching, without Thunderbird."""

    CALENDARS = [{'id': 'home', 'name': 'Home', 'type': 'storage', 'account': ''},
                 {'id': 'g', 'name': 'me@gmail.com', 'type': 'caldav', 'account': 'me@gmail.com'},
                 {'id': 'h', 'name': 'Holidays', 'type': 'ics', 'readOnly': True, 'account': ''},
                 {'id': 'o', 'name': 'Calendar', 'type': 'caldav', 'account': 'Vet@Outlook.com'},
                 {'id': 'f', 'name': 'Family', 'type': 'caldav', 'account': 'me@gmail.com'}]

    def test_one_account_offers_no_choice(self):
        # Gmail with Thunderbird's own Home calendar and a holiday list is one account.
        self.assertFalse(Sources('calendar', self.CALENDARS[:3]).active())
        self.assertFalse(Sources('calendar', []).active())
        # An add-on from before accounts sends none: never offered, even with many calendars.
        old = [{k: v for k, v in c.items() if k != 'account'} for c in self.CALENDARS]
        self.assertFalse(Sources('calendar', old).active())
        self.assertTrue(Sources('calendar', self.CALENDARS).active())

    def test_names_tell_two_calendars_called_calendar_apart(self):
        sources = Sources('calendar', self.CALENDARS)
        self.assertEqual(sources.choices(), [
            ('all', 'All calendars'),
            ('account:me@gmail.com', 'Gmail, me@gmail.com, all calendars'),
            ('source:g', 'Calendar, Gmail, me@gmail.com'),
            ('source:f', 'Family, Gmail, me@gmail.com'),
            ('source:o', 'Calendar, Outlook, vet@outlook.com'),
            ('source:home', 'Home, on this computer'),
            ('source:h', 'Holidays')])
        self.assertEqual(sources.ids('account:me@gmail.com'), {'g', 'f'})
        self.assertEqual(sources.ids('source:o'), {'o'})
        self.assertIsNone(sources.ids('all'))
        # In each event line: the name alone, unless another calendar has it.
        two = Sources('calendar', self.CALENDARS + [{'id': 'w', 'name': 'Calendar', 'account': 'me@work.example'}])
        self.assertEqual([two.short(two.find(i)) for i in ('o', 'w', 'f')], ['Calendar, Outlook', 'Calendar, me@work.example', 'Family'])

    def test_spoken_names_with_speech_punctuation(self):
        sources = Sources('calendar', self.CALENDARS)
        for said, choice in (('Gmail', 'account:me@gmail.com'), ('gmail.', 'account:me@gmail.com'),
                             ('me at gmail dot com', 'account:me@gmail.com'), ('Outlook', 'source:o'),
                             ('vet@outlook.com', 'source:o'), ('Family', 'source:f'), ('Holidays.', 'source:h'),
                             ('home', 'source:home'), ('All', 'all'), ('all calendars', 'all'), ('every calendar', 'all')):
            with self.subTest(said=said):
                self.assertEqual(sources.match(said), [choice])
        self.assertEqual(sources.match('Calendar'), ['source:o'], 'Gmail\'s main calendar is named by its address')
        self.assertEqual(sources.match('nonsense'), [])
        self.assertEqual(sources.match(''), [])
        # Two Gmail accounts: "Gmail" fits both, so the person is asked.
        both = Sources('calendar', self.CALENDARS + [{'id': 'x', 'name': 'Work', 'account': 'other@gmail.com'}])
        self.assertEqual(both.match('Gmail'), ['account:me@gmail.com', 'source:x'])

    def test_the_choice_is_kept_atomically_and_a_lost_one_is_said_once(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            sources = Sources('calendar', self.CALENDARS)
            self.assertEqual(pim_sources.resolve(folder, 'calendar', sources), ('all', ''))
            self.assertTrue(pim_sources.remember(folder, 'calendar', 'source:o', 'Calendar, Outlook, vet@outlook.com'))
            self.assertFalse(list(Path(folder).glob('*.writing')), 'written through a temporary file')
            self.assertEqual(pim_sources.resolve(folder, 'calendar', sources), ('source:o', ''))
            gone = Sources('calendar', [c for c in self.CALENDARS if c['id'] != 'o'] + [{'id': 'x', 'name': 'Work', 'account': 'w@example.org'}])
            self.assertEqual(pim_sources.resolve(folder, 'calendar', gone),
                             ('all', 'Calendar, Outlook, vet@outlook.com is no longer in Thunderbird, so all calendars are shown.'))
            self.assertEqual(pim_sources.resolve(folder, 'calendar', gone), ('all', ''), 'said once')
            # A damaged file is the same as no choice.
            (Path(folder) / pim_sources.FILE).write_text('{not json', encoding='utf-8')
            self.assertEqual(pim_sources.resolve(folder, 'calendar', sources), ('all', ''))
            # Contacts and Calendar keep their own choices in the same file.
            pim_sources.remember(folder, 'contacts', 'all', 'All address books')
            pim_sources.remember(folder, 'calendar', 'source:f', 'Family, Gmail, me@gmail.com')
            kept = json.loads((Path(folder) / pim_sources.FILE).read_text(encoding='utf-8'))
            self.assertEqual(set(kept), {'calendar', 'contacts'})

    def test_a_choice_that_cannot_be_written_says_so(self):
        with mock.patch.object(pim_sources, 'write_text_atomic', side_effect=PermissionError('locked')):
            self.assertFalse(pim_sources.remember('.', 'calendar', 'all', 'All calendars'))


class CalendarSourceTests(CalendarHarness):
    thunderbird_env = TWO

    def test_opening_says_what_is_shown_and_shows_every_calendar(self):
        self.open()
        self.assertEqual(self.said(), 'Calendar. Showing all calendars. Today, Monday, October 26. 3 events. '
                                      'Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 3.')
        self.assertEqual([e.title for e in calendar_screen.EVENTS], ['Doctor appointment', 'Team call', 'Soccer practice'])

    def test_control_l_chooses_with_the_arrows_and_enter_and_it_is_kept(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+L', mode), 'choose calendar')
        self.press('Ctrl+L', mode)
        self.assertEqual(self.said(), 'Choose the calendars to show. All calendars, 1 of 7. '
                                      'Use the arrows and Enter, or say a name. Escape cancels.')
        self.assertEqual(app.app_context(mode)['confirmation'], 'all')
        for _ in range(4):
            self.press('Down', mode)
        self.assertEqual(self.said(), 'Calendar, Outlook, vet@outlook.com, 5 of 7.')
        # The window shows the list too.
        lines, selected = display_screen._menu_lines(app, mode, app.app_context(mode))
        self.assertEqual((lines[4], selected), ('Calendar, Outlook, vet@outlook.com', 4))
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Showing Calendar, Outlook, vet@outlook.com. Today, Monday, October 26. 1 event. '
                                      'Two PM to three PM, Team call, Calendar, 1 of 1.')
        self.assertIsNone(calendar_screen.PENDING)
        # Moving through days keeps the choice, without saying it again.
        self.press('Right', mode)
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. No events.')
        # Next time Calendar opens, the same calendar is shown, and said.
        app.handle('close calendar', mode)
        self.open()
        self.assertTrue(self.said().startswith('Calendar. Showing Calendar, Outlook, vet@outlook.com. Today'))

    def test_escape_keeps_what_was_shown(self):
        mode = self.open()
        self.press('Ctrl+L', mode)
        self.press('Down', mode)
        self.press('Escape', mode)
        self.assertEqual(self.said(), 'Not changed. Showing all calendars.')
        self.assertEqual(calendar_screen.SOURCE, 'all')

    def test_voice_commands(self):
        mode = self.open()
        app.handle('Show Gmail calendar.', mode)
        self.assertEqual(self.said(), 'Showing Gmail, me@gmail.com, all calendars. Today, Monday, October 26. 2 events. '
                                      'Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 2.')
        app.handle('Which calendar am I in?', mode)
        self.assertEqual(self.said(), 'Showing Gmail, me@gmail.com, all calendars. To choose another, press Control+L.')
        app.handle('show the Family calendar', mode)
        self.assertTrue(self.said().startswith('Showing Family, Gmail, me@gmail.com. Today, Monday, October 26. 1 event. '
                                               'Five PM to six PM, Soccer practice, Family'))
        app.handle('Where am I?', mode)
        self.assertTrue(self.said().startswith('Showing Family, Gmail, me@gmail.com. Today, Monday, October 26.'))
        app.handle('Show Outlook.', mode)
        self.assertTrue(self.said().startswith('Showing Calendar, Outlook, vet@outlook.com.'))
        app.handle('Show all calendars.', mode)
        self.assertTrue(self.said().startswith('Showing all calendars. Today, Monday, October 26. 3 events.'))
        # A name that is no calendar: say so, and offer the list.
        app.handle('show the Bowling calendar', mode)
        self.assertTrue(self.said().startswith('No calendar is called bowling. Choose the calendars to show.'))
        app.handle('Outlook', mode)
        self.assertEqual(calendar_screen.SOURCE, 'source:cal-outlook')
        # Days still work with the same words.
        app.handle('show Friday', mode)
        self.assertEqual(calendar_screen.DAY, date(2026, 10, 30))
        app.handle('go to November 11', mode)
        self.assertEqual(calendar_screen.DAY, date(2026, 11, 11))

    def test_from_the_main_menu(self):
        mode = app.handle('Show my Outlook calendar.', 'awake')
        self.assertEqual(mode, 'calendar')
        self.assertTrue(self.said().startswith('Showing Calendar, Outlook, vet@outlook.com. Today, Monday, October 26. 1 event.'))

    def test_a_new_event_goes_to_the_calendar_shown(self):
        mode = self.open()
        app.handle('show Outlook calendar', mode)
        mode = app.handle('Add appointment tomorrow at 3 PM called dentist', mode)
        self.assertEqual(self.said(), 'Add dentist, Tomorrow, Tuesday, October 27, three PM to four PM, to Calendar, Outlook, '
                                      'vet@outlook.com? Say Yes to save or No to cancel. To use another calendar, say change calendar.')
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved dentist in Calendar, Outlook, vet@outlook.com. Tomorrow, Tuesday, October 27. '
                                      'Three PM to four PM, dentist, Calendar, 1 of 1.')

    def test_with_all_shown_the_usual_calendar_is_used_and_named(self):
        mode = self.open()
        mode = app.handle('Add appointment tomorrow at 3 PM called dentist', mode)
        self.assertTrue(self.said().startswith('Add dentist, Tomorrow, Tuesday, October 27, three PM to four PM, '
                                               'to Calendar, Gmail, me@gmail.com?'))

    def test_a_calendar_that_cannot_be_changed_is_shown(self):
        mode = self.open()
        app.handle('show the Holidays calendar', mode)
        app.handle('go to November 11', mode)
        self.assertEqual(self.said(), 'Wednesday, November 11. 1 event. All day, Veterans Day, Holidays, 1 of 1.')
        app.handle('delete event', mode)
        self.assertEqual(self.said(), 'The Holidays calendar cannot be changed, so Veterans Day cannot be deleted.')
        mode = app.handle('Add appointment November 12 at 3 PM called dentist', mode)
        self.assertTrue(self.said().startswith('Holidays cannot be changed. Add dentist, Thursday, November 12, three PM to four PM, '
                                               'to Calendar, Gmail, me@gmail.com?'))
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved dentist in Calendar, Gmail, me@gmail.com. Thursday, November 12. No events. '
                                      'It is in Calendar, Gmail, me@gmail.com, which is not shown now. '
                                      'To see it, press Control+L and choose All calendars.')

    def test_moving_an_event_to_a_calendar_not_shown_says_where_it_went(self):
        mode = self.open()
        app.handle('show Outlook calendar', mode)
        mode = app.handle('move to another calendar', mode)
        self.assertTrue(self.said().startswith('Move Team call to Home, on this computer?'))
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved Team call. Today, Monday, October 26. No events. It is in Home, on this computer, '
                                      'which is not shown now. To see it, press Control+L and choose All calendars.')

    def test_an_empty_calendar(self):
        mode = self.open()
        app.handle('show Family calendar', mode)
        self.press('Right', mode)
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. No events.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'No events. Press Right Arrow for the next day.')

    def test_the_alt_menu_has_choose_calendar_last(self):
        mode = self.open()
        self.press('Alt', mode)
        self.press('Letter:3', mode)
        entries = app.SCREEN_MENU.entries()
        self.assertEqual(entries[-1], ('Choose calendar', 'Ctrl+L', 'choose calendar'))
        self.press('End', mode)
        self.assertEqual(self.said(), 'Choose calendar (C), 4 of 4. Control+L. Say choose calendar.')
        self.press('Enter', mode)
        self.assertTrue(self.said().startswith('Choose the calendars to show.'))

    def test_a_kept_calendar_that_is_gone_shows_all_and_says_so_once(self):
        pim_sources.remember(self.root, 'calendar', 'source:cal-gone', 'Calendar, Yahoo, old@yahoo.com')
        self.open()
        self.assertEqual(self.said(), 'Calendar. Calendar, Yahoo, old@yahoo.com is no longer in Thunderbird, so all calendars are shown. '
                                      'Today, Monday, October 26. 3 events. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 3.')
        app.handle('close calendar', 'calendar')
        self.open()
        self.assertTrue(self.said().startswith('Calendar. Showing all calendars. Today'))


class CalendarSourceFailingTests(CalendarHarness):
    thunderbird_env = dict(TWO, FAKE_THUNDERBIRD_FAILED_CALENDAR='Calendar')

    def test_a_calendar_that_cannot_be_read(self):
        mode = self.open()
        # All shown: the others still answer, and the one that failed is named with its account.
        self.assertIn('2 events.', self.said())
        self.assertTrue(self.said().endswith('Calendar, Outlook, vet@outlook.com could not be read.'))
        app.handle('show Gmail calendar', mode)
        self.assertNotIn('could not be read', self.said(), 'a calendar not shown is not mentioned')
        app.handle('show Outlook calendar', mode)
        self.assertEqual(self.said(), 'Showing Calendar, Outlook, vet@outlook.com. Today, Monday, October 26. No events. '
                                      'Calendar, Outlook, vet@outlook.com could not be read.')


class CalendarOneAccountTests(CalendarHarness):
    """One account (the usual simulated Thunderbird): nothing about choosing is said or offered."""

    def test_nothing_is_said_or_offered(self):
        mode = self.open()
        self.assertNotIn('Showing', self.said())
        self.press('Ctrl+L', mode)
        self.assertEqual(self.said(), 'You have one email account in Thunderbird, so all your calendars are shown.')
        self.assertIsNone(calendar_screen.PENDING)
        app.handle('which calendar am I in', mode)
        self.assertEqual(self.said(), 'All your calendars are shown.')
        app.handle('where am i', mode)
        self.assertTrue(self.said().startswith('Today, Monday, October 26.'))
        self.press('Alt', mode)
        self.press('Letter:3', mode)
        self.assertNotIn('choose calendar', [entry[2] for entry in app.SCREEN_MENU.entries()])
        self.assertFalse((self.root / pim_sources.FILE).exists())


class CalendarOldAddonTests(CalendarHarness):
    thunderbird_env = dict(TWO, FAKE_THUNDERBIRD_NO_ACCOUNTS='1')

    def test_an_add_on_without_accounts_shows_everything_as_before(self):
        mode = self.open()
        self.assertTrue(self.said().startswith('Calendar. Today, Monday, October 26. 3 events.'))
        self.assertEqual(calendar_screen.hidden_commands(), ('choose calendar',))


class ContactsSourceHarness(CompanionHarness):
    thunderbird_env = TWO

    def open(self):
        mode = app.handle('Contacts', 'awake')
        self.assertEqual(mode, 'contacts')
        return mode


class ContactsSourceTests(ContactsSourceHarness):
    def test_opening_says_what_is_shown(self):
        self.open()
        self.assertEqual(self.said(), 'Contacts. Showing all address books. 6 contacts. Ann Brown, ann@example.com, 1 of 6.')

    def test_control_l_chooses_and_a_person_in_two_accounts_is_in_both(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+L', mode), 'choose address book')
        self.press('Ctrl+L', mode)
        self.assertEqual(self.said(), 'Choose the address books to show. All address books, 1 of 6. '
                                      'Use the arrows and Enter, or say a name. Escape cancels.')
        self.assertEqual(contacts_screen.choice_names(), [
            'All address books', 'Address book, Gmail, me@gmail.com', 'Contacts, Outlook, vet@outlook.com',
            'Personal Address Book', 'Collected Addresses', 'Company directory'])
        self.press('Down', mode)
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Contacts, Outlook, vet@outlook.com, 3 of 6.')
        self.press('Enter', mode)
        # John Smith is in Gmail and Outlook; the full list says him once, Outlook's list has him too.
        self.assertEqual(self.said(), 'Showing Contacts, Outlook, vet@outlook.com. 2 contacts. John Smith, john@example.com, 1 of 2.')
        self.assertEqual([c.name for c in contacts_screen.CONTACTS], ['John Smith', 'Sam Rivera'])
        self.press('Enter', mode)
        self.press('End', mode)
        self.press('Up', mode)
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Saved in: Thunderbird address book Contacts, Outlook, vet@outlook.com, 4 of 6.')
        app.handle('close contacts', 'contacts')
        self.open()
        self.assertTrue(self.said().startswith('Contacts. Showing Contacts, Outlook, vet@outlook.com. 2 contacts.'))

    def test_voice_commands(self):
        mode = self.open()
        app.handle('Show Gmail contacts.', mode)
        self.assertEqual(self.said(), 'Showing Address book, Gmail, me@gmail.com. 2 contacts. John Smith, john@example.com, 555-0100, 1 of 2.')
        app.handle('Which address book am I in?', mode)
        self.assertEqual(self.said(), 'Showing Address book, Gmail, me@gmail.com. To choose another, press Control+L.')
        app.handle('where am I', mode)
        self.assertTrue(self.said().startswith('Contacts. Showing Address book, Gmail, me@gmail.com. 2 contacts.'))
        app.handle('show the Outlook address book', mode)
        self.assertTrue(self.said().startswith('Showing Contacts, Outlook, vet@outlook.com.'))
        app.handle('show all contacts', mode)
        self.assertTrue(self.said().startswith('Showing all address books. 6 contacts.'))
        # Finding and selecting a person by name still work.
        app.handle('find Mary', mode)
        self.assertEqual(self.said(), 'Mary Jones, mary@example.com, 4 of 6.')
        app.handle('select address book', mode)
        self.assertTrue(self.said().startswith('Choose the address books to show.'))
        app.handle('escape', mode)
        self.assertEqual(self.said(), 'Not changed. Showing all address books.')

    def test_from_the_main_menu(self):
        mode = app.handle('show Outlook contacts', 'awake')
        self.assertEqual(mode, 'contacts')
        self.assertTrue(self.said().startswith('Showing Contacts, Outlook, vet@outlook.com. 2 contacts.'))

    def test_a_new_contact_goes_into_the_address_book_shown(self):
        mode = self.open()
        app.handle('show Outlook contacts', mode)
        mode = self.press('Ctrl+N', mode)
        mode = app.handle('Bob Lee', mode)
        mode = app.handle('bob@example.com', mode)
        mode = app.handle('skip', mode)
        self.assertEqual(self.said(), 'Saved Bob Lee in Contacts, Outlook, vet@outlook.com. Bob Lee, bob@example.com, 1 of 3.')
        # All at once, too.
        app.handle('Add contact Cara Diaz as cara@example.com', mode)
        self.assertEqual(self.said(), 'Saved Cara Diaz in Contacts, Outlook, vet@outlook.com. Cara Diaz, cara@example.com, 2 of 4.')

    def test_with_all_shown_the_usual_address_book_is_used_and_named(self):
        mode = self.open()
        app.handle('Add contact Cara Diaz as cara@example.com', mode)
        self.assertEqual(self.said(), 'Saved Cara Diaz in Address book, Gmail, me@gmail.com. Cara Diaz, cara@example.com, 2 of 7.')

    def test_an_address_book_that_cannot_be_changed(self):
        mode = self.open()
        app.handle('show the Company directory', mode)
        self.assertEqual(self.said(), 'Showing Company directory. 1 contact. Help Desk, help@work.example, 555-0111, 1 of 1.')
        app.handle('delete contact', mode)
        self.assertIn('cannot be changed', self.said())
        app.handle('Add contact Cara Diaz as cara@example.com', mode)
        self.assertEqual(self.said(), 'Company directory cannot be changed. Saved Cara Diaz in Address book, Gmail, me@gmail.com. '
                                      'It is not in the address books shown now. To see it, press Control+L and choose All address books. '
                                      'Help Desk, help@work.example, 555-0111, 1 of 1.')

    def test_an_empty_address_book(self):
        mode = self.open()
        app.handle('show Outlook contacts', mode)
        for _ in range(2):
            app.handle('delete contact', mode)
            app.handle('yes', mode)
        self.assertEqual(self.said(), 'Deleted Sam Rivera. No contacts here. Press Control+L to show another address book, '
                                      'or Control+N to add a contact.')
        for key in ('Down', 'Enter', 'Ctrl+E', 'Delete', 'F2'):
            with self.subTest(key=key):
                self.press(key, mode)
                self.assertIn('No contacts here.', self.said())

    def test_the_alt_menu_has_choose_address_book_last(self):
        mode = self.open()
        self.press('Alt', mode)
        self.press('Letter:2', mode)
        self.assertEqual(app.SCREEN_MENU.entries()[-1], ('Choose address book', 'Ctrl+L', 'choose address book'))

    def test_a_kept_address_book_that_is_gone_shows_all_and_says_so_once(self):
        pim_sources.remember(self.root, 'contacts', 'source:book-gone', 'Contacts, Yahoo, old@yahoo.com')
        self.open()
        self.assertEqual(self.said(), 'Contacts. Contacts, Yahoo, old@yahoo.com is no longer in Thunderbird, so all address books '
                                      'are shown. 6 contacts. Ann Brown, ann@example.com, 1 of 6.')
        app.handle('where am i', 'contacts')
        self.assertTrue(self.said().startswith('Contacts. Showing all address books. 6 contacts.'))


class ContactsFailingTests(ContactsSourceHarness):
    thunderbird_env = dict(TWO, FAKE_THUNDERBIRD_FAILED_BOOK='book-outlook')

    def test_an_address_book_that_cannot_be_read(self):
        mode = self.open()
        self.assertTrue(self.said().endswith('Some Thunderbird address books could not be read. Try again in a minute.'))
        app.handle('show Gmail contacts', mode)
        self.assertTrue(self.said().startswith('Showing Address book, Gmail, me@gmail.com. 2 contacts.'))
        app.handle('show Outlook contacts', mode)
        app.handle('refresh contacts', mode)
        self.assertEqual(self.said(), 'Contacts. Showing Contacts, Outlook, vet@outlook.com. No contacts here. Press Control+L to show '
                                      'another address book, or Control+N to add a contact. Contacts, Outlook, vet@outlook.com could '
                                      'not be read. Try again in a minute.')


class ContactsOneAccountTests(CompanionHarness):
    def test_nothing_is_said_or_offered(self):
        mode = app.handle('Contacts', 'awake')
        self.assertEqual(self.said(), 'Contacts. 5 contacts. Ann Brown, ann@example.com, 1 of 5.')
        self.press('Ctrl+L', mode)
        self.assertEqual(self.said(), 'You have one email account in Thunderbird, so all your contacts are shown.')
        self.assertEqual(contacts_screen.hidden_commands(), ('choose address book',))
        app.handle('show Gmail contacts', mode)
        self.assertEqual(self.said(), 'Contacts. 5 contacts. Ann Brown, ann@example.com, 1 of 5.')


class MenuFileTests(unittest.TestCase):
    def test_the_items_are_last_with_their_keys(self):
        self.assertEqual(MENUS['calendar'][1][-1][1][-1], ('Choose calendar', 'Ctrl+L', 'choose calendar'))
        self.assertEqual(MENUS['contacts'][1][-1][1][-1], ('Choose address book', 'Ctrl+L', 'choose address book'))


if __name__ == '__main__':
    unittest.main()
