"""App mode and slider keys for the web screen.

Browse mode is Companion's page cursor: letters move by heading, link and so on.
App mode sends each key straight to the web page, so a site's own shortcuts
work (YouTube's K, J, L, F and M, for example). Insert+Space switches modes, as
in common screen readers. JAWS, NVDA and Narrator take Insert+Space for
themselves before Companion sees it (JAWS: layered commands; NVDA and
Narrator: their own browse and scan modes). Control+Shift+Space does the same
job and is used by none of them; Control+Alt+Space also still works. The first
web page in a session with one of them running says so once.

A slider is a control such as a video's seek bar or a volume level. Enter on a
slider, or Tab onto it, lets the arrow keys change it on the page.
"""
import os
import re
import time

APP_MODE = 'App mode.'
BROWSE_MODE = 'Browse mode.'

# Keys Companion keeps in app mode. Everything else goes to the page.
# F5 reloads, F6 and F7 list headings and links, Alt+Left and Alt+Right go
# back and forward, and Control+L starts a search, exactly as in browse mode.
# F12 says the time (twice, the date) and Control+F12 puts the microphone to sleep.
COMPANION_KEYS = {'F5', 'F6', 'F7', 'F12', 'Control+F12', 'Alt+Left', 'Alt+Right', 'Control+L', 'Control+l'}

# Tk key names for keys without a printed character, and the browser's names.
PAGE_KEY_NAMES = {
    'Up': 'ArrowUp', 'Down': 'ArrowDown', 'Left': 'ArrowLeft', 'Right': 'ArrowRight',
    'Prior': 'PageUp', 'Next': 'PageDown', 'Page_Up': 'PageUp', 'Page_Down': 'PageDown',
    'Home': 'Home', 'End': 'End', 'Return': 'Enter', 'KP_Enter': 'Enter', 'Escape': 'Escape',
    'Tab': 'Tab', 'ISO_Left_Tab': 'Tab', 'BackSpace': 'Backspace', 'Delete': 'Delete',
    'space': 'Space', 'Insert': 'Insert',
    **{'F' + str(n): 'F' + str(n) for n in range(1, 13)},
}
MODIFIER_KEYS = {'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Meta_L', 'Meta_R',
                 'Super_L', 'Super_R', 'Win_L', 'Win_R', 'Caps_Lock', 'Num_Lock', 'Scroll_Lock', 'App'}

# Keys that change a slider, and the browser's names for them.
SLIDER_KEYS = {'Up': 'ArrowUp', 'Down': 'ArrowDown', 'Left': 'ArrowLeft', 'Right': 'ArrowRight',
               'Prior': 'PageUp', 'Next': 'PageDown', 'Page_Up': 'PageUp', 'Page_Down': 'PageDown',
               'Home': 'Home', 'End': 'End'}


def _modifiers(state):
    return bool(state & 4), bool(state & 1), bool(state & (8 | 0x20000))


TOGGLE_KEY = 'Control+Shift+Space'


def toggle_chord(keysym, state, insert_held=False):
    """True for the keys that switch modes: Insert+Space, Control+Shift+Space
    and Control+Alt+Space."""
    if keysym != 'space':
        return False
    control, shift, alt = _modifiers(state)
    if insert_held and not (control or alt):
        return True
    if control and shift and not alt:
        return True
    return control and alt and not shift


# Screen readers that keep Insert+Space, by their program names.
SCREEN_READERS = (('jfw.exe', 'JAWS'), ('nvda.exe', 'NVDA'), ('narrator.exe', 'Narrator'))
NOTICE_STATE = {'given': False, 'checked_at': -1e9, 'readers': ()}
NOTICE_RECHECK = 30.0


def running_programs():
    """Lower-case names of the programs running now (Windows); empty elsewhere or on failure."""
    if os.name != 'nt':
        return set()
    try:
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                        ('szExeFile', ctypes.c_wchar * 260)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return set()
        names = set()
        try:
            entry = Entry(); entry.dwSize = ctypes.sizeof(Entry)
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                names.add(entry.szExeFile.lower())
                found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return names
    except Exception:
        return set()


def screen_readers(programs):
    names = {str(p).lower() for p in programs}
    return [reader for program, reader in SCREEN_READERS if program in names]


def screen_reader_notice(readers):
    if not readers:
        return ''
    names = readers[0] if len(readers) == 1 else ', '.join(readers[:-1]) + ' and ' + readers[-1]
    verb, own = ('is', 'it uses Insert+Space for itself') if len(readers) == 1 else ('are', 'they use Insert+Space for themselves')
    return (names + ' ' + verb + ' running, and ' + own + '. '
            'To switch between browse mode and app mode, press ' + TOGGLE_KEY + '.')


def screen_reader_notice_once(now=None):
    """The notice for the first web page of the session while a screen reader runs.

    Said once per session. Without a screen reader, the programs are looked at
    again only after NOTICE_RECHECK seconds, so pages open quickly."""
    if NOTICE_STATE['given']:
        return ''
    now = time.monotonic() if now is None else now
    if now - NOTICE_STATE['checked_at'] < NOTICE_RECHECK:
        return ''
    NOTICE_STATE['checked_at'] = now
    readers = screen_readers(running_programs())
    if not readers:
        return ''
    NOTICE_STATE['given'] = True
    return screen_reader_notice(readers)


def page_key(keysym, char, state):
    """The browser key name for a Tk key event, or None for a key with no page meaning."""
    if keysym in MODIFIER_KEYS:
        return None
    control, shift, alt = _modifiers(state)
    if keysym == 'ISO_Left_Tab':
        shift = True
    if not control and not alt and char and len(char) == 1 and char.isprintable() and keysym != 'space':
        return char  # 'K' is Shift+K; the character already carries Shift.
    if keysym in PAGE_KEY_NAMES:
        base = PAGE_KEY_NAMES[keysym]
    elif len(keysym) == 1 and keysym.isalpha():
        base = 'Key' + keysym.upper()
    elif len(keysym) == 1 and keysym.isdigit():
        base = 'Digit' + keysym
    elif char and len(char) == 1 and char.isprintable():
        base = char
    else:
        return None
    return '+'.join([name for name, held in (('Control', control), ('Alt', alt), ('Shift', shift)) if held] + [base])


def window_request(keysym, char, state, context):
    """What the Companion window sends for a key in app mode or on a slider.

    None leaves the key to Companion's usual handling. An empty string means
    the key does nothing here. Otherwise the result is a keyboard request."""
    control, shift, alt = _modifiers(state)
    if context.get('web_slider') and not context.get('web_app_mode'):
        if keysym in SLIDER_KEYS and not alt:
            return 'Web:Slider:' + keysym.replace('Page_Up', 'Prior').replace('Page_Down', 'Next')
        if keysym in ('Tab', 'ISO_Left_Tab'):
            return 'Web:ShiftTab' if shift or keysym == 'ISO_Left_Tab' else 'Web:Tab'
        if keysym == 'Escape':
            return 'Web:Escape'
        if keysym in ('Return', 'space') and not control and not alt:
            return 'Web:Activate'
        if char and char.isprintable() and not control and not alt:
            return ''
        return None
    if not context.get('web_app_mode'):
        return None
    if keysym in MODIFIER_KEYS or toggle_chord(keysym, state):
        return None
    name = ('Alt+' if alt and not control else 'Control+' if control and not alt else '') + keysym
    if name in COMPANION_KEYS:
        return None
    key = page_key(keysym, char, state)
    return 'Web:Pass:' + key if key else ''


def mode_request(command):
    """'app', 'browse' or 'toggle' for a spoken mode change, else None.

    Speech adds commas, periods and capitals, so those are ignored."""
    words = ' '.join(re.sub(r"[^\w\s']", ' ', str(command).lower()).split())
    words = re.sub(r'^please | please$', '', words)
    words = re.sub(r'\bapplication\b', 'app', words)
    words = re.sub(r'\bvirtual cursor\b', 'cursor', words)
    if re.fullmatch(r'(?:toggle|switch) (?:app|browse|focus) mode|toggle (?:the )?cursor', words):
        return 'toggle'
    if re.fullmatch(r'(?:(?:turn on|switch to|go to|use|enter|start) )?(?:app|focus) mode(?: on)?', words):
        return 'app'
    if re.fullmatch(r'(?:app|focus) mode off|(?:turn off|leave|exit|stop) (?:app|focus) mode', words):
        return 'browse'
    if re.fullmatch(r'(?:(?:turn on|switch to|go to|use|enter|start|back to) )?browse mode(?: on)?', words):
        return 'browse'
    if re.fullmatch(r'browse mode off|(?:turn off|leave|exit|stop) browse mode', words):
        return 'app'
    if re.fullmatch(r'(?:turn )?(?:the )?cursor (on|off)|turn (on|off) (?:the )?cursor', words):
        state = re.search(r'\b(on|off)\b', words)[1]
        return 'browse' if state == 'on' else 'app'
    return None


def slider_value(item):
    """What a slider says about its value: the site's words, else the number."""
    text = str(item.get('value_text') or '').strip()
    if text:
        return text
    now = item.get('value_now')
    return str(now) if now not in (None, '') else 'value not provided'


def _focus_item(focus):
    """A focused element read from the page, in the shape web_browse.describe reads."""
    item = {k: focus.get(k) for k in ('label', 'role', 'tag', 'type', 'value_text', 'value_now', 'checked')}
    expanded = focus.get('expanded')
    if expanded in ('true', 'false'):
        item['expanded'] = expanded == 'true'
    if focus.get('selected'):
        item['selected_label'] = focus['selected']
    if focus.get('editable') and not focus.get('password'):
        item['value'] = focus.get('value', '')
        item['editable'] = True
    return item


def describe_focus(focus):
    from web_browse import describe, kind
    item = _focus_item(focus)
    notice = describe(item)
    if kind(item) == 'edit' and focus.get('editable') and not focus.get('password') and str(focus.get('value') or '').strip():
        notice = notice[:-1] + ', ' + ' '.join(str(focus['value']).split())[:180] + '.'
    return notice


def _line_at(text, caret):
    start = text.rfind('\n', 0, caret) + 1
    end = text.find('\n', caret)
    return text[start:len(text) if end < 0 else end]


def page_key_feedback(key, before, after):
    """Speech after a key went to the page: new focus, a changed value, and page announcements."""
    from keyboard_text import character
    parts = []
    old, new = (before or {}).get('focus'), (after or {}).get('focus')
    if new and (not old or new.get('id') != old.get('id')):
        parts.append(describe_focus(new))
    elif new:
        role = new.get('role') or ''
        value = lambda f: (f.get('value_text'), f.get('value_now'))
        if role in ('slider', 'spinbutton', 'scrollbar', 'progressbar', 'meter') and value(new) != value(old):
            parts.append(slider_value(new) + '.')
        elif new.get('editable') and not new.get('password'):
            text, caret = str(new.get('value') or ''), new.get('caret')
            was = str(old.get('value') or '')
            if text != was:
                if len(key) == 1:
                    parts.append(character(key))
                elif len(text) < len(was):
                    removed = was[len(text):] if was.startswith(text) else ''
                    if not removed:
                        # Text after the caret stayed; what went is just before it.
                        removed = was[(caret or 0):(caret or 0) + len(was) - len(text)]
                    parts.append(character(removed[-1:]) if removed else 'Deleted.')
            elif isinstance(caret, int) and key in ('ArrowLeft', 'ArrowRight'):
                parts.append(character(text[caret]) if caret < len(text) else 'Blank.')
            elif isinstance(caret, int) and key in ('ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'):
                parts.append(_line_at(text, caret).strip() or 'Blank.')
        else:
            if new.get('checked') != old.get('checked') and new.get('checked') is not None:
                parts.append('Checked.' if new.get('checked') else 'Not checked.')
            if new.get('expanded') != old.get('expanded') and new.get('expanded') in ('true', 'false'):
                parts.append('Expanded.' if new['expanded'] == 'true' else 'Collapsed.')
            if new.get('selected') != old.get('selected') and new.get('selected'):
                parts.append(new['selected'] + '.')
    for text in dict.fromkeys((after or {}).get('live') or []):
        parts.append(text if text.endswith(('.', '!', '?')) else text + '.')
    return ' '.join(p for p in parts if p)


def page_change_feedback(before, after):
    """Speech for changes the page made by itself between keys in app mode.

    A site may answer a key a moment later, or move focus on its own. Said:
    new focus, a changed slider value, checked or expanded state, and page
    announcements. Text typed into a box is echoed when typed, not here."""
    if not after:
        return ''
    parts = []
    old, new = (before or {}).get('focus'), after.get('focus')
    if before is not None and new and (not old or new.get('id') != old.get('id')):
        parts.append(describe_focus(new))
    elif before is not None and new and old:
        role = new.get('role') or ''
        value = lambda f: (f.get('value_text'), f.get('value_now'))
        if role in ('slider', 'spinbutton', 'scrollbar', 'progressbar', 'meter') and value(new) != value(old):
            parts.append(slider_value(new) + '.')
        if new.get('checked') != old.get('checked') and new.get('checked') is not None:
            parts.append('Checked.' if new.get('checked') else 'Not checked.')
        if new.get('expanded') != old.get('expanded') and new.get('expanded') in ('true', 'false'):
            parts.append('Expanded.' if new['expanded'] == 'true' else 'Collapsed.')
    for text in dict.fromkeys(after.get('live') or []):
        parts.append(text if text.endswith(('.', '!', '?')) else text + '.')
    return ' '.join(p for p in parts if p)
