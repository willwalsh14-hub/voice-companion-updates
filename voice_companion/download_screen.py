"""The Downloads screen: one list for every browser download, newest first.

Control+D opens it from a web page or the Web browser menu. Up and Down read
each download; Space pauses or resumes; Delete cancels (after a Yes or No
question) or takes a finished download off the list; Control+Up and
Control+Down move a queued download in line; Enter opens a finished file in
Voice Companion where it can, otherwise in its Windows app. Escape goes back
to the page or menu you came from, where you were.

companion.py calls into this module from a few small hooks; the model itself
is downloads.py. `m` is the companion module.
"""
import os
import queue
import time
from pathlib import Path

import downloads
from menu_navigation import announce_item, next_match

MODE = 'downloads'
HELP = ('Downloads. Up and Down Arrow move through your downloads, newest first. Home and End reach the ends of the list. '
        'Space pauses or resumes a download. Delete cancels a download after you answer Yes. '
        'On a finished download, Delete takes it off the list and keeps the file. '
        'Control+Up Arrow and Control+Down Arrow move a queued download earlier or later in line. Say "Move to next" to make it start next. '
        'Enter opens a finished file. Say "Download details" to hear its size and folder. Say "Open downloads folder" to see the folder in File manager. '
        'Alt opens the downloads menu. Escape goes back to where you were.')
# Phrases that open the list. "Downloads" alone is a menu choice on the Web browser
# menu and the main menu; on a web page it could be the name of a link.
OPEN_ANYWHERE = ('download manager', 'open download manager', 'open the download manager', 'show download manager',
                 'show downloads', 'show my downloads', 'web downloads', 'browser downloads')
OPEN_ON_MENUS = ('downloads', 'open downloads', 'my downloads')
NOT_HERE = ('media', 'podcast', 'settings', 'task_input', 'document_name', 'search', 'chrome_setup')
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'enter')
NO = ('no', 'no thanks', 'escape', 'go back', 'back', 'do not cancel')
# Words that answer only one of the two questions. "Cancel it" means Yes to
# "Cancel this download?" but No to "Run this program?".
ANSWERS = {'cancel': (('yes cancel it', 'cancel it', 'cancel download'), ('keep it', 'keep downloading', 'do not cancel it')),
           'run': (('run it', 'yes run it', 'open it'), ('cancel it', 'do not run it', 'cancel'))}
PASS_THROUGH = ('help', 'options', 'open options', 'main menu', 'back to main menu', 'exit companion', 'go to sleep',
                'wake up', 'user guide', 'open user guide', 'manual', 'open manual', 'release notes', 'what mode',
                'what version', 'toggle audio')

RETURN_MODE = 'web'
INDEX = 0
SELECTED = None     # the id of the download you are on, so new arrivals at the top do not move you
PENDING = None      # ('cancel', record) or ('run', record): a Yes or No question
OPENED = None       # the file Enter opened in the document reader
CHOICE = 'no'
LAST_REVISION = None
LAST_FOLDER_CHECK = 0.0


def manager(m):
    """The shared model, saving its history in the app's data folder."""
    result = downloads.manager(m.APP)
    result.folder_setting = lambda: m.PREFERENCES.get('download_folder', '')
    return result


def items(m):
    return manager(m).items


def follow(records):
    """Keep INDEX on the same download when the list above it changes."""
    global INDEX
    if not records:
        INDEX = 0
        return
    place = next((i for i, r in enumerate(records) if r.id == SELECTED), None)
    INDEX = place if place is not None else max(0, min(INDEX, len(records) - 1))


def select(records, index):
    global INDEX, SELECTED
    INDEX = index
    SELECTED = records[index].id if records else None


def current(m):
    records = items(m)
    follow(records)
    return records[INDEX] if records else None


def describe(m, index=None):
    records = items(m)
    follow(records)
    if not records:
        return 'No downloads yet. Files you download are saved in ' + manager(m).folder().name + '. Escape goes back.'
    index = INDEX if index is None else index
    return announce_item(manager(m).label(records[index]), index, len(records), hotkey=False)


# Hooks from companion.py ---------------------------------------------------------
def context(m, value):
    """app_context: a Yes or No question owns the keys while it is asked."""
    if PENDING is not None:
        value['confirmation'] = CHOICE
        value['menu_letters'] = False
    return value


def menu_state(m):
    """menu_state: the list for Home, End and first letters."""
    records = items(m)
    labels = manager(m).labels()
    if not labels or len(labels) != len(records):
        return None
    follow(records)
    return labels, INDEX, lambda index: select(records, index)


def navigation_key(m, key):
    """The command for a key on the Downloads screen, or None when the key is handled here."""
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
        if key == 'Enter':
            return CHOICE
        if key == 'Escape':
            return 'no'
        return None
    if key.startswith('Letter:'):
        return '@downloads:letter:' + key[7:]
    return {'Up': 'previous', 'Left': 'previous', 'Down': 'next', 'Right': 'next', 'Home': 'home', 'End': 'end',
            'Enter': 'open download', 'Escape': 'close downloads'}.get(key)


def open_manager(m, mode):
    """Show the list. Escape later returns to `mode`, where the user was."""
    global RETURN_MODE, PENDING
    if mode != MODE:
        RETURN_MODE = mode
    PENDING = None
    select(items(m), 0)
    m.speak('Downloads. ' + describe(m))
    return MODE


def back_to_list(m):
    """Return from a file opened from this list (reader or player)."""
    global PENDING
    PENDING = None
    m.speak('Downloads. ' + describe(m))
    return MODE


def reader_back(m):
    """Escape in the document reader: back to this list, but only for the file opened from it."""
    m.task_navigation().reader_return = 'files'
    reader = getattr(m, 'FILE_READER', None)
    path = getattr(reader, 'path', None)
    if OPENED is None or path is None or os.path.normcase(str(Path(path))) != os.path.normcase(str(OPENED)):
        return None
    return back_to_list(m)


def leave(m):
    """Escape: back to the page or menu the list was opened from."""
    global PENDING
    PENDING = None
    target = RETURN_MODE or 'awake'
    web = getattr(m, 'web_session', None)
    if target == 'web' and not (web is not None and web.snapshot):
        target = 'browser_menu' if web is not None else 'awake'
    if target == 'browser_menu' and web is None:
        target = 'awake'
    if target != 'awake':
        # handle() says Main menu itself when it returns there.
        m.speak(m.options_return_notice(target))
    return target


def poll(m, mode):
    """Main loop: say download news, keep embedded Edge's folder current, and
    close embedded Edge once its last download ends if the user had left the page."""
    global LAST_REVISION, LAST_FOLDER_CHECK
    model = downloads.current()
    if model is None:
        return mode
    model.folder_setting = lambda: m.PREFERENCES.get('download_folder', '')
    now = time.monotonic()
    if now - LAST_FOLDER_CHECK > 1:
        LAST_FOLDER_CHECK = now
        model.push_folder()
    while True:
        try:
            notice = model.notices.get_nowait()
        except queue.Empty:
            break
        m.speak(notice)
    logical = m.SLEEP_RETURN_MODE if mode == 'sleep' else mode
    web = getattr(m, 'web_session', None)
    if (web is not None and getattr(web, 'close_after_downloads', False) is True and not model.busy('edge')
            and logical not in ('web', MODE) and not web.snapshot):
        web.close_after_downloads = False
        try:
            web.backend.close()
        except Exception:
            pass
    if logical == MODE and model.revision != LAST_REVISION:
        LAST_REVISION = model.revision
        m.sync_app_context(mode)
    return mode


# Commands --------------------------------------------------------------------------
def handle(m, text, mode, typed=False):
    """Commands for the Downloads screen, and the phrases that open it. None: not ours."""
    raw = text.strip()
    command = m.normalized_command(raw)
    if mode != MODE:
        if mode in NOT_HERE or m.writing_dictation(mode, typed) or getattr(m, 'NAMED_LIST', None):
            return None
        web = getattr(m, 'web_session', None)
        if mode == 'browser_menu' and getattr(getattr(web, 'menu', None), 'level', 'menu') != 'menu':
            return None   # a favorite's name, or a name in a favorites list
        if command in OPEN_ANYWHERE or mode in ('awake', 'browser_menu') and command in OPEN_ON_MENUS:
            return open_manager(m, mode)
        return None
    try:
        return command_here(m, raw, command)
    except (ValueError, OSError) as exc:
        m.speak(str(exc))
        return MODE


def command_here(m, raw, command):
    global PENDING, CHOICE
    model = manager(m)
    if PENDING is not None:
        action, record = PENDING
        yes, no = ANSWERS[action]
        if command in YES + yes:
            PENDING = None
            if action == 'cancel':
                m.speak(model.cancel(record))
            else:
                m.speak(launch(record))
            return MODE
        if command in NO + no or command in ('cancel', 'close downloads'):
            PENDING = None
            m.speak(('Not cancelled. ' if action == 'cancel' else 'Not opened. ') + describe(m))
            return MODE
        if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
            return None
        m.speak(question(action, record))
        return MODE
    if command in PASS_THROUGH or command.startswith(('help with ', 'help for ')) or m.speech_control(raw) \
            or m.speech_setting_request(raw) is not None or m.spoken_control(command) or command in m.VOICE_LIST_COMMANDS:
        return None
    if command in ('close downloads', 'close download manager', 'leave downloads', 'exit downloads', 'escape', 'go back', 'back'):
        return leave(m)
    if command in OPEN_ANYWHERE + OPEN_ON_MENUS + ('where am i', 'list downloads'):
        m.speak('Downloads. ' + describe(m))
        return MODE
    records = model.items
    follow(records)
    moves = {'next': 1, 'next download': 1, 'down': 1, 'previous': -1, 'previous download': -1, 'up': -1}
    ends = {'home': 0, 'first download': 0, 'start of list': 0, 'top of list': 0, 'first item': 0,
            'end': -1, 'last download': -1, 'end of list': -1, 'bottom of list': -1, 'last item': -1}
    if command in moves or command in ends or raw.startswith('@downloads:letter:'):
        if not records:
            m.speak(describe(m))
            return MODE
        old = max(0, min(INDEX, len(records) - 1))
        if raw.startswith('@downloads:letter:'):
            found = next_match([model.display(r) for r in records], old, raw[-1])
            if found is None:
                m.speak('No download starts with ' + raw[-1].upper() + '.')
                return MODE
            select(records, found)
            m.speak(('End of matching items. ' if found == old else '') + describe(m))
            return MODE
        if command in ends:
            select(records, 0 if ends[command] == 0 else len(records) - 1)
            edge = 'Start of list. ' if INDEX == old == 0 and ends[command] == 0 else 'End of list. ' if INDEX == old and ends[command] else ''
        else:
            select(records, max(0, min(len(records) - 1, old + moves[command])))
            edge = ('Start of list. ' if moves[command] < 0 else 'End of list. ') if INDEX == old else ''
        m.speak(edge + describe(m))
        return MODE
    record = current(m)
    known = ('open download', 'enter', 'okay', 'ok', 'that one', 'open', 'open it', 'open file', 'download details', 'details',
             'status', 'pause or resume download', 'toggle download', 'pause download', 'resume download', 'continue download',
             'retry download', 'try again', 'cancel download', 'stop download', 'delete download', 'remove download',
             'remove from list', 'move to next', 'make next', 'move download to next', 'start next', 'move download up',
             'move download earlier', 'move up', 'move earlier', 'move download down', 'move download later', 'move down',
             'move later', 'open downloads folder', 'open download folder', 'show in folder', 'open folder')
    if command in known and command not in ('open downloads folder', 'open download folder', 'show in folder', 'open folder') and record is None:
        m.speak(describe(m))
        return MODE
    if command in ('open download', 'enter', 'okay', 'ok', 'that one', 'open', 'open it', 'open file'):
        return open_record(m, record)
    if command in ('download details', 'details', 'status'):
        m.speak(model.details(record))
        return MODE
    if command in ('pause or resume download', 'toggle download'):
        m.speak(model.toggle(record))
        return MODE
    if command == 'pause download':
        m.speak(model.toggle(record) if record.state in ('downloading', 'queued') else model.label(record) + '. It is not downloading.')
        return MODE
    if command in ('resume download', 'continue download', 'retry download', 'try again'):
        m.speak(model.toggle(record) if record.state in ('paused', 'failed') else model.label(record) + '.')
        return MODE
    if command in ('cancel download', 'stop download', 'delete download'):
        if record.state not in downloads.UNFINISHED:
            if command == 'delete download':
                m.speak(model.remove(record) + ' ' + describe(m))
            else:
                m.speak(model.label(record) + '. Nothing to cancel. Press Delete to take it off the list.')
            return MODE
        PENDING, CHOICE = ('cancel', record), 'no'
        m.speak(question('cancel', record))
        return MODE
    if command in ('remove download', 'remove from list'):
        if record.state in downloads.UNFINISHED:
            m.speak(model.label(record) + '. Cancel it first with Delete.')
            return MODE
        m.speak(model.remove(record) + ' ' + describe(m))
        return MODE
    if command in ('move to next', 'make next', 'move download to next', 'start next'):
        m.speak(model.make_next(record))
        return MODE
    if command in ('move download up', 'move download earlier', 'move up', 'move earlier'):
        m.speak(model.move(record, -1))
        return MODE
    if command in ('move download down', 'move download later', 'move down', 'move later'):
        m.speak(model.move(record, 1))
        return MODE
    if command in ('open downloads folder', 'open download folder', 'show in folder', 'open folder'):
        return open_folder(m, record)
    # A file name, or its first words, selects that download.
    if records and command:
        matches = [i for i, r in enumerate(records) if m.normalized_command(model.display(r)).startswith(command)]
        if len(matches) == 1:
            select(records, matches[0])
            m.speak(describe(m))
            return MODE
    m.speak('Downloads. Say pause download, cancel download, open download, or move to next. Press F1 for help, or Escape to go back.')
    return MODE


def question(action, record):
    name = downloads.DownloadManager.display(record)
    if action == 'cancel':
        return 'Cancel ' + name + '? The part already downloaded is deleted. Say Yes or No. Escape keeps it downloading.'
    return name + ' is a program. Run it? Only run programs from websites you trust. Say Yes or No.'


# Opening files -----------------------------------------------------------------------
def open_record(m, record):
    """Enter: a finished file opens in Voice Companion when it can, otherwise in its Windows app."""
    global PENDING, CHOICE, OPENED
    model = manager(m)
    if record.state != 'finished':
        m.speak(model.details(record) + (' Space pauses or resumes it.' if record.state in ('downloading', 'paused', 'queued') else ''))
        return MODE
    path = Path(record.path) if record.path else None
    if path is None or not path.is_file():
        m.speak(model.display(record) + ' is no longer in its folder. It may have been moved or deleted.')
        return MODE
    from task_navigation import MEDIA_TYPES, DOCUMENT_TYPES
    from document_reader import SUPPORTED
    suffix = path.suffix.casefold()
    nav = m.task_navigation()
    if suffix in MEDIA_TYPES:
        nav.picker = None
        result = nav.selected_file(path)
        # Escape in the player comes back to this list, not to a File manager folder.
        nav.player_files, nav.player_picker, nav.player_return = None, None, MODE
        return result
    if suffix in SUPPORTED or suffix in DOCUMENT_TYPES:
        nav.picker = None
        nav.reader_return, OPENED = MODE, path
        return nav.open_document(path)
    if suffix in downloads.PROGRAM_TYPES:
        PENDING, CHOICE = ('run', record), 'no'
        m.speak(question('run', record))
        return MODE
    m.speak(launch(record))
    return MODE


def launch(record):
    """Open a file in the app Windows uses for its type."""
    path = Path(record.path)
    name = downloads.DownloadManager.display(record)
    if not path.is_file():
        return name + ' is no longer in its folder. It may have been moved or deleted.'
    starter = getattr(os, 'startfile', None)
    if starter is None:
        return 'Opening files in other apps works only on Windows.'
    try:
        starter(str(path))
    except OSError:
        return 'Windows has no app for ' + name + '. Ask your helper which app opens this type of file.'
    return 'Opening ' + name + ' in its Windows app. Return to Voice Companion with Alt+Tab.'


def open_folder(m, record):
    """Show the download folder in Voice Companion's File manager, on this file."""
    from file_manager import FileManager
    folder = Path(record.path).parent if record is not None and record.path else manager(m).folder()
    if not folder.is_dir():
        m.speak('The download folder is not available. Check Web browser options.')
        return MODE
    m.FILE_MANAGER = FileManager()
    nav = m.task_navigation()
    nav.picker = None
    nav.reader_return = 'files'
    m.FILE_MANAGER.pick_extensions = None
    m.speak('File manager. ' + m.FILE_MANAGER.refresh(str(folder), preserve=record.path if record is not None else None))
    return 'files'
