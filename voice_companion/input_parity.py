"""Reviewable command inventory from the same menus used by the application."""
import re
from pathlib import Path

# Direct keys for actions that used to need a typed command (Alt+T).
# Each key runs the same spoken command, so voice and keyboard give the same result.
# The window sends these keys only on the named screen; see screen_key().
SCREEN_KEYS = {
    'awake': {'F1': 'help', 'Ctrl+V': 'what version', 'Ctrl+M': 'what mode',
              'Ctrl+G': 'open user guides', 'Ctrl+T': 'start tutorial'},
    'note': {'Ctrl+S': 'save note', 'Ctrl+R': 'read from top', 'F1': 'help with notepad'},
    'email_draft': {'Ctrl+S': 'save draft', 'Ctrl+R': 'read email draft', 'F1': 'help with writing email',
                    'Ctrl+B': 'toggle bold', 'Ctrl+I': 'toggle italic', 'Ctrl+U': 'toggle underline',
                    'Ctrl+K': 'list contacts', 'Ctrl+O': 'attach a file', 'Ctrl+L': 'list attachments',
                    'Ctrl+Z': 'undo', 'Ctrl+Y': 'redo'},
    # Email: one letter per action, no modifiers (Will, October 2026). Letters act in the
    # message list and in an open message; K and B stay for links and buttons in a message.
    'mailbox': {'N': 'new email', 'R': 'reply', 'A': 'reply all', 'F': 'forward', 'M': 'move',
                'G': 'go to folder', 'W': 'which folder', 'I': 'message details',
                'C': 'copy sender address', 'E': 'new email to sender', 'D': 'open email draft',
                'P': 'list contacts', 'U': 'clear selection', 'S': 'what is selected', 'L': 'list attachments',
                'Next': 'next messages', 'Prior': 'previous messages',
                'F1': 'help with email', 'F7': 'list links', 'F8': 'list buttons'},
    'file_reader': {'Ctrl+F': 'find {value}', 'Ctrl+R': 'read document', 'F1': 'help with document reader'},
    'web': {'F1': 'help with web', 'Ctrl+F': 'find {value}', 'F8': 'list fields', 'Ctrl+Shift+W': 'close browser window',
            'Ctrl+D': 'download manager'},
    'browser_menu': {'F1': 'help with web', 'Ctrl+Shift+W': 'close browser window', 'Ctrl+D': 'download manager'},
    # The Downloads screen (download_screen.py). Up, Down, Home, End, Enter and Escape work as in every list.
    'downloads': {'F1': 'help with downloads', 'Space': 'pause or resume download', 'Delete': 'delete download',
                  'Ctrl+Up': 'move download earlier', 'Ctrl+Down': 'move download later'},
    # Contacts (contacts_screen.py). Up, Down, Home, End, letters, Enter and Escape work as in every list.
    'contacts': {'F1': 'help with contacts', 'Ctrl+N': 'add contact', 'Ctrl+E': 'email contact',
                 'Ctrl+F': 'find contact {value}', 'Delete': 'delete contact', 'F2': 'edit contact',
                 'Ctrl+L': 'choose address book'},
    # Calendar (calendar_screen.py). Enter reads the event; Escape goes to the main menu.
    'calendar': {'F1': 'help with calendar', 'Up': 'previous event', 'Down': 'next event', 'Left': 'previous day',
                 'Right': 'next day', 'Home': 'today', 'Prior': 'previous week', 'Next': 'next week',
                 'Ctrl+N': 'add event', 'Delete': 'delete event', 'F2': 'edit event', 'Ctrl+G': 'go to date',
                 'Ctrl+L': 'choose calendar'},
    # The Attachments list (attachment_screen.py). Up, Down, Home, End, Enter and Escape work as in every list.
    # Letters move to the next name that starts with them, as in Contacts, so actions use Control.
    'attachments': {'F1': 'help with attachments', 'Ctrl+S': 'save attachment', 'Ctrl+Shift+S': 'save all attachments',
                    'Ctrl+D': 'save attachment to folder', 'Ctrl+I': 'read attachment details',
                    'Delete': 'remove attachment', 'Ctrl+O': 'attach a file'},
    # File manager: email the chosen files, or add them to the email draft that is open.
    'files': {'Ctrl+N': 'email this file', 'Ctrl+O': 'attach to draft'},
    'media': {'F1': 'help with radio'},  # 'help with podcasts' when Podcasts is open
    'word_processor': {'F1': 'help with documents'},
    'recent_documents': {'F1': 'help with documents'},
    'media_menu': {'F1': 'help with media player'},
    'notepad_menu': {'F1': 'help with notepad'},
    'reader_menu': {'F1': 'help with document reader'},
    'reader_recent': {'F1': 'help with document reader'},
    'local_media': {'F1': 'help with media player'},
    # YouTube (youtube_screen.py): one key per action, most as on YouTube itself (K, J, L, M, C, F).
    'youtube': dict(__import__('youtube_screen').KEYS),
    # Sentences in an open help topic (Up and Down move by paragraph).
    'help': {'Ctrl+Down': 'next sentence', 'Ctrl+Up': 'previous sentence'},
    'tutorial': {'Ctrl+R': 'repeat', 'F1': 'list commands', 'Ctrl+L': 'lesson list',
                 'Ctrl+F': 'find lesson {value}', 'Ctrl+E': 'explain command {value}',
                 'Ctrl+Down': 'continue reading'},
    'sleep': {'Ctrl+F12': 'wake up'},
}
# Email: Alt and a number says one line of the message's header, as screen reader users know
# from Outlook (Will, October 2026). It works in the message list and in an open message.
# The window sends these itself (mail_header_key); chord() and screen_key() leave Alt keys alone,
# so they are not in SCREEN_KEYS. companion.navigation_key turns them into the spoken questions.
MAIL_HEADER_KEYS = {'Alt+1': 'who is it from', 'Alt+2': 'who is it to', 'Alt+3': 'read the cc',
                    'Alt+4': 'what is the subject', 'Alt+5': 'when was it sent', 'Alt+6': 'how many attachments'}
# Earlier keys that still work, so nobody's habits break. The guide and menus name the
# simpler keys above; these are not listed there (Will, October 2026).
OLD_KEYS = {
    'awake': {'Ctrl+Shift+V': 'what version', 'Ctrl+Shift+M': 'what mode',
              'Ctrl+Shift+G': 'open user guides', 'Ctrl+Shift+T': 'start tutorial'},
    'note': {'Ctrl+Shift+R': 'read from top'},
    # Control+T attached files until 0.2.172; Control+O does now (Will, October 2026).
    # The first use in a session says the new key (attachment_screen.old_key_used).
    'email_draft': {'Ctrl+Shift+R': 'read email draft', 'Ctrl+T': 'attach a file'},
    'attachments': {'Ctrl+T': 'attach a file'},
    'files': {'Ctrl+T': 'attach to draft'},
    'file_reader': {'Ctrl+Shift+R': 'read document'},
    'tutorial': {'Ctrl+Shift+R': 'repeat', 'Ctrl+Shift+F': 'explain command {value}'},
    'mailbox': {'Ctrl+I': 'message details', 'Ctrl+Shift+I': 'which folder', 'Ctrl+O': 'open email draft',
                'Ctrl+K': 'list contacts', 'Ctrl+Shift+K': 'add contact {value}', 'Ctrl+Shift+A': 'clear selection',
                'Ctrl+Shift+S': 'what is selected', 'Ctrl+Shift+M': 'move folder'},
}
# Control+F12 puts the microphone to sleep from any main screen; while asleep it wakes it.
# F12 alone, or Insert+F12, says the time (twice quickly, the date) on every screen; see time_date.py.
for _mode in ('awake', 'note', 'email_draft', 'mailbox', 'file_reader', 'web', 'browser_menu', 'media', 'word_processor',
              'recent_documents', 'media_menu', 'local_media', 'notepad_menu', 'reader_menu', 'reader_recent', 'help', 'tutorial', 'document', 'files', 'document_menu', 'downloads',
              'contacts', 'calendar', 'attachments', 'youtube'):
    SCREEN_KEYS.setdefault(_mode, {})['Ctrl+F12'] = 'go to sleep'
# Keys that work only in one part of a screen.
HELP_ARTICLE_ONLY = {'Ctrl+Down', 'Ctrl+Up'}
# Formatting keys apply to the message body, not to an address or subject box.
BODY_ONLY = {'Ctrl+B', 'Ctrl+I', 'Ctrl+U'}


def chord(keysym, state):
    """The key name used in SCREEN_KEYS for a Tk key event."""
    from key_state import modifiers
    control, shift, alt = modifiers(state)  # Num Lock is not Alt on Windows.
    if alt or keysym in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R'):
        return None
    # Some keyboards report the comma and period keys by their characters, not their names.
    keysym = {',': 'comma', '.': 'period'}.get(keysym, keysym)
    name = keysym.upper() if len(keysym) == 1 else {'Page_Down': 'Next', 'Page_Up': 'Prior', 'space': 'Space'}.get(keysym, keysym)
    if control:
        return 'Ctrl+' + ('Shift+' if shift else '') + name
    return None if shift else name


def screen_key(mode, keysym, state, context, email_field=None, typed_text=''):
    """The SCREEN_KEYS key for this event, or None to leave the key alone."""
    key = chord(keysym, state)
    if key is None or (key not in SCREEN_KEYS.get(mode, {}) and key not in OLD_KEYS.get(mode, {})):
        return None
    # A Yes or No question owns the keyboard until it is answered.
    if context.get('confirmation') is not None or context.get('named_list'):
        return None
    if mode == 'youtube' and key == 'F1' and context.get('task_input'):
        return key  # help from the YouTube search box
    # Private and naming boxes keep their typed text until Enter or Escape.
    if any(context.get(name) for name in ('web_private', 'media_input', 'browser_name_input', 'file_name_input', 'task_input')):
        return None
    # Email letters: never while a command is typed or while a folder or account list
    # takes letters to jump through its names.
    if mode == 'mailbox' and len(key) == 1 and (typed_text or context.get('menu_letters')):
        return None
    if mode == 'youtube' and typed_text and not (key.startswith('Ctrl+') or re.fullmatch(r'F\d+', key)):
        return None  # letters, Space and digits edit a typed command (F is a letter; F1 to F12 are not)
    if mode == 'youtube' and context.get('menu_letters') and len(key) == 1 and key.isalpha():
        return None  # in the YouTube menu and the filter lists, letters move to the next name
    if mode == 'awake' and typed_text and key.startswith('Ctrl+'):
        return None  # Control+V pastes into a typed command; the main menu keys wait.
    if mode == 'downloads' and key == 'Space' and typed_text:
        return None  # a space inside a typed command
    if mode in ('contacts', 'calendar') and typed_text and not key.startswith(('Ctrl+', 'F')):
        return None  # arrows, Home and Delete edit a typed command
    if mode == 'mailbox' and key in ('Next', 'Prior') and (context.get('source') or typed_text):
        return None  # Page keys scroll an open message; they change pages only in the list.
    if mode == 'email_draft' and key in BODY_ONLY and email_field:
        return None
    if mode == 'help' and key in HELP_ARTICLE_ONLY and not context.get('help_reader'):
        return None
    return key


def mail_header_key(keysym, state):
    """'Alt+1' to 'Alt+6' for Alt and a number key (with no Control or Shift), else None."""
    from key_state import modifiers
    control, shift, alt = modifiers(state)
    if not alt or control or shift:
        return None
    key = 'Alt+' + str(keysym)
    return key if key in MAIL_HEADER_KEYS else None


def screen_command(key, mode):
    """The spoken command for a SCREEN_KEYS key. A command that needs a value opens a prompt."""
    command = SCREEN_KEYS.get(mode, {}).get(key) or OLD_KEYS.get(mode, {}).get(key)
    if command and '{value}' in command:
        return '@prompt:' + command
    return command


def settings_phrase(field):
    """What to say in Options to change a field: its name, or its key name when the name is long."""
    name=field.label if ';' not in field.label else field.key.replace('_',' ')
    return 'set '+name+' to'


def catalog():
    import companion as app
    from file_manager import MENU
    from task_navigation import DOCUMENT_ACTIONS
    from settings_model import CATEGORIES,fields
    rows=[]
    def add(area,name,voice,key):rows.append({'area':area,'action':name,'voice':voice,'keyboard':key})
    for name,voice in app.MAIN_MENU_CHOICES:add('Main menu',name,voice,'First letter, arrows, Enter')
    for name,key,voice in MENU:add('File manager',name,voice,key)
    for group,items in DOCUMENT_ACTIONS.items():
        for name,key,voice in items:add('Document: '+group,name,voice,key or 'Alt, group number, item key')
    nav=app.task_navigation()
    for mode in ('word_processor','notepad_menu','reader_menu','media_menu','local_media'):
        for name,key,voice in nav.items(mode):add(mode.replace('_',' ').title(),name,name if voice.startswith('@') else voice,key or 'First letter, arrows, Enter')
    from app_settings import ACTIONS
    for category in CATEGORIES:
        for field in fields(category,{}):
            if field.kind=='action':add(category,field.label,ACTIONS[field.key],'Alt+O, arrows to '+category+', Tab to the button, Enter')
            else:add(category,field.label.split(';')[0],settings_phrase(field)+' ...','Alt+O, arrows to '+category+', Tab to the field, arrows or Space, Enter to save')
    from media_navigation import RADIO,RADIO_STATION,PODCASTS
    for area,actions in (('Radio',RADIO),('Radio station actions',RADIO_STATION),('Podcasts',PODCASTS)):
        for action in actions:add(area,action.label,action.label,'Actions list: arrows to the action, Enter')
    from screen_menus import MENUS,items as menu_items
    for mode,(title,_) in MENUS.items():
        for group,name,key,voice in menu_items(mode):
            spoken='Options, then '+voice.split(':',1)[1]+' options' if voice.startswith('@options:') else name if voice.startswith('web key ') else voice
            add('Alt menu, '+title[:-len(' menu')]+': '+group,name,spoken,key)
    from browser_menu import BrowserMenu
    from types import SimpleNamespace
    # Bypass history loading: choices depends only on the session's page state.
    menu=object.__new__(BrowserMenu)
    for name,voice in menu.choices(SimpleNamespace(snapshot=None,backend=None),False):add('Browser menu',name,name,'First letters, arrows, Enter')
    add('All command views','Enter a spoken command with the keyboard','Say the desired command','Alt+T, type the same command, Enter')
    return rows


def render():
    lines=['Voice Companion voice and keyboard command inventory','',
           'This inventory covers declared menu actions. It is not a claim of exhaustive testing of every website or microphone phrase.',
           'Dynamic items such as files, emails, stations and favorites use their spoken names or list arrows and Enter.','']
    area=None
    for row in catalog():
        if row['area']!=area:area=row['area'];lines.extend(['',area])
        lines.append(row['action']+' | Say: '+row['voice']+' | Keys: '+row['keyboard'])
    return '\n'.join(lines)+'\n'


if __name__=='__main__':print(render())
