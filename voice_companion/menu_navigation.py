"""Shared first-letter cycling and spoken menu key labels."""
def quick_key(name):
    return next((c.lower() for c in str(name).strip() if c.isalpha()), '')


def menu_label(name):
    key = quick_key(name)
    return str(name) + (' (' + key.upper() + ')' if key else '')


def next_match(names, current, letter):
    matches = [i for i, name in enumerate(names) if quick_key(name) == letter.lower()]
    if not matches:
        return None
    if current in matches:
        return matches[min(matches.index(current) + 1, len(matches)-1)]
    return matches[0]


# Key ids (as the window sends them) and the words the guide uses for them. Menus speak
# and show keys in the guide's words: "Control+O", "Up Arrow", "Page Down".
KEY_WORDS = {'Ctrl': 'Control', 'Esc': 'Escape', 'PgUp': 'Page Up', 'PgDn': 'Page Down', 'Prior': 'Page Up',
             'Next': 'Page Down', 'PageUp': 'Page Up', 'PageDown': 'Page Down', 'Del': 'Delete', 'Ins': 'Insert',
             'ShiftTab': 'Shift+Tab', 'BackSpace': 'Backspace', 'Return': 'Enter', 'Up': 'Up Arrow',
             'Down': 'Down Arrow', 'Left': 'Left Arrow', 'Right': 'Right Arrow', 'comma': 'Comma', 'period': 'Period'}


def spoken_key(key):
    """A key id in the guide's words: 'Ctrl+Shift+S' is 'Control+Shift+S', 'Up' is 'Up Arrow'.
    Only whole key names between + signs change; menu paths such as 'Alt, 3, 2' stay."""
    return '+'.join(KEY_WORDS.get(part, part) for part in str(key or '').split('+'))


def announce_item(name, index, count, hotkey=True):
    return f'{menu_label(name) if hotkey else name}, {index + 1} of {count}.'


def boundary_notice(index,count):
    if count<=0:return 'Empty list.'
    if count==1:return 'Start and end of list.'
    return 'Start of list.' if index<=0 else 'End of list.' if index>=count-1 else ''

def bounded_index(index,step,count):
    if count<=0:return None
    if index is None:return 0 if step>=0 else count-1
    return max(0,min(count-1,index+step))
