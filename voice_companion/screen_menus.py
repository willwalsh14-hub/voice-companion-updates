"""Alt menus for the main screens: Email, email drafts, Notepad, the web page,
Help, lessons and Document reader.

Each menu is built like the Word processor's document menu: Alt opens it,
groups are numbered, arrows move, Enter chooses, Escape goes back one level,
and Alt closes it. Every item runs the same spoken command as its voice
command or shortcut key, so the result is the same whichever way it is given.

The Word processor, File manager and Media player keep their own Alt menus
(task_navigation.py and file_manager.py). Radio and Podcasts use their actions
list as the menu, the main menu is already a menu, and on the Web browser menu
Alt says where you are.
"""
from menu_navigation import announce_item, next_match

# (label, key, command). The key is the shortcut that does the same thing, or ''
# when there is none; the menu then says its menu path, such as "Alt, 3, 2".
# A command with {value} asks for the value first. @options:<category> opens
# that page of Options.
MENUS = {
    'mailbox': ('Email menu', [
        ('Messages', [
            ('Check for new mail', 'F5', 'list messages'),
            ('Read message', 'Enter', 'open current message'),
            ('Reply', 'R', 'reply'),
            ('Reply all', 'A', 'reply all'),
            ('Forward', 'F', 'forward'),
            ('New email', 'N', 'new email'),
            ('Delete', 'Delete', 'delete'),
            ('Move to folder', 'M', 'move'),
            ('Message details', 'I', 'message details')]),
        ('Select', [
            ('Mark or unmark message', 'Space', 'toggle message selection'),
            ('Select all messages', 'Ctrl+A', 'select all messages'),
            ('Clear selection', 'U', 'clear selection'),
            ('What is selected', 'S', 'what is selected'),
            ('Copy sender address', 'C', 'copy sender address'),
            ('Email the sender', 'E', 'new email to sender')]),
        ('Go to folder', [
            ('Inbox', '', 'go to inbox'),
            ('Sent', '', 'go to sent'),
            ('Drafts', '', 'go to drafts'),
            ('Trash', '', 'go to trash'),
            ('Spam', '', 'go to spam'),
            ('Choose a folder', 'G', 'go to folder'),
            ('Which folder', 'W', 'which folder')]),
        ('Folder actions', [
            ('Create folder', '', 'create folder'),
            ('Rename folder', 'F2', 'rename folder'),
            ('Move folder', '', 'move folder'),
            ('Delete empty folder', '', 'delete folder'),
            ('List folders', '', 'list folders')]),
        ('Drafts and contacts', [
            ('Email drafts', 'D', 'open email draft'),
            ('List contacts', 'P', 'list contacts'),
            ('Add contact', '', 'add contact {value}')]),
        ('Accounts', [
            ('Add account', '', 'add account'),
            ('Switch account', '', 'switch account'),
            ('Which account', '', 'which email account'),
            ('Remove account', '', 'remove email account'),
            ('Add Thunderbird account', '', 'add thunderbird account')]),
        ('Pages', [
            ('Next page of messages', 'Page Down', 'next messages'),
            ('Previous page of messages', 'Page Up', 'previous messages'),
            ('Newest message in folder', 'Ctrl+Home', 'newest message'),
            ('Oldest message in folder', 'Ctrl+End', 'oldest message')]),
        ('Options and help', [
            ('Email options', '', '@options:Email'),
            ('Messages per page', '', 'email list size'),
            ('Email help', 'F1', 'help with email'),
            ('Main menu', '', 'main menu')]),
        # Last, so the Alt paths people learned for the groups above stay the same.
        ('Attachments', [
            ('List attachments', 'L', 'list attachments'),
            ('Save all attachments', '', 'save all attachments'),
            ('Open attachment', '', 'open attachment')]),
    ]),
    'email_draft': ('Email draft menu', [
        ('Message', [
            ('Send', 'Alt+S', 'send email'),
            ('Save draft', 'Ctrl+S', 'save draft'),
            ('Read draft', 'Ctrl+R', 'read email draft'),
            ('Save and close', '', 'leave email'),
            ('Discard draft', '', 'discard draft')]),
        ('Fields', [
            ('To', '', 'go to to'),
            ('Cc', '', 'go to cc'),
            ('Bcc', '', 'go to bcc'),
            ('Subject', '', 'go to subject'),
            ('Message body', '', 'go to body')]),
        ('Format', [
            ('Bold', 'Ctrl+B', 'toggle bold'),
            ('Italic', 'Ctrl+I', 'toggle italic'),
            ('Underline', 'Ctrl+U', 'toggle underline')]),
        ('Contacts and help', [
            ('List contacts', 'Ctrl+K', 'list contacts'),
            ('Add contact', '', 'add contact {value}'),
            ('Email options', '', '@options:Email'),
            ('Writing email help', 'F1', 'help with writing email')]),
        ('Attachments', [
            ('Attach a file', 'Ctrl+T', 'attach a file'),
            ('List attachments', 'Ctrl+L', 'list attachments'),
            ('Remove an attachment', '', 'remove attachment')]),
    ]),
    'note': ('Notepad menu', [
        ('Notepad', [
            ('Save and close note', 'Ctrl+S', 'save note'),
            ('Read note', 'Ctrl+R', 'read from top'),
            ('Saved notes', 'Ctrl+O', 'list notes'),
            ('New line', '', 'new line'),
            ('Notepad options', '', '@options:Notepad'),
            ('Notepad help', 'F1', 'help with notepad'),
            ('Main menu', '', 'main menu')]),
    ]),
    'web': ('Web page menu', [
        ('Page', [
            ('Back', 'Alt+Left', 'web key Back'),
            ('Forward', 'Alt+Right', 'web key Forward'),
            ('Refresh', 'F5', 'web key Refresh'),
            ('Search or enter address', 'Ctrl+L', 'web key Search'),
            ('Find on page', 'Ctrl+F', 'find {value}'),
            ('Top of page', '', 'top of page'),
            ('Bottom of page', '', 'bottom of page')]),
        ('Lists', [
            ('Headings', 'F6', 'list headings'),
            ('Links', 'F7', 'list links'),
            ('Form fields', 'F8', 'list fields'),
            ('Downloads', 'Ctrl+D', 'download manager')]),
        ('Favorites', [
            ('Add favorite', '', 'add favorite'),
            ('Go to favorite', '', 'go to favorite'),
            ('Web history', '', 'web history'),
            ('Web browser menu', 'Escape', 'web browser menu')]),
        ('Options and help', [
            ('Web browser options', '', '@options:Web browser'),
            ('Set up Chrome', '', 'set up chrome'),
            ('Web help', 'F1', 'help with web')]),
    ]),
    'downloads': ('Downloads menu', [
        ('Download', [
            ('Open', 'Enter', 'open download'),
            ('Pause or resume', 'Space', 'pause or resume download'),
            ('Cancel or remove from list', 'Delete', 'delete download'),
            ('Details', '', 'download details'),
            ('Open downloads folder', '', 'open downloads folder')]),
        ('Queue', [
            ('Move to next', '', 'move to next'),
            ('Move earlier', 'Ctrl+Up', 'move download earlier'),
            ('Move later', 'Ctrl+Down', 'move download later')]),
        ('Options and help', [
            ('Download folder', '', '@options:Web browser'),
            ('Downloads help', 'F1', 'help with downloads'),
            ('Close downloads', 'Escape', 'close downloads')]),
    ]),
    'contacts': ('Contacts menu', [
        ('Contact', [
            ('Email this person', 'Ctrl+E', 'email contact'),
            ('Open details', 'Enter', 'open contact'),
            ('Read all details', '', 'read contact'),
            ('Phone number', '', 'call details'),
            ('Delete contact', 'Delete', 'delete contact')]),
        ('Contacts', [
            ('Add contact', 'Ctrl+N', 'add contact'),
            ('Find contact', 'Ctrl+F', 'find contact {value}'),
            ('Refresh contacts', '', 'refresh contacts'),
            ('Contacts help', 'F1', 'help with contacts'),
            ('Main menu', '', 'main menu')]),
    ]),
    'calendar': ('Calendar menu', [
        ('Event', [
            ('Read event', 'Enter', 'read event'),
            ('Next event', 'Down', 'next event'),
            ('Previous event', 'Up', 'previous event'),
            ('Add event', 'Ctrl+N', 'add event'),
            ('Edit event', 'F2', 'edit event'),
            ('Delete event', 'Delete', 'delete event')]),
        ('Day', [
            ('Today', 'Home', 'today'),
            ('Next day', 'Right', 'next day'),
            ('Previous day', 'Left', 'previous day'),
            ('Next week', 'Page Down', 'next week'),
            ('Previous week', 'Page Up', 'previous week'),
            ('Go to a date', 'Ctrl+G', 'go to date')]),
        ('Calendar', [
            ('Refresh calendar', '', 'refresh calendar'),
            ('Calendar help', 'F1', 'help with calendar'),
            ('Main menu', '', 'main menu')]),
    ]),
    'help': ('Help menu', [
        ('Help', [
            ('Help topics', '', 'help topics'),
            ('Subsections', '', 'list subsections'),
            ('Release notes', '', 'release notes'),
            ('Lessons', '', 'start tutorial'),
            ('Open the guide folder', '', 'open user guides'),
            ('Close help', '', 'close help'),
            ('Main menu', '', 'main menu')]),
    ]),
    'tutorial': ('Lessons menu', [
        ('Lessons', [
            ('Lesson list', 'Ctrl+L', 'lesson list'),
            ('Repeat', 'Ctrl+R', 'repeat'),
            ('Continue reading', 'Ctrl+Down', 'continue reading'),
            ('Next lesson', '', 'next lesson'),
            ('Previous lesson', '', 'previous lesson'),
            ('Find lesson', 'Ctrl+F', 'find lesson {value}'),
            ('Explain a command', 'Ctrl+E', 'explain command {value}'),
            ('List commands', 'F1', 'list commands'),
            ('Main menu', '', 'main menu')]),
    ]),
    'attachments': ('Attachments menu', [
        ('Attachment', [
            ('Open', 'Enter', 'open attachment'),
            ('Save in Downloads', 'Ctrl+S', 'save attachment'),
            ('Save to a folder', '', 'save attachment to folder'),
            ('Save all', 'Ctrl+Shift+S', 'save all attachments'),
            ('Remove from this email', 'Delete', 'remove attachment'),
            ('Attach another file', 'Ctrl+T', 'attach a file'),
            ('Attachments help', 'F1', 'help with attachments'),
            ('Close the list', 'Escape', 'close attachments')]),
    ]),
    'file_reader': ('Document reader menu', [
        ('Read', [
            ('Read document', 'Ctrl+R', 'read document'),
            ('Find text', 'Ctrl+F', 'find {value}'),
            ('Beginning of document', '', 'go to beginning of document'),
            ('End of document', '', 'go to end of document'),
            ('Formatting', 'Insert+F', 'what formatting'),
            ('Selected text', 'Insert+Shift+Down', 'what is selected')]),
        ('Move by', [
            ('Next heading', 'H', 'next heading'),
            ('Previous heading', 'Shift+H', 'previous heading'),
            ('Next table', 'T', 'next table'),
            ('Next list', 'L', 'next list'),
            ('Next link', 'K', 'next link'),
            ('Follow link', 'Enter', 'follow link'),
            ('Next bookmark', 'B', 'next bookmark'),
            ('Next footnote', 'N', 'next footnote')]),
        ('Leave and help', [
            ('Back to files', 'Escape', 'back to files'),
            ('Main menu', '', 'main menu'),
            ('Document reader help', 'F1', 'help with document reader')]),
    ]),
}

# Screens where Alt does something here. Word processor, document, File manager
# and Media player handle Alt themselves when it is pressed.
ALT_SCREENS = frozenset(MENUS) | {'awake', 'browser_menu', 'media', 'podcast'}
# Spoken ways to open a menu, besides its name ("email menu").
OPEN_PHRASES = {'mailbox': ('menu', 'open menu', 'mail menu'), 'file_reader': ('menu', 'open menu', 'reader menu'),
                'email_draft': ('draft menu',), 'web': ('page menu',), 'help': (), 'note': ('note menu',),
                'tutorial': ('tutorial menu', 'lesson menu'), 'downloads': ('menu', 'open menu', 'download menu'),
                'contacts': ('menu', 'open menu', 'contact menu'), 'calendar': ('menu', 'open menu'),
                'attachments': ('menu', 'open menu', 'attachment menu')}
NUMBER_WORDS = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9'}
# Context flags that mean a text box, list or question owns the keyboard.
BUSY_CONTEXT = ('named_list', 'task_input', 'account_entry', 'browser_name_input', 'file_name_input', 'media_input',
                'media_form', 'web_private', 'web_field', 'web_name_list')


def menu_path(mode, group, index):
    """The keys that reach an item from the screen: Alt, then numbers."""
    groups = MENUS[mode][1]
    return 'Alt, ' + ((str(group + 1) + ', ') if len(groups) > 1 else '') + str(index + 1)


def items(mode):
    """Every item of a screen's menu as (group, label, key or menu path, command)."""
    rows = []
    for group, (name, entries) in enumerate(MENUS[mode][1]):
        for index, (label, key, command) in enumerate(entries):
            rows.append((name, label, key or menu_path(mode, group, index), command))
    return rows


def alt_menu_key(context, command_entry_active=False, typed_text=''):
    """What a lone Alt press (pressed and let go) sends, or None to leave it alone."""
    mode = context.get('mode')
    if command_entry_active:
        return None
    if context.get('screen_menu'):
        return 'Alt'
    if mode not in ALT_SCREENS or context.get('confirmation') is not None:
        return None
    if any(context.get(name) for name in BUSY_CONTEXT):
        return None
    # A typed command is kept. Notes and email boxes keep their text: the window
    # adds it first, as Enter or Tab would.
    if typed_text.strip() and mode not in ('note', 'email_draft'):
        return None
    return 'Alt'


def alt_unavailable(context, command_entry_active=False, typed_text=''):
    """What to say when a lone Alt opens no menu, so the key is never silent."""
    if command_entry_active or typed_text.strip() and context.get('mode') not in ('note', 'email_draft'):
        return 'No Alt menu while a command is typed. Press Enter to run it, or Escape to clear it.'
    if context.get('confirmation') is not None:
        return 'Answer the question first. Press Y for yes or N for no.'
    if any(context.get(name) for name in BUSY_CONTEXT):
        return 'No Alt menu here. Finish this first, or press Escape to go back.'
    return 'This screen has no Alt menu.'


def overlay_key(keysym, char, state):
    """The menu key for a key pressed while a menu is open.

    None lets the key do its usual job (shortcuts, F keys, Alt combinations);
    '' swallows it so that it does not type into the page or message."""
    from key_state import modifiers
    control, shift, alt = modifiers(state)
    if control or alt:
        return None
    named = {'Up': 'Up', 'Down': 'Down', 'Left': 'Left', 'Right': 'Right', 'Home': 'Home', 'End': 'End',
             'Return': 'Enter', 'KP_Enter': 'Enter', 'Escape': 'Escape', 'ISO_Left_Tab': 'ShiftTab',
             'Tab': 'ShiftTab' if shift else 'Tab'}
    if keysym in named:
        return named[keysym]
    if char and len(char) == 1 and char.isalnum():
        return 'Letter:' + char
    if keysym in ('space', 'BackSpace', 'Delete', 'Prior', 'Next') or char and char.isprintable():
        return ''
    return None


class ScreenMenu:
    def __init__(self, app):
        self.app = app
        self.mode = None
        self.group = None
        self.index = 0

    def is_open(self, mode):
        return self.mode is not None and self.mode == mode

    def entries(self):
        groups = MENUS[self.mode][1]
        if len(groups) == 1:
            return groups[0][1]
        if self.group is None:
            return [(name, '', None) for name, _ in groups]
        return groups[self.group][1]

    def describe(self, index):
        entries = self.entries()
        label, key, command = entries[index]
        text = announce_item(label, index, len(entries))
        if command is None:
            return text
        groups = MENUS[self.mode][1]
        group = 0 if len(groups) == 1 else self.group
        text += ' ' + (key or menu_path(self.mode, group, index)) + '.'
        if not command.startswith(('@', 'web key ')) and '{value}' not in command:
            text += ' Say ' + command + '.'
        return text

    def keyboard(self, key, mode):
        """Keys for the menu. Called first in companion.navigation_key."""
        if self.mode is not None and self.mode != mode:
            self.close()
        if self.mode is None:
            if key != 'Alt':
                return None
            if mode in MENUS:
                return '@sm:open'
            if mode in ALT_SCREENS:
                return '@sm:alt'
            return None
        nav = {'Up': 'previous', 'Left': 'previous', 'ShiftTab': 'previous', 'Down': 'next', 'Right': 'next',
               'Tab': 'next', 'Home': 'home', 'End': 'end', 'Enter': 'enter', 'Escape': 'escape', 'Alt': 'close'}
        if key in nav:
            return '@sm:' + nav[key]
        if key.startswith('Letter:'):
            return '@sm:letter:' + key[7:]
        if key == 'Control':
            return None
        # Any other key closes the menu and does its usual job.
        self.close()
        return None

    def close(self):
        self.mode = None
        self.group = None
        self.index = 0

    def available(self, mode):
        """None when the menu can open, or what to say when a step must finish first."""
        m = self.app
        busy = 'Finish or cancel this step first. Then press Alt for the menu.'
        if m.NAMED_LIST or getattr(m, 'ACCOUNT_PICKER', None):
            return 'Close this list first. Then press Alt for the menu.'
        if mode == 'mailbox':
            mail = m.mail_session
            if mail is None:
                return 'Email is not open.'
            if getattr(mail, 'pending', None) or getattr(mail, 'folder_picker', None) or getattr(mail, 'folder_choice', None):
                return busy
        if mode == 'email_draft' and (m.email_draft is None or m.pending_send is not None):
            return 'Answer the send question first. Then press Alt for the menu.' if m.email_draft else busy
        if mode == 'web':
            web = m.web_session
            if web is None or getattr(web, 'pending', None) or getattr(web, 'submit_pending', None) or getattr(web, 'list_focus', None) or getattr(web, 'keyboard_field', None):
                return busy
        if mode == 'help' and m.help_session is None:
            return busy
        if mode == 'tutorial' and m.tutorial_session is None:
            return busy
        if mode == 'file_reader' and m.FILE_READER is None:
            return busy
        return None

    def open(self, mode):
        m = self.app
        problem = self.available(mode)
        if problem:
            m.speak(problem)
            return mode
        self.mode = mode
        self.group = None
        self.index = 0
        title = MENUS[mode][0]
        m.speak(title + '. ' + self.describe(0))
        return mode

    def other_alt(self, mode):
        """Alt on screens that already are, or already have, a menu."""
        m = self.app
        if mode == 'awake':
            if m.MAIN_MENU_INDEX is None:
                m.MAIN_MENU_INDEX = 0
            names = [name for name, _ in m.MAIN_MENU_CHOICES]
            m.speak('Main menu. ' + announce_item(names[m.MAIN_MENU_INDEX], m.MAIN_MENU_INDEX, len(names)))
            return mode
        if mode == 'browser_menu':
            return m.handle('web browser menu', mode, typed=True)
        if mode in ('media', 'podcast') and m.MEDIA_HUB:
            nav = m.media_navigation()
            if nav.form or nav.pending:
                m.speak('Finish or cancel this form first. Then press Alt for the menu.')
                return mode
            result = nav.keyboard('Tab')
            name = 'Podcasts menu. ' if nav.section == 'podcast' else 'Radio menu. '
            if nav.focus == 'actions':
                m.speak(name + result.split('. ', 1)[-1] if result.startswith('Actions. ') else name + result)
            else:
                m.speak('Menu closed. ' + result)
            return mode
        return None

    def run(self, command, mode):
        m = self.app
        if command.startswith('@options:'):
            m.SETTINGS_RETURN_MODE = mode
            result = m.open_settings(mode)
            if result == 'settings' and m.SETTINGS_PANEL:
                m.SETTINGS_PANEL.command(command.split(':', 1)[1])
            return result
        if '{value}' in command:
            return m.task_navigation().action(command, mode)
        return m.handle(command, mode, typed=True)

    def choose(self, index, mode):
        entries = self.entries()
        if not 0 <= index < len(entries):
            self.app.speak('Choose a number from 1 to ' + str(len(entries)) + '.')
            return mode
        label, _, command = entries[index]
        if command is None:
            self.group = index
            self.index = 0
            self.app.speak(label + ' menu. ' + self.describe(0))
            return mode
        self.close()
        return self.run(command, mode)

    def back(self, mode):
        m = self.app
        if len(MENUS[self.mode][1]) > 1 and self.group is not None:
            self.index = self.group
            self.group = None
            m.speak(MENUS[self.mode][0] + '. ' + self.describe(self.index))
            return mode
        self.close()
        m.speak('Menu closed. ' + m.options_return_notice(mode))
        return mode

    def move(self, command, mode):
        entries = self.entries()
        old = self.index
        if command == 'home':
            self.index = 0
        elif command == 'end':
            self.index = len(entries) - 1
        else:
            self.index = max(0, min(len(entries) - 1, old + (1 if command == 'next' else -1)))
        edge = ''
        if self.index == old:
            edge = 'Start of list. ' if command in ('previous', 'home') else 'End of list. '
        self.app.speak(edge + self.describe(self.index))
        return mode

    def letter(self, value, mode):
        if value.isdigit():
            return self.choose(int(value) - 1, mode)
        entries = self.entries()
        index = next_match([entry[0] for entry in entries], self.index, value)
        if index is None:
            self.app.speak('No item starts with ' + value.upper() + '.')
            return mode
        self.index = index
        self.app.speak(self.describe(index))
        return mode

    def handle(self, text, mode):
        """Menu keys and voice commands. None means the screen handles the text."""
        m = self.app
        if self.mode is not None and self.mode != mode:
            self.close()
        if text.startswith('@sm:'):
            action = text[4:]
            if action == 'open':
                return self.open(mode) if self.mode is None else self.back(mode)
            if action == 'alt':
                return self.other_alt(mode) or mode
            if self.mode is None:
                return mode
            if action == 'close':
                self.close()
                m.speak('Menu closed. ' + m.options_return_notice(mode))
                return mode
            if action in ('next', 'previous', 'home', 'end'):
                return self.move(action, mode)
            if action == 'enter':
                return self.choose(self.index, mode)
            if action == 'escape':
                return self.back(mode)
            if action.startswith('letter:'):
                return self.letter(action[7:], mode)
            return mode
        command = m.normalized_command(text)
        if self.mode is None:
            if mode in MENUS and command in (MENUS[mode][0].lower(),) + OPEN_PHRASES.get(mode, ()):
                return self.open(mode)
            return None
        if command in ('next', 'previous', 'next item', 'previous item'):
            return self.move(command.split()[0], mode)
        if command in ('first item', 'start of list', 'top of list'):
            return self.move('home', mode)
        if command in ('last item', 'end of list', 'bottom of list'):
            return self.move('end', mode)
        if command in ('enter', 'okay', 'ok', 'that one', 'confirm', 'confirm that', 'open', 'open it'):
            return self.choose(self.index, mode)
        if command in ('escape', 'go back', 'back', 'cancel'):
            return self.back(mode)
        if command in ('close menu', 'exit menu', 'leave menu', 'alt'):
            return self.handle('@sm:close', mode)
        number = NUMBER_WORDS.get(command, command)
        if number.isdigit() and len(number) == 1:
            return self.choose(int(number) - 1, mode)
        for index, entry in enumerate(self.entries()):
            if command == entry[0].lower():
                return self.choose(index, mode)
        # Any other command closes the menu and works as usual on this screen.
        self.close()
        return None
