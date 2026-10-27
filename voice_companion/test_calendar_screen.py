"""The Calendar screen, end to end, and how events are said.

The real add-on (background.js and pim.js) runs in Node against the simulated
Thunderbird (fake-messenger.cjs), whose calendar answers like the add-on's
calendar Experiment. The clock is fixed at Monday, October 26, 2026, 9:15 AM,
and the local time zone at five hours behind UTC, so every line is predictable.
"""
import unittest
from datetime import date, datetime, timedelta, timezone
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
        self.assertEqual(self.said(), 'Group meeting repeats. Delete only this one, on Tomorrow, Tuesday, October 27, nine AM to ten AM, '
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

    def test_whats_on_my_calendar_from_other_screens(self):
        self.assertEqual(app.handle("What's on my calendar today?", 'awake'), 'awake')
        self.assertEqual(self.said(), 'Today, Monday, October 26. 1 event. Ten thirty AM to eleven AM, Doctor appointment, me@gmail.com.')
        app.handle('what do I have tomorrow', 'awake')
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. 1 event. Nine AM to ten AM, Group meeting, me@gmail.com.')
        app.handle("what's on my calendar Saturday", 'awake')
        self.assertEqual(self.said(), 'Saturday, October 31. No events.')
        # Writing comes first: in a note these words are text.
        mode = app.handle('write a note', 'awake')
        self.assertEqual(app.handle("What's on my calendar today?", mode), 'note')
        self.assertNotIn('Doctor appointment', self.said())


class OnlyHomeCalendarTests(CalendarHarness):
    thunderbird_env = {'FAKE_THUNDERBIRD_ONLY_HOME_CALENDAR': '1'}

    def test_guidance_when_only_thunderbirds_own_calendar_is_there(self):
        self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. No events. ' + calendar_screen.GUIDANCE)


class BrokenCalendarTests(CalendarHarness):
    thunderbird_env = {'FAKE_THUNDERBIRD_CALENDAR_BROKEN': '1'}

    def test_a_damaged_answer_is_said_plainly_and_the_screen_still_works(self):
        mode = self.open()
        self.assertEqual(self.said(), 'Calendar. Today, Monday, October 26. No events. Thunderbird could not list its calendars.')
        self.press('Right', mode)
        self.assertEqual(self.said(), 'Tomorrow, Tuesday, October 27. No events. Thunderbird could not list its calendars.')
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
