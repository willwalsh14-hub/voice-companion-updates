"""The Calendar screen: one day at a time, from Thunderbird's calendars.

Keys (each also has a voice command, see input_parity.SCREEN_KEYS and the guide):
- It opens on today: "Today, Monday, October 26. 3 events." then the first event.
- Up and Down move through the day's events: "Ten thirty AM to eleven AM, Doctor, Google".
- Left and Right go to the previous or next day. Page Up and Page Down go a week.
  Home goes back to today. Enter reads the time, place and notes.
- Control+G goes to a date that you type or say ("October 30", "next Friday", "10/30").
- Control+N adds an event: when, then its name, then a Yes or No question.
- F2 edits the event: choose what to change (name, day and time, length, all day,
  place, notes, calendar), say the new value, then answer a Yes or No question.
  "Rename event to Dentist", "move event to Friday at 2" and "change the time to
  3 PM" do it in one step. A repeating event asks "this one or all".
- Delete removes one after a Yes or No question; for a repeating event it asks
  "this one or all". F1 is help, Escape goes to the main menu, Alt opens the menu.
- With two or more email accounts in Thunderbird, Control+L chooses which calendar
  to show: one, every one of an account, or all (pim_sources.py). "Show Gmail
  calendar", "show all calendars" and "which calendar am I in" do it by voice.
  With one account none of this is offered or said.

"What's on my calendar today?" (or tomorrow, or a day) works from any screen
where words are commands, and "add appointment tomorrow at 3 PM called doctor"
from the main menu. companion.py calls into this module from a few small hooks;
`m` is the companion module.
"""
import re
from datetime import datetime, time, timedelta

import calendar_events
import pim_sources
import thunderbird_pim
from menu_navigation import announce_item
from spoken_dates import MONTH_NAMES, WEEKDAYS, find_day, missing_date, only_day, parse_day, parse_when, spoken_day

MODE = 'calendar'
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'save', 'save it', 'yes save it', 'enter', 'do it')
NO = ('no', 'no thanks', 'cancel', 'escape', 'go back', 'back', 'do not save', 'keep it', 'do not delete')
PASS_THROUGH = ('help', 'options', 'open options', 'exit companion', 'go to sleep',
                'wake up', 'user guide', 'open user guide', 'manual', 'open manual', 'release notes', 'what mode',
                'what version', 'toggle audio', 'calendar menu', 'menu', 'open menu')
OPEN = ('calendar', 'open calendar', 'my calendar', 'show calendar', 'show my calendar', 'open my calendar', 'calendar app',
        'go to calendar', 'go to my calendar', 'open the calendar', 'view calendar', 'view my calendar', 'my schedule',
        'show my schedule', 'open my schedule', 'my appointments', 'show my appointments')
ADD_WORDS = r'(?:add|new|create|make|schedule|set up|book|put)(?: an?| new)?(?: (?:event|appointment|meeting|reminder))?'
ADD = re.compile(r'(?:' + ADD_WORDS + r')(?:(?: to| on)(?: my)? calendar)?')
# "Add an appointment", "schedule a doctor appointment", "add a reminder": up to two words may name it,
# but a note, email or list about a meeting is not an event.
ADD_START = re.compile(r'(?:add|new|create|make|schedule|book)(?: an?| new)? (?:event|appointment|meeting|reminder)\b|'
                       r'(?:add|create|make|schedule|book)(?: an?| new| my)?'
                       r'(?: (?!(?:notes?|e ?mails?|messages?|lists?|contacts?|documents?|files?|letters?|about|for|to)\b)\w+){1,2}'
                       r' (?:appointment|meeting)\b')
ASK = re.compile(r"(?:(?:what s|what is|whats) on (?:my )?(?:calendar|schedule)|(?:what s|what is|whats) on|"
                 r"what do i have(?: on| going on)?|what s happening|read (?:my )?calendar(?: for)?|"
                 r"(?:my )?(?:calendar|schedule) for)(?: for)?(?: (?P<day>.+))?")
GUIDANCE = ('Only the calendar kept on this computer is in Thunderbird. To hear your Google or other online calendar here, '
            'add it to Thunderbird: open Thunderbird, choose New Calendar, then On the Network, and sign in. '
            'Press F1 for the steps.')

DAY = None             # the day on screen
EVENTS = []
INDEX = 0
NOTICE = ''            # why events may be missing
CALENDARS = []         # [{'id', 'name', 'type', 'readOnly'}]
PENDING = None         # ('add', draft), ('delete', event), ('edit-field', event) or ('edit', EDIT): a question
CHOICE = 'no'
CHOICES = ('yes', 'no')
DRAFT = {}             # the new event while its time and name are asked
LAST_CALENDAR = None   # the calendar the last event was added to
EDIT = {}              # the event being changed and its new values (see start_editing)
SOURCES = None         # pim_sources.Sources for CALENDARS
SOURCE = pim_sources.ALL   # which calendars are shown: 'all', 'account:<email>' or 'source:<id>'
SOURCE_NOTICE = ''     # said once when the calendar chosen last time is gone
NOTICE_SAID = False    # the last show() said SOURCE_NOTICE, so "Showing all calendars" is not said too


def today(m):
    return m.CLOCK().date()


def reset():
    global DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES, SOURCES, SOURCE, SOURCE_NOTICE
    DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES = None, [], 0, '', [], None, {}, 'no', ('yes', 'no')
    SOURCES, SOURCE, SOURCE_NOTICE = None, pim_sources.ALL, ''
    EDIT.clear()


# ---- Reading Thunderbird -----------------------------------------------------------

def load_calendars(m):
    """The calendars that are turned on, and which of them are shown (the choice kept from last time)."""
    global CALENDARS, SOURCES, SOURCE, SOURCE_NOTICE
    CALENDARS = [c for c in thunderbird_pim.calendars() if isinstance(c, dict)]
    first = SOURCES is None
    SOURCES = pim_sources.Sources('calendar', CALENDARS)
    if first:
        SOURCE, SOURCE_NOTICE = pim_sources.resolve(m.APP, 'calendar', SOURCES)
    elif not SOURCES.has(SOURCE):
        # The calendar on screen was removed in Thunderbird while Calendar was open.
        SOURCE_NOTICE = SOURCES.words(SOURCE) + ' is no longer in Thunderbird, so all calendars are shown.' if SOURCES.active() else ''
        SOURCE = pim_sources.ALL
        pim_sources.remember(m.APP, 'calendar', SOURCE, SOURCES.words(SOURCE))
    return CALENDARS


def choosing():
    """True when there is a choice of calendars (two or more email accounts)."""
    return SOURCES is not None and SOURCES.active()


def showing():
    """'Showing all calendars.' or 'Showing Calendar, Outlook, vet@outlook.com.'; '' with one account."""
    return 'Showing ' + lower_first(SOURCES.words(SOURCE)) + '.' if choosing() else ''


def lower_first(words):
    return words[0].lower() + words[1:] if words.startswith('All ') else words


def shown_ids():
    return SOURCES.ids(SOURCE) if choosing() else None


def calendar_words(calendar):
    """How a calendar is named in a question: 'the Home calendar', or with more than one account
    'Calendar, Outlook, vet@outlook.com', so two calendars called Calendar are told apart."""
    if choosing():
        return SOURCES.label(SOURCES.find(calendar.get('id')) or dict(calendar, account=''))
    return 'the ' + str(calendar.get('name') or '') + ' calendar'


def hidden_commands():
    """Alt menu items that do not apply: choosing a calendar, with only one account."""
    return () if choosing() else ('choose calendar',)


def events_on(day, ids=None):
    """The events on one local day, from every calendar, or only those with these calendar ids."""
    start, end = calendar_events.day_range(day)
    # A little before and after, so an event across midnight is found from either side.
    answer = thunderbird_pim.events(start - 86400000, end + 86400000)
    failed = [str(name) for name in answer.get('failed', []) if name]
    events = calendar_events.for_day(answer['events'], day)
    if ids is not None:
        events = [event for event in events if event.calendar_id in ids]
        names = {str(c.get('name') or '') for c in CALENDARS if str(c.get('id')) in ids}
        failed = [name for name in failed if name in names]
    if choosing():
        for event in events:
            found = SOURCES.find(event.calendar_id)
            if found:
                event.calendar = SOURCES.short(found)    # two calendars called Calendar are told apart
    return events, failed


def show(m, day, opening=False):
    """Go to a day and say it: "Today, Monday, October 26. 3 events. <first event>"."""
    global DAY, EVENTS, INDEX, NOTICE, SOURCE_NOTICE
    DAY, INDEX, NOTICE = day, 0, ''
    try:
        if opening or not CALENDARS:
            load_calendars(m)
        EVENTS, failed = events_on(day, shown_ids())
        if failed:
            NOTICE = ' '.join(failed_words(name) for name in failed[:3])
    except thunderbird_pim.PimError as exc:
        EVENTS, NOTICE = [], str(exc)
    words = spoken_day(day, today(m)) + '. ' + day_summary()
    global NOTICE_SAID
    NOTICE_SAID = bool(SOURCE_NOTICE)
    if SOURCE_NOTICE:
        words = SOURCE_NOTICE + ' ' + words
        SOURCE_NOTICE = ''
    if NOTICE:
        words += ' ' + NOTICE
    elif opening and CALENDARS and all(c.get('type') == 'storage' for c in CALENDARS):
        words += ' ' + GUIDANCE
    elif opening and not CALENDARS:
        words += ' Thunderbird has no calendars turned on. Press F1 to learn how to add one.'
    return words


def failed_words(name):
    """'The Holidays calendar could not be read.' With two accounts, the calendar's full name when only one has it."""
    same = [c for c in CALENDARS if str(c.get('name') or '') == name]
    if choosing() and len(same) == 1:
        return SOURCES.label(SOURCES.find(same[0].get('id'))) + ' could not be read.'
    return 'The ' + name + ' calendar could not be read.'


def day_summary():
    if not EVENTS:
        return 'No events.'
    count = str(len(EVENTS)) + (' event. ' if len(EVENTS) == 1 else ' events. ')
    return count + describe()


def describe(index=None):
    if not EVENTS:
        return 'No events.'
    index = INDEX if index is None else index
    return announce_item(EVENTS[index].line(DAY), index, len(EVENTS), hotkey=False)


def current():
    return EVENTS[INDEX] if EVENTS and 0 <= INDEX < len(EVENTS) else None


def open_screen(m, mode=None, day=None, quiet=False):
    """quiet: open on the day without saying it, when the next question is said at once
    ("Add an event." from the main menu); a second message would cut off the first."""
    reset()
    if not thunderbird_pim.usable(m.APP):
        m.speak(thunderbird_pim.NO_THUNDERBIRD)
        return mode or 'awake'
    global OPENED
    words = show(m, day or today(m), opening=True)
    OPENED = 'Calendar. ' + with_showing(words)
    if not quiet:
        m.speak(OPENED)
    return MODE


OPENED = ''            # what opening the screen says, kept when it opened quietly


def with_showing(words):
    """The day's words after which calendars are shown, unless a notice already said it."""
    return (showing() + ' ' if choosing() and not NOTICE_SAID else '') + words


# ---- Hooks from companion.py -------------------------------------------------------

def context(m, value):
    if PENDING is not None:
        value['confirmation'] = CHOICE
    value['menu_letters'] = False
    return value


def navigation_key(m, key):
    global CHOICE
    if PENDING is not None:
        if key.lower() in ('y', 'alt+y'):
            return 'this one' if CHOICES[0] == 'this one' else 'yes'
        if key.lower() in ('n', 'alt+n'):
            return 'no'
        if key in ('Up', 'Left', 'ShiftTab', 'Home', 'Down', 'Right', 'Tab', 'End'):
            place = CHOICES.index(CHOICE) if CHOICE in CHOICES else len(CHOICES) - 1
            if key in ('Home', 'End'):
                wanted = 0 if key == 'Home' else len(CHOICES) - 1
            else:
                wanted = place + (-1 if key in ('Up', 'Left', 'ShiftTab') else 1)
            if not 0 <= wanted < len(CHOICES) or wanted == place:
                m.speak('Start of list.' if wanted <= 0 and key in ('Up', 'Left', 'ShiftTab', 'Home') else 'End of list.')
            else:
                CHOICE = CHOICES[wanted]
                m.speak(choice_words())
                m.sync_app_context(MODE)
            return None
        if key in ('Enter', 'Space') and PENDING[0] == 'edit-field':
            return '@calendar:field ' + CHOICE      # "time" alone would say the clock time
        if key in ('Enter', 'Space') and PENDING[0] == 'source':
            return '@calendar:source ' + CHOICE
        return {'Enter': CHOICE, 'Space': CHOICE, 'Escape': 'no', 'F1': 'help with calendar'}.get(key)
    return {'Up': 'previous event', 'Down': 'next event', 'Enter': 'read event', 'Escape': 'close calendar'}.get(key)


def choice_names():
    """The lines the window shows while choosing a calendar, else None."""
    if PENDING and PENDING[0] == 'source':
        return [SOURCES.words(choice) for choice in CHOICES]
    return None


def choice_words():
    """What the arrows say on a question: "Yes.", "This one only.", or "Place: VA clinic, 5 of 7."."""
    if PENDING and PENDING[0] == 'edit-field':
        return announce_item(field_label(CHOICE, PENDING[1]), CHOICES.index(CHOICE), len(CHOICES), hotkey=False)
    if PENDING and PENDING[0] == 'source':
        return announce_item(SOURCES.words(CHOICE), CHOICES.index(CHOICE), len(CHOICES), hotkey=False)
    return {'this one': 'This one only.', 'all': 'All of them.'}.get(CHOICE, CHOICE.capitalize() + '.')


# ---- Commands -------------------------------------------------------------------

def handle(m, text, mode, typed=False):
    """Calendar commands, the phrases that open it, and "what's on my calendar" anywhere. None: not ours."""
    raw = text.strip()
    if raw.startswith('@calendar:'):
        return internal(m, raw, mode)
    command = m.normalized_command(raw)
    if mode != MODE:
        if mode in ('task_input', 'document_name', 'search', 'settings') or m.clock_text_entry(mode, typed) \
                or getattr(m, 'NAMED_LIST', None):
            return None
        asked = ASK.fullmatch(command)
        if asked and (asked['day'] is None or whole_day(asked['day'], m) is not None):
            if command.startswith(('what s on', 'what is on', 'whats on')) and 'calendar' not in command and 'schedule' not in command \
                    and mode not in ('awake',):
                return None     # "what's on" alone is only for the main menu; elsewhere it may be about a page
            return tell_day(m, asked['day'] or 'today', mode)
        if mode == 'awake' and command in OPEN:
            return open_screen(m, mode)
        wanted = SHOW.fullmatch(command)
        if mode == 'awake' and wanted and wanted['verb'] and wanted['kind'] and wanted['what'] not in ('my', 'the'):
            # "Show Gmail calendar" from the main menu: open Calendar on that calendar, and say it once.
            opened = open_screen(m, mode, quiet=True)
            if opened != MODE:
                return opened
            if not choosing():
                m.speak(OPENED)
                return MODE
            return show_source(m, wanted['what'], lead='Calendar. ')
        if mode == 'awake' and (ADD.fullmatch(command) and command.split()[0] in ('add', 'new', 'create', 'schedule')
                                and command not in ('add', 'new', 'create', 'schedule') or ADD_START.match(command)):
            opened = open_screen(m, mode, quiet=True)
            if opened != MODE:
                return opened
            return start_adding(m, raw)
        return None
    try:
        return command_here(m, raw, command)
    except (ValueError, OSError) as exc:
        m.speak(str(exc))
        return MODE


def tell_day(m, words, mode):
    """'What's on my calendar tomorrow?' from any screen: the day's events, without moving."""
    day = whole_day(words, m) or today(m)
    if not thunderbird_pim.usable(m.APP):
        m.speak(thunderbird_pim.NO_THUNDERBIRD)
        return mode
    try:
        events, _ = events_on(day)
    except thunderbird_pim.PimError as exc:
        m.speak(str(exc))
        return mode
    head = spoken_day(day, today(m)) + '. '
    if not events:
        m.speak(head + 'No events.')
        return mode
    lines = [event.line(day) for event in events[:12]]
    more = len(events) - len(lines)
    m.speak(head + str(len(events)) + (' event. ' if len(events) == 1 else ' events. ') + '. '.join(lines) + '.' +
            (' And ' + str(more) + ' more. Open Calendar to hear them.' if more > 0 else ''))
    return mode


def command_here(m, raw, command):
    global PENDING, INDEX, CHOICE
    if PENDING is not None:
        return answer(m, command)
    if command in PASS_THROUGH or command.startswith(('help with ', 'help for ')) or m.speech_control(raw) \
            or m.speech_setting_request(raw) is not None or m.spoken_control(command) or command in m.VOICE_LIST_COMMANDS:
        return None
    if command in ('close calendar', 'leave calendar', 'exit calendar', 'escape', 'go back', 'back', 'main menu',
                   'back to main menu', 'go to main menu'):
        reset()
        return 'awake'      # handle() says Main menu when the screen changes
    if command in OPEN + ('where am i', 'which day', 'what day is this', 'read day', 'repeat', 'refresh', 'refresh calendar'):
        words = show(m, DAY or today(m), opening=command.startswith('refresh'))
        if command in OPEN + ('where am i',):
            words = 'Calendar. ' + with_showing(words)      # the screen's name first, as on Contacts
        elif command in ('refresh', 'refresh calendar'):
            words = with_showing(words)
        m.speak(words)
        return MODE
    if command in CHOOSE:
        return start_choosing(m)
    if command in WHICH:
        m.speak(showing() + ' ' + CHOOSE_HINT if choosing() else 'All your calendars are shown.')
        return MODE
    days = {'today': 0, 'go to today': 0, 'back to today': 0, 'home': 0, 'next day': 1, 'previous day': -1,
            'next week': 7, 'previous week': -7, 'last week': -7, 'day after': 1, 'day before': -1}
    if command in days:
        base = today(m) if days[command] == 0 else DAY or today(m)
        m.speak(show(m, base + timedelta(days=days[command])))
        return MODE
    if command in ('tomorrow', 'yesterday'):
        m.speak(show(m, today(m) + timedelta(days=1 if command == 'tomorrow' else -1)))
        return MODE
    moves = {'next event': 1, 'next': 1, 'down': 1, 'previous event': -1, 'previous': -1, 'up': -1}
    ends = {'first event': 0, 'start of list': 0, 'first item': 0, 'last event': -1, 'end of list': -1, 'last item': -1}
    if command in moves or command in ends:
        if not EVENTS:
            m.speak('No events. Press Right Arrow for the next day.')
            return MODE
        old = INDEX
        if command in ends:
            INDEX = 0 if ends[command] == 0 else len(EVENTS) - 1
            edge = 'Start of list. ' if INDEX == old == 0 and ends[command] == 0 else 'End of list. ' if INDEX == old and ends[command] else ''
        else:
            INDEX = max(0, min(len(EVENTS) - 1, old + moves[command]))
            edge = ('Start of list. ' if moves[command] < 0 else 'End of list. ') if INDEX == old else ''
        m.speak(edge + describe())
        return MODE
    if command in ('read event', 'event details', 'details', 'read details', 'enter', 'okay', 'ok', 'that one', 'open',
                   'open event', 'read it', 'what is this'):
        event = current()
        m.speak(event.details(today(m)) if event else 'No events on this day.')
        return MODE
    if command in GO_TO_WORDS:
        return ask(m, 'go-to', GO_TO_PROMPT)
    if command in EDIT_START:
        return start_editing(m)
    phrase = edit_phrase(raw)
    if phrase is not None:
        return start_editing(m, *phrase)
    if command in ('delete event', 'remove event', 'delete this event', 'delete appointment', 'cancel event',
                   'cancel appointment', 'delete', 'remove', 'delete it', 'remove it', 'delete the event', 'delete the appointment',
                   'delete this appointment', 'cancel the appointment', 'cancel this appointment', 'cancel my appointment',
                   'delete my appointment', 'cancel the event', 'cancel this event'):
        event = current()
        if event is None:
            m.speak('No events on this day.')
            return MODE
        return ask_delete(m, event)
    if ADD.fullmatch(command) and command.split()[0] in ('add', 'new', 'create', 'schedule', 'make', 'book', 'put', 'set') \
            or ADD_START.match(command):
        return start_adding(m, raw)
    wanted = SHOW.fullmatch(command)
    if wanted and wanted['what'] not in ('my', 'the') and (wanted['verb'] and wanted['kind'] or
                                                           (wanted['verb'] or wanted['kind']) and choosing() and SOURCES.match(wanted['what'])):
        # "Show Gmail calendar", "show all calendars", "Family calendar", "Outlook".
        return show_source(m, wanted['what'])
    day = day_command(m, command, raw)
    if day is not None:
        m.speak(show(m, day))
        return MODE
    asked_for = re.fullmatch(r'(?:go to|jump to|move to|show|show me|open|read) (?:the )?(?:date |day )?(.+)', command)
    if asked_for and looks_like_date(asked_for[1]):
        # "go to February 30": a date was meant. Say why, and ask again.
        return ask(m, 'go-to', date_problem(m, raw))
    m.speak('Calendar. Say next event, next day, today, read event, add event, edit event, or delete event. '
            'Press F1 for help, or Escape for the main menu.')
    return MODE


def whole_day(words, m):
    """The day when the words are only a day ("Friday", "October 30", "tomorrow"); else None.
    "the menu tonight" is not a day, though it ends with one."""
    words = words.strip()
    found, span = find_day(words, today(m))
    return found if found is not None and span == (0, len(words)) else None


def day_command(m, command, raw=''):
    """The day for "go to October 30", "what's on Friday", or a day said alone ("Friday", "the 30th",
    "Friday, October 30"). The raw words keep "10/30" and "2026-12-25", which the command form loses."""
    asked = ASK.fullmatch(command)
    if asked:
        return whole_day(asked['day'] or 'today', m)
    moved = re.fullmatch(r'(?:go to|show|show me|open|jump to|move to|read) (.+)', command)
    words = moved[1] if moved else command
    found, span = find_day(words, today(m))
    if found is not None and span == (0, len(words)) or found is not None and moved:
        return found
    return date_from(m, raw) if raw else None


# ---- Choosing which calendars are shown ----------------------------------------------
#
# Only with two or more email accounts (pim_sources.Sources.active). Control+L lists
# All calendars, each account, and each calendar; the arrows and Enter choose. Or say
# "show Gmail calendar", "show the Family calendar", "show all calendars".

CHOOSE = ('choose calendar', 'choose a calendar', 'choose calendars', 'choose which calendar', 'pick a calendar', 'pick calendar',
          'select calendar', 'select a calendar', 'list calendars', 'calendar list', 'my calendars', 'switch calendars',
          'other calendars')
WHICH = ('which calendar', 'which calendars', 'which calendar am i in', 'what calendar am i in', 'which calendar is this',
         'what calendar is this', 'which calendar am i looking at', 'what calendar am i looking at', 'which calendars am i looking at',
         'what calendars am i looking at', 'which calendar is shown', 'which calendars are shown', 'what calendar is shown',
         'what calendars are shown', 'which calendar is open')
SHOW = re.compile(r'(?:(?P<verb>show|view|see|open|use|switch to|change to|go to|look at|look in)(?: me)?(?: only| just)? )?'
                  r'(?:(?:the|my|only|just) )*(?P<what>.+?)(?P<kind>(?: calendars?)+)?')
CHOOSE_HINT = 'To choose another, press Control+L.'


def start_choosing(m, first=None, lead=''):
    """Control+L: the list of calendars to show, on the one shown now."""
    global PENDING, CHOICE, CHOICES
    if not choosing():
        m.speak('You have one email account in Thunderbird, so all your calendars are shown.')
        return MODE
    CHOICES = tuple(value for value, _ in SOURCES.choices())
    CHOICE = first if first in CHOICES else SOURCE if SOURCE in CHOICES else CHOICES[0]
    PENDING = ('source', None)
    m.speak(lead + 'Choose the calendars to show. ' + choice_words() +
            ' Use the arrows and Enter, or say a name. Escape cancels.')
    return MODE


def show_source(m, words, lead=''):
    """'Show Gmail calendar': show it, or ask which when the words fit more than one.
    lead: 'Calendar. ' when the screen opens with this."""
    if not choosing():
        m.speak(show(m, DAY or today(m)))
        return MODE
    found = SOURCES.match(words)
    if len(found) == 1:
        return use_source(m, found[0], lead)
    if found:
        return start_choosing(m, found[0], lead + 'More than one calendar fits. ')
    return start_choosing(m, None, lead + 'No calendar is called ' + words + '. ')


def use_source(m, choice, lead=''):
    """Show these calendars, keep the choice for next time, and say the day."""
    global SOURCE, PENDING
    PENDING = None
    SOURCE = choice
    kept = pim_sources.remember(m.APP, 'calendar', choice, SOURCES.words(choice))
    words = show(m, DAY or today(m))
    m.speak(lead + showing() + ' ' + words + ('' if kept else ' This choice could not be saved for next time.'))
    return MODE


def source_answer(m, command):
    """An answer while the list of calendars is open."""
    global CHOICE
    if command.startswith('@'):
        value = command.split(' ', 1)[1] if ' ' in command else ''
        return use_source(m, value) if value in CHOICES else MODE
    if command in NO:
        global PENDING
        PENDING = None
        m.speak('Not changed. ' + showing())
        return MODE
    steps = {'next': 1, 'down': 1, 'next item': 1, 'previous': -1, 'up': -1, 'previous item': -1}
    if command in steps:
        place = CHOICES.index(CHOICE) + steps[command]
        if 0 <= place < len(CHOICES):
            CHOICE = CHOICES[place]
            m.speak(choice_words())
        else:
            m.speak(('Start of list. ' if place < 0 else 'End of list. ') + choice_words())
        return MODE
    if command in YES or command in ('that one', 'this one'):
        return use_source(m, CHOICE)
    wanted = SHOW.fullmatch(command)
    found = SOURCES.match(wanted['what'] if wanted else command)
    if len(found) == 1:
        return use_source(m, found[0])
    if found:
        CHOICE = found[0]
        m.speak('More than one calendar fits. ' + choice_words() + ' Use the arrows and Enter.')
        return MODE
    m.speak('Choose the calendars to show. ' + choice_words() + ' Use the arrows and Enter, or say a name. Escape cancels.')
    return MODE


GO_TO_WORDS = ('go to date', 'go to a date', 'go to day', 'go to a day', 'go to', 'jump to date', 'jump to a date',
               'choose a date', 'pick a date', 'choose date', 'enter a date', 'find a date', 'find date')
GO_TO_PROMPT = 'Go to a date. Type or say a date, such as October 30 or next Friday, then press Enter. Escape cancels.'
DATE_WORDS = re.compile(r'\d|\b(?:' + '|'.join(sorted(MONTH_NAMES, key=len, reverse=True) + list(WEEKDAYS) + [
    'today', 'tomorrow', 'yesterday', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth',
    'tenth', 'eleventh', 'twelfth', r'\w+teenth', r'\w+tieth']) + r')\b')


def looks_like_date(words):
    """True when the words were meant as a date (a number, a month or a weekday), so a bad one is worth a reason."""
    return bool(DATE_WORDS.search(words))


def date_from(m, words):
    """The day typed or said as a date alone ("go to" and "date" may come first), or None."""
    words = re.sub(r'^\s*(?:(?:go|jump|move) to|show(?: me)?|open|read)\s+', '', str(words), flags=re.I)
    words = re.sub(r'^\s*(?:the\s+)?(?:date|day)\s+(?=\S)', '', words, flags=re.I)
    return only_day(words, today(m))


def go_to_step(m, value):
    """The answer to Control+G: go to that day, or say why not and ask again."""
    day = date_from(m, value)
    if day is None:
        return ask(m, 'go-to', date_problem(m, value))
    m.speak(show(m, day))
    return MODE


def date_problem(m, words):
    """Why the words are not a date, and what to say instead."""
    problem = missing_date(words, today(m))
    if problem.startswith('There is no'):
        problem += ' Say another date.'
    return (problem or 'I did not catch a date. Say a date such as October 30 or next Friday.') + ' Escape cancels.'


def internal(m, raw, mode):
    action, _, value = raw[len('@calendar:'):].partition(' ')
    if action in ('add-when', 'add-time', 'add-title'):
        return add_step(m, action[4:], value.strip())
    if action == 'go-to':
        return go_to_step(m, value)
    if action.startswith('edit-'):
        return edit_step(m, action[5:], value)
    if action == 'field' and PENDING is not None and PENDING[0] == 'edit-field':
        return answer(m, value.strip())
    if action == 'source' and PENDING is not None and PENDING[0] == 'source':
        return source_answer(m, '@source ' + value.strip())
    return mode


# ---- Adding ----------------------------------------------------------------------

def writable():
    return [c for c in CALENDARS if not c.get('readOnly')]


def default_calendar(m):
    """The calendar on screen when one can be changed; else the last calendar used, else an online one
    (Google first if it is named for the account), else Home."""
    choices = writable()
    if not choices:
        return None
    ids = shown_ids()
    if ids is not None:
        shown = [c for c in choices if c['id'] in ids]
        if shown:
            # One account shown: the last one used there, else its main calendar (named by its address).
            last = next((c for c in shown if c['id'] == LAST_CALENDAR), None)
            main = next((c for c in shown if str(c.get('name', '')).casefold() == str(c.get('account', '')).casefold()), None)
            return last or main or shown[0]
    by_id = {c['id']: c for c in choices}
    if LAST_CALENDAR in by_id:
        return by_id[LAST_CALENDAR]
    try:
        selected = m.ProtectedStore(m.APP).selected()
    except Exception:
        selected = None
    account = (selected[1] if selected else '').casefold()
    online = [c for c in choices if c.get('type') != 'storage']
    named = [c for c in online if account and account in str(c.get('name', '')).casefold()]
    return (named or online or choices)[0]


def prompt(m, step, words):
    return ask(m, 'add-' + step, words)


def ask(m, action, words, optional=False):
    """A text box for one answer; Escape cancels. With optional, Enter with nothing typed is an answer too."""
    m.task_navigation().prompt = ('@calendar:' + action + ' {value}', MODE) + ((True,) if optional else ())
    m.speak(words)
    return 'task_input'


WHEN_PROMPT = 'New event. When is it? For example, say tomorrow at 3 PM, or Friday all day. Escape cancels.'


def start_adding(m, raw=''):
    """Control+N, "add event", or everything at once: "add appointment tomorrow at 3 PM called doctor"."""
    global DRAFT
    DRAFT = {}
    try:
        if not CALENDARS:
            load_calendars(m)
    except thunderbird_pim.PimError as exc:
        m.speak(str(exc))
        return MODE
    if not writable():
        m.speak('Thunderbird has no calendar that can be changed. Press F1 to learn how to add one.')
        return MODE
    words = ADD.sub('', m.normalized_command(raw), count=1).strip() if raw else ''
    if not words:
        return prompt(m, 'when', WHEN_PROMPT)
    # Keep the spoken capitals for the name: remove the command words from the original text.
    original = re.sub(r'^\s*\S+(?:\s+(?:an?|new))?(?:\s+(?:event|appointment|meeting|reminder))?(?:\s+(?:to|on)(?:\s+my)?\s+calendar)?',
                      '', raw, count=1, flags=re.I)
    return add_step(m, 'when', original)


NOT_A_NAME = ('no', 'no thanks', 'none', 'nothing', 'skip', 'no name', 'i don t know')
FILLER = ('for', 'on', 'at', 'to', 'in', 'a', 'an', 'the', 'my', 'and', 'of')


def title_from(rest):
    rest = re.sub(r"^['’]s\b", '', rest.strip(' .,!?')).strip(' .,!?')     # "tomorrow's meeting": the day is gone
    if all(word.casefold() in FILLER for word in rest.split()):
        return ''       # "make an appointment for Friday at 10": "for" is not a name; ask for one
    called = re.search(r'\b(?:called|named|titled|about)\s+(.+)$', rest, re.I)
    if called:
        return called[1].strip(' .,!?')
    return rest


def add_step(m, step, value):
    global DRAFT
    now = m.CLOCK()
    if step == 'when':
        found = parse_when(value, now, future=True, base=DAY)
        if found.day is None:
            return prompt(m, 'when', 'I did not catch a day or time. Say something like tomorrow at 3 PM, '
                                     'or October 30 all day. Escape cancels.')
        DRAFT = {'when': found, 'title': title_from(found.rest), 'said': value}
        if found.start is None and not found.all_day:
            return prompt(m, 'time', spoken_day(found.day, today(m)) + '. What time? Say a time like 3 PM, '
                                     'or say all day. Escape cancels.')
        return next_question(m)
    if 'when' not in DRAFT:
        return prompt(m, 'when', WHEN_PROMPT)
    if step == 'time':
        command = m.normalized_command(value)
        if command in ('all day', 'all day long', 'the whole day', 'whole day'):
            DRAFT['when'].all_day = True
            return next_question(m)
        # A time alone ("3", "10:30") is read as "at 3".
        spoken = value if re.match(r'\s*(?:at|from|between|by)\b', value, re.I) else 'at ' + value
        # "Friday evening", then "7": the evening still counts.
        period = re.search(r'\b(?:morning|afternoon|evening|tonight|night)\b', DRAFT.get('said', ''), re.I)
        if period and not re.search(r'\b(?:[ap]\.?\s?m\b|morning|afternoon|evening|night|noon|midnight)', value, re.I):
            spoken += ' ' + {'tonight': 'in the evening', 'night': 'at night'}.get(period[0].lower(), 'in the ' + period[0].lower())
        found = parse_when(spoken, now, future=True, base=DRAFT['when'].day)
        if found.start is None and not found.all_day:
            return prompt(m, 'time', 'I did not catch a time. Say a time like 3 PM or 10:30 AM, or say all day.')
        DRAFT['when'] = found
        if not DRAFT['title']:
            DRAFT['title'] = title_from(found.rest)
        return next_question(m)
    if step == 'title':
        title = ' '.join(value.split()).strip(' .,!?')
        if not title:
            return prompt(m, 'title', 'Type or say a name for the event, such as Doctor, then press Enter.')
        if m.normalized_command(title) in NOT_A_NAME:
            return prompt(m, 'title', 'An event needs a name. Type or say a name, such as Doctor, then press Enter. Escape cancels.')
        if len(title) > 200:
            return prompt(m, 'title', 'That name is too long. Say a shorter name.')
        DRAFT['title'] = title
        return next_question(m)
    return MODE


def next_question(m):
    """After the time: the name if it is still missing, then the Yes or No question."""
    global PENDING, CHOICE, CHOICES
    if not DRAFT.get('title'):
        return prompt(m, 'title', 'What is it called? Type or say a name, such as Doctor, then press Enter.')
    DRAFT['calendar'] = DRAFT.get('calendar') or default_calendar(m)
    ids = shown_ids()
    if ids is not None and DRAFT['calendar']['id'] not in ids and 'note' not in DRAFT:
        # Only calendars that cannot be changed are shown, such as a holiday list.
        DRAFT['note'] = SOURCES.words(SOURCE) + ' cannot be changed. '
    PENDING, CHOICE, CHOICES = ('add', DRAFT), 'yes', ('yes', 'no')
    m.speak(add_question(m))
    return MODE


def draft_time_words(m):
    when = DRAFT['when']
    if when.all_day:
        return spoken_day(when.day, today(m)) + ', all day'
    start, end = when.start_end()
    from time_date import spoken_time
    finish = spoken_time(end) + (' the next day' if end.date() != start.date() else '')
    return spoken_day(when.day, today(m)) + ', ' + spoken_time(start) + ' to ' + finish


def add_question(m):
    when = DRAFT['when']
    passed = ''
    if not when.all_day and when.start_end()[0] < m.CLOCK() or when.all_day and when.day < today(m):
        passed = 'That time has already passed. '
    others = len(writable()) > 1
    return (DRAFT.get('note', '') + 'Add ' + DRAFT['title'] + ', ' + draft_time_words(m) + ', to ' + calendar_words(DRAFT['calendar']) +
            '? ' + passed + 'Say Yes to save or No to cancel.' + (' To use another calendar, say change calendar.' if others else ''))


def save_draft(m):
    global LAST_CALENDAR, DRAFT, INDEX
    when, calendar, title = DRAFT['when'], DRAFT['calendar'], DRAFT['title']
    if when.all_day:
        start, end = when.day.isoformat(), (when.day + timedelta(days=1)).isoformat()
        first = datetime.combine(when.day, time())
    else:
        first, last = when.start_end()
        start, end = calendar_events.utc_ms(first), calendar_events.utc_ms(last)
    DRAFT = {}
    try:
        added = thunderbird_pim.add_event(calendar['id'], title, start, end, all_day=when.all_day)
    except thunderbird_pim.PimError as exc:
        m.speak(not_saved(exc, 'The event was not saved.'))
        return MODE
    LAST_CALENDAR = calendar['id']
    words = show(m, when.day)
    # The new event: by the id Thunderbird gave it, else by its name and start (another event may share the name).
    new_id = str(added.get('id') or '') if isinstance(added, dict) else ''
    place = next((i for i, e in enumerate(EVENTS) if new_id and e.id == new_id), None)
    if place is None:
        place = next((i for i, e in enumerate(EVENTS) if e.title == title and e.start == first), None)
    if place is not None:
        INDEX = place
        words = spoken_day(when.day, today(m)) + '. ' + describe()
    elif not_shown(calendar['id']):
        words += ' ' + not_shown_words(calendar)
    m.speak('Saved ' + title + ' in ' + calendar_words(calendar) + '. ' + words)
    return MODE


def not_saved(exc, words):
    """Why saving failed, and that nothing was saved, unless the reason already says so."""
    text = str(exc)
    return text if 'could not save' in text else text + ' ' + words


def not_shown(calendar_id):
    ids = shown_ids()
    return ids is not None and calendar_id not in ids


def not_shown_words(calendar):
    return ('It is in ' + calendar_words(calendar) + ', which is not shown now. To see it, press Control+L and choose All calendars.')


# ---- Deleting --------------------------------------------------------------------

def ask_delete(m, event):
    global PENDING, CHOICE, CHOICES
    if any(c.get('id') == event.calendar_id and c.get('readOnly') for c in CALENDARS):
        m.speak('The ' + event.calendar + ' calendar cannot be changed, so ' + event.name + ' cannot be deleted.')
        return MODE
    PENDING = ('delete', event)
    if event.recurring:
        CHOICES, CHOICE = ('this one', 'all', 'no'), 'this one'
    else:
        CHOICES, CHOICE = ('yes', 'no'), 'no'
    m.speak(delete_question(m, event))
    return MODE


def on_day(day, m):
    """'on Friday, October 30', or 'tomorrow, Tuesday, October 27' (never "on Tomorrow")."""
    words = spoken_day(day, today(m))
    first, _, rest = words.partition(', ')
    if first in ('Today', 'Tomorrow', 'Yesterday') and rest:
        return first.lower() + ', ' + rest
    return 'on ' + words


def delete_question(m, event):
    hours = event.when(DAY)[0].lower() + event.when(DAY)[1:]
    if event.recurring:
        return (event.name + ' repeats. Delete only this one, ' + on_day(DAY, m) + ', ' + hours + ', or all of them? '
                'Say this one, all, or no. Escape keeps it.')
    return 'Delete ' + event.name + ', ' + spoken_day(DAY, today(m)) + ', ' + hours + '? Say Yes or No. Escape keeps it.'


def answer(m, command):
    """An answer to the add, delete or edit question."""
    global PENDING, CHOICE
    action, subject = PENDING
    if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
        return None
    if action in ('edit-field', 'edit'):
        return edit_answer(m, action, command)
    if action == 'source':
        return source_answer(m, command)
    if action == 'add':
        if command in ('change calendar', 'another calendar', 'other calendar', 'switch calendar', 'next calendar'):
            choices = writable()
            place = next((i for i, c in enumerate(choices) if c['id'] == subject['calendar']['id']), -1)
            subject['calendar'] = choices[(place + 1) % len(choices)]
            m.speak(add_question(m))
            return MODE
        if command in YES:
            PENDING = None
            return save_draft(m)
        if command in NO:
            PENDING = None
            m.speak('Not saved. ' + (spoken_day(DAY, today(m)) + '. ' + describe() if DAY else ''))
            return MODE
        m.speak(add_question(m))
        return MODE
    event = subject
    one = command in ('this one', 'only this one', 'just this one', 'this one only', 'one', 'only this')
    every = command in ('all', 'all of them', 'every one', 'all events', 'delete all', 'the whole series', 'the series')
    if event.recurring and (one or every) or not event.recurring and command in YES:
        PENDING = None
        try:
            thunderbird_pim.delete_event(event.calendar_id, event.id, event.occurrence if one else '', all_of_them=every or not event.recurring)
        except thunderbird_pim.PimError as exc:
            m.speak(str(exc))
            return MODE
        what = ('Deleted all of ' + event.name + '. ') if every else ('Deleted ' + event.name + ' on this day. ') if one else ('Deleted ' + event.name + '. ')
        keep = INDEX
        words = show(m, DAY)
        if EVENTS:
            globals()['INDEX'] = min(keep, len(EVENTS) - 1)
            words = describe()
        m.speak(what + words)
        return MODE
    if command in NO:
        PENDING = None
        m.speak('Not deleted. ' + describe())
        return MODE
    m.speak(delete_question(m, event))
    return MODE


# ---- Editing ---------------------------------------------------------------------
#
# F2 or "edit event": a short list of what to change, then one text box, then a Yes or No
# question that names the result. "Rename event to Dentist", "move event to Friday at 2",
# "change the time to 3 PM", "change the place to Room 4" and "make it all day" skip the list.
# EDIT holds the event and, once chosen, the new values: title, location, notes, calendar,
# and the timing (all_day, start, end, as local times like calendar_events.Event).

FIELDS = ('name', 'time', 'length', 'all day', 'place', 'notes', 'calendar')
FIELD_WORDS = {
    'name': ('name', 'title', 'the name', 'the title', 'rename', 'rename it', 'change the name', 'change name'),
    'time': ('time', 'day and time', 'date and time', 'day', 'date', 'the day', 'the date', 'the time', 'when', 'move', 'move it',
             'start time', 'change the time', 'change the day', 'change the date'),
    'length': ('length', 'how long', 'end', 'end time', 'the length', 'the end time', 'duration', 'change the length'),
    'all day': ('all day', 'make it all day', 'give it a time', 'not all day', 'add a time'),
    'place': ('place', 'location', 'where', 'the place', 'the location', 'address', 'change the place'),
    'notes': ('notes', 'note', 'the notes', 'description', 'change the notes'),
    'calendar': ('calendar', 'the calendar', 'change calendar', 'another calendar', 'other calendar', 'move to another calendar'),
}
EDIT_START = ('edit event', 'edit', 'change event', 'edit this event', 'change this event', 'edit the event', 'change the event',
              'modify event', 'edit appointment', 'change appointment', 'edit it', 'change it', 'edit this appointment')
_OF_EVENT = r'(?: (?:of|for) (?:the |this )?(?:event|appointment))?'
EDIT_PHRASES = (
    ('calendar', r'change (?:the |its )?calendar' + _OF_EVENT + r'|move (?:it |this |the )?(?:event |appointment )?to '
                 r'(?:another|a different|the other) calendar'),
    ('name', r'(?:rename|retitle)(?: the| this)?(?: event| appointment| it)?(?: (?:to|as) (?P<to>.+)| (?P<value>.+))?'),
    ('name', r'change (?:the |its )?(?:name|title)' + _OF_EVENT + r'(?: to (?P<value>.+))?'),
    # "Move to Friday" alone goes to Friday (see day_command); moving the event names it.
    ('time', r'move(?: the| this)? (?:event|appointment|it)(?: to (?P<value>.+))?'),
    ('time', r'reschedule(?: the| this)?(?: event| appointment| it)?(?: (?:to|for) (?P<to>.+))?'),
    ('time', r'change (?:the |its )?(?:day and time|date and time|time|date|day|start time)' + _OF_EVENT + r'(?: to (?P<value>.+))?'),
    ('length', r'change (?:the |its )?(?:length|end time|end)' + _OF_EVENT + r'(?: to (?P<value>.+))?'),
    ('make all day', r'make (?:it|this|the event|this event) (?:an )?all day(?: event)?'),
    ('place', r'change (?:the |its )?(?:place|location)' + _OF_EVENT + r'(?: to (?P<value>.+))?'),
    ('notes', r'change (?:the |its )?(?:notes?|description)' + _OF_EVENT + r'(?: to (?P<value>.+))?'),
)
NONE_WORDS = ('', 'none', 'no', 'nothing', 'remove', 'remove it', 'clear', 'clear it', 'delete', 'delete it', 'skip', 'blank',
              'no place', 'no location', 'no notes', 'remove the place', 'remove the notes', 'leave it blank')
ONE = ('this one', 'only this one', 'just this one', 'this one only', 'one', 'only this')
EVERY = ('all', 'all of them', 'every one', 'all events', 'the whole series', 'the series', 'change all')


def edit_phrase(raw):
    """(field, value) for a one-step edit such as "move event to Friday at 2", else None. The value
    keeps what was said ("3:30", capitals); it is None when only the field was named."""
    words = ' '.join(str(raw).replace('’', "'").split()).strip(' .!?,')
    for field, pattern in EDIT_PHRASES:
        found = re.fullmatch(pattern, words, re.I)
        if found:
            groups = found.groupdict()
            value = groups.get('to') or groups.get('value')
            return field, (value.strip(' .!?,') if value else None)
    return None


def field_for(command):
    return next((field for field, words in FIELD_WORDS.items() if command in words), None)


def read_only(event):
    return any(c.get('id') == event.calendar_id and c.get('readOnly') for c in CALENDARS)


def other_calendars(event):
    return [c for c in writable() if c.get('id') != event.calendar_id]


def fields_for(event):
    return tuple(f for f in FIELDS if f != 'calendar' or other_calendars(event))


def length_words(all_day, start, end):
    if all_day:
        days = max(1, (end - start).days)
        return str(days) + (' day' if days == 1 else ' days')
    minutes = max(0, int((end - start).total_seconds() // 60))
    hours, rest = divmod(minutes, 60)
    parts = ([str(hours) + (' hour' if hours == 1 else ' hours')] if hours else []) + \
            ([str(rest) + (' minute' if rest == 1 else ' minutes')] if rest or not hours else [])
    return ' '.join(parts)


def when_words(m, all_day, start, end, now_day=None):
    """'Friday, October 30, two PM to three PM', or 'Today, Monday, October 26, all day, until Wednesday, October 28'."""
    from time_date import spoken_time
    day, now_day = start.date(), now_day or today(m)
    words = spoken_day(day, now_day)
    if all_day:
        last = (end - timedelta(days=1)).date()
        return words + ', all day' + (', until ' + spoken_day(last, now_day) if last > day else '')
    finish = spoken_time(end)
    if end.date() == day + timedelta(days=1) and end.time() != time():
        finish += ' the next day'
    elif end.date() > day + timedelta(days=1):
        finish += ' on ' + spoken_day(end.date(), now_day)
    return words + ', ' + spoken_time(start) + ' to ' + finish


def field_label(field, event):
    """What the arrows say for each thing that can be changed, with what it is now."""
    if field == 'name':
        return 'Name: ' + event.name
    if field == 'time':
        return 'Day and time: ' + when_words(None, event.all_day, event.start, event.end, EDIT.get('today') or event.start.date())
    if field == 'length':
        return 'Length: ' + length_words(event.all_day, event.start, event.start + event.length)
    if field == 'all day':
        return 'Give it a time' if event.all_day else 'Make it all day'
    if field == 'place':
        return 'Place: ' + (event.location or 'none')
    if field == 'notes':
        notes = ' '.join(event.notes.split())
        return 'Notes: ' + ((notes[:100].rsplit(' ', 1)[0] + ' and more') if len(notes) > 100 else notes or 'none')
    return 'Calendar: ' + (event.calendar or 'none')


def start_editing(m, field=None, value=None):
    """F2 or "edit event" (no field): ask what to change. With a field, go straight to it."""
    global PENDING, CHOICE, CHOICES
    event = current()
    if event is None:
        m.speak('No events on this day.')
        return MODE
    if read_only(event):
        m.speak('The ' + event.calendar + ' calendar cannot be changed, so ' + event.name + ' cannot be edited.')
        return MODE
    EDIT.clear()
    EDIT.update(zip(('all_day', 'start', 'end'), event_timing(event)), event=event, today=today(m))
    if field is None:
        PENDING, CHOICES = ('edit-field', event), fields_for(event)
        CHOICE = CHOICES[0]
        m.speak('Edit ' + event.name + '. What do you want to change? ' + choice_words() +
                ' Use the arrows and Enter, or say what to change. Escape cancels.')
        return MODE
    return choose_field(m, field, value)


def choose_field(m, field, value=None):
    event = EDIT['event']
    name = event.name
    if field == 'make all day':
        if event.all_day:
            EDIT.clear()
            m.speak(name + ' is already all day. ' + describe())
            return MODE
        field = 'all day'
    EDIT['field'] = field
    if field == 'calendar':
        others = other_calendars(event)
        if not others:
            m.speak('There is no other calendar that can be changed. ' + describe())
            EDIT.clear()
            return MODE
        EDIT['calendar'] = others[0]
        return confirm_edit(m)
    if field == 'all day':
        if not event.all_day:
            first = datetime.combine(event.start.date(), time())
            last = (event.end - timedelta(microseconds=1)).date() if event.end > event.start else event.start.date()
            EDIT.update(all_day=True, start=first, end=datetime.combine(last + timedelta(days=1), time()))
            return confirm_edit(m)
        return ask(m, 'edit-start', 'What time does ' + name + ' start? Say a time like 3 PM. Escape cancels.')
    if value:
        return edit_step(m, {'name': 'name', 'time': 'when', 'length': 'length', 'place': 'place', 'notes': 'notes'}[field], value)
    if field == 'name':
        return ask(m, 'edit-name', 'New name for ' + name + '? Type or say it, then press Enter. Escape cancels.')
    if field == 'time':
        return ask(m, 'edit-when', TIME_PROMPT.format(name=name))
    if field == 'length':
        if event.all_day:
            return ask(m, 'edit-length', 'How many days is ' + name + '? Say a number of days, such as 2 days. Escape cancels.')
        return ask(m, 'edit-length', 'How long is ' + name + '? ' + LENGTH_HINT)
    if field == 'place':
        return ask(m, 'edit-place', 'New place for ' + name + '? Type or say it, then press Enter. '
                                    'To remove the place, press Enter or say none. Escape cancels.', optional=True)
    return ask(m, 'edit-notes', 'New notes for ' + name + '? Type or say them, then press Enter. '
                                'To remove the notes, press Enter or say none. Escape cancels.', optional=True)


TIME_PROMPT = 'New day and time for {name}? For example, say Friday at 2 PM, or 3 PM. Escape cancels.'
LENGTH_HINT = 'Say a length, such as 45 minutes or 2 hours, or an end time, such as until 4 PM. Escape cancels.'


def edit_step(m, step, value):
    """One answer in the edit flow: check it, keep it, then ask the Yes or No question."""
    if not EDIT.get('event'):
        return MODE
    event, now = EDIT['event'], m.CLOCK()
    value = str(value).strip()
    length = EDIT['end'] - EDIT['start']
    if step == 'name':
        title = ' '.join(value.split()).strip(' .,!?')
        if not title:
            return ask(m, 'edit-name', 'Type or say a new name, then press Enter. Escape cancels.')
        if len(title) > 200:
            return ask(m, 'edit-name', 'That name is too long. Say a shorter name. Escape cancels.')
        EDIT['title'] = title
    elif step == 'when':
        found = parse_when(value, now, future=True, base=event.start.date())
        if found.day is None and not re.match(r'\s*(?:at|from|between)\b', value, re.I):
            found = parse_when('at ' + value, now, future=True, base=event.start.date())     # "3", "10:30"
        if found.day is None:
            return ask(m, 'edit-when', 'I did not catch a day or time. Say something like Friday at 2 PM, or 3 PM. Escape cancels.')
        if found.all_day:
            days = max(1, length.days) if event.all_day else 1
            start = datetime.combine(found.day, time())
            EDIT.update(all_day=True, start=start, end=start + timedelta(days=days))
        elif found.start is None:
            # A day alone keeps the time (and all day stays all day).
            start = datetime.combine(found.day, EDIT['start'].time())
            EDIT.update(start=start, end=start + length)
        else:
            start, end = found.start_end()
            if found.end is None and not found.minutes:
                end = start + (length if not event.all_day and length > timedelta(0) else timedelta(hours=1))
            EDIT.update(all_day=False, start=start, end=end)
    elif step == 'length':
        start = EDIT['start']
        if EDIT['all_day']:
            days = re.fullmatch(r'(?:for )?(.+?)(?: whole)?(?: days?)?(?: long)?', m.normalized_command(value))
            from spoken_dates import number
            count = number(days[1]) if days else None
            if not count or count > 62:
                return ask(m, 'edit-length', 'I did not catch a number of days. Say a number, such as 2 days. Escape cancels.')
            EDIT['end'] = start + timedelta(days=count)
        else:
            until = re.match(r'\s*(?:until|till|til|to|ends? at|ending at|it ends at|end time)\s+(.+)', value, re.I)
            if until:
                said = until[1] if re.match(r'\s*(?:at|by)\b', until[1], re.I) else 'at ' + until[1]
                found = parse_when(said, now, future=True, base=start.date())
                if found.start is None:
                    return ask(m, 'edit-length', 'I did not catch an end time. ' + LENGTH_HINT)
                end = datetime.combine(start.date(), found.start)
                EDIT['end'] = end if end > start else end + timedelta(days=1)
            else:
                found = parse_when(value if re.match(r'\s*for\b', value, re.I) else 'for ' + value, now, future=True, base=start.date())
                if not found.minutes:
                    return ask(m, 'edit-length', 'I did not catch a length. Say a length, such as 45 minutes, '
                                                 'or an end time, such as until 4 PM. Escape cancels.')
                EDIT['end'] = start + timedelta(minutes=found.minutes)
    elif step == 'start':
        said = value if re.match(r'\s*(?:at|from|between)\b', value, re.I) else 'at ' + value
        found = parse_when(said, now, future=True, base=event.start.date())
        if found.start is None:
            return ask(m, 'edit-start', 'I did not catch a time. Say a time like 3 PM. Escape cancels.')
        EDIT.update(all_day=False, start=found.start_end()[0], end=found.start_end()[1])
    elif step in ('place', 'notes'):
        text = '' if m.normalized_command(value) in NONE_WORDS else value
        if step == 'place':
            text = ' '.join(text.split()).strip(' .,!?')
            if len(text) > 300:
                return ask(m, 'edit-place', 'That place is too long. Say a shorter one. Escape cancels.', optional=True)
            EDIT['location'] = text
        else:
            text = ' '.join(text.split())
            if len(text) > 4000:
                return ask(m, 'edit-notes', 'Those notes are too long. Say shorter notes. Escape cancels.', optional=True)
            EDIT['notes'] = text
    return confirm_edit(m)


def event_timing(event):
    """(all day, start, end) with the end at start plus the real length, so the night the clocks go
    back does not shorten an event that is edited."""
    return event.all_day, event.start, event.start + event.length


def timing_changed():
    return (EDIT['all_day'], EDIT['start'], EDIT['end']) != event_timing(EDIT['event'])


def edit_changes():
    """What is different from the event now, as the add-on's changes (start and end still local)."""
    event, changes = EDIT['event'], {}
    if 'title' in EDIT and EDIT['title'] != event.title:
        changes['title'] = EDIT['title']
    if 'location' in EDIT and EDIT['location'] != event.location:
        changes['location'] = EDIT['location']
    if 'notes' in EDIT and EDIT['notes'] != ' '.join(event.notes.split()):
        changes['notes'] = EDIT['notes']
    if 'calendar' in EDIT and EDIT['calendar']['id'] != event.calendar_id:
        changes['calendarId'] = EDIT['calendar']['id']
    if timing_changed():
        changes['timing'] = True
    return changes


def edit_question(m):
    event, field = EDIT['event'], EDIT['field']
    name = event.name
    if field == 'name':
        return 'Rename ' + name + ' to ' + EDIT['title'] + '?'
    if field == 'place':
        return ('Change the place for ' + name + ' to ' + EDIT['location'] + '?') if EDIT['location'] else \
            'Remove the place from ' + name + '?'
    if field == 'notes':
        return ('Change the notes for ' + name + ' to: ' + EDIT['notes'].rstrip('.!?') + '?') if EDIT['notes'] else \
            'Remove the notes from ' + name + '?'
    if field == 'calendar':
        return 'Move ' + name + ' to ' + calendar_words(EDIT['calendar']) + '?'
    return 'Change ' + name + ' to ' + when_words(m, EDIT['all_day'], EDIT['start'], EDIT['end']) + '?'


def confirm_edit(m):
    """The Yes or No question that names the result. A repeating event asks this one or all."""
    global PENDING, CHOICE, CHOICES
    event = EDIT['event']
    if not edit_changes():
        PENDING = None
        EDIT.clear()
        m.speak('That is the same as before, so nothing was changed. ' + describe())
        return MODE
    passed = ''
    if timing_changed() and (EDIT['start'] < m.CLOCK() if not EDIT['all_day'] else EDIT['start'].date() < today(m)):
        passed = ' That time has already passed.'
    name = event.name
    if not event.recurring:
        CHOICES = ('yes', 'no')
        tail = ' Say Yes to save or No to cancel.'
        if EDIT['field'] == 'calendar' and len(other_calendars(event)) > 1:
            tail += ' To use another calendar, say change calendar.'
    elif 'calendarId' in edit_changes():
        CHOICES = ('yes', 'no')
        tail = ' ' + name + ' repeats. All of them will move. Say Yes to save or No to cancel.'
    elif timing_changed() and (EDIT['start'].date() != event.start.date() or EDIT['all_day'] != event.all_day):
        CHOICES = ('this one', 'no')
        tail = (' ' + name + ' repeats. Only this one can ' +
                ('move to another day.' if EDIT['start'].date() != event.start.date() else 'be changed that way.') +
                ' Say this one, or no. Escape cancels.')
    else:
        CHOICES = ('this one', 'all', 'no')
        tail = (' ' + name + ' repeats. Change only this one, ' + on_day(event.start.date(), m) +
                ', or all of them? Say this one, all, or no. Escape cancels.')
    PENDING, CHOICE = ('edit', EDIT), CHOICES[0]
    m.speak(edit_question(m) + passed + tail)
    return MODE


def edit_answer(m, action, command):
    global PENDING, CHOICE
    event = EDIT.get('event')
    if event is None:
        PENDING = None
        return MODE
    if action == 'edit-field':
        field = field_for(command)
        if field in CHOICES:
            PENDING = None
            return choose_field(m, field)
        if command in NO:
            PENDING = None
            EDIT.clear()
            m.speak('Not changed. ' + describe())
            return MODE
        m.speak('What do you want to change in ' + event.name + '? ' + choice_words() +
                ' Use the arrows and Enter, or say what to change. Escape cancels.')
        return MODE
    if EDIT.get('field') == 'calendar' and command in ('change calendar', 'another calendar', 'other calendar', 'switch calendar',
                                                       'next calendar'):
        others = other_calendars(event)
        place = next((i for i, c in enumerate(others) if c['id'] == EDIT['calendar']['id']), -1)
        EDIT['calendar'] = others[(place + 1) % len(others)]
        return confirm_edit(m)
    scope = None
    if 'this one' in CHOICES and command in ONE:
        scope = 'one'
    elif 'all' in CHOICES and command in EVERY:
        scope = 'all'
    elif 'yes' in CHOICES and command in YES:
        scope = 'all' if event.recurring else 'single'
    elif command in NO:
        PENDING = None
        EDIT.clear()
        m.speak('Not changed. ' + describe())
        return MODE
    if scope is None:
        return confirm_edit(m)
    PENDING = None
    return save_edit(m, scope)


def save_edit(m, scope):
    """Save through Thunderbird, then go to the event's day and say it."""
    global INDEX
    event, changes = EDIT['event'], edit_changes()
    timing = changes.pop('timing', False)
    if timing:
        if scope == 'all':
            # Every day keeps its own date: a later or earlier start and a new length, the same for each.
            shift = int((EDIT['start'] - event.start).total_seconds() // 60)
            if shift:
                changes['shift'] = shift
            if EDIT['all_day']:
                changes['length'] = max(1, (EDIT['end'] - EDIT['start']).days) * 1440
            else:
                changes['length'] = int((EDIT['end'] - EDIT['start']).total_seconds() // 60)
        elif EDIT['all_day']:
            changes.update(allDay=True, start=EDIT['start'].date().isoformat(), end=EDIT['end'].date().isoformat())
        else:
            changes.update(allDay=False, start=calendar_events.utc_ms(EDIT['start']), end=calendar_events.utc_ms(EDIT['end']))
    name = EDIT.get('title') or event.name
    day = EDIT['start'].date() if timing else DAY
    start = EDIT['start'] if timing else event.start
    EDIT.clear()
    try:
        answer_ = thunderbird_pim.modify_event(event.calendar_id, event.id, changes,
                                               occurrence=event.occurrence if scope == 'one' else '', all_of_them=scope == 'all')
    except thunderbird_pim.PimError as exc:
        m.speak(not_saved(exc, 'The change was not saved.'))
        return MODE
    ids = {event.id, str((answer_ or {}).get('id') or '') if isinstance(answer_, dict) else ''}
    words = show(m, day)
    place = next((i for i, e in enumerate(EVENTS) if e.id in ids and e.start == start), None)
    if place is None:
        place = next((i for i, e in enumerate(EVENTS) if e.id in ids), None)
    if place is not None:
        INDEX = place
        words = spoken_day(day, today(m)) + '. ' + describe() + (' ' + NOTICE if NOTICE else '')
    elif 'calendarId' in changes and not_shown(changes['calendarId']):
        target = next((c for c in CALENDARS if c.get('id') == changes['calendarId']), {'id': changes['calendarId'], 'name': ''})
        words += ' ' + not_shown_words(target)
    m.speak('Saved ' + name + {'one': ', this one only', 'all': ', all of them'}.get(scope, '') + '. ' + words)
    return MODE
