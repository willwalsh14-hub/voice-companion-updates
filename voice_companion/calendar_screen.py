"""The Calendar screen: one day at a time, from Thunderbird's calendars.

Keys (each also has a voice command, see input_parity.SCREEN_KEYS and the guide):
- It opens on today: "Today, Monday, October 26. 3 events." then the first event.
- Up and Down move through the day's events: "Ten thirty AM to eleven AM, Doctor, Google".
- Left and Right go to the previous or next day. Page Up and Page Down go a week.
  Home goes back to today. Enter reads the time, place and notes.
- Control+N adds an event: when, then its name, then a Yes or No question.
  Delete removes one after a Yes or No question; for a repeating event it asks
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
from spoken_dates import find_day, parse_day, parse_when, spoken_day

MODE = 'calendar'
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'save', 'save it', 'yes save it', 'enter', 'do it')
NO = ('no', 'no thanks', 'cancel', 'escape', 'go back', 'back', 'do not save', 'keep it', 'do not delete')
PASS_THROUGH = ('help', 'options', 'open options', 'main menu', 'back to main menu', 'exit companion', 'go to sleep',
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
PENDING = None         # ('add', draft) or ('delete', event): a question
CHOICE = 'no'
CHOICES = ('yes', 'no')
DRAFT = {}             # the new event while its time and name are asked
LAST_CALENDAR = None   # the calendar the last event was added to


def today(m):
    return m.CLOCK().date()


def reset():
    global DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES
    DAY, EVENTS, INDEX, NOTICE, CALENDARS, PENDING, DRAFT, CHOICE, CHOICES = None, [], 0, '', [], None, {}, 'no', ('yes', 'no')


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
                m.speak({'this one': 'This one only.', 'all': 'All of them.'}.get(CHOICE, CHOICE.capitalize() + '.'))
                m.sync_app_context(MODE)
            return None
        return {'Enter': CHOICE, 'Escape': 'no'}.get(key)
    return {'Up': 'previous event', 'Down': 'next event', 'Enter': 'read event', 'Escape': 'close calendar'}.get(key)


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
        if asked and (asked['day'] is None or parse_day(asked['day'], today(m)) is not None or asked['day'] in ('today', 'tonight')):
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
    day = parse_day(words, today(m)) or today(m)
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
    if command in ('close calendar', 'leave calendar', 'exit calendar', 'escape', 'go back', 'back'):
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
    day = day_command(m, command)
    if day is not None:
        m.speak(show(m, day))
        return MODE
    m.speak('Calendar. Say next event, next day, today, read event, add event, or delete event. '
            'Press F1 for help, or Escape for the main menu.')
    return MODE


def day_command(m, command):
    """The day for "go to October 30", "what's on Friday", or a day said alone ("Friday", "the 30th")."""
    asked = ASK.fullmatch(command)
    if asked:
        return parse_day(asked['day'] or 'today', today(m))
    moved = re.fullmatch(r'(?:go to|show|show me|open|jump to|move to|read) (.+)', command)
    words = moved[1] if moved else command
    found, span = find_day(words, today(m))
    if found is not None and span == (0, len(words)) or found is not None and moved:
        return found
    return None


def internal(m, raw, mode):
    action, _, value = raw[len('@calendar:'):].partition(' ')
    if action in ('add-when', 'add-time', 'add-title'):
        return add_step(m, action[4:], value.strip())
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
    m.task_navigation().prompt = ('@calendar:add-' + step + ' {value}', MODE)
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
    else:
        first, last = when.start_end()
        start, end = calendar_events.utc_ms(first), calendar_events.utc_ms(last)
    DRAFT = {}
    try:
        thunderbird_pim.add_event(calendar['id'], title, start, end, all_day=when.all_day)
    except thunderbird_pim.PimError as exc:
        m.speak(str(exc) + ' The event was not saved.')
        return MODE
    LAST_CALENDAR = calendar['id']
    words = show(m, when.day)
    place = next((i for i, e in enumerate(EVENTS) if e.title == title), None)
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
    """An answer to the add or delete question."""
    global PENDING, CHOICE
    action, subject = PENDING
    if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
        return None
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
