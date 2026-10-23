"""App mode and slider keys for the web screen.

Browse mode is Companion's page cursor: letters move by heading, link and so on.
App mode sends each key straight to the web page, so a site's own shortcuts
work (YouTube's K, J, L, F and M, for example). Insert+Space switches modes, as
in common screen readers. Screen readers take Insert+Space for themselves, so
Control+Alt+Space does the same job when one is running.

A slider is a control such as a video's seek bar or a volume level. Enter on a
slider, or Tab onto it, lets the arrow keys change it on the page.
"""
import re

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


def toggle_chord(keysym, state, insert_held=False):
    """True for Insert+Space or Control+Alt+Space, the keys that switch modes."""
    if keysym != 'space':
        return False
    control, shift, alt = _modifiers(state)
    if insert_held and not (control or alt):
        return True
    return control and alt and not shift


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
    if keysym in MODIFIER_KEYS:
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
