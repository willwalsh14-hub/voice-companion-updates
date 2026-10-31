"""Display options for low-vision users: colors, text size, font and what the screen shows.

Nothing here touches Tk. The window (app_window.py) asks this module for colors,
font sizes and the words to show, so the rules can be tested on any computer.

What the window shows comes from the app context (companion.sync_app_context):
- 'display_title': the screen name, such as "Email: Inbox".
- 'display_items' and 'display_index': the list or menu on screen and the selected item.
- 'display_text' and 'display_caret': text to show when the editor is not in use, such as a note.
- 'display': the saved display options (see DEFAULTS).
"""
import re

# The order of the color schemes is the order "change colors" moves through.
SCHEMES = ('yellow on black', 'white on black', 'black on white', 'black on yellow', 'Windows colors')

# fg: text. bg: background. line: the line with the caret or reading place, or None.
# reverse: the "automatic" highlight for the selected item.
SCHEME_COLORS = {
    'yellow on black': {'fg': '#FFFF00', 'bg': '#000000', 'line': '#002B5C', 'reverse': ('#000000', '#FFFF00')},
    'white on black': {'fg': '#FFFFFF', 'bg': '#000000', 'line': '#002B5C', 'reverse': ('#000000', '#FFFFFF')},
    'black on white': {'fg': '#000000', 'bg': '#FFFFFF', 'line': '#D6E9FF', 'reverse': ('#FFFFFF', '#000000')},
    'black on yellow': {'fg': '#000000', 'bg': '#FFFF00', 'line': '#FFD24D', 'reverse': ('#FFFF00', '#000000')},
    # Windows names: they follow the Windows theme and Windows high contrast themes.
    'Windows colors': {'fg': 'SystemWindowText', 'bg': 'SystemWindow', 'line': None,
                       'reverse': ('SystemHighlightText', 'SystemHighlight')},
}

# Highlight for the selected item, the selection and the caret.
HIGHLIGHTS = ('automatic', 'yellow', 'cyan', 'green', 'pink', 'orange', 'white', 'black')
HIGHLIGHT_COLORS = {'yellow': '#FFFF00', 'cyan': '#00FFFF', 'green': '#00FF00', 'pink': '#FF66FF',
                    'orange': '#FF9900', 'white': '#FFFFFF', 'black': '#000000'}

SIZES = (100, 125, 150, 175, 200, 250, 300, 350, 400)
SIZE_CHOICES = tuple(str(size) + ' percent' for size in SIZES)

# Clear fonts. APHont and Atkinson Hyperlegible are made for low vision; they
# are listed only when installed. Verdana comes with Windows.
FONTS = ('APHont', 'Arial', 'Atkinson Hyperlegible', 'Atkinson Hyperlegible Next', 'Segoe UI', 'Tahoma', 'Verdana')
ALWAYS_INSTALLED = ('Arial', 'Segoe UI', 'Tahoma', 'Verdana')
FALLBACK_FONT = 'Verdana'

SPACING = ('single', 'one and a half', 'double')

# Large, high-contrast text by default: the people who use Companion are blind or
# have low vision. Yellow on black is the usual high-contrast choice for low vision.
DEFAULTS = {'display_colors': 'yellow on black', 'display_text_size': '200 percent', 'display_font': 'Verdana',
            'display_bold': False, 'display_highlight': 'automatic', 'display_spacing': 'one and a half',
            'display_full_screen': True}
DEFAULT_SIZE = 200
SIZE_RANGE = ('Text size can be 100 to 400 percent: 100, 125, 150, 175, 200, 250, 300, 350 or 400. '
              'Nothing changed. Say text size, then one of these.')

# Base sizes in points at 100 percent.
BASE_SIZES = {'title': 20, 'message': 13, 'body': 12, 'hint': 11, 'heading': 16}


def settings(preferences):
    """The display options from saved preferences, with defaults for anything missing or damaged."""
    preferences = preferences if isinstance(preferences, dict) else {}
    values = {key: preferences.get(key, value) for key, value in DEFAULTS.items()}
    if values['display_colors'] not in SCHEMES:
        values['display_colors'] = DEFAULTS['display_colors']
    if size_value(values['display_text_size']) is None:
        values['display_text_size'] = DEFAULTS['display_text_size']
    if values['display_highlight'] not in HIGHLIGHTS:
        values['display_highlight'] = DEFAULTS['display_highlight']
    if values['display_spacing'] not in SPACING:
        values['display_spacing'] = DEFAULTS['display_spacing']
    if not isinstance(values['display_font'], str) or not values['display_font'].strip():
        values['display_font'] = DEFAULTS['display_font']
    for key in ('display_bold', 'display_full_screen'):
        values[key] = bool(values[key])
    return values


def size_value(value):
    """200 from '200 percent', '200%', 200 or 'two hundred percent'; None when it is not a listed size."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    else:
        text = re.sub(r'[^0-9a-z ]+', ' ', str(value).casefold().replace('%', ' percent'))
        text = re.sub(r'\s+', ' ', text).strip()
        text = re.sub(r'\s*percent$', '', text).strip()
        words = {'one hundred': 100, 'a hundred': 100, 'hundred': 100, 'one hundred twenty five': 125,
                 'one hundred and twenty five': 125, 'one fifty': 150, 'one hundred fifty': 150,
                 'one hundred and fifty': 150, 'one seventy five': 175, 'one hundred seventy five': 175,
                 'one hundred and seventy five': 175, 'two hundred': 200, 'two fifty': 250,
                 'two hundred fifty': 250, 'two hundred and fifty': 250, 'three hundred': 300,
                 'three fifty': 350, 'three hundred fifty': 350, 'three hundred and fifty': 350,
                 'four hundred': 400, 'normal': DEFAULT_SIZE}
        if text in words:
            number = words[text]
        elif text.isdigit():
            number = int(text)
        else:
            return None
    return number if number in SIZES else None


def size_choice(value):
    """The stored choice ('200 percent') for a spoken or typed size, or None."""
    number = size_value(value)
    return None if number is None else str(number) + ' percent'


def percent(values):
    return size_value(values.get('display_text_size')) or DEFAULT_SIZE


def step_size(current, direction):
    """The next text size up (direction 1) or down (-1), or the same size at either end."""
    current = size_value(current) or DEFAULT_SIZE
    index = SIZES.index(current)
    return SIZES[max(0, min(len(SIZES) - 1, index + direction))]


def scaled(points, values):
    """A font size in points for this text size. Points follow Windows display scaling."""
    return max(1, round(points * percent(values) / 100))


def font_family(values, installed=None):
    """The chosen font, or Verdana when it is not installed on this computer."""
    family = str(values.get('display_font') or FALLBACK_FONT).strip()
    if installed is None:
        return family
    found = {name.casefold(): name for name in installed}
    if family.casefold() in found:
        return found[family.casefold()]
    return found.get(FALLBACK_FONT.casefold(), FALLBACK_FONT if not found else next(iter(sorted(found.values()))))


def font_choices(installed=None, saved=None):
    """Fonts to offer: the clear fonts that are installed, plus the saved one."""
    if installed is None:
        names = list(FONTS)
    else:
        have = {name.casefold() for name in installed}
        names = [name for name in FONTS if name in ALWAYS_INSTALLED or name.casefold() in have]
    if saved and str(saved).strip() and str(saved).strip().casefold() not in (n.casefold() for n in names):
        names.append(str(saved).strip())
    return tuple(sorted(names, key=str.casefold))


def font(values, role='body', installed=None, weight=None, slant='roman'):
    """A Tk font tuple for a part of the window: (family, size, weight, slant)."""
    size = scaled(BASE_SIZES.get(role, BASE_SIZES['body']), values)
    if weight is None:
        weight = 'bold' if role in ('title', 'heading') or values.get('display_bold') else 'normal'
    return (font_family(values, installed), size, weight, slant)


def line_gap(values, line_height):
    """Extra pixels above and below each line, for the chosen line spacing."""
    extra = {'single': 0.1, 'one and a half': 0.5, 'double': 1.0}.get(values.get('display_spacing'), 0.5)
    total = max(1, round(line_height * extra))
    return total // 2, total - total // 2


def _luminance(color):
    color = color.lstrip('#')
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(first, second):
    """WCAG contrast ratio of two #RRGGBB colors."""
    bright, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (bright + .05) / (dark + .05)


def _is_hex(color):
    return isinstance(color, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', color) is not None


def colors(values):
    """Every color the window needs, for the saved scheme and highlight.

    fg/bg: text and background. select_fg/select_bg: the selected item and
    selected text. caret: the insertion caret. line: the caret or reading line
    background, or None to leave it unmarked. focus: the outline of the control
    with keyboard focus."""
    scheme = SCHEME_COLORS.get(values.get('display_colors'), SCHEME_COLORS[DEFAULTS['display_colors']])
    fg, bg = scheme['fg'], scheme['bg']
    select_fg, select_bg = scheme['reverse']
    wanted = HIGHLIGHT_COLORS.get(values.get('display_highlight'))
    # A highlight too close to the background would hide the selection; then the
    # automatic, reversed colors are used instead.
    if wanted and (not _is_hex(bg) or contrast(wanted, bg) >= 3):
        select_bg = wanted
        select_fg = '#000000' if contrast(wanted, '#000000') >= contrast(wanted, '#FFFFFF') else '#FFFFFF'
    caret = select_bg if not _is_hex(bg) or not _is_hex(select_bg) or contrast(select_bg, bg) >= 3 else fg
    return {'fg': fg, 'bg': bg, 'select_fg': select_fg, 'select_bg': select_bg, 'caret': caret,
            'line': scheme['line'], 'focus': select_bg if caret == select_bg else fg}


def scheme_name(values):
    name = values.get('display_colors')
    return name if name in SCHEMES else DEFAULTS['display_colors']


def spoken_scheme(name):
    """What is said when the color scheme changes: "Yellow on black."."""
    return name[0].upper() + name[1:] + '.'


# ---- Voice commands -----------------------------------------------------------------

_BIGGER = ('bigger text', 'larger text', 'text bigger', 'text larger', 'make text bigger', 'make the text bigger',
           'make text larger', 'make the text larger', 'increase text size', 'increase the text size',
           'bigger text size', 'larger text size', 'zoom in', 'magnify', 'increase magnification', 'more magnification')
_SMALLER = ('smaller text', 'text smaller', 'make text smaller', 'make the text smaller', 'decrease text size',
            'decrease the text size', 'reduce text size', 'smaller text size', 'zoom out', 'decrease magnification',
            'less magnification')
_NORMAL = ('normal text size', 'normal text', 'normal size text', 'reset text size', 'reset the text size',
           'default text size', 'standard text size', 'reset zoom', 'normal zoom')
_CONTRAST = ('high contrast', 'high contrast colors', 'high contrast colours', 'use high contrast',
             'turn on high contrast', 'high contrast on')
_CYCLE = ('change colors', 'change colours', 'change the colors', 'change the colours', 'change color scheme',
          'change colour scheme', 'next color scheme', 'next colour scheme', 'color scheme', 'colour scheme',
          'switch colors', 'switch colours', 'cycle colors', 'cycle colours', 'other colors', 'other colours')
_FULL_TOGGLE = ('full screen', 'fullscreen', 'toggle full screen', 'maximize window', 'maximise window')
_FULL_ON = ('full screen on', 'turn on full screen', 'show full screen', 'maximize the window', 'maximise the window',
            'go full screen')
_FULL_OFF = ('full screen off', 'turn off full screen', 'exit full screen', 'leave full screen', 'restore window',
             'restore the window', 'window not full screen')
_STATUS = ('what text size', 'what is the text size', 'what s the text size', 'which text size', 'what colors',
           'what colours', 'which colors', 'which colours', 'what color scheme', 'what colour scheme',
           'which color scheme', 'display settings', 'what are the display settings')


def _plain(text):
    text = str(text).casefold().replace('’', "'").replace('%', ' percent')
    text = re.sub(r"[^0-9a-z ]+", ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'^(?:please |can you |could you |companion )+', '', re.sub(r' please$', '', text)).strip()


def voice_request(text):
    """The display action for a spoken or typed command, or None.

    Actions: ('size', +1 or -1), ('size_set', percent), ('size_reset', None),
    ('colors', None) to move to the next scheme, ('scheme', name), ('full_screen', True, False or None to switch),
    ('status', None)."""
    words = _plain(text)
    if not words:
        return None
    if words in _BIGGER:
        return ('size', 1)
    if words in _SMALLER:
        return ('size', -1)
    if words in _NORMAL:
        return ('size_reset', None)
    if words in _CONTRAST:
        return ('scheme', 'yellow on black')
    if words in _CYCLE:
        return ('colors', None)
    if words in _FULL_TOGGLE:
        return ('full_screen', None)
    if words in _FULL_ON:
        return ('full_screen', True)
    if words in _FULL_OFF:
        return ('full_screen', False)
    if words in _STATUS:
        return ('status', None)
    match = re.fullmatch(r'(?:set |change |make )?(?:the )?text size(?: to)? (.+)', words)
    if match and size_value(match[1]) is not None:
        return ('size_set', size_value(match[1]))
    if match and re.fullmatch(r'\d+(?: percent)?', match[1]):
        # A number that is not a size ("Text size 1000"): say the sizes (Will, October 31, 2026).
        return ('size_invalid', None)
    match = re.fullmatch(r'(?:use |set |change to |switch to )?(?:the )?(?:colors? |colours? |color scheme |colour scheme )?(?:to )?'
                         r'(yellow on black|white on black|black on white|black on yellow|windows colou?rs)'
                         r'(?: colou?rs| colou?r scheme)?', words)
    if match:
        found = match[1].replace('colours', 'colors')
        return ('scheme', 'Windows colors' if found == 'windows colors' else found)
    return None


KEY_REQUESTS = {'Display:Bigger': ('size', 1), 'Display:Smaller': ('size', -1), 'Display:Reset': ('size_reset', None),
                'Display:Colors': ('colors', None), 'Display:FullScreen': ('full_screen', None)}


def display_key(keysym, state):
    """The display request for a key press, or None.

    Control+Plus (or Control+Equals), Control+Minus and Control+0 change the text
    size, as in a web browser. Control+9 changes the color scheme. F11 switches full
    screen on or off. Shift is allowed only with Plus, which needs it on most keyboards."""
    from key_state import modifiers
    control, shift, alt = modifiers(state)
    if alt:
        return None
    if keysym == 'F11' and not control and not shift:
        return 'Display:FullScreen'
    if not control:
        return None
    if keysym in ('plus', 'equal', 'KP_Add'):
        return 'Display:Bigger'
    if shift:
        return None
    if keysym in ('minus', 'KP_Subtract'):
        return 'Display:Smaller'
    if keysym in ('0', 'KP_0'):
        return 'Display:Reset'
    if keysym == '9':
        return 'Display:Colors'
    return None


def apply_request(values, request):
    """Change the display options for one request.

    Returns (new values, the words to say). The values passed in are not changed."""
    values = settings(values)
    action, argument = request[0], request[1]
    if action == 'status':
        return values, status(values)
    if action == 'size_invalid':
        return values, SIZE_RANGE
    if action in ('size', 'size_set', 'size_reset'):
        old = percent(values)
        new = step_size(old, argument) if action == 'size' else argument if action == 'size_set' else DEFAULT_SIZE
        values['display_text_size'] = str(new) + ' percent'
        words = 'Text size ' + str(new) + ' percent.'
        if action == 'size' and new == old:
            words = ('Largest text size. ' if argument > 0 else 'Smallest text size. ') + words
        return values, words
    if action == 'colors':
        values['display_colors'] = SCHEMES[(SCHEMES.index(scheme_name(values)) + 1) % len(SCHEMES)]
        return values, spoken_scheme(values['display_colors'])
    if action == 'scheme':
        values['display_colors'] = argument
        return values, spoken_scheme(argument)
    if action == 'full_screen':
        values['display_full_screen'] = (not values['display_full_screen']) if argument is None else bool(argument)
        return values, 'Full screen on.' if values['display_full_screen'] else 'Full screen off.'
    raise ValueError('Unknown display request.')


def status(values):
    values = settings(values)
    return ('Text size ' + str(percent(values)) + ' percent. ' + spoken_scheme(scheme_name(values)) +
            ' Font ' + font_family(values) + '. Full screen ' + ('on.' if values['display_full_screen'] else 'off.'))


# ---- What the screen shows ----------------------------------------------------------

SCREEN_NAMES = {
    'awake': 'Main menu', 'files': 'File manager', 'document': 'Document', 'document_menu': 'Document menu',
    'word_processor': 'Word processor menu', 'recent_documents': 'Recent documents', 'reader_menu': 'Document reader menu',
    'reader_recent': 'Document reader: Recent documents', 'file_reader': 'Document reader', 'notepad_menu': 'Notepad menu',
    'note': 'Note', 'mailbox': 'Email', 'email_draft': 'Email draft', 'contacts': 'Contacts', 'calendar': 'Calendar',
    'downloads': 'Downloads', 'attachments': 'Attachments', 'web': 'Web page', 'browser_menu': 'Web browser menu',
    'search': 'Web search', 'regular_browser': 'Regular browser', 'help': 'Help', 'tutorial': 'Tutorial',
    'media_menu': 'Media menu', 'media': 'Radio', 'podcast': 'Podcasts', 'local_media': 'Media player', 'youtube': 'YouTube',
    'settings': 'Options', 'document_name': 'Name the document', 'document_save': 'Save the document', 'draft_leave': 'Leave the email', 'document_replace': 'Replace a file',
    'power_confirm': 'Shut down or restart', 'exit_confirm': 'Exit Voice Companion', 'update_offer': 'Update available',
    'update_download': 'Downloading update', 'chrome_setup': 'Chrome setup', 'list': 'List', 'task_input': 'Enter a value',
}


def screen_name(mode, detail=''):
    """"Email: Inbox" from the mode and a detail; the mode alone when there is no detail."""
    name = SCREEN_NAMES.get(mode) or str(mode or 'Voice Companion').replace('_', ' ').capitalize()
    detail = ' '.join(str(detail or '').split())
    return name + ': ' + detail if detail else name


def item_line(label, key=''):
    """A list line: the item, then its shortcut key, such as "Reply (R)"."""
    label = ' '.join(str(label).split())
    key = ' '.join(str(key or '').split())
    return label + (' (' + key + ')' if key else '')


def visible_window(items, index, size=200):
    """At most `size` items around the selected one, so a very long list stays quick to draw.

    Returns (items to show, the selected index within them, how many were skipped above)."""
    items = list(items)
    if len(items) <= size:
        return items, index, 0
    if index is None or not 0 <= index < len(items):
        return items[:size], index if index is not None and 0 <= index < size else None, 0
    start = max(0, min(len(items) - size, index - size // 2))
    return items[start:start + size], index - start, start


def fit_plan(height, heading, entry, label, message_line, message_lines, least, wanted):
    """How the window shares its height at large text sizes: (message lines, show the
    "Type a command" label, list height).

    The command box always stays: it has the keyboard on many screens, and Windows drops
    key presses sent to a widget with no room. The list keeps room for at least one line.
    The last message gives way first (it is also spoken): fewer lines, then hidden.
    Then the label above the command box."""
    base = heading + 14 + entry
    for lines, show_label in ((message_lines, True), (1, True), (0, True), (0, False)):
        lines = min(lines, message_lines)
        fixed = base + (message_line * lines + 8 if lines else 0) + (label + 6 if show_label else 0)
        if height - fixed >= least or (lines, show_label) == (0, False):
            return lines, show_label, max(1, min(wanted, height - fixed))
