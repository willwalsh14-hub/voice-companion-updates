"""The Calendar screen, end to end, and how events are said.

The real add-on (background.js and pim.js) runs in Node against the simulated
Thunderbird (fake-messenger.cjs), whose calendar answers like the add-on's
calendar Experiment. The clock is fixed at Monday, October 26, 2026, 9:15 AM,
and the local time zone at five hours behind UTC, so every line is predictable.
"""
import unittest
from datetime import date, datetime, timedelta, timezone, tzinfo
from unittest import mock

import calendar_events
import calendar_screen
import companion as app
import thunderbird_pim
from test_contacts_screen import CompanionHarness

NOW = datetime(2026, 10, 26, 9, 15)
ZONE = timezone(timedelta(hours=-5))


class CalendarHarness(CompanionHarness):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(mock.patch.object(app, 'CLOCK', lambda: NOW))
        self.stack.enter_context(mock.patch.object(calendar_events, 'LOCAL_ZONE', ZONE))
        self.stack.enter_context(mock.patch.object(calendar_screen, 'LAST_CALENDAR', None))
        calendar_screen.reset()
        self.addCleanup(calendar_screen.reset)

    def open(self):
        mode = app.handle('Calendar', 'awake')
        self.assertEqual(mode, 'calendar')
        return mode

    def events_on(self, day):
        events, _ = calendar_screen.events_on(day)
        return [(e.title, e.start, e.calendar) for e in events]


class CalendarScreenTests(CalendarHarness):
    def test_opens_on_today_with_the_count_then_the_first_event(self):
        self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. 1 event. '
                         'Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 1.')

    def test_everyday_words_open_the_calendar_from_the_main_menu(self):
        for words in ('Go to calendar.', 'go to my calendar', 'View my calendar', 'open the calendar', 'Show my schedule',
                      'open my schedule', 'my appointments'):
            with self.subTest(words=words):
                self.assertEqual(app.handle(words, 'awake'), 'calendar')
                self.assertTrue(self.said().startswith('Calendar. Today, Monday, October 26. 1 event.'), self.said())
                calendar_screen.reset()

    def test_days_and_weeks_with_the_arrow_and_page_keys(self):
        mode = self.open()
        self.press('Right', mode)
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. 1 event. Nine AM to ten AM, Group meeting, me@gmail.com, 1 of 1.')
        self.press('Home', mode)
        self.assertTrue(self.said().startswith('Today, Monday, October 26. 1 event.'))
        self.press('Left', mode)
        self.assertEqual(self.said(), 'Yesterday, Sunday, October 25. No events.')
        self.press('Next', mode)
        self.assertEqual(self.said(), 'Sunday, November 1. No events.')
        self.press('Next', mode)
        self.assertEqual(self.said(), 'Sunday, November 8. No events.')
        self.press('Prior', mode)
        self.assertEqual(calendar_screen.DAY, date(2026, 11, 1))

    def test_up_and_down_stop_at_the_ends_and_enter_reads_the_details(self):
        mode = self.open()
        self.press('Down', mode)
        self.assertEqual(self.said(), 'End of list. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 1.')
        self.press('Up', mode)
        self.assertTrue(self.said().startswith('Start of list.'))
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Doctor appointment. Today, Monday, October 26, ten thirty AM to eleven AM. Place: VA clinic. '
                                      'Notes: Bring the card. Calendar: me@gmail.com.')

    def test_voice_commands_for_days(self):
        mode = self.open()
        for words, day in (('Tomorrow.', date(2026, 10, 27)), ('go to November 11', date(2026, 11, 11)),
                           ("What's on Friday?", date(2026, 10, 30)), ('next day', date(2026, 10, 31)),
                           ('previous week', date(2026, 10, 24)), ('Today', date(2026, 10, 26)), ('the 30th', date(2026, 10, 30)),
                           ('next week', date(2026, 11, 6))):
            with self.subTest(words=words):
                app.handle(words, mode)
                self.assertEqual(calendar_screen.DAY, day)
        app.handle('go to November 11', mode)
        self.assertEqual(self.said(), 'Wednesday, November 11. 1 event. All day, Veterans Day, Holidays, 1 of 1.')

    def test_control_n_adds_an_event_with_simple_prompts_and_a_question(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+N', mode), 'add event')
        mode = self.press('Ctrl+N', mode)
        self.assertEqual((mode, self.said()), ('task_input', calendar_screen.WHEN_PROMPT))
        mode = app.handle('tomorrow at 3 PM', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'What is it called? Type or say a name, such as Doctor, then press Enter.'))
        mode = app.handle('Dentist', mode)
        self.assertEqual(mode, 'calendar')
        self.assertEqual(self.said(), 'Add Dentist, Tomorrow, Tuesday, October 27, three PM to four PM, to the me@gmail.com calendar? '
                                      'Say Yes to save or No to cancel. To use another calendar, say change calendar.')
        self.assertEqual(app.app_context(mode)['confirmation'], 'yes')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Saved Dentist in the me@gmail.com calendar. Tomorrow, Tuesday, October 27. '
                                      'Three PM to four PM, Dentist, me@gmail.com, 2 of 2.')
        # Kept in UTC: 3 PM here is 8 PM UTC.
        self.assertIn(('Dentist', datetime(2026, 10, 27, 15), 'me@gmail.com'), self.events_on(date(2026, 10, 27)))

    def test_everything_at_once_from_the_main_menu_and_changing_the_calendar(self):
        mode = app.handle('Add appointment tomorrow at 3 PM called doctor.', 'awake')
        self.assertEqual(mode, 'calendar')
        self.assertTrue(self.said().startswith('Add doctor, Tomorrow, Tuesday, October 27, three PM to four PM, to the me@gmail.com calendar?'))
        app.handle('change calendar', mode)
        self.assertIn('to the Home calendar?', self.said())
        app.handle('Yes.', mode)
        self.assertTrue(self.said().startswith('Saved doctor in the Home calendar.'))
        self.assertIn(('doctor', datetime(2026, 10, 27, 15), 'Home'), self.events_on(date(2026, 10, 27)))

    def test_natural_words_for_adding_name_the_event_or_ask_for_its_name(self):
        # A word between "a" and "appointment", and "reminder", from the main menu.
        mode = app.handle('Schedule a doctor appointment for tomorrow at 3 PM.', 'awake')
        self.assertEqual(mode, 'calendar')
        self.assertTrue(self.said().startswith('Add doctor appointment, Tomorrow, Tuesday, October 27, three PM to four PM,'), self.said())
        app.handle('no', mode)
        # "for" alone is not a name: ask for one.
        mode = app.handle('Make an appointment for Friday at 10 AM.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'What is it called? Type or say a name, such as Doctor, then press Enter.'))
        mode = app.handle('Dentist', mode)
        self.assertTrue(self.said().startswith('Add Dentist, Friday, October 30, ten AM to eleven AM,'), self.said())
        app.handle('no', mode)
        mode = app.handle('Add a reminder for tomorrow at 9.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'What is it called? Type or say a name, such as Doctor, then press Enter.'))
        self.press('Escape', mode)
        # Not a calendar request: a note about a meeting stays a note.
        self.assertIsNone(calendar_screen.ADD_START.match('make a note about the meeting'))
        self.assertIsNone(calendar_screen.ADD_START.match('add a note about meeting'))
        # A day is not part of the name ("Schedule tomorrow's meeting" is not called "'s meeting").
        app.handle("Schedule tomorrow's meeting at 2 PM.", 'calendar')
        self.assertTrue(self.said().startswith('Add meeting, Tomorrow, Tuesday, October 27, two PM to three PM,'), self.said())
        app.handle('no', 'calendar')

    def test_delete_it_deletes_the_event(self):
        mode = self.open()
        app.handle('Delete it.', mode)
        self.assertEqual(self.said(), 'Delete Doctor appointment, Today, Monday, October 26, ten thirty AM to eleven AM? Say Yes or No. Escape keeps it.')
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Deleted Doctor appointment. Today, Monday, October 26. No events.')
        self.press('Right', mode)
        app.handle('cancel my appointment', mode)
        self.assertTrue(self.said().startswith('Group meeting repeats.'), self.said())

    def test_when_thunderbird_cannot_save_the_message_says_what_to_do_once(self):
        import thunderbird_mailbox
        from mailbox_access import MailboxError
        real = thunderbird_mailbox.call

        def failing(method, **params):
            if method in ('addEvent', 'modifyEvent', 'deleteEvent'):
                raise MailboxError({'addEvent': 'Thunderbird could not save the event.', 'modifyEvent': 'Thunderbird could not save the change.',
                                    'deleteEvent': 'Thunderbird could not delete the event.'}[method])
            return real(method, **params)
        mode = self.open()
        with mock.patch.object(thunderbird_mailbox, 'call', failing):
            app.handle('add event tomorrow at 3 PM called Lunch', mode)
            app.handle('yes', mode)
            self.assertEqual(self.said(), 'Thunderbird could not save the event. Close Thunderbird, then try again.')
            app.handle('rename event to Dentist', mode)
            app.handle('yes', mode)
            self.assertEqual(self.said(), 'Thunderbird could not save the change. Close Thunderbird, then try again.')
            app.handle('delete event', mode)
            app.handle('yes', mode)
            self.assertEqual(self.said(), 'Thunderbird could not delete the event. Close Thunderbird, then try again.')
            # A reason that is not about saving still says the event was not saved.
            with mock.patch.object(thunderbird_pim, 'add_event', side_effect=thunderbird_pim.PimError('That calendar cannot be changed. Choose another calendar.')):
                app.handle('add event tomorrow at 3 PM called Lunch', mode)
                app.handle('yes', mode)
            self.assertEqual(self.said(), 'That calendar cannot be changed. Choose another calendar. The event was not saved.')

    def test_the_part_of_the_day_said_first_counts_for_the_time_said_next(self):
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('Friday evening', mode)
        self.assertEqual(self.said(), 'Friday, October 30. What time? Say a time like 3 PM, or say all day. Escape cancels.')
        mode = app.handle('7', mode)
        self.assertEqual(self.said(), 'What is it called? Type or say a name, such as Doctor, then press Enter.')
        # "No" is not a name: ask again.
        mode = app.handle('No.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'An event needs a name. Type or say a name, such as Doctor, '
                                                             'then press Enter. Escape cancels.'))
        mode = app.handle('Bingo', mode)
        self.assertTrue(self.said().startswith('Add Bingo, Friday, October 30, seven PM to eight PM,'), self.said())
        app.handle('no', mode)
        # "In the afternoon" after the time is not the name.
        mode = app.handle('add event', mode)
        mode = app.handle('Friday', mode)
        mode = app.handle('two thirty in the afternoon', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'What is it called? Type or say a name, such as Doctor, then press Enter.'))

    def test_a_day_without_a_time_asks_for_the_time_or_all_day(self):
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('next Friday', mode)
        self.assertEqual(self.said(), 'Friday, October 30. What time? Say a time like 3 PM, or say all day. Escape cancels.')
        mode = app.handle('all day', mode)
        mode = app.handle('Fishing trip', mode)
        self.assertTrue(self.said().startswith('Add Fishing trip, Friday, October 30, all day, to the me@gmail.com calendar?'))
        app.handle('yes', mode)
        self.assertEqual(calendar_screen.current().line(date(2026, 10, 30)), 'All day, Fishing trip, me@gmail.com')

    def test_a_time_alone_uses_the_day_on_screen_and_no_cancels(self):
        mode = self.open()
        self.press('Right', mode); self.press('Right', mode)
        mode = self.press('Ctrl+N', mode)
        mode = app.handle('lunch at noon for 90 minutes', mode)
        self.assertTrue(self.said().startswith('Add lunch, Wednesday, October 28, twelve PM to one thirty PM'))
        self.press('Down', mode)
        self.assertEqual(self.said(), 'No.')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Not saved. Wednesday, October 28. No events.')
        self.assertEqual(self.events_on(date(2026, 10, 28)), [])

    def test_bad_times_are_asked_again_and_escape_cancels(self):
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('whenever', mode)
        self.assertEqual(mode, 'task_input')
        self.assertTrue(self.said().startswith('I did not catch a day or time.'))
        mode = app.handle('Friday', mode)
        mode = app.handle('soon', mode)
        self.assertTrue(self.said().startswith('I did not catch a time.'))
        mode = self.press('Escape', mode)
        self.assertEqual((mode, self.said()), ('calendar', 'Canceled. Back to calendar.'))

    def test_after_adding_the_new_event_is_selected_even_when_another_has_its_name(self):
        mode = self.press('Ctrl+N', self.open())
        mode = app.handle('today at 3 PM called Doctor appointment', mode)
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved Doctor appointment in the me@gmail.com calendar. Today, Monday, October 26. '
                                      'Three PM to four PM, Doctor appointment, me@gmail.com, 2 of 2.')

    def test_a_past_time_is_said_before_saving(self):
        mode = self.press('Ctrl+N', self.open())
        app.handle('today at 8 AM called Walk', mode)
        self.assertIn('That time has already passed.', self.said())

    def test_delete_asks_and_a_repeating_event_asks_this_one_or_all(self):
        mode = self.open()
        self.press('Delete', mode)
        self.assertEqual(self.said(), 'Delete Doctor appointment, Today, Monday, October 26, ten thirty AM to eleven AM? Say Yes or No. Escape keeps it.')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Not deleted. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 1.')
        self.press('Delete', mode); self.press('Up', mode); self.press('Enter', mode)
        self.assertEqual(self.said(), 'Deleted Doctor appointment. Today, Monday, October 26. No events.')
        # A repeating event: this one only (the first choice), then the rest of the series stays.
        self.press('Right', mode)
        self.press('Delete', mode)
        self.assertEqual(self.said(), 'Group meeting repeats. Delete only this one, tomorrow, Tuesday, October 27, nine AM to ten AM, '
                                      'or all of them? Say this one, all, or no. Escape keeps it.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'All of them.')
        self.press('Up', mode)
        self.assertEqual(self.said(), 'This one only.')
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Start of list.')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Deleted Group meeting on this day. Tomorrow, Tuesday, October 27. No events.')
        self.assertEqual([e[0] for e in self.events_on(date(2026, 11, 3))], ['Group meeting'])
        app.handle('go to November 3', mode)
        app.handle('delete event', mode)
        app.handle('all of them', mode)
        self.assertEqual(self.events_on(date(2026, 11, 3)), [])

    def test_read_only_calendars_are_protected(self):
        mode = self.open()
        app.handle('go to November 11', mode)
        self.press('Delete', mode)
        self.assertEqual(self.said(), 'The Holidays calendar cannot be changed, so Veterans Day cannot be deleted.')

    def test_escape_goes_to_the_main_menu(self):
        self.assertEqual(self.press('Escape', self.open()), 'awake')

    def test_an_unknown_command_names_what_to_say_including_edit(self):
        mode = self.open()
        app.handle('zebra quantum lantern', mode)
        self.assertEqual(self.said(), 'Calendar. Say next event, next day, today, read event, add event, edit event, or delete event. '
                                      'Press F1 for help, or Escape for the main menu.')
        # F1 works while a question is open, as everywhere else.
        self.press('F2', mode)
        self.assertEqual(app.navigation_key('F1', mode), 'help with calendar')

    def test_whats_on_my_calendar_from_other_screens(self):
        self.assertEqual(app.handle("What's on my calendar today?", 'awake'), 'awake')
        self.assertEqual(self.said(), 'Today, Monday, October 26. 1 event. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com.')
        app.handle('what do I have tomorrow', 'awake')
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. 1 event. Nine AM to ten AM, Group meeting, me@gmail.com.')
        app.handle("what's on my calendar Saturday", 'awake')
        self.assertEqual(self.said(), 'Saturday, October 31. No events.')
        # Only a whole day counts: this is a question about a menu, not the calendar.
        self.speech.reset_mock()
        app.handle('What is on the menu tonight?', 'awake')
        self.assertNotIn('Doctor appointment', self.everything_said())
        self.assertNotIn('No events', self.everything_said())
        # Writing comes first: in a note these words are text.
        mode = app.handle('write a note', 'awake')
        self.assertEqual(app.handle("What's on my calendar today?", mode), 'note')
        self.assertNotIn('Doctor appointment', self.said())


class GoToDateTests(CalendarHarness):
    def test_control_g_asks_for_a_date_and_asks_again_after_a_bad_one(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('Ctrl+G', mode), 'go to date')
        mode = self.press('Ctrl+G', mode)
        self.assertEqual((mode, self.said()), ('task_input', calendar_screen.GO_TO_PROMPT))
        mode = app.handle('February 30', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'There is no February 30. Say another date. Escape cancels.'))
        mode = app.handle('whenever', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'I did not catch a date. Say a date such as October 30 or next Friday. '
                                                             'Escape cancels.'))
        # Typed the way people write a date: the slash is kept.
        mode = app.handle('10/30', mode)
        self.assertEqual(mode, 'calendar')
        self.assertEqual(self.said(), 'Friday, October 30. No events.')
        mode = self.press('Ctrl+G', mode)
        mode = self.press('Escape', mode)
        self.assertEqual((mode, self.said()), ('calendar', 'Canceled. Back to calendar.'))
        self.assertEqual(calendar_screen.DAY, date(2026, 10, 30))

    def test_go_to_by_voice_in_every_form(self):
        mode = self.open()
        for words, day in (('Go to October 30.', date(2026, 10, 30)), ('go to 10/30', date(2026, 10, 30)),
                           ('Go to December 25, 2026.', date(2026, 12, 25)), ('go to the 15th', date(2026, 11, 15)),
                           ('go to next Friday', date(2026, 10, 30)), ('Go to Friday, October 30.', date(2026, 10, 30)),
                           ('go to 2027-01-04', date(2027, 1, 4)), ('go to date November 11', date(2026, 11, 11)),
                           ('Tuesday, November 3', date(2026, 11, 3)), ('go to last Friday', date(2026, 10, 23))):
            with self.subTest(words=words):
                app.handle('today', mode)
                self.assertEqual(app.handle(words, mode), 'calendar')
                self.assertEqual(calendar_screen.DAY, day)
        # It says the day and its events, the same as moving by day.
        app.handle('go to November 3', mode)
        self.assertEqual(self.said(), 'Tuesday, November 3. 1 event. Nine AM to ten AM, Group meeting, me@gmail.com, 1 of 1.')

    def test_go_to_date_alone_or_a_bad_date_by_voice_opens_the_prompt(self):
        mode = self.open()
        self.assertEqual((app.handle('Go to date.', mode), self.said()), ('task_input', calendar_screen.GO_TO_PROMPT))
        mode = self.press('Escape', 'task_input')
        self.assertEqual(app.handle('go to February 30', mode), 'task_input')
        self.assertEqual(self.said(), 'There is no February 30. Say another date. Escape cancels.')
        mode = app.handle('February 28', 'task_input')
        self.assertEqual((mode, calendar_screen.DAY), ('calendar', date(2027, 2, 28)))

    def test_the_menu_item_has_the_key(self):
        from screen_menus import items
        self.assertIn(('Day', 'Go to a date', 'Ctrl+G', 'go to date'), items('calendar'))


class EditEventTests(CalendarHarness):
    def test_f2_lists_what_to_change_and_renames_with_a_question(self):
        mode = self.open()
        self.assertEqual(app.navigation_key('F2', mode), 'edit event')
        self.press('F2', mode)
        self.assertEqual(self.said(), 'Edit Doctor appointment. What do you want to change? Name: Doctor appointment, 1 of 7. '
                                      'Use the arrows and Enter, or say what to change. Escape cancels.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'Day and time: Today, Monday, October 26, ten thirty AM to eleven AM, 2 of 7.')
        labels = [calendar_screen.field_label(f, calendar_screen.EDIT['event']) for f in calendar_screen.CHOICES]
        self.assertEqual(labels, ['Name: Doctor appointment', 'Day and time: Today, Monday, October 26, ten thirty AM to eleven AM',
                                  'Length: 30 minutes', 'Make it all day', 'Place: VA clinic', 'Notes: Bring the card.',
                                  'Calendar: me@gmail.com'])
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Name: Doctor appointment, 1 of 7.')
        self.press('Up', mode)
        self.assertEqual(self.said(), 'Start of list.')
        mode = self.press('Enter', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'New name for Doctor appointment? Type or say it, then press Enter. '
                                                             'Escape cancels.'))
        mode = app.handle('Dentist.', mode)
        self.assertEqual((mode, self.said()), ('calendar', 'Rename Doctor appointment to Dentist? Say Yes to save or No to cancel.'))
        self.assertEqual(app.app_context(mode)['confirmation'], 'yes')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Saved Dentist. Today, Monday, October 26. Ten thirty AM to eleven AM, Dentist, me@gmail.com, 1 of 1.')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Dentist. Today, Monday, October 26, ten thirty AM to eleven AM. Place: VA clinic. '
                                      'Notes: Bring the card. Calendar: me@gmail.com.', 'the place and notes are kept')

    def test_move_and_change_the_time_by_voice(self):
        mode = self.open()
        app.handle('Move event to Friday at 2.', mode)
        self.assertEqual(self.said(), 'Change Doctor appointment to Friday, October 30, two PM to two thirty PM? '
                                      'Say Yes to save or No to cancel.')
        app.handle('Yes.', mode)
        self.assertEqual(self.said(), 'Saved Doctor appointment. Friday, October 30. Two PM to two thirty PM, Doctor appointment, '
                                      'me@gmail.com, 1 of 1.')
        self.assertEqual(self.events_on(date(2026, 10, 26)), [])
        app.handle('change the time to 3 PM', mode)
        self.assertEqual(self.said(), 'Change Doctor appointment to Friday, October 30, three PM to three thirty PM? '
                                      'Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        # A day alone keeps the time.
        app.handle('change the date to Wednesday', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Wednesday, October 28, three PM to three thirty PM?'))
        app.handle('yes', mode)
        self.assertEqual(self.events_on(date(2026, 10, 28)), [('Doctor appointment', datetime(2026, 10, 28, 15), 'me@gmail.com')])
        # A time that has passed is said, as when adding.
        app.handle('change the time to today at 8 AM', mode)
        self.assertIn('That time has already passed.', self.said())
        app.handle('no', mode)
        self.assertEqual(self.said(), 'Not changed. Three PM to three thirty PM, Doctor appointment, me@gmail.com, 1 of 1.')

    def test_move_to_a_day_still_goes_there_and_move_it_edits(self):
        mode = self.open()
        app.handle('Move to Friday.', mode)
        self.assertEqual((calendar_screen.DAY, calendar_screen.PENDING), (date(2026, 10, 30), None))
        app.handle('today', mode)
        app.handle('Move it to Friday.', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Friday, October 30, ten thirty AM'))
        app.handle('no', mode)
        app.handle('Reschedule to Thursday at 9', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Thursday, October 29, nine AM to nine thirty AM?'))

    def test_bad_times_are_asked_again_and_escape_cancels(self):
        mode = self.open()
        mode = app.handle('move event', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'New day and time for Doctor appointment? For example, say Friday at 2 PM, '
                                                             'or 3 PM. Escape cancels.'))
        mode = app.handle('soon', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'I did not catch a day or time. Say something like Friday at 2 PM, or 3 PM. '
                                                             'Escape cancels.'))
        mode = self.press('Escape', mode)
        self.assertEqual((mode, self.said()), ('calendar', 'Canceled. Back to calendar.'))
        self.assertEqual(self.events_on(date(2026, 10, 26))[0][1], datetime(2026, 10, 26, 10, 30))
        # Escape on the list of what to change, and No on the question, change nothing.
        self.press('F2', mode)
        self.press('Escape', mode)
        self.assertEqual(self.said(), 'Not changed. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 1.')
        self.assertIsNone(calendar_screen.PENDING)

    def test_length_and_end_time(self):
        mode = self.open()
        app.handle('edit event', mode)
        mode = app.handle('Length.', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'How long is Doctor appointment? Say a length, such as 45 minutes or 2 hours, '
                                                             'or an end time, such as until 4 PM. Escape cancels.'))
        mode = app.handle('45 minutes', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Today, Monday, October 26, ten thirty AM to eleven fifteen AM?'))
        app.handle('yes', mode)
        app.handle('edit event', mode)
        mode = app.handle('end time', mode)
        mode = app.handle('until noon', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Today, Monday, October 26, ten thirty AM to twelve PM?'))
        app.handle('yes', mode)
        self.assertEqual(calendar_screen.current().end, datetime(2026, 10, 26, 12))
        app.handle('edit event', mode)
        mode = app.handle('length', mode)
        mode = app.handle('a while', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'I did not catch a length. Say a length, such as 45 minutes, '
                                                             'or an end time, such as until 4 PM. Escape cancels.'))

    def test_all_day_and_back_to_a_time(self):
        mode = self.open()
        app.handle('make it all day', mode)
        self.assertEqual(self.said(), 'Change Doctor appointment to Today, Monday, October 26, all day? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        self.assertEqual(calendar_screen.current().line(date(2026, 10, 26)), 'All day, Doctor appointment, me@gmail.com')
        app.handle('Make it all day.', mode)
        self.assertEqual(self.said(), 'Doctor appointment is already all day. All day, Doctor appointment, me@gmail.com, 1 of 1.')
        self.press('F2', mode)
        self.press('Down', mode); self.press('Down', mode); self.press('Down', mode)
        self.assertEqual(self.said(), 'Give it a time, 4 of 7.')
        mode = self.press('Enter', mode)
        self.assertEqual((mode, self.said()), ('task_input', 'What time does Doctor appointment start? Say a time like 3 PM. Escape cancels.'))
        mode = app.handle('2 PM', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Today, Monday, October 26, two PM to three PM?'))
        app.handle('yes', mode)
        # An all-day event's length is in days.
        app.handle('make it all day', mode); app.handle('yes', mode)
        app.handle('edit event', mode)
        mode = app.handle('length', mode)
        self.assertEqual(self.said(), 'How many days is Doctor appointment? Say a number of days, such as 2 days. Escape cancels.')
        mode = app.handle('3 days', mode)
        self.assertTrue(self.said().startswith('Change Doctor appointment to Today, Monday, October 26, all day, until Wednesday, October 28?'))
        app.handle('yes', mode)
        self.assertEqual(calendar_screen.current().end, datetime(2026, 10, 29))

    def test_place_and_notes_can_be_changed_or_removed(self):
        mode = self.open()
        app.handle('Change the place to Room 4.', mode)
        self.assertEqual(self.said(), 'Change the place for Doctor appointment to Room 4? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        app.handle('edit event', mode)
        mode = app.handle('notes', mode)
        self.assertEqual(self.said(), 'New notes for Doctor appointment? Type or say them, then press Enter. '
                                      'To remove the notes, press Enter or say none. Escape cancels.')
        mode = app.handle('Bring the blue card.', mode)
        self.assertEqual(self.said(), 'Change the notes for Doctor appointment to: Bring the blue card? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        self.press('Enter', mode)
        self.assertIn('Place: Room 4. Notes: Bring the blue card.', self.said())
        app.handle('edit event', mode)
        mode = app.handle('place', mode)
        mode = self.press('Enter', mode)      # nothing typed: remove it
        self.assertEqual(self.said(), 'Remove the place from Doctor appointment? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        self.press('Enter', mode)
        self.assertNotIn('Place:', self.said())

    def test_moving_to_another_calendar(self):
        mode = self.open()
        app.handle('edit event', mode)
        app.handle('calendar', mode)
        self.assertEqual(self.said(), 'Move Doctor appointment to the Home calendar? Say Yes to save or No to cancel.')
        app.handle('yes', mode)
        self.assertEqual(self.said(), 'Saved Doctor appointment. Today, Monday, October 26. Ten thirty AM to eleven AM, '
                                      'Doctor appointment, Home, 1 of 1.')
        self.assertEqual(self.events_on(date(2026, 10, 26)), [('Doctor appointment', datetime(2026, 10, 26, 10, 30), 'Home')])

    def test_a_repeating_event_asks_this_one_or_all(self):
        mode = self.open()
        self.press('Right', mode)
        app.handle('Rename event to Team meeting.', mode)
        self.assertEqual(self.said(), 'Rename Group meeting to Team meeting? Group meeting repeats. Change only this one, tomorrow, '
                                      'Tuesday, October 27, or all of them? Say this one, all, or no. Escape cancels.')
        self.assertEqual(app.app_context(mode)['confirmation'], 'this one')
        self.press('Enter', mode)
        self.assertEqual(self.said(), 'Saved Team meeting, this one only. Tomorrow, Tuesday, October 27. '
                                      'Nine AM to ten AM, Team meeting, me@gmail.com, 1 of 1.')
        self.assertEqual([e[0] for e in self.events_on(date(2026, 11, 3))], ['Group meeting'])
        # All of them: a later start, the same day.
        app.handle('change the time to 10 AM', mode)
        app.handle('all of them', mode)
        self.assertTrue(self.said().startswith('Saved Team meeting, all of them.'))
        self.assertEqual(self.events_on(date(2026, 11, 3)), [('Group meeting', datetime(2026, 11, 3, 10), 'me@gmail.com')])
        # Another day: only this one can move.
        app.handle('move event to Thursday', mode)
        self.assertEqual(self.said(), 'Change Team meeting to Thursday, October 29, ten AM to eleven AM? Team meeting repeats. '
                                      'Only this one can move to another day. Say this one, or no. Escape cancels.')
        app.handle('all', mode)
        self.assertTrue(self.said().endswith('Only this one can move to another day. Say this one, or no. Escape cancels.'))
        self.press('Down', mode)
        self.assertEqual(self.said(), 'No.')
        self.press('Enter', mode)
        self.assertTrue(self.said().startswith('Not changed.'))

    def test_read_only_and_empty_days_and_thunderbird_errors(self):
        mode = self.open()
        app.handle('go to November 11', mode)
        self.press('F2', mode)
        self.assertEqual(self.said(), 'The Holidays calendar cannot be changed, so Veterans Day cannot be edited.')
        self.assertIsNone(calendar_screen.PENDING)
        app.handle('yesterday', mode)
        self.press('F2', mode)
        self.assertEqual(self.said(), 'No events on this day.')
        app.handle('today', mode)
        app.handle('rename event to Dentist', mode)
        with mock.patch.object(thunderbird_pim, 'call', side_effect=thunderbird_pim.PimError('Thunderbird is not open.')):
            app.handle('yes', mode)
        self.assertEqual(self.said(), 'Thunderbird is not open. The change was not saved.')
        self.assertEqual(self.events_on(date(2026, 10, 26))[0][0], 'Doctor appointment')
        # The same name again changes nothing.
        app.handle('rename event to Doctor appointment', mode)
        self.assertEqual(self.said(), 'That is the same as before, so nothing was changed. '
                                      'Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com, 1 of 1.')

    def test_edit_and_go_to_keys_are_on_the_menu(self):
        from screen_menus import items
        self.assertIn(('Event', 'Edit event', 'F2', 'edit event'), items('calendar'))


class Eastern2026(tzinfo):
    """US Eastern time in 2026, with daylight saving (March 8 to November 1), without tzdata (Windows has none)."""
    START, END = datetime(2026, 3, 8, 7), datetime(2026, 11, 1, 6)       # the changes, in UTC

    def utcoffset(self, dt):
        naive = dt.replace(tzinfo=None)
        summer = datetime(2026, 3, 8, 2) <= naive < datetime(2026, 11, 1, 1) or \
            datetime(2026, 11, 1, 1) <= naive < datetime(2026, 11, 1, 2) and not dt.fold
        return timedelta(hours=-4 if summer else -5)

    def dst(self, dt):
        return timedelta(hours=1) if self.utcoffset(dt) == timedelta(hours=-4) else timedelta(0)

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        summer = self.START <= utc < self.END
        local = utc + timedelta(hours=-4 if summer else -5)
        repeated = not summer and datetime(2026, 11, 1, 1) <= local < datetime(2026, 11, 1, 2)
        return local.replace(tzinfo=self, fold=1 if repeated else 0)


class DaylightSavingTests(CalendarHarness):
    """November 1, 2026: clocks go back an hour at 2 AM, so the day has 25 hours."""

    def setUp(self):
        super().setUp()
        self.stack.enter_context(mock.patch.object(calendar_events, 'LOCAL_ZONE', Eastern2026()))

    def at(self, *utc):
        return int(datetime(*utc, tzinfo=timezone.utc).timestamp() * 1000)

    def test_the_long_day_and_the_repeated_hour(self):
        items = [{'id': str(n), 'calendarId': 'c', 'calendar': 'Home', 'title': title, 'allDay': False, 'start': start, 'end': start + 1800000}
                 for n, (title, start) in enumerate((('First', self.at(2026, 11, 1, 5, 30)), ('Second', self.at(2026, 11, 1, 6, 30)),
                                                      ('Late', self.at(2026, 11, 2, 4, 30))))]
        start, end = calendar_events.day_range(date(2026, 11, 1))
        self.assertEqual(end - start, 25 * 3600000)
        lines = [e.line(date(2026, 11, 1)) for e in calendar_events.for_day(items, date(2026, 11, 1))]
        # First is 1:30 summer time to 1:00 winter time: half an hour, though the clock goes back.
        self.assertEqual(lines, ['One thirty AM to one AM, First, Home', 'One thirty AM to two AM, Second, Home',
                                 'Eleven thirty PM to twelve AM, Late, Home'])
        self.assertEqual(calendar_events.for_day(items, date(2026, 11, 2)), [])
        first = calendar_events.for_day(items, date(2026, 11, 1))[0]
        self.assertEqual(first.length, timedelta(minutes=30))
        self.assertEqual(calendar_screen.length_words(False, first.start, first.start + first.length), '30 minutes')

    def test_moving_an_event_across_the_change_keeps_its_clock_time(self):
        mode = self.open()
        app.handle('move event to November 2 at 10:30', mode)
        app.handle('yes', mode)
        event = next(e for e in self.relay_events() if e['id'] == 'e1')
        # 10:30 AM in winter time is 3:30 PM UTC (it was 2:30 PM UTC in summer time).
        self.assertEqual(event['start'], self.at(2026, 11, 2, 15, 30))
        self.assertEqual(self.said(), 'Saved Doctor appointment. Monday, November 2. Ten thirty AM to eleven AM, Doctor appointment, '
                                      'me@gmail.com, 1 of 1.')

    def relay_events(self):
        return thunderbird_pim.events(self.at(2026, 11, 1), self.at(2026, 11, 4))['events']


class OnlyHomeCalendarTests(CalendarHarness):
    thunderbird_env = {'FAKE_THUNDERBIRD_ONLY_HOME_CALENDAR': '1'}

    def test_guidance_when_only_thunderbirds_own_calendar_is_there(self):
        self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. No events. ' + calendar_screen.GUIDANCE)


class BrokenCalendarTests(CalendarHarness):
    thunderbird_env = {'FAKE_THUNDERBIRD_CALENDAR_BROKEN': '1'}

    def test_a_damaged_answer_is_said_plainly_and_the_screen_still_works(self):
        mode = self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. No events. Thunderbird could not list its calendars. '
                                      'Close Thunderbird, then try again.')
        self.press('Right', mode)
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. No events. Thunderbird could not list its calendars. '
                                      'Close Thunderbird, then try again.')
        self.press('Down', mode)
        self.assertEqual(self.said(), 'No events. Press Right Arrow for the next day.')


class OldAddonCalendarTests(CalendarHarness):
    old_addon = True

    def test_an_old_add_on_copy_asks_for_an_update(self):
        self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. No events. ' + thunderbird_pim.UPDATE_NEEDED)


class NoThunderbirdCalendarTests(CalendarHarness):
    thunderbird = False

    def test_without_thunderbird_the_calendar_explains_how_to_set_it_up(self):
        self.assertEqual(app.handle('calendar', 'awake'), 'awake')
        self.assertEqual(self.said(), thunderbird_pim.NO_THUNDERBIRD)
        app.handle("what's on my calendar today", 'awake')
        self.assertEqual(self.said(), thunderbird_pim.NO_THUNDERBIRD)


class SpokenEventTests(unittest.TestCase):
    """Events across midnight, all-day events, missing titles and damaged answers (calendar_events.py)."""

    def setUp(self):
        patcher = mock.patch.object(calendar_events, 'LOCAL_ZONE', ZONE)
        patcher.start(); self.addCleanup(patcher.stop)

    def ms(self, *parts):
        return calendar_events.utc_ms(datetime(*parts))

    def test_an_event_across_midnight_is_on_both_days(self):
        item = {'id': 'p', 'calendarId': 'c', 'calendar': 'Home', 'title': 'Party', 'allDay': False,
                'start': self.ms(2026, 10, 30, 22), 'end': self.ms(2026, 10, 31, 1)}
        first = calendar_events.for_day([item], date(2026, 10, 30))
        second = calendar_events.for_day([item], date(2026, 10, 31))
        self.assertEqual(first[0].line(date(2026, 10, 30)), 'Ten PM to one AM tomorrow, Party, Home')
        self.assertEqual(second[0].line(date(2026, 10, 31)), 'Until one AM, Party, Home')
        self.assertEqual(calendar_events.for_day([item], date(2026, 11, 1)), [])

    def test_all_day_events_and_several_days(self):
        trip = {'id': 't', 'calendarId': 'c', 'calendar': '', 'title': 'Trip', 'allDay': True, 'start': '2026-10-30', 'end': '2026-11-02'}
        self.assertEqual(calendar_events.for_day([trip], date(2026, 10, 31))[0].line(date(2026, 10, 31)), 'All day, until Sunday, Trip')
        self.assertEqual(calendar_events.for_day([trip], date(2026, 11, 1))[0].line(date(2026, 11, 1)), 'All day, Trip')
        self.assertEqual(calendar_events.for_day([trip], date(2026, 11, 2)), [])
        # More than a week away, the weekday alone is not enough: the date is said too.
        long_trip = dict(trip, end='2026-11-16')
        self.assertEqual(calendar_events.for_day([long_trip], date(2026, 10, 31))[0].line(date(2026, 10, 31)),
                         'All day, until Sunday, November 15, Trip')
        meeting = {'id': 'm', 'calendarId': 'c', 'calendar': '', 'title': 'Retreat', 'allDay': False,
                   'start': self.ms(2026, 10, 30, 9), 'end': self.ms(2026, 11, 12, 17)}
        self.assertEqual(calendar_events.for_day([meeting], date(2026, 10, 30))[0].line(date(2026, 10, 30)),
                         'Nine AM to five PM on Thursday, November 12, Retreat')

    def test_no_title_and_order(self):
        items = [{'id': 'a', 'calendarId': 'c', 'calendar': 'Home', 'title': '', 'allDay': False,
                  'start': self.ms(2026, 10, 30, 14), 'end': self.ms(2026, 10, 30, 15)},
                 {'id': 'b', 'calendarId': 'c', 'calendar': 'Home', 'title': 'Holiday', 'allDay': True, 'start': '2026-10-30', 'end': '2026-10-31'},
                 {'id': 'c', 'calendarId': 'c', 'calendar': 'Home', 'title': 'Early', 'allDay': False,
                  'start': self.ms(2026, 10, 30, 8, 5), 'end': self.ms(2026, 10, 30, 8, 5)}]
        lines = [e.line(date(2026, 10, 30)) for e in calendar_events.for_day(items, date(2026, 10, 30))]
        self.assertEqual(lines, ['All day, Holiday, Home', 'Eight oh five AM, Early, Home', 'Two PM to three PM, No title, Home'])

    def test_damaged_events_are_left_out(self):
        damaged = [{'id': 'x', 'allDay': True, 'start': 'soon', 'end': 'later'}, {'id': 'y', 'allDay': False, 'start': 'noon'},
                   {'id': '', 'allDay': False, 'start': 0, 'end': 1}, 'not an event', {'allDay': False, 'start': 1e20, 'end': 1e20}]
        self.assertEqual(calendar_events.for_day(damaged, date(2026, 10, 30)), [])

    def test_utc_round_trip_keeps_the_moment(self):
        moment = datetime(2026, 10, 30, 15, 30)
        self.assertEqual(calendar_events.local(calendar_events.utc_ms(moment)), moment)
        self.assertEqual(calendar_events.utc_ms(moment), int(datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc).timestamp() * 1000))


if __name__ == '__main__':
    unittest.main()
