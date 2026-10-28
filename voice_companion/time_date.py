"""Check the time and date by voice or keyboard.

F12, or Insert+F12 as in common screen readers, says the time. Pressed twice
quickly, it says the date. Spoken requests: "What time is it?", "What's the
date?", "What day is it?", "What year is it?" and similar.

In a document, email, note or name box, writing comes first: a single word such
as "Time." or "The date." is text. Only a clear question is a request there.
"""
import re
import sys
import time
import unicodedata

# Two presses within this many seconds are a double press, as in JAWS and NVDA.
DOUBLE_PRESS_SECONDS = 0.5

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')

# Requests, after apostrophes and punctuation are removed ("what's" is "whats").
_NOW = r'(?: (?:now|right now|today|please))?'
TIME_QUESTIONS = (r'(?:what time is it|whats the time|what is the time|(?:tell me|say|check) the time'
                  r'|(?:whats|what is) the current time)' + _NOW)
TIME_WORDS = r'(?:the )?(?:current )?time' + _NOW
DATE_QUESTIONS = (r'(?:what date is it|(?:whats|what is) (?:the|todays) date|todays date'
                  r'|(?:tell me|say|check) (?:the|todays) date|what day is (?:it|today)|(?:whats|what is) today'
                  # Older users ask for one part; the full date answers each.
                  r'|(?:what|which) (?:day of the week|month|year) is (?:it|this|today)'
                  r'|(?:whats|what is) the (?:day|month|year)|which day is it)' + _NOW)
DATE_WORDS = r'(?:the )?date' + _NOW
BOTH_QUESTIONS = r'(?:(?:whats|what is|tell me|say) the (?:date and time|time and date))' + _NOW
BOTH_WORDS = r'(?:the )?(?:date and time|time and date)' + _NOW


def _words(text):
    text = str(text).casefold().replace('’', "'").replace("'", '')
    words = ' '.join(re.sub(r'[^\w\s]', ' ', text).split())
    return re.sub(r'^(?:please|ok|okay|hey) ', '', words)


def clock_request(text, writing=False):
    """'time', 'date', 'both' or None for something said or typed.

    With writing=True (a document, email, note or name box), only a clear
    question counts; "Time." or "The date." alone is text."""
    words = _words(text)
    for kind, question, single in (('time', TIME_QUESTIONS, TIME_WORDS), ('date', DATE_QUESTIONS, DATE_WORDS),
                                   ('both', BOTH_QUESTIONS, BOTH_WORDS)):
        if re.fullmatch(question, words) or not writing and re.fullmatch(single, words):
            return kind
    return None


ONES = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven',
        'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen')
TENS = {2: 'twenty', 3: 'thirty', 4: 'forty', 5: 'fifty'}


def _number_words(number):
    if number < 20:
        return ONES[number]
    tens, ones = divmod(number, 10)
    return TENS[tens] + ('-' + ONES[ones] if ones else '')


def spoken_time(when):
    """'nine fifty-five PM', 'three oh seven AM', 'twelve PM': twelve-hour time in words, as
    people say it. Words, not 9:55, so no voice pauses after the hour or says zero (Will, October 2026)."""
    hour = _number_words(when.hour % 12 or 12)
    minute = '' if when.minute == 0 else ' oh ' + ONES[when.minute] if when.minute < 10 else ' ' + _number_words(when.minute)
    return hour + minute + (' AM' if when.hour < 12 else ' PM')


def clean(text):
    """Remove hidden direction marks and extra spaces from a Windows date."""
    text = ''.join(ch for ch in str(text) if unicodedata.category(ch) != 'Cf')
    return ' '.join(text.split())


def with_weekday(text, weekday):
    """The date, with the day of the week first if the format left it out."""
    return text if weekday.casefold() in text.casefold() else weekday + ', ' + text


def regional_long_date(when):
    """The date in the long format chosen in Windows region settings, or None."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(name, wintypes.WORD) for name in
                    ('wYear', 'wMonth', 'wDayOfWeek', 'wDay', 'wHour', 'wMinute', 'wSecond', 'wMilliseconds')]

    moment = SYSTEMTIME(when.year, when.month, (when.weekday() + 1) % 7, when.day, 0, 0, 0, 0)
    kernel = ctypes.windll.kernel32

    def formatted(flags, picture):
        buffer = ctypes.create_unicode_buffer(256)
        # None is the user's own locale (LOCALE_NAME_USER_DEFAULT).
        if not kernel.GetDateFormatEx(None, flags, ctypes.byref(moment), picture, buffer, len(buffer), None):
            return ''
        return clean(buffer.value)

    DATE_LONGDATE = 2
    text = formatted(DATE_LONGDATE, None)
    weekday = formatted(0, 'dddd')
    if not text:
        return None
    return with_weekday(text, weekday) if weekday else text


def spoken_date(when, regional=None):
    """'Thursday, October 22, 2026', or the Windows long date with the weekday."""
    reader = regional_long_date if regional is None else regional
    try:
        text = clean(reader(when) or '')
    except Exception:
        text = ''
    if text:
        return text
    return f'{DAYS[when.weekday()]}, {MONTHS[when.month - 1]} {when.day}, {when.year}'


def announcement(kind, when):
    """What the app says for a request: the time, the date, or both."""
    if kind == 'time':
        return spoken_time(when) + '.'
    if kind == 'date':
        return spoken_date(when) + '.'
    return spoken_time(when) + '. ' + spoken_date(when) + '.'


def clock_key(keysym, state, insert_held=False):
    """True for F12 or Insert+F12. Control+F12 and Shift+F12 are other keys.

    Alt and lock keys are ignored: on Windows, Tk reports Num Lock with the bit
    that means Alt elsewhere, and F12 must work either way."""
    return keysym == 'F12' and not state & (1 | 4)


def save_as_key(mode, prefix_held, command_entry):
    """True when F12 is Save As: in an open document, without Insert or Caps Lock held.

    Insert+F12 and Caps Lock+F12 still say the time in a document, and F12 says the
    time on every other screen (Will, October 2026)."""
    return mode == 'document' and not prefix_held and not command_entry


def key_request(count):
    """The keyboard request for the first or second quick press."""
    return 'Double:F12' if count == 2 else 'F12'


# Keyboard requests and what each one says.
KEY_REQUESTS = {'F12': 'time', 'Double:F12': 'date'}


class PressCounter:
    """Counts quick presses of the clock key: 1, then 2, then 1 again."""

    def __init__(self, clock=time.monotonic, window=DOUBLE_PRESS_SECONDS):
        self.clock = clock
        self.window = window
        self.when = None
        self.count = 0

    def press(self):
        now = self.clock()
        quick = self.when is not None and now - self.when <= self.window
        self.count = 2 if quick and self.count == 1 else 1
        self.when = now
        return self.count
