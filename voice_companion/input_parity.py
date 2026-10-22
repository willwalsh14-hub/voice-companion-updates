"""Reviewable command inventory from the same menus used by the application."""
from pathlib import Path

# Direct keys for actions that used to need a typed command (Alt+T).
# Each key runs the same spoken command, so voice and keyboard give the same result.
# The window sends these keys only on the named screen; see screen_key().
SCREEN_KEYS = {
    'awake': {'F1': 'help'},
    'note': {'Ctrl+S': 'save note', 'Ctrl+Shift+R': 'read from top', 'F1': 'help with notepad'},
    'email_draft': {'Ctrl+S': 'save draft', 'Ctrl+Shift+R': 'read email draft', 'F1': 'help with writing email',
                    'Ctrl+B': 'toggle bold', 'Ctrl+I': 'toggle italic', 'Ctrl+U': 'toggle underline'},
    'mailbox': {'Next': 'next messages', 'Prior': 'previous messages', 'Ctrl+I': 'message details',
                'F1': 'help with email'},
    'file_reader': {'Ctrl+F': 'find {value}', 'Ctrl+Shift+R': 'read document', 'F1': 'help with document reader'},
    'web': {'F1': 'help with web'},
    'browser_menu': {'F1': 'help with web'},
    'media': {'F1': 'help with radio'},  # 'help with podcasts' when Podcasts is open
    'word_processor': {'F1': 'help with documents'},
    'recent_documents': {'F1': 'help with documents'},
    'media_menu': {'F1': 'help with media player'},
    'local_media': {'F1': 'help with media player'},
}
# Formatting keys apply to the message body, not to an address or subject box.
BODY_ONLY = {'Ctrl+B', 'Ctrl+I', 'Ctrl+U'}


def chord(keysym, state):
    """The key name used in SCREEN_KEYS for a Tk key event."""
    control, shift, alt = bool(state & 4), bool(state & 1), bool(state & (8 | 0x20000))
    if alt or keysym in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R'):
        return None
    name = keysym.upper() if len(keysym) == 1 else {'Page_Down': 'Next', 'Page_Up': 'Prior'}.get(keysym, keysym)
    if control:
        return 'Ctrl+' + ('Shift+' if shift else '') + name
    return None if shift else name


def screen_key(mode, keysym, state, context, email_field=None, typed_text=''):
    """The SCREEN_KEYS key for this event, or None to leave the key alone."""
    key = chord(keysym, state)
    if key is None or key not in SCREEN_KEYS.get(mode, {}):
        return None
    # A Yes or No question owns the keyboard until it is answered.
    if context.get('confirmation') is not None or context.get('named_list'):
        return None
    # Private and naming boxes keep their typed text until Enter or Escape.
    if any(context.get(name) for name in ('web_private', 'media_input', 'browser_name_input', 'file_name_input', 'task_input')):
        return None
    if mode == 'mailbox' and key in ('Next', 'Prior') and (context.get('source') or typed_text):
        return None  # Page keys scroll an open message; they change pages only in the list.
    if mode == 'email_draft' and key in BODY_ONLY and email_field:
        return None
    return key


def screen_command(key, mode):
    """The spoken command for a SCREEN_KEYS key. A command that needs a value opens a prompt."""
    command = SCREEN_KEYS.get(mode, {}).get(key)
    if command and '{value}' in command:
        return '@prompt:' + command
    return command


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
    for mode in ('word_processor','media_menu','local_media'):
        for name,key,voice in nav.items(mode):add(mode.replace('_',' ').title(),name,name if voice.startswith('@') else voice,key or 'First letter, arrows, Enter')
    for category in CATEGORIES:
        for field in fields(category,{}):add(category,field.label,'Choose '+field.label,'Arrows to field; Space, text or arrows; Enter to save')
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
