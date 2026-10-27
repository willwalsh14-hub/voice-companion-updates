"""Calendar events from Thunderbird, in this computer's local time, and how they are said.

Thunderbird gives timed events as moments in UTC (milliseconds since 1970) and
all-day events as dates. Voice Companion keeps the UTC moments and only turns
them into local time to say them, so the time zone never shifts an event.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from spoken_dates import spoken_day
from time_date import DAYS, MONTHS, spoken_time

# Tests set a fixed zone; None means this computer's own time zone (with its daylight saving).
LOCAL_ZONE = None
NO_TITLE = 'No title'


def local(ms):
    """A UTC moment (milliseconds) as a local date and time, without a zone attached."""
    moment = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return (moment.astimezone(LOCAL_ZONE) if LOCAL_ZONE else moment.astimezone()).replace(tzinfo=None)


def utc_ms(when):
    """A local date and time (no zone attached) as a UTC moment in milliseconds."""
    if LOCAL_ZONE:
        return int(when.replace(tzinfo=LOCAL_ZONE).timestamp() * 1000)
    return int(when.timestamp() * 1000)     # naive: Python uses this computer's zone


def day_range(day):
    """The UTC moments that cover one local day, midnight to midnight."""
    start = datetime.combine(day, time())
    return utc_ms(start), utc_ms(start + timedelta(days=1))


def later_day(later, day):
    """'Sunday' for a day in the coming week; 'Sunday, November 15' when it is further away,
    so the weekday alone cannot be mistaken for this week's."""
    name = DAYS[later.weekday()]
    if (later - day).days > 6:
        name += ', ' + MONTHS[later.month - 1] + ' ' + str(later.day)
    return name


@dataclass
class Event:
    id: str
    calendar_id: str
    calendar: str
    title: str
    all_day: bool
    start: datetime          # local; for an all-day event, midnight of its first day
    end: datetime            # local; for an all-day event, midnight after its last day
    location: str = ''
    notes: str = ''
    recurring: bool = False
    occurrence: str = ''

    @property
    def name(self):
        return self.title or NO_TITLE

    def on(self, day):
        """True when the event happens on this local day (an event across midnight is on both)."""
        first = datetime.combine(day, time())
        return self.start < first + timedelta(days=1) and self.end > first or self.start == first and self.end == first

    def when(self, day):
        """The time part as said on a given day: 'ten thirty AM to eleven AM', 'All day',
        'Until one AM', 'Ten PM to one AM tomorrow'."""
        if self.all_day:
            last = (self.end - timedelta(days=1)).date()
            if last > day:
                return 'All day, until ' + later_day(last, day)
            return 'All day'
        starts_today, ends_today = self.start.date() == day, self.end.date() == day or self.end == datetime.combine(day + timedelta(days=1), time())
        if not starts_today and not ends_today:
            return 'All day'
        if not starts_today:
            return 'Until ' + spoken_time(self.end)
        if self.end <= self.start:
            return spoken_time(self.start)
        end = spoken_time(self.end) + ('' if ends_today else ' tomorrow' if self.end.date() == day + timedelta(days=1) else
                                       ' on ' + later_day(self.end.date(), day))
        return spoken_time(self.start) + ' to ' + end

    def line(self, day):
        """What Up and Down say: time, title, calendar. 'Ten thirty AM to eleven AM, Doctor, Google'."""
        text = self.when(day) + ', ' + self.name + (', ' + self.calendar if self.calendar else '')
        return text[0].upper() + text[1:]

    def details(self, today):
        """What Enter says: title, day and time, place, notes, calendar, and whether it repeats."""
        day = self.start.date()
        parts = [self.name, spoken_day(day, today) + ', ' + self.when(day)[0].lower() + self.when(day)[1:]]
        if self.location:
            parts.append('Place: ' + self.location)
        if self.notes.strip():
            parts.append('Notes: ' + ' '.join(self.notes.split()))
        if self.calendar:
            parts.append('Calendar: ' + self.calendar)
        if self.recurring:
            parts.append('This event repeats')
        return '. '.join(part.rstrip('.') for part in parts) + '.'


def from_thunderbird(item):
    """An Event from the add-on's answer, or None when it is damaged."""
    try:
        if item.get('allDay'):
            start = datetime.combine(date.fromisoformat(str(item['start'])), time())
            end = datetime.combine(date.fromisoformat(str(item['end'])), time())
            if end <= start:
                end = start + timedelta(days=1)
        else:
            start, end = local(float(item['start'])), local(float(item['end']))
            if end < start:
                end = start
        return Event(id=str(item.get('id') or ''), calendar_id=str(item.get('calendarId') or ''),
                     calendar=str(item.get('calendar') or ''), title=' '.join(str(item.get('title') or '').split()),
                     all_day=bool(item.get('allDay')), start=start, end=end,
                     location=' '.join(str(item.get('location') or '').split()), notes=str(item.get('notes') or ''),
                     recurring=bool(item.get('recurring')), occurrence=str(item.get('occurrence') or ''))
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def for_day(items, day):
    """The events on one day: all-day first, then by start time, then by name."""
    events = [event for event in (from_thunderbird(item) for item in items if isinstance(item, dict)) if event and event.id]
    events = [event for event in events if event.on(day)]
    return sorted(events, key=lambda e: (not e.all_day, e.start, e.name.casefold()))
