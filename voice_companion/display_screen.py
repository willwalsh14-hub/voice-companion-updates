"""What the window shows for low-vision users, from the app's state.

companion.sync_app_context adds these keys to the context it sends to the window:
- display_title: the screen name, such as "Email: Inbox" or "Calendar: Today, Monday, October 26".
- display_items, display_index, display_count, display_offset: the list or menu on
  screen (at most 200 lines around the selected one) and the selected line.
- display_text, display_caret: text to show when the editor is not in use (a note).
- display: the saved display options (display_options.settings).

`m` is the companion module. Nothing here speaks or changes what is selected.
"""
from pathlib import Path

import display_options as options

LIMIT = 200


def _safe(function, default=''):
    try:
        value = function()
    except Exception:
        return default
    return default if value is None else value


def title(m, mode, value):
    """The screen name and, after a colon, where you are on it."""
    if value.get('named_list') and getattr(m, 'NAMED_LIST', None):
        return ' '.join(str(m.NAMED_LIST.get('title') or 'List').split()).rstrip('.:') or 'List'
    if value.get('named_list'):
        return 'Choose an email account'
    menu = getattr(m, 'SCREEN_MENU', None)
    if menu is not None and _safe(lambda: menu.is_open(mode), False):
        from screen_menus import MENUS
        name, groups = MENUS[mode]
        if len(groups) > 1 and menu.group is not None:
            return name + ': ' + groups[menu.group][0]
        return name
    detail = ''
    if mode == 'files':
        manager = getattr(m, 'FILE_MANAGER', None)
        if manager is not None:
            from file_manager import location_name
            detail = 'menu' if manager.menu else _safe(lambda: location_name(manager.location))
    elif mode == 'document':
        path = _safe(lambda: m.document.path, None)
        detail = Path(str(path)).name if path else ''
    elif mode == 'document_menu':
        group = _safe(lambda: m.task_navigation().group, None)
        if group:
            return str(group) + ' menu'
    elif mode == 'file_reader':
        detail = _safe(lambda: m.FILE_READER.title)
    elif mode == 'local_media':
        detail = _safe(lambda: m.task_navigation().media_title)
    elif mode == 'mailbox':
        mail = getattr(m, 'mail_session', None)
        if mail is not None:
            detail = _safe(lambda: mail.folder_name)
            if mail.view == 'message' and mail.current and mail.rows:
                detail += ', message ' + str(mail.current) + ' of ' + str(len(mail.rows))
    elif mode == 'email_draft':
        detail = _safe(lambda: m.email_draft.subject)
    elif mode == 'calendar':
        import calendar_screen
        if calendar_screen.DAY is not None:
            from spoken_dates import spoken_day
            detail = _safe(lambda: spoken_day(calendar_screen.DAY, calendar_screen.today(m)))
    elif mode == 'contacts':
        import contacts_screen
        if contacts_screen.VIEW == 'details':
            detail = 'details'
    elif mode == 'web':
        detail = _safe(lambda: m.web_session.snapshot['title'])
    elif mode == 'youtube':
        detail = _safe(lambda: __import__('youtube_screen').title_detail(m))
    elif mode == 'help':
        session = getattr(m, 'help_session', None)
        if session is not None and session.level != 'topics':
            detail = _safe(lambda: session.topics[session.topic_index]['name'])
    return options.screen_name(mode, detail)


def _confirmation(m, mode, value):
    choice = value.get('confirmation')
    choices = ('yes', 'no')
    if mode in ('calendar', 'contacts'):
        # Choosing which calendars or address books to show: their names.
        screen = __import__(mode + '_screen')
        names = _safe(screen.choice_names, None)
        if names and choice in screen.CHOICES:
            return list(names), list(screen.CHOICES).index(choice)
    if mode == 'calendar':
        import calendar_screen
        choices = tuple(calendar_screen.CHOICES)
    if mode == 'draft_leave':
        import draft_leave
        if choice in draft_leave.CHOICES:
            return [draft_leave.NAMES[c] for c in draft_leave.CHOICES], draft_leave.CHOICES.index(choice)
    if choice not in choices:
        return None
    names = {'this one': 'This one only', 'all': 'All of them'}
    return [names.get(c, str(c).capitalize()) for c in choices], choices.index(choice)


def _menu_lines(m, mode, value):
    """(lines, selected index) for the list or menu on screen, or None."""
    menu = getattr(m, 'SCREEN_MENU', None)
    if menu is not None and _safe(lambda: menu.is_open(mode), False):
        from screen_menus import MENUS, menu_path
        groups = MENUS[mode][1]
        group = 0 if len(groups) == 1 else menu.group
        lines = [options.item_line(label, key or (menu_path(mode, group, i) if command is not None else ''))
                 for i, (label, key, command) in enumerate(menu.entries())]
        return lines, menu.index
    if value.get('confirmation') is not None:
        return _confirmation(m, mode, value)
    if mode == 'files':
        return None  # The File manager has its own list in the window (file_items).
    if mode == 'mailbox':
        mail = getattr(m, 'mail_session', None)
        if mail is not None and not mail.folder_picker and mail.view == 'folder':
            if not mail.rows:
                return None
            from mail_voice import sender_name
            lines = []
            for number, row in enumerate(mail.rows, 1):
                parts = [sender_name(row), row.get('subject') or 'No subject', row.get('date') or '']
                line = '. '.join(' '.join(str(p).split()) for p in parts if str(p).strip())
                lines.append(('Marked. ' if number in mail.selected else '') + line)
            return lines, (mail.current - 1) if mail.current else None
    if mode == 'calendar':
        import calendar_screen
        if calendar_screen.EVENTS:
            return [e.line(calendar_screen.DAY) for e in calendar_screen.EVENTS], calendar_screen.INDEX
        if calendar_screen.DAY is not None:
            return ['No events.'], None
        return None
    if mode == 'contacts':
        import contacts_screen
        if contacts_screen.VIEW == 'details' and contacts_screen.DETAILS:
            return [label for label, _ in contacts_screen.DETAILS], contacts_screen.DETAIL_INDEX
    state = m.menu_state(mode)
    if state is None:
        return None
    names, index = list(state[0]), state[1]
    keys = _safe(lambda: m.task_navigation().items(mode), None)
    if keys and len(keys) == len(names) and [k[0] for k in keys] == names:
        names = [options.item_line(name, key) for name, key, _ in keys]
    return names, index


# The window's hint line under the typing box (Will, October 2026): short, because large
# text wraps it. Asleep: how to wake up. Microphone off: use the keys. Otherwise the screen,
# F1 for help where F1 opens help, and the main keys of that screen.
HINT_KEYS = {
    'awake': 'Arrows move. Enter opens.',
    'document': 'Alt opens the menu. Control+S saves.',
    'email_draft': 'Tab moves between boxes. Alt+S sends.',
    'mailbox': 'Enter opens. R replies. N writes new.',
    'note': 'Control+S saves.',
    'files': 'Arrows move. Enter opens. Alt opens the menu.',
    'web': 'Escape opens the browser menu.',
    'help': 'Arrows move. Escape goes back.',
    'file_reader': 'Arrows read. Escape goes back.',
    'local_media': 'Space plays or pauses. Alt opens the menu.',
    'tutorial': 'F1 lists commands.',
    'youtube': 'Enter plays. Space pauses. Alt opens the menu.',
}
QUESTION_HINT = 'Arrows choose. Enter answers.'


def has_help_key(mode):
    from input_parity import SCREEN_KEYS
    # In the tutorial F1 lists the lesson's commands; HINT_KEYS says that instead.
    return mode != 'tutorial' and ('F1' in SCREEN_KEYS.get(mode, {}) or mode in ('document', 'files'))


def hint(m, mode, value):
    """The hint line for this screen. `mode` is the app's mode ('sleep' while asleep)."""
    microphone = _safe(m.microphone_state, 'on')
    if mode == 'sleep':
        if microphone == 'on':
            return 'Asleep. Say Wake up, or press Control+F12.'
        return 'Microphone off. Press Control+F12 to wake up.'
    logical = value.get('mode')
    name = options.SCREEN_NAMES.get(logical) or options.screen_name(logical)
    if microphone != 'on':
        lead = 'Microphone off. Use the keys.' if microphone == 'off' else 'Microphone not working. Use the keys.'
        return lead + (' F1 for help.' if has_help_key(logical) else '')
    if value.get('confirmation') is not None:
        return name + '. ' + QUESTION_HINT
    keys = HINT_KEYS.get(logical)
    if logical == 'mailbox' and value.get('source'):
        keys = 'Alt+1 to Alt+6 read the header. R replies.'   # a message is open
    if keys is None and _safe(lambda: m.menu_state(logical), None) is not None:
        keys = 'Arrows move. Enter chooses.'
    return ' '.join(part for part in (name + '.', 'F1 for help.' if has_help_key(logical) else '', keys or '') if part)


def context(m, mode, value):
    """The display keys for one app context. `mode` is the app's mode ('sleep' while asleep)."""
    logical = value.get('mode')
    result = {'display': options.settings(getattr(m, 'PREFERENCES', {}))}
    name = title(m, logical, value)
    if mode == 'sleep':
        name += ', microphone asleep'
    result['display_title'] = name
    result['display_hint'] = hint(m, mode, value)
    lines = _menu_lines(m, logical, value)
    if lines is not None:
        items, index = lines
        if index is not None and not 0 <= index < len(items):
            index = None
        shown, shown_index, offset = options.visible_window(items, index, LIMIT)
        result.update(display_items=[' '.join(str(item).split()) for item in shown], display_index=shown_index,
                      display_count=len(items), display_offset=offset)
    if logical == 'note':
        text = ' '.join(getattr(m, 'note_buffer', []) or [])
        result.update(display_text=text, display_caret=len(text))
    return result
