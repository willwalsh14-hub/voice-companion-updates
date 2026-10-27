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

"What's on my calendar today?" (or tomorrow, or a day) works from any screen
where words are commands, and "add appointment tomorrow at 3 PM called doctor"
from the main menu. companion.py calls into this module from a few small hooks;
`m` is the companion module.
"""
import re
from datetime import datetime, time, timedelta

import calendar_events
import thunderbird_pim
from menu_navigation import announce_item
from spoken_dates import MONTH_NAMES, WEEKDAYS, find_day, missing_date, only_day, parse_day, parse_when, spoken_day

MODE = 'calendar'
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'save', 'save it', 'yes save it', 'enter', 'do it')
NO = ('no', 'no thanks', 'cancel', 'escape', 'go back', 'back', 'do not save', 'keep it', 'do not delete')
PASS_THROUGH = ('help', 'options', 'open options', 'exit companion', 'go to sleep',
                'wake up', 'user guide', 'open user guide', 'manual', 'open manual', 'release notes', 'what mode',
                'what version', 'toggle audio', 'calendar menu', 'menu', 'open menu')
OPEN = ('calendar', 'open calendar', 'my calendar', 'show calendar', 'show my calendar', 'open my calendar', 'calendar app')
ADD_WORDS = r'(?:add|new|create|make|schedule|set up|book|put)(?: an?| new)?(?: (?:event|appointment|meeting|reminder))?'
ADD = re.compile(r'(?:' + ADD_WORDS + r')(?:(?: to| on)(?: my)? calendar)?')
ADD_START = re.compile(r'(?:add|new|create|make|schedule|book)(?: an?| new)? (?:event|appointment|meeting)\b')
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


def today(m):
    return m.CLOCK().date()


def reset():
    global DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES
    DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES = None, [], 0, '', [], None, {}, 'no', ('yes', 'no')
    EDIT.clear()


# ---- Reading Thunderbird -----------------------------------------------------------

def load_calendars(m):
    """The calendars that are turned on. A failure is a notice, never an exception."""
    global CALENDARS
    CALENDARS = [c for c in thunderbird_pim.calendars() if isinstance(c, dict)]
    return CALENDARS


def events_on(day):
    """The events on one local day, from every calendar."""
    start, end = calendar_events.day_range(day)
    # A little before and after, so an event across midnight is found from either side.
    answer = thunderbird_pim.events(start - 86400000, end + 86400000)
    failed = [str(name) for name in answer.get('failed', []) if name]
    return calendar_events.for_day(answer['events'], day), failed


def show(m, day, opening=False):
    """Go to a day and say it: "Today, Monday, October 26. 3 events. <first event>"."""
    global DAY, EVENTS, INDEX, NOTICE
    DAY, INDEX, NOTICE = day, 0, ''
    try:
        if opening or not CALENDARS:
            load_calendars(m)
        EVENTS, failed = events_on(day)
        if failed:
            NOTICE = ' '.join('The ' + name + ' calendar could not be read.' for name in failed[:3])
    except thunderbird_pim.PimError as exc:
        EVENTS, NOTICE = [], str(exc)
    words = spoken_day(day, today(m)) + '. ' + day_summary()
    if NOTICE:
        words += ' ' + NOTICE
    elif opening and CALENDARS and all(c.get('type') == 'storage' for c in CALENDARS):
        words += ' ' + GUIDANCE
    elif opening and not CALENDARS:
        words += ' Thunderbird has no calendars turned on. Press F1 to learn how to add one.'
    return words


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


def open_screen(m, mode=None, day=None):
    reset()
    if not thunderbird_pim.usable(m.APP):
        m.speak(thunderbird_pim.NO_THUNDERBIRD)
        return mode or 'awake'
    m.speak('Calendar. ' + show(m, day or today(m), opening=True))
    return MODE


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
        return {'Enter': CHOICE, 'Space': CHOICE, 'Escape': 'no', 'F1': 'help with calendar'}.get(key)
    return {'Up': 'previous event', 'Down': 'next event', 'Enter': 'read event', 'Escape': 'close calendar'}.get(key)


def choice_words():
    """What the arrows say on a question: "Yes.", "This one only.", or "Place: VA clinic, 5 of 7."."""
    if PENDING and PENDING[0] == 'edit-field':
        return announce_item(field_label(CHOICE, PENDING[1]), CHOICES.index(CHOICE), len(CHOICES), hotkey=False)
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
        if mode == 'awake' and (ADD.fullmatch(command) and command.split()[0] in ('add', 'new', 'create', 'schedule')
                                and command not in ('add', 'new', 'create', 'schedule') or ADD_START.match(command)):
            opened = open_screen(m, mode)
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
        m.speak(show(m, DAY or today(m), opening=command.startswith('refresh')))
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
                   'cancel appointment', 'delete', 'remove'):
        event = current()
        if event is None:
            m.speak('No events on this day.')
            return MODE
        return ask_delete(m, event)
    if ADD.fullmatch(command) and command.split()[0] in ('add', 'new', 'create', 'schedule', 'make', 'book', 'put', 'set') \
            or ADD_START.match(command):
        return start_adding(m, raw)
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
    return mode


# ---- Adding ----------------------------------------------------------------------

def writable():
    return [c for c in CALENDARS if not c.get('readOnly')]


def default_calendar(m):
    """The last calendar used, else an online one (Google first if it is named for the account), else Home."""
    choices = writable()
    if not choices:
        return None
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


def title_from(rest):
    rest = rest.strip(' .,!?')
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
        DRAFT = {'when': found, 'title': title_from(found.rest)}
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
    return ('Add ' + DRAFT['title'] + ', ' + draft_time_words(m) + ', to the ' + DRAFT['calendar']['name'] + ' calendar? ' + passed +
            'Say Yes to save or No to cancel.' + (' To use another calendar, say change calendar.' if others else ''))


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
        m.speak(str(exc) + ' The event was not saved.')
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
    m.speak('Saved ' + title + ' in the ' + calendar['name'] + ' calendar. ' + words)
    return MODE


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


def delete_question(m, event):
    when = spoken_day(DAY, today(m)) + ', ' + event.when(DAY)[0].lower() + event.when(DAY)[1:]
    if event.recurring:
        return (event.name + ' repeats. Delete only this one, on ' + when + ', or all of them? '
                'Say this one, all, or no. Escape keeps it.')
    return 'Delete ' + event.name + ', ' + when + '? Say Yes or No. Escape keeps it.'


def answer(m, command):
    """An answer to the add, delete or edit question."""
    global PENDING, CHOICE
    action, subject = PENDING
    if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
        return None
    if action in ('edit-field', 'edit'):
        return edit_answer(m, action, command)
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
    ('time', r'(?:move|reschedule)(?: the| this)?(?: event| appointment| it)?(?: to (?P<value>.+))?'),
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
        return 'Move ' + name + ' to the ' + EDIT['calendar']['name'] + ' calendar?'
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
        tail = (' ' + name + ' repeats. Change only this one, on ' + spoken_day(event.start.date(), today(m)) +
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
        m.speak(str(exc) + ' The change was not saved.')
        return MODE
    ids = {event.id, str((answer_ or {}).get('id') or '') if isinstance(answer_, dict) else ''}
    words = show(m, day)
    place = next((i for i, e in enumerate(EVENTS) if e.id in ids and e.start == start), None)
    if place is None:
        place = next((i for i, e in enumerate(EVENTS) if e.id in ids), None)
    if place is not None:
        INDEX = place
        words = spoken_day(day, today(m)) + '. ' + describe() + (' ' + NOTICE if NOTICE else '')
    m.speak('Saved ' + name + {'one': ', this one only', 'all': ', all of them'}.get(scope, '') + '. ' + words)
    return MODE
