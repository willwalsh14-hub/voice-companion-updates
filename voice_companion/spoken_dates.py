"""Dates and times as people say them, for the Calendar.

"tomorrow at 3 PM", "next Friday", "October 30 at 10", "the 30th", "10/30",
"in 3 days", "from 2 to 3:30", "for 45 minutes", "all day", "noon".

Speech recognizers add periods, commas and capitals ("3 P.M.", "Friday."),
and say numbers as words ("three thirty"); all of that is accepted.

parse_day() finds one day (for "go to October 30" or "what's on Friday").
parse_when() finds a day, a time or time range, and what is left, which is
the event's name ("add appointment tomorrow at 3 PM called doctor").
Nothing here knows the clock: callers pass today or now.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from time_date import DAYS, MONTHS, spoken_time

WEEKDAYS = tuple(day.casefold() for day in DAYS)
MONTH_NAMES = {name.casefold(): number for number, name in enumerate(MONTHS, 1)}
MONTH_NAMES.update({name.casefold()[:3]: number for number, name in enumerate(MONTHS, 1)})
MONTH_NAMES.update({'sept': 9})

_UNITS = ('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
          'sixteen seventeen eighteen nineteen').split()
_ORDINALS = ('zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth '
             'thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth').split()
NUMBER_WORDS = {word: n for n, word in enumerate(_UNITS)}
NUMBER_WORDS.update({word: n for n, word in enumerate(_ORDINALS)})
NUMBER_WORDS.update({'twenty': 20, 'twentieth': 20, 'thirty': 30, 'thirtieth': 30, 'forty': 40, 'fifty': 50,
                     'a': 1, 'an': 1, 'oh': 0})
for _tens, _name in ((20, 'twenty'), (30, 'thirty'), (40, 'forty'), (50, 'fifty')):
    for _n in range(1, 10):
        for _sep in (' ', '-'):
            NUMBER_WORDS[_name + _sep + _UNITS[_n]] = _tens + _n
            NUMBER_WORDS[_name + _sep + _ORDINALS[_n]] = _tens + _n

# One number: digits ("30", "30th") or words ("thirty", "thirty-first", "twenty one").
_WORD_NUMBERS = sorted((w for w in NUMBER_WORDS if w not in ('a', 'an', 'oh')), key=len, reverse=True)
NUM = r'(?:\d{1,4}(?:st|nd|rd|th)?|' + '|'.join(re.escape(w) for w in _WORD_NUMBERS) + r')'
MONTH = r'(?:' + '|'.join(sorted(MONTH_NAMES, key=len, reverse=True)) + r')\.?'
WEEKDAY = r'(?:' + '|'.join(WEEKDAYS) + r')'
MERIDIEM = r'(?:a\.?\s?m\.?|p\.?\s?m\.?)'
# 3, 3:30, 3 30, three thirty, 3 oh 5, 15:00, then AM or PM.
CLOCK = (r'(?P<{p}h>' + NUM + r')(?:(?::|\s)(?P<{p}m>[0-5]\d|oh\s' + NUM + r'|' + NUM + r'))?'
         r'(?:\s?o\'?clock)?(?:\s?(?P<{p}x>' + MERIDIEM + r'))?')
NAMED_TIMES = {'noon': time(12, 0), 'midday': time(12, 0), 'midnight': time(0, 0)}


def number(text):
    """The value of NUM, or None."""
    text = str(text).strip().casefold()
    if not text:
        return None
    digits = re.fullmatch(r'(\d+)(?:st|nd|rd|th)?', text)
    if digits:
        return int(digits[1])
    if text.startswith('oh '):
        return number(text[3:])
    return NUMBER_WORDS.get(text.replace('  ', ' '))


def prepare(text):
    """Lower case, with the same length, so places found here are places in the original text.
    Commas and other sentence marks become spaces; periods stay for "p.m." and "10.30"."""
    lowered = str(text).casefold().replace('’', "'")
    if len(lowered) != len(str(text)):
        lowered = str(text).lower()
    return re.sub(r'[,;!?"()]', ' ', lowered)


@dataclass
class When:
    day: date = None
    start: time = None
    end: time = None
    minutes: int = None          # a length: "for 45 minutes"
    all_day: bool = False
    rest: str = ''               # the words that were not a date or time
    spans: list = field(default_factory=list)

    def start_end(self):
        """(start, end) datetimes for a timed event; an hour long unless a length or end was given."""
        start = datetime.combine(self.day, self.start)
        if self.end is not None:
            end = datetime.combine(self.day, self.end)
            if end <= start:
                end += timedelta(days=1)      # "from 10 PM to 1 AM" goes past midnight
        else:
            end = start + timedelta(minutes=self.minutes or 60)
        return start, end


# ---- Days ------------------------------------------------------------------------

def _closest_year(month, day, today, future):
    """The year that makes month and day the date people mean. For a new event, the next one
    to come; for looking at the calendar, the nearest one (October 20 is last week, not next year)."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    if future:
        upcoming = [c for c in candidates if c >= today]
        return min(upcoming) if upcoming else None
    return min(candidates, key=lambda c: (abs((c - today).days), c < today))


def _full_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


DAY_PATTERNS = (
    ('after', r'\b(?:the\s)?day\safter\stomorrow\b'),
    ('relative', r'\b(?:today|tonight|this\s(?:morning|afternoon|evening))\b'),
    ('tomorrow', r'\btomorrow(?:\s(?:morning|afternoon|evening|night))?\b'),
    ('yesterday', r'\byesterday\b'),
    ('in', r'\bin\s(?P<n>' + NUM + r'|a|an)\s(?P<unit>days?|weeks?)\b'),
    ('monthday', r'\b(?:on\s)?(?P<month>' + MONTH + r')\s(?:the\s)?(?P<day>' + NUM + r')(?:\s?,?\s(?P<year>\d{4}))?\b'),
    ('daymonth', r'\b(?:on\s)?(?:the\s)?(?P<day>' + NUM + r')\s(?:of\s)?(?P<month>' + MONTH + r')(?:\s?,?\s(?P<year>\d{4}))?\b'),
    # 2026-12-25, as many programs write a date. (Never 10-30: that is a time range.)
    ('iso', r'\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b'),
    ('numeric', r'\b(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2}|\d{4}))?\b'),
    ('weekday', r'\b(?:on\s)?(?P<which>this\scoming\s|this\spast\s|this\s|next\s|coming\s|last\s|previous\s|past\s)?(?P<weekday>' + WEEKDAY + r')\b'),
    ('ordinal', r'\b(?:on\s)?the\s(?P<day>\d{1,2}(?:st|nd|rd|th)|' + '|'.join(re.escape(w) for w in _ORDINALS[1:] + ['twentieth', 'thirtieth']
                                                                          + [t + s + o for t in ('twenty', 'thirty') for s in (' ', '-') for o in _ORDINALS[1:10]]) + r')\b'),
)


def find_day(text, today, future=False):
    """(date, (start, end) of the words) for the first day named in text, or (None, None)."""
    low = prepare(text)
    best = None
    for kind, pattern in DAY_PATTERNS:
        for match in re.finditer(pattern, low):
            found = _day_from(kind, match, today, future)
            if found is not None and (best is None or match.start() < best[1][0]):
                best = (found, match.span())
            if found is not None:
                break
    return best if best else (None, None)


def _day_from(kind, match, today, future):
    if kind == 'relative':
        return today
    if kind == 'tomorrow':
        return today + timedelta(days=1)
    if kind == 'after':
        return today + timedelta(days=2)
    if kind == 'yesterday':
        return today - timedelta(days=1)
    if kind == 'in':
        count = 1 if match['n'] in ('a', 'an') else number(match['n'])
        if count is None or count > 400:
            return None
        return today + timedelta(days=count * (7 if match['unit'].startswith('week') else 1))
    if kind == 'weekday':
        wanted = WEEKDAYS.index(match['weekday'])
        if (match['which'] or '').strip() in ('last', 'previous', 'past', 'this past'):
            return today - timedelta(days=(today.weekday() - wanted) % 7 or 7)     # "last Friday": the one before today
        ahead = (wanted - today.weekday()) % 7
        if (match['which'] or '').strip() == 'next' and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    month = _month(kind, match)
    day = number(match['day'])
    if kind == 'ordinal':
        month = today.month
    if not month or not day or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    year = match.groupdict().get('year')
    if year:
        year = int(year)
        if year < 100:
            year += 2000
        return _full_date(year, month, day)
    if kind == 'ordinal':
        # "the 30th": this month, or next month once it has passed.
        found = _full_date(today.year, today.month, day)
        if found is None or found < today:
            following = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
            found = _full_date(following.year, following.month, day)
        return found
    return _closest_year(month, day, today, future)


def _month(kind, match):
    if kind in ('numeric', 'iso'):
        return int(match['month'])
    return MONTH_NAMES.get(str(match.groupdict().get('month') or '').rstrip('.'))


LEADING_WEEKDAY = r'^(?:on\s+)?' + WEEKDAY + r'\s*,?\s+(?=\S)'


def only_day(text, today, future=False):
    """The day when the words are only a date, else None: "Friday", "the 15th", "10/30",
    or "Friday, October 30, 2026" the way Companion says a date. "Friday lunch" is not a date."""
    words = str(text).strip().strip(' .,!?;:')
    found, span = find_day(words, today, future)
    if found is not None and span == (0, len(words)):
        return found
    # A weekday before a full date: the date says which day it is.
    weekday = re.match(LEADING_WEEKDAY, prepare(words))
    if weekday:
        rest = words[weekday.end():]
        found, span = find_day(rest, today, future)
        if found is not None and span == (0, len(rest)):
            return found
    return None


def missing_date(text, today):
    """A plain sentence when the words name a month and day that do not exist ("There is no
    February 30."), else ''."""
    import calendar
    low = prepare(text)
    for kind, pattern in DAY_PATTERNS:
        if kind not in ('monthday', 'daymonth', 'numeric', 'iso'):
            continue
        for match in re.finditer(pattern, low):
            month, day = _month(kind, match), number(match['day'])
            if not month or not 1 <= month <= 12 or not day or _day_from(kind, match, today, False) is not None:
                continue
            year = match.groupdict().get('year')
            if month == 2 and day == 29 and not year:
                leap = next(y for y in range(today.year, today.year + 8) if calendar.isleap(y))
                return 'February 29 is only in a leap year, such as ' + str(leap) + '. Say the year too.'
            year = int(year) + (2000 if year and int(year) < 100 else 0) if year else None
            return 'There is no ' + MONTHS[month - 1] + ' ' + str(day) + (', ' + str(year) if year else '') + '.'
    return ''


def parse_day(text, today):
    """The day named in text ("Friday", "October 30", "tomorrow"), or None."""
    return find_day(text, today)[0]


# ---- Times -----------------------------------------------------------------------

def _clock(match, prefix):
    hour = number(match[prefix + 'h'])
    minute_text = match[prefix + 'm']
    minute = number(minute_text) if minute_text else 0
    meridiem = (match[prefix + 'x'] or '').replace('.', '').replace(' ', '')
    if hour is None or minute is None or not 0 <= minute <= 59:
        return None
    if hour > 23 or (meridiem and not 1 <= hour <= 12):
        return None
    return hour, minute, meridiem


def _resolve(hour, minute, meridiem, hint):
    """A time of day. With no AM or PM: hours 1 to 6 are afternoon, 7 to 11 morning, as people
    usually mean them, unless the words said "morning", "tonight" or "evening"."""
    if meridiem == 'am':
        hour = 0 if hour == 12 else hour
    elif meridiem == 'pm':
        hour = hour if hour == 12 else hour + 12
    elif hour <= 12:
        if hint == 'am':
            hour = 0 if hour == 12 else hour
        elif hint == 'pm':
            hour = hour if hour == 12 else hour + 12
        elif 1 <= hour <= 6:
            hour += 12
    return time(hour % 24, minute)


TIME_RANGE = (r'\b(?:from\s|between\s)?(?:at\s)?' + CLOCK.replace('{p}', 'a') + r'\s?(?:to|until|till|through|and|-)\s?' +
              CLOCK.replace('{p}', 'b') + r'\b')
TIME_ONE = r'\b(?:at\s|by\s|around\s|starting\sat\s)?(?:(?P<named>noon|midday|midnight)|' + CLOCK.replace('{p}', 'a') + r')\b'
LENGTH = (r'\bfor\s(?:(?P<half>half\san\shour)|(?P<n>' + NUM + r'|a|an)\s(?P<unit>hours?|hrs?|minutes?|mins?)'
          r'(?:\s(?:and\s)?(?:(?P<andhalf>a\shalf)|(?P<m>' + NUM + r')\s(?:minutes?|mins?)))?)\b')
ALL_DAY = r'\b(?:all\sday|all-day|the\swhole\sday)(?:\slong)?\b'


def _hint(low):
    if re.search(r'\b(?:tonight|evening|this\safternoon|tomorrow\s(?:afternoon|evening|night))\b', low):
        return 'pm'
    if re.search(r'\bmorning\b', low):
        return 'am'
    return ''


def _taken(spans, start, end):
    return any(start < b and a < end for a, b in spans)


def parse_when(text, now, future=True, base=None):
    """A day, time and length from words such as "tomorrow from 2 to 3:30 PM called dentist".

    "Tomorrow" and "Friday" count from today. When only a time is given, the day is base
    (the day shown in the Calendar), else today. rest holds the other words."""
    today = now.date() if isinstance(now, datetime) else now
    low = prepare(text)
    result = When()
    found, span = find_day(text, today, future)
    if found is not None:
        result.day = found
        result.spans.append(span)
    hint = _hint(low)
    if re.search(ALL_DAY, low):
        match = re.search(ALL_DAY, low)
        result.all_day = True
        result.spans.append(match.span())
    for match in re.finditer(TIME_RANGE, low):
        if _taken(result.spans, *match.span()):
            continue
        first, second = _clock(match, 'a'), _clock(match, 'b')
        if not first or not second or not (first[2] or second[2] or match[0].lstrip().startswith(('from', 'between', 'at'))):
            continue
        meridiem_a, meridiem_b = first[2], second[2]
        end = _resolve(second[0], second[1], meridiem_b, hint)
        if meridiem_b and not meridiem_a and first[0] <= 12:
            # "11 to 1 PM": the start is the same half of the day unless that puts it after the end.
            start = _resolve(first[0], first[1], meridiem_b, hint)
            if start >= end:
                start = _resolve(first[0], first[1], 'am' if meridiem_b == 'pm' else 'pm', hint)
        else:
            start = _resolve(first[0], first[1], meridiem_a, hint)
            if not meridiem_b and second[0] <= 12:
                end = _resolve(second[0], second[1], meridiem_a, hint)
                if end <= start and not meridiem_a:
                    end = _resolve(second[0], second[1], 'pm', hint)
        result.start, result.end = start, end
        result.spans.append(match.span())
        break
    if result.start is None:
        for match in re.finditer(TIME_ONE, low):
            if _taken(result.spans, *match.span()):
                continue
            if match['named']:
                result.start = NAMED_TIMES[match['named']]
            else:
                clock = _clock(match, 'a')
                words = match[0].strip()
                # A bare number is a time only with "at", AM or PM, a colon, or o'clock ("3 people" is not).
                if not clock or not (words.startswith(('at ', 'by ', 'around ', 'starting ')) or clock[2] or ':' in words or 'clock' in words):
                    continue
                result.start = _resolve(clock[0], clock[1], clock[2], hint)
            result.spans.append(match.span())
            break
    length = re.search(LENGTH, low)
    if length and not _taken(result.spans, *length.span()):
        if length['half']:
            minutes = 30
        else:
            count = 1 if length['n'] in ('a', 'an') else number(length['n'])
            minutes = None if count is None else count * (60 if length['unit'].startswith('h') else 1)
            if minutes is not None and length['andhalf']:
                minutes += 30
            elif minutes is not None and length['m']:
                extra = number(length['m'])
                minutes = minutes + extra if extra is not None else minutes
        if minutes and 0 < minutes <= 24 * 60:
            result.minutes = minutes
            result.spans.append(length.span())
    if result.day is None and (result.start is not None or result.all_day):
        result.day = base or today
    rest = list(str(text))
    for a, b in result.spans:
        rest[a:b] = [' '] * (b - a)
    # Words such as "on", "at" or "from" left beside a date or time are not part of a name.
    words = ' '.join(''.join(rest).split()).strip(' .,:;-')
    words = re.sub(r'^(?:(?:on|at|from|for|the|by)\s+)+|(?:\s+(?:on|at|from|for|the|by))+$', '', words, flags=re.I)
    result.rest = words.strip(' .,:;-')
    return result


def spoken_day(day, today):
    """'Today, Monday, October 26', 'Tomorrow, Tuesday, October 27', 'Friday, October 30',
    with the year when it is not this year."""
    name = DAYS[day.weekday()] + ', ' + MONTHS[day.month - 1] + ' ' + str(day.day)
    if day.year != today.year:
        name += ', ' + str(day.year)
    offset = (day - today).days
    return {0: 'Today, ', 1: 'Tomorrow, ', -1: 'Yesterday, '}.get(offset, '') + name


def spoken_clock(moment):
    """A time in words, as the app's clock says it: 'three thirty PM'."""
    return spoken_time(moment)
