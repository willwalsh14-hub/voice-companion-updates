"""Dates and times as people say them, for the Calendar (spoken_dates.py)."""
import unittest
from datetime import date, datetime, time

from spoken_dates import missing_date, number, only_day, parse_day, parse_when, spoken_day

NOW = datetime(2026, 10, 26, 9, 15)     # a Monday morning
TODAY = NOW.date()


class SpokenDayTests(unittest.TestCase):
    def test_words_for_nearby_days(self):
        for words, expected in (('today', TODAY), ('Tonight', TODAY), ('tomorrow', date(2026, 10, 27)),
                                ('the day after tomorrow', date(2026, 10, 28)), ('yesterday', date(2026, 10, 25)),
                                ('in 3 days', date(2026, 10, 29)), ('in two weeks', date(2026, 11, 9)),
                                ('in a week', date(2026, 11, 2))):
            with self.subTest(words=words):
                self.assertEqual(parse_day(words, TODAY), expected)

    def test_weekdays(self):
        # Today counts for "Monday"; "next Monday" is the one after today.
        for words, expected in (('Friday', date(2026, 10, 30)), ('Friday.', date(2026, 10, 30)), ('on friday', date(2026, 10, 30)),
                                ('next Friday', date(2026, 10, 30)), ('this Friday', date(2026, 10, 30)),
                                ('Monday', TODAY), ('next Monday', date(2026, 11, 2)), ('Sunday', date(2026, 11, 1))):
            with self.subTest(words=words):
                self.assertEqual(parse_day(words, TODAY), expected)

    def test_last_and_previous_weekdays_go_back(self):
        # "last Friday" was read as the coming Friday.
        for words, expected in (('last Friday', date(2026, 10, 23)), ('previous Monday', date(2026, 10, 19)),
                                ('last Sunday', date(2026, 10, 25)), ('this past Tuesday', date(2026, 10, 20))):
            with self.subTest(words=words):
                self.assertEqual(parse_day(words, TODAY), expected)

    def test_month_and_day_in_many_spoken_forms(self):
        for words in ('October 30', 'october 30th', 'Oct. 30', 'October thirtieth', 'the 30th of October', '30 October',
                      'thirtieth of October', 'the 30th', '10/30', '10/30/2026', 'Go to October 30.', "What's on October 30?"):
            with self.subTest(words=words):
                self.assertEqual(parse_day(words, TODAY), date(2026, 10, 30))
        self.assertEqual(parse_day('October thirty-first', TODAY), date(2026, 10, 31))
        self.assertEqual(parse_day('December 31, 2027', TODAY), date(2027, 12, 31))

    def test_the_nearest_year_when_looking_and_the_next_one_when_adding(self):
        self.assertEqual(parse_day('October 20', TODAY), date(2026, 10, 20), 'last week, not next year')
        self.assertEqual(parse_day('January 5', TODAY), date(2027, 1, 5))
        self.assertEqual(parse_when('October 20 at 3', NOW).day, date(2027, 10, 20), 'a new event is never put in the past')
        # "the 5th" after the 5th has passed this month: next month.
        self.assertEqual(parse_day('the 5th', TODAY), date(2026, 11, 5))

    def test_impossible_or_missing_dates(self):
        for words in ('February 30', '13/40', 'hello there', '', 'the 45th', 'in 900 days', '2026-02-30'):
            with self.subTest(words=words):
                self.assertIsNone(parse_day(words, TODAY))

    def test_a_typed_date_with_the_year_first_and_a_date_said_with_its_weekday(self):
        # 2026-12-25 is how many programs write a date; "Friday, October 30" is how Companion says one.
        self.assertEqual(parse_day('2026-12-25', TODAY), date(2026, 12, 25))
        for words in ('Friday, October 30', 'Friday October 30th.', 'on Friday, October 30, 2026'):
            with self.subTest(words=words):
                self.assertEqual(only_day(words, TODAY), date(2026, 10, 30))
        # Only a date counts: other words make it not a date.
        for words in ('Friday lunch', 'October 30 dentist', 'banana', ''):
            with self.subTest(words=words):
                self.assertIsNone(only_day(words, TODAY))

    def test_a_date_that_does_not_exist_is_named(self):
        self.assertEqual(missing_date('February 30', TODAY), 'There is no February 30.')
        self.assertEqual(missing_date('the 31st of November', TODAY), 'There is no November 31.')
        self.assertEqual(missing_date('2/30/2027', TODAY), 'There is no February 30, 2027.')
        self.assertEqual(missing_date('October 45', TODAY), 'There is no October 45.')
        # February 29 exists only in a leap year: say which year has one.
        self.assertEqual(missing_date('February 29', TODAY), 'February 29 is only in a leap year, such as 2028. Say the year too.')
        for words in ('October 30', 'Friday', 'hello', ''):
            self.assertEqual(missing_date(words, TODAY), '', words)

    def test_spoken_day_names_today_tomorrow_and_other_years(self):
        self.assertEqual(spoken_day(TODAY, TODAY), 'Today, Monday, October 26')
        self.assertEqual(spoken_day(date(2026, 10, 27), TODAY), 'Tomorrow, Tuesday, October 27')
        self.assertEqual(spoken_day(date(2026, 10, 25), TODAY), 'Yesterday, Sunday, October 25')
        self.assertEqual(spoken_day(date(2026, 10, 30), TODAY), 'Friday, October 30')
        self.assertEqual(spoken_day(date(2027, 1, 5), TODAY), 'Tuesday, January 5, 2027')

    def test_number_words(self):
        for words, value in (('three', 3), ('thirty-one', 31), ('twenty first', 21), ('30th', 30), ('oh five', 5), ('fifteen', 15)):
            self.assertEqual(number(words), value, words)
        self.assertIsNone(number('banana'))


class SpokenTimeTests(unittest.TestCase):
    def check(self, words, day, start, end=None, minutes=None, rest=''):
        found = parse_when(words, NOW)
        self.assertEqual((found.day, found.start, found.end, found.minutes, found.rest), (day, start, end, minutes, rest), words)

    def test_times_with_and_without_am_or_pm(self):
        tomorrow = date(2026, 10, 27)
        self.check('tomorrow at 3 PM', tomorrow, time(15))
        self.check('Tomorrow at 3 P.M.', tomorrow, time(15))
        self.check('tomorrow at 3:30 pm', tomorrow, time(15, 30))
        self.check('tomorrow at three thirty', tomorrow, time(15, 30))
        self.check('tomorrow at 3 oh 5 pm', tomorrow, time(15, 5))
        self.check("tomorrow at 10 o'clock", tomorrow, time(10))
        self.check('tomorrow at 15:00', tomorrow, time(15))
        self.check('tomorrow at noon', tomorrow, time(12))
        self.check('tomorrow at midnight', tomorrow, time(0))
        self.check('tomorrow at 12 am', tomorrow, time(0))
        # No AM or PM: 1 to 6 are afternoon, 7 to 11 morning, unless the words say otherwise.
        self.check('Friday at 2', date(2026, 10, 30), time(14))
        self.check('Friday at 9', date(2026, 10, 30), time(9))
        self.check('tonight at 8', TODAY, time(20))
        self.check('tomorrow evening at 7', tomorrow, time(19))
        self.check('tomorrow morning at 6', tomorrow, time(6))

    def test_a_time_alone_is_today(self):
        self.check('at 10', TODAY, time(10))
        self.check('noon', TODAY, time(12))

    def test_ranges_and_lengths(self):
        self.check('from 2 to 3:30', TODAY, time(14), time(15, 30))
        self.check('3 to 4 pm', TODAY, time(15), time(16))
        self.check('11 to 1 pm', TODAY, time(11), time(13))
        self.check('between 9 and 10 am', TODAY, time(9), time(10))
        self.check('Friday at 2 for 45 minutes', date(2026, 10, 30), time(14), minutes=45)
        self.check('at 1 for an hour and a half', TODAY, time(13), minutes=90)
        self.check('at 1 for half an hour', TODAY, time(13), minutes=30)
        self.check('at 1 for 2 hours', TODAY, time(13), minutes=120)
        found = parse_when('meeting from 10 PM to 1 AM', NOW)
        start, end = found.start_end()
        self.assertEqual((start, end), (datetime(2026, 10, 26, 22), datetime(2026, 10, 27, 1)), 'past midnight ends the next day')
        self.assertEqual(parse_when('tomorrow at 3 pm', NOW).start_end()[1], datetime(2026, 10, 27, 16), 'an hour long by default')

    def test_all_day(self):
        found = parse_when('all day Friday', NOW)
        self.assertEqual((found.day, found.all_day, found.start), (date(2026, 10, 30), True, None))

    def test_the_other_words_are_the_name(self):
        self.check('Doctor appointment tomorrow at three thirty', date(2026, 10, 27), time(15, 30), rest='Doctor appointment')
        self.check('lunch on Friday at noon.', date(2026, 10, 30), time(12), rest='lunch')
        self.check('Bob at the cafe tomorrow at 1', date(2026, 10, 27), time(13), rest='Bob at the cafe')
        # A number that is not a time stays in the name.
        self.check('dinner for 3 people Friday at 7 pm', date(2026, 10, 30), time(19), rest='dinner for 3 people')

    def test_the_part_of_the_day_sets_the_time_and_is_not_part_of_the_name(self):
        friday, tomorrow = date(2026, 10, 30), date(2026, 10, 27)
        self.check('Friday at 7 in the evening', friday, time(19))
        self.check('Friday at 9 in the morning', friday, time(9))
        self.check('at two thirty in the afternoon', TODAY, time(14, 30))
        self.check('Friday evening at 7', friday, time(19))
        self.check('Friday morning at 6', friday, time(6))
        self.check('tomorrow at 8 at night', tomorrow, time(20))
        self.check('tomorrow at 12 at night', tomorrow, time(0))
        self.check('haircut Friday at 3 in the afternoon', friday, time(15), rest='haircut')
        # A day with its part of the day, and no time yet.
        found = parse_when('Friday evening', NOW)
        self.assertEqual((found.day, found.start, found.rest), (friday, None, ''))
        # A name that only starts with a part of the day keeps it.
        self.check('Morning walk tomorrow at 7', tomorrow, time(7), rest='Morning walk')

    def test_nothing_found(self):
        found = parse_when('Doctor', NOW)
        self.assertEqual((found.day, found.start, found.rest), (None, None, 'Doctor'))
        self.assertIsNone(parse_when('at 45', NOW).start)
        self.assertIsNone(parse_when('at 13 pm', NOW).start)


if __name__ == '__main__':
    unittest.main()
