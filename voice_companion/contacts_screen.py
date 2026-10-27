"""The Contacts screen: one sorted list of every contact, from Thunderbird and this computer.

Keys (each also has a voice command, see input_parity.SCREEN_KEYS and the guide):
- Up and Down read "Name, email, phone". Home and End reach the ends of the list.
  A letter moves to the next name that starts with it.
- Enter opens the contact: Email this person comes first, then the name, each email,
  each phone and the address book. Escape goes back to the list.
- Control+E starts an email to the contact at once. Control+N adds a contact
  (name, then email, then phone). Delete removes one after a Yes or No question.
  Control+F finds a contact by name. F1 is help. Alt opens the Contacts menu.
- Escape on the list goes to the main menu.

"Email John", "send an email to John Smith" and "write to Mary" also work from
the main menu (email_by_name). companion.py calls into this module from a few
small hooks; `m` is the companion module.
"""
import re

import contacts_book
import thunderbird_pim
from menu_navigation import announce_item, next_match

MODE = 'contacts'
HELP_TOPIC = 'contacts'
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'yes delete it', 'delete it', 'enter')
NO = ('no', 'no thanks', 'cancel', 'escape', 'go back', 'back', 'keep it', 'do not delete')
SKIP = ('', 'skip', 'none', 'no email', 'no phone', 'no phone number', 'leave it out', 'leave it blank', 'nothing')
PASS_THROUGH = ('help', 'options', 'open options', 'exit companion', 'go to sleep',
                'wake up', 'user guide', 'open user guide', 'manual', 'open manual', 'release notes', 'what mode',
                'what version', 'toggle audio', 'new email', 'contacts menu', 'menu', 'open menu')
OPEN = ('contacts', 'open contacts', 'my contacts', 'contact list', 'address book', 'open address book', 'show contacts',
        'show my contacts')
# Names that are commands after "email", never a person ("email list size", "email the sender").
RESERVED = ('commands', 'draft', 'drafts', 'help', 'list size', 'lists', 'sender', 'the sender', 'to sender', 'topics',
            'options', 'settings', 'menu', 'account', 'accounts', 'address', 'me', 'it', 'them', 'this person', 'contact',
            'this contact', 'the contact')
EMAIL_TO = re.compile(r'(?:(?:send|write|compose|start|create)(?: a| an| new)* (?:e mail|email|message|note) to'
                      r'|write to|e mail|email) (?P<name>.+)')

CONTACTS = []          # the list on screen
INDEX = 0
SELECTED = None        # the contact's key, so a reload keeps your place
VIEW = 'list'          # 'list' or 'details'
DETAILS = []           # [(label, action)] for the open contact
DETAIL_INDEX = 0
PENDING = None         # ('delete', contact): a Yes or No question
CHOICE = 'no'
ADDING = {}            # the new contact while its name, email and phone are asked
BOOK = None


def book(m):
    selected = None
    try:
        selected = m.ProtectedStore(m.APP).selected()
    except Exception:
        selected = None
    return contacts_book.ContactBook(m.APP, selected[1] if selected else '')


def reset():
    global CONTACTS, INDEX, SELECTED, VIEW, DETAILS, DETAIL_INDEX, PENDING, ADDING, BOOK
    CONTACTS, INDEX, SELECTED, VIEW, DETAILS, DETAIL_INDEX, PENDING, ADDING, BOOK = [], 0, None, 'list', [], 0, None, {}, None


def reload(m, keep=None):
    """Read the contacts again; stay on the same contact (or `keep`) when it is still there."""
    global CONTACTS, BOOK
    BOOK = book(m)
    CONTACTS = BOOK.load()
    wanted = keep or SELECTED
    place = next((i for i, c in enumerate(CONTACTS) if c.key == wanted), None)
    select(place if place is not None else min(INDEX, max(0, len(CONTACTS) - 1)))
    return BOOK.notice


def select(index):
    global INDEX, SELECTED
    INDEX = max(0, min(index, len(CONTACTS) - 1)) if CONTACTS else 0
    SELECTED = CONTACTS[INDEX].key if CONTACTS else None


def current():
    return CONTACTS[INDEX] if CONTACTS and 0 <= INDEX < len(CONTACTS) else None


def describe(index=None):
    if not CONTACTS:
        return 'No contacts yet. Press Control+N to add one, or say add contact.'
    index = INDEX if index is None else index
    return announce_item(CONTACTS[index].label(), index, len(CONTACTS), hotkey=False)


def summary(notice=''):
    count = len(CONTACTS)
    text = 'Contacts. ' + (str(count) + (' contact. ' if count == 1 else ' contacts. ') if count else '') + describe()
    return text + (' ' + notice if notice else '')


# ---- Hooks from companion.py -------------------------------------------------------

def context(m, value):
    """app_context: a Yes or No question owns the keys; letters move only in the list."""
    if PENDING is not None:
        value['confirmation'] = CHOICE
        value['menu_letters'] = False
    elif VIEW == 'details':
        value['menu_letters'] = False
    return value


def menu_state(m):
    if VIEW != 'list' or PENDING is not None or not CONTACTS:
        return None
    return [c.label() for c in CONTACTS], INDEX, select


def navigation_key(m, key):
    """The command for a key on the Contacts screen, or None when the key does nothing here."""
    global CHOICE
    if PENDING is not None:
        if key.lower() in ('y', 'alt+y'):
            return 'yes'
        if key.lower() in ('n', 'alt+n'):
            return 'no'
        if key in ('Up', 'Left', 'ShiftTab', 'Home', 'Down', 'Right', 'Tab', 'End'):
            choice = 'yes' if key in ('Up', 'Left', 'ShiftTab', 'Home') else 'no'
            if choice == CHOICE:
                m.speak('Start of list.' if choice == 'yes' else 'End of list.')
            else:
                CHOICE = choice
                m.speak(choice.capitalize() + '.')
                m.sync_app_context(MODE)
            return None
        return {'Enter': CHOICE, 'Escape': 'no'}.get(key)
    if key.startswith('Letter:'):
        return '@contacts:letter:' + key[7:] if VIEW == 'list' else None
    if VIEW == 'details':
        return {'Up': 'previous', 'Left': 'previous', 'Down': 'next', 'Right': 'next', 'Home': 'home', 'End': 'end',
                'Enter': '@contacts:choose', 'Escape': 'back to contacts'}.get(key)
    return {'Up': 'previous contact', 'Left': 'previous contact', 'Down': 'next contact', 'Right': 'next contact',
            'Home': 'first contact', 'End': 'last contact', 'Enter': 'open contact', 'Escape': 'close contacts'}.get(key)


def open_screen(m, mode=None):
    reset()
    notice = reload(m)
    m.speak(summary(notice))
    return MODE


# ---- Commands -------------------------------------------------------------------

LIST_WORDS = ('next', 'previous', 'cancel', 'close list', 'escape', 'go back', 'back', 'start of list', 'beginning of list',
              'first item', 'end of list', 'last item')


def handle(m, text, mode, typed=False):
    """Commands for the Contacts screen, the phrases that open it, and "email <name>". None: not ours."""
    raw = text.strip()
    if raw.startswith('@contacts:'):
        return internal(m, raw, mode)
    command = m.normalized_command(raw)
    open_list = getattr(m, 'NAMED_LIST', None)
    if open_list and (command in LIST_WORDS or command in getattr(m, 'PICK_CONFIRM', ())):
        return None     # a list opened over this screen (which address, which John) answers first
    result = _handle(m, raw, command, mode, typed)
    if result is not None and open_list is not None and m.NAMED_LIST is open_list:
        m.NAMED_LIST = None     # another command was given: the old list closes
    return result


def _handle(m, raw, command, mode, typed):
    if mode != MODE:
        if mode in ('task_input', 'document_name', 'search', 'settings') or m.writing_dictation(mode, typed):
            return None
        if mode == 'awake' and command in OPEN:
            return open_screen(m, mode)
        if mode == 'awake' and command in ('add contact', 'new contact', 'add a contact', 'create contact', 'add new contact'):
            open_screen(m, mode)
            return start_adding(m)
        if mode == 'awake':
            return email_by_name(m, command, mode)
        return None
    try:
        return command_here(m, raw, command)
    except (ValueError, OSError) as exc:
        m.speak(str(exc))
        return MODE


def command_here(m, raw, command):
    global PENDING, CHOICE, VIEW, DETAIL_INDEX
    if PENDING is not None:
        _, contact = PENDING
        if command in YES:
            PENDING = None
            BOOK.delete(contact)
            name = contact.name
            VIEW = 'list'       # the deleted contact's details are gone
            reload(m)
            m.speak('Deleted ' + name + '. ' + describe())
            return MODE
        if command in NO:
            PENDING = None
            m.speak('Not deleted. ' + (describe() if VIEW == 'list' else details_item()))
            return MODE
        if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
            return None
        m.speak(question(contact))
        return MODE
    if command in PASS_THROUGH or command.startswith(('help with ', 'help for ')) or m.speech_control(raw) \
            or m.speech_setting_request(raw) is not None or m.spoken_control(command) or command in m.VOICE_LIST_COMMANDS:
        return None
    if command in ('close contacts', 'leave contacts', 'exit contacts', 'main menu', 'back to main menu', 'go to main menu') \
            or command in ('escape', 'go back', 'back') and VIEW == 'list':
        reset()
        return 'awake'      # handle() says Main menu when the screen changes
    if command in ('back to contacts', 'back to list', 'contact list', 'escape', 'go back', 'back') and VIEW == 'details':
        VIEW = 'list'
        m.speak('Contacts. ' + describe())
        return MODE
    if command in OPEN + ('where am i', 'list contacts'):
        VIEW = 'list'
        m.speak(summary())
        return MODE
    if command in ('refresh', 'refresh contacts', 'reload contacts', 'update contacts', 'sync contacts'):
        notice = reload(m)
        m.speak(summary(notice))
        return MODE
    if command in ('add contact', 'new contact', 'add a contact', 'create contact', 'add new contact', 'create a contact'):
        return start_adding(m)
    added = re.fullmatch(r'(?:add|save|create) contact (.+?) (?:as|at|email is) (.+)', raw.rstrip('.!?'), re.I)
    if added:
        where = BOOK.add(added[1], added[2])
        reload(m)
        found = next((i for i, c in enumerate(CONTACTS) if c.name.casefold() == added[1].strip().casefold()), None)
        if found is not None:
            select(found)
        m.speak('Saved ' + added[1].strip() + ' ' + where + '. ' + describe())
        return MODE
    if VIEW == 'details' and command in ('next', 'previous', 'home', 'end', 'next item', 'previous item', 'first item', 'last item'):
        old = DETAIL_INDEX
        step = {'next': 1, 'next item': 1, 'previous': -1, 'previous item': -1}.get(command)
        DETAIL_INDEX = 0 if command in ('home', 'first item') else len(DETAILS) - 1 if command in ('end', 'last item') else \
            max(0, min(len(DETAILS) - 1, old + step))
        edge = ''
        if DETAIL_INDEX == old:
            edge = 'Start of list. ' if command in ('previous', 'previous item', 'home', 'first item') else 'End of list. '
        m.speak(edge + details_item())
        return MODE
    moves = {'next': 1, 'next contact': 1, 'down': 1, 'previous': -1, 'previous contact': -1, 'up': -1}
    ends = {'home': 0, 'first contact': 0, 'start of list': 0, 'top of list': 0, 'first item': 0,
            'end': -1, 'last contact': -1, 'end of list': -1, 'bottom of list': -1, 'last item': -1}
    if command in moves or command in ends:
        if VIEW == 'details':
            VIEW = 'list'
        if not CONTACTS:
            m.speak(describe())
            return MODE
        old = INDEX
        if command in ends:
            select(0 if ends[command] == 0 else len(CONTACTS) - 1)
            edge = 'Start of list. ' if INDEX == old == 0 and ends[command] == 0 else 'End of list. ' if INDEX == old and ends[command] else ''
        else:
            select(old + moves[command])
            edge = ('Start of list. ' if moves[command] < 0 else 'End of list. ') if INDEX == old else ''
        m.speak(edge + describe())
        return MODE
    contact = current()
    if command in ('open contact', 'enter', 'okay', 'ok', 'that one', 'open', 'open it', 'details', 'contact details', 'show details'):
        if VIEW == 'details':
            return internal(m, '@contacts:choose', MODE)
        if contact is None:
            m.speak(describe())
            return MODE
        return open_details(m, contact)
    if command in ('read contact', 'read details', 'read contact details', 'read all details', 'who is this'):
        m.speak(full_details(contact) if contact else describe())
        return MODE
    if command in ('call details', 'phone', 'phone number', 'read phone', 'read phone number', 'what is the phone number',
                   'what s the phone number', 'what is their phone number', 'what s their number', 'call'):
        m.speak(phone_details(contact) if contact else describe())
        return MODE
    if command in ('email contact', 'email this person', 'email this contact', 'email them', 'email him', 'email her',
                   'write to them', 'send an email to them', 'new email to contact', 'email the contact'):
        if contact is None:
            m.speak(describe())
            return MODE
        return email_contact(m, contact)
    if command in ('delete contact', 'remove contact', 'delete this contact', 'delete', 'remove'):
        if contact is None:
            m.speak(describe())
            return MODE
        if contact.read_only:
            m.speak(contact.name + ' is in a Thunderbird address book that cannot be changed, so it cannot be deleted.')
            return MODE
        PENDING, CHOICE = ('delete', contact), 'no'
        m.speak(question(contact))
        return MODE
    found = re.fullmatch(r'(?:find contact|search for contact|search for|look up contact|look up|go to contact|go to|'
                         r'read contact|select contact|select|find) (.+)', command)
    if found:
        return find_contact(m, found[1])
    by_name = email_by_name(m, command, MODE)
    if by_name is not None:
        return by_name
    # A name said on its own selects that contact.
    matches = contacts_book.find(CONTACTS, command, loose=True) if command else []
    if len(matches) == 1:
        VIEW = 'list'
        select(CONTACTS.index(matches[0]))
        m.speak(describe())
        return MODE
    m.speak('Contacts. Say next contact, find and a name, email contact, add contact, or delete contact. '
            'Press F1 for help, or Escape for the main menu.')
    return MODE


def internal(m, raw, mode):
    """'@contacts:' requests from keys, prompts and lists."""
    global DETAIL_INDEX, VIEW
    action, _, value = raw[len('@contacts:'):].partition(' ')
    action, _, letter = action.partition(':')
    value = value.strip()
    if action == 'letter':
        if not CONTACTS:
            m.speak(describe())
            return MODE
        found = next_match([c.name for c in CONTACTS], INDEX, letter)
        if found is None:
            m.speak('No contact starts with ' + letter.upper() + '.')
            return MODE
        old = INDEX
        select(found)
        m.speak(('End of matching items. ' if found == old else '') + describe())
        return MODE
    if action == 'choose':
        if not DETAILS:
            return MODE
        label, request = DETAILS[DETAIL_INDEX]
        if request:
            return internal(m, request, MODE)
        m.speak(label + '.')
        return MODE
    if action == 'write':
        return write_to(m, letter, mode)
    if action == 'none':
        m.speak(letter + ' has no email address saved.')
        return mode
    if action == 'delete':
        return command_here(m, 'delete contact', 'delete contact')
    if action in ('add-name', 'add-email', 'add-phone'):
        return add_step(m, action[4:], value)
    if action == 'find':
        return find_contact(m, value)
    return mode


# ---- Details ---------------------------------------------------------------------

def open_details(m, contact):
    global VIEW, DETAILS, DETAIL_INDEX
    VIEW, DETAIL_INDEX = 'details', 0
    DETAILS = details_for(contact)
    m.speak(contact.name + '. ' + details_item() + ' Escape goes back to the list.')
    return MODE


def details_for(contact):
    items = []
    if len(contact.emails) == 1:
        items.append(('Email this person', '@contacts:write:' + contact.emails[0]))
    for email in contact.emails if len(contact.emails) > 1 else ():
        items.append(('Email this person at ' + email, '@contacts:write:' + email))
    items.append(('Name: ' + (contact.name or 'none'), ''))
    for email in contact.emails:
        items.append(('Email: ' + email, '@contacts:write:' + email))
    if not contact.emails:
        items.append(('No email address', ''))
    for phone in contact.phones:
        items.append(('Phone: ' + phone, ''))
    items.append(('Saved in: ' + contact.where(), ''))
    if not contact.read_only:
        items.append(('Delete contact', '@contacts:delete'))
    return items


def details_item():
    if not DETAILS:
        return describe()
    return announce_item(DETAILS[DETAIL_INDEX][0], DETAIL_INDEX, len(DETAILS), hotkey=False)


def full_details(contact):
    parts = [contact.name or 'No name']
    parts += ['Email ' + e for e in contact.emails] or ['No email address']
    parts += ['Phone ' + p for p in contact.phones]
    parts.append(contact.where())
    return '. '.join(parts) + '.'


def phone_details(contact):
    if not contact.phones:
        return contact.name + ' has no phone number saved.'
    return contact.name + ', phone ' + ', or '.join(contact.phones) + '.'


def question(contact):
    return ('Delete ' + contact.name + ' from your contacts? ' +
            ('It is also removed from Thunderbird. ' if contact.source == 'thunderbird' else '') +
            'Say Yes or No. Escape keeps it.')


# ---- Finding ---------------------------------------------------------------------

def find_contact(m, spoken):
    global VIEW
    if not CONTACTS:
        m.speak(describe())
        return MODE
    matches = contacts_book.find(CONTACTS, spoken, loose=True)
    if not matches:
        m.speak('No contact named ' + spoken.strip() + '. ' + describe())
        return MODE
    VIEW = 'list'
    select(CONTACTS.index(matches[0]))
    more = len(matches) - 1
    m.speak(describe() + (' ' + str(more) + (' more match.' if more == 1 else ' more matches.') if more else ''))
    return MODE


# ---- Email -----------------------------------------------------------------------

def email_contact(m, contact):
    """Start an email to this contact; ask which address when there are several."""
    if not contact.emails:
        m.speak(contact.name + ' has no email address saved.')
        return MODE
    if len(contact.emails) == 1:
        return write_to(m, contact.emails[0], MODE, contact.name)
    m.speak('Which email address for ' + contact.name + '? ' +
            m.open_named_list('Email addresses', [(e, '@contacts:write:' + e) for e in contact.emails]))
    return MODE


def write_to(m, address, mode, name=''):
    """A new email draft with the recipient filled in; the subject comes next."""
    if not address:
        return mode
    if not name:
        owner = next((c for c in CONTACTS if address in c.emails), None)
        name = owner.name if owner else ''
    m.speak('Email to ' + (name + ', ' if name else '') + address + '.')
    m.EMAIL_LINK_RECIPIENT = address
    return m.handle('new email', 'awake', typed=True)


def email_by_name(m, command, mode):
    """"email John", "send an email to John Smith", "write to Mary". None when no contact
    has that name and the words may be another command."""
    match = EMAIL_TO.fullmatch(command)
    if not match:
        return None
    name = match['name'].strip()
    explicit = not command.startswith(('email ', 'e mail '))
    if name in RESERVED or name.startswith(('to ', 'list', 'draft', 'the sender')) or '@' in name or len(name.split()) > 5:
        return None     # a command word, an address, or a sentence ("write to the council about the road")
    if mode == MODE and CONTACTS:
        contacts = CONTACTS
    else:
        people = book(m)
        contacts = people.cached()
        # Thunderbird's contacts not read yet (Thunderbird not started): read them now, starting it.
        if contacts_book.CACHE['at'] is None and thunderbird_pim.usable(m.APP):
            contacts = people.load()
    matches = contacts_book.find(contacts, name)
    if not matches:
        if not explicit or len(name.split()) > 3:
            return None     # maybe another command, or a sentence ("write to the council about the road")
        m.speak('No contact named ' + name + '. Say open contacts to hear your contacts, or add contact to save one.')
        return mode
    if len(matches) == 1:
        contact = matches[0]
        if not contact.emails:
            m.speak(contact.name + ' has no email address saved. Open Contacts to add one.')
            return mode
        if len(contact.emails) == 1:
            return write_to(m, contact.emails[0], mode, contact.name)
        m.speak('Which email address for ' + contact.name + '? ' +
                m.open_named_list('Email addresses', [(e, '@contacts:write:' + e) for e in contact.emails]))
        return mode
    choices = []
    for contact in matches[:9]:
        for email in contact.emails or ['']:
            choices.append((contact.name + (', ' + email if email else ', no email address'),
                            '@contacts:write:' + email if email else '@contacts:none:' + contact.name))
    m.speak('Which ' + name + '? ' + m.open_named_list('Contacts', choices))
    return mode


# ---- Adding ----------------------------------------------------------------------

def start_adding(m):
    """Control+N: ask for the name, then the email address, then the phone number."""
    global ADDING
    ADDING = {}
    return prompt(m, 'name', 'New contact. Type or say the name, then press Enter. Escape cancels.')


def prompt(m, step, words):
    nav = m.task_navigation()
    # A third value True: Enter with nothing typed (or "skip") leaves this one out.
    nav.prompt = ('@contacts:add-' + step + ' {value}', MODE, step != 'name')
    m.speak(words)
    return 'task_input'


def add_step(m, step, value):
    global ADDING
    value = value.strip().rstrip('.!?').strip() if step != 'email' else value.strip()
    if step == 'name':
        if not value:
            return prompt(m, 'name', 'Type or say the name, then press Enter. Escape cancels.')
        if len(value) > 80 or re.search(r'[\r\n<>]', value):
            return prompt(m, 'name', 'Use a shorter name without symbols. Type or say the name, then press Enter.')
        ADDING = {'name': value}
        return prompt(m, 'email', 'Email address for ' + value + '? Type or say it, then press Enter. '
                                  'To leave it out, press Enter or say skip.')
    if 'name' not in ADDING:
        return start_adding(m)
    if step == 'email':
        if m.normalized_command(value) in SKIP:
            ADDING['email'] = ''
        else:
            from email_draft import normalize_recipient
            try:
                ADDING['email'] = normalize_recipient(value)
            except ValueError as exc:
                return prompt(m, 'email', str(exc) + ' Type it again, or press Enter to leave it out.')
        return prompt(m, 'phone', 'Phone number? Type or say it, then press Enter. To leave it out, press Enter or say skip.')
    phone = '' if m.normalized_command(value) in SKIP else value
    if not ADDING.get('email') and not phone:
        return prompt(m, 'phone', 'A contact needs an email address or a phone number. Type a phone number, '
                                  'or press Escape to cancel.')
    details, ADDING = ADDING, {}
    global BOOK
    BOOK = BOOK or book(m)
    try:
        where = BOOK.add(details['name'], details.get('email', ''), phone)
    except ValueError as exc:
        m.speak(str(exc) + ' The contact was not saved.')
        return MODE
    reload(m)
    found = next((i for i, c in enumerate(CONTACTS) if c.name.casefold() == details['name'].casefold()
                  and (not details.get('email') or details['email'] in c.emails)), None)
    if found is not None:
        select(found)
    m.speak('Saved ' + details['name'] + ' ' + where + '. ' + describe())
    return MODE
