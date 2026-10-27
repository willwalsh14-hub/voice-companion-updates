"""Email attachments on screen: the Attachments list, saving, opening and attaching.

The Attachments list shows the files of the open message (from Email) or of the
email being written (from an email draft). Up and Down move, Home and End reach
the ends, Enter opens a file inside Voice Companion where it can, Control+S
saves it, Control+Shift+S saves them all, Delete takes a file off a draft, and
Escape goes back to the message or draft, where you were.

Attaching uses File manager as a picker, like opening a document: Enter on a
file attaches it (Space marks several first) and returns to the draft. In File
manager, Control+N starts a new email with the chosen files, and Control+T adds
them to the email draft that is open.

companion.py calls into this module from a few small hooks. `m` is the
companion module.
"""
import os
import re
import time
from pathlib import Path

import mail_attachments as files_words
from mail_attachments import AttachmentError
from menu_navigation import next_match

MODE = 'attachments'
HELP = ('Attachments. Up and Down Arrow move through the files. Home and End reach the ends of the list. '
        'Enter opens a file. Documents open in Document reader, and sound and video in the media player. '
        'Control+S saves the file in your Downloads folder. Control+Shift+S saves them all. '
        'Say "save attachment to" and a folder, such as Documents, to save it somewhere else. '
        'In an email you are writing, Delete takes a file off, and Control+T attaches another. '
        'Alt opens the attachments menu. Escape goes back.')

SOURCE = None        # 'message' or 'draft'
RETURN_MODE = 'mailbox'
ITEMS = []
INDEX = 0
MESSAGE = None       # (message id, folder id) of the message the list belongs to
PENDING = None       # ('run', item): a Yes or No question; ('folder', items): a folder is being typed
CHOICE = 'no'
OPENED = None        # the file Enter opened in Document reader, so Escape there comes back here

LIST_PHRASES = ('list attachments', 'show attachments', 'open attachments', 'read attachments', 'what is attached',
                'what attachments', 'attachments list', 'list the attachments', 'show the attachments',
                'hear attachments', 'which attachments')
MAIL_EXTRA = ('attachments', 'attachment')
ATTACH_PHRASES = ('attach a file', 'attach file', 'attach files', 'attach a document', 'attach another file',
                  'add attachment', 'add an attachment', 'insert attachment')
REMOVE_PHRASES = ('remove attachment', 'remove an attachment', 'remove the attachment', 'delete attachment',
                  'delete the attachment', 'take off attachment')
SAVE_ONE = ('save attachment', 'save the attachment', 'save this attachment', 'save file', 'save this file')
SAVE_ALL = ('save all attachments', 'save all the attachments', 'save attachments', 'save the attachments', 'save all')
OPEN_ONE = ('open attachment', 'open the attachment', 'open this attachment', 'open file', 'open it', 'open',
            'enter', 'okay', 'ok', 'that one')
CLOSE = ('close attachments', 'close attachment list', 'close list', 'escape', 'go back', 'back',
         'back to message', 'back to email', 'back to draft', 'back to email draft')
EMAIL_FILE = ('email this file', 'email these files', 'email the file', 'email the files', 'email file', 'email files',
              'email selected files', 'send this file by email', 'send these files by email', 'send by email',
              'mail this file', 'new email with this file')
ATTACH_TO_DRAFT = ('attach to draft', 'attach to email', 'attach to email draft', 'attach to the email',
                   'attach to the draft', 'attach this file', 'attach these files', 'attach to open email')
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'enter', 'open it', 'run it')
NO = ('no', 'no thanks', 'escape', 'go back', 'back', 'cancel', 'do not open')
PASS_THROUGH = ('options', 'open options', 'exit companion', 'go to sleep', 'wake up', 'user guide', 'open user guide',
                'manual', 'open manual', 'release notes', 'what mode', 'what version', 'toggle audio')


# Places ----------------------------------------------------------------------

def default_folder(m):
    """The Downloads folder, or the download folder chosen in Web browser options."""
    import downloads
    chosen = str(m.PREFERENCES.get('download_folder', '') or '').strip()
    if chosen and Path(chosen).is_dir():
        return Path(chosen)
    folder = downloads.default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def spoken_folder(value):
    """A folder from what was said or typed: Documents, Desktop, Downloads, Music, or a full path."""
    from downloads import spoken_path
    from file_manager import START_PLACES, _place
    text = spoken_path(value)
    plain = re.sub(r'[^a-z ]+', ' ', text.casefold()).strip()
    plain = re.sub(r'\s+', ' ', re.sub(r'^(?:my|the) ', '', plain)).removesuffix(' folder').strip()
    if plain in START_PLACES and START_PLACES[plain] is not None and plain not in ('c', 'c drive'):
        return Path(_place(plain))
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not text or not path.is_absolute():
        raise AttachmentError('Say a folder such as Documents, Desktop or Downloads, or type its full path.')
    if not path.is_dir():
        raise AttachmentError('That folder does not exist. Say a folder such as Documents or Desktop, or type its full path.')
    return path


def where(folder):
    return 'your ' + folder.name + ' folder' if folder.name else str(folder)


# The list --------------------------------------------------------------------

def _set(items, index=0):
    global ITEMS, INDEX
    ITEMS = list(items)
    INDEX = max(0, min(index, len(ITEMS) - 1)) if ITEMS else 0


def describe(index=None):
    if not ITEMS:
        return 'No attachments.'
    index = INDEX if index is None else index
    return files_words.label(ITEMS[index], index, len(ITEMS))


def current():
    return ITEMS[INDEX] if ITEMS and 0 <= INDEX < len(ITEMS) else None


def message_items(m):
    """The files of the message that is open or highlighted, and the message they belong to."""
    mail = m.mail_session
    if mail is None or not mail.rows or not mail.current:
        raise AttachmentError('Choose a message first.')
    row = mail.rows[mail.current - 1]
    message = (row['id'], mail.folder_id)
    found = getattr(mail, 'attachments', None) if mail.view == 'message' else None
    if found is None or getattr(mail, 'attachments_for', None) != message:
        found = mail.client().attachments(row['id'], mail.folder_id)
        mail.attachments, mail.attachments_for = found, message
    if not isinstance(found, list):
        raise AttachmentError('The attachments of this message could not be listed. Try again.')
    return message, found


def open_for_message(m, mode):
    """The Attachments list of the open (or highlighted) message."""
    global SOURCE, RETURN_MODE, MESSAGE, PENDING
    from mail_accounts import AccountError
    from mailbox_access import MailboxError
    try:
        message, found = message_items(m)
    except (AttachmentError, MailboxError, AccountError) as exc:
        m.speak(str(exc))
        return mode
    if not found:
        m.speak('This message has no attachments.')
        return mode
    SOURCE, RETURN_MODE, MESSAGE, PENDING = 'message', 'mailbox', message, None
    _set(found)
    m.speak('Attachments. ' + files_words.summary(found) + ' ' + describe())
    return MODE


def open_for_draft(m, mode, hint=''):
    global SOURCE, RETURN_MODE, MESSAGE, PENDING
    draft = m.email_draft
    found = draft.all_attachments() if draft is not None else []
    if not found:
        m.speak('This email has no attachments. Press Control+T to attach a file.')
        return mode
    SOURCE, RETURN_MODE, MESSAGE, PENDING = 'draft', 'email_draft', None, None
    _set(found)
    m.speak('Attachments. ' + files_words.summary(found) + ' ' + hint + describe())
    return MODE


def back(m):
    """Escape: back to the message or the draft, where you were."""
    global PENDING
    PENDING = None
    if RETURN_MODE == 'email_draft' and m.email_draft is not None:
        field = m.email_draft.compose_step or 'body'
        m.speak('Back to email draft. ' + {'recipient': 'To field.', 'subject': 'Subject field.', 'cc': 'CC field.',
                                           'bcc': 'BCC field.'}.get(field, 'Message body.'))
        m.focus_email_entry()
        return 'email_draft'
    if RETURN_MODE == 'mailbox' and m.mail_session is not None:
        mail = m.mail_session
        m.speak('Back to message.' if mail.view == 'message' else 'Back to ' + mail.folder_name + '.')
        return 'mailbox'
    return 'awake'


def back_to_list(m):
    """From the reader or player opened from this list."""
    global PENDING
    PENDING = None
    if not ITEMS:
        return back(m)
    m.speak('Attachments. ' + describe())
    return MODE


def reader_back(m):
    """Escape in Document reader: back to this list, for the file opened from it."""
    m.task_navigation().reader_return = 'files'
    reader = getattr(m, 'FILE_READER', None)
    path = getattr(reader, 'path', None)
    if OPENED is None or path is None or os.path.normcase(str(Path(path))) != os.path.normcase(str(OPENED)):
        return None
    return back_to_list(m)


# Hooks from companion.py ------------------------------------------------------

def context(m, value):
    if PENDING is not None and PENDING[0] == 'run':
        value['confirmation'] = CHOICE
        value['menu_letters'] = False
    elif PENDING is not None and PENDING[0] == 'folder':
        value['task_input'] = True
        value['menu_letters'] = False
    return value


def menu_state(m):
    if PENDING is not None or not ITEMS:
        return None
    def select(index):
        global INDEX
        INDEX = index
    return [item['name'] for item in ITEMS], INDEX, select


def navigation_key(m, key):
    global CHOICE
    if PENDING is not None and PENDING[0] == 'folder':
        return 'cancel' if key == 'Escape' else None
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
        return '@attachments:letter:' + key[7:]
    return {'Up': 'previous', 'Left': 'previous', 'Down': 'next', 'Right': 'next', 'Home': 'home', 'End': 'end',
            'Enter': 'open attachment', 'Escape': 'close attachments'}.get(key)


def handle(m, text, mode, typed=False):
    """Commands for attachments on every screen where they apply. None: not ours."""
    raw = text.strip()
    command = m.normalized_command(raw)
    try:
        if mode == MODE:
            return command_here(m, raw, command)
        if getattr(m, 'NAMED_LIST', None):
            return None
        if mode == 'mailbox':
            return mailbox_command(m, raw, command, mode)
        if mode == 'email_draft':
            return draft_command(m, raw, command, mode)
        if mode == 'files':
            return files_command(m, command, mode)
    except AttachmentError as exc:
        m.speak(str(exc))
        return mode
    return None


def mailbox_command(m, raw, command, mode):
    mail = m.mail_session
    if mail is None or getattr(mail, 'pending', None) or getattr(mail, 'folder_picker', None) or getattr(mail, 'folder_choice', None):
        return None
    if command in LIST_PHRASES + MAIL_EXTRA:
        return open_for_message(m, mode)
    folder_request = re.fullmatch(r'save (?:all )?(?:the )?attachments? to (.+)', raw, re.I)
    if command in SAVE_ALL + SAVE_ONE + OPEN_ONE[:3] or folder_request:
        from mail_accounts import AccountError
        from mailbox_access import MailboxError
        global SOURCE, RETURN_MODE, MESSAGE
        try:
            message, found = message_items(m)
        except (MailboxError, AccountError) as exc:
            m.speak(str(exc))
            return mode
        if not found:
            m.speak('This message has no attachments.')
            return mode
        SOURCE, RETURN_MODE, MESSAGE = 'message', 'mailbox', message
        _set(found)
        if command in OPEN_ONE[:3]:
            if len(found) == 1:
                return open_item(m, found[0], mode)
            return open_for_message(m, mode)
        every = command in SAVE_ALL or bool(folder_request and re.match(r'save (?:all )?(?:the )?attachments ', raw, re.I))
        if not every and len(found) > 1:
            m.speak('This message has ' + files_words.count_words(len(found)) + '. Choose one and press Control+S, '
                    'or say save all attachments.')
            return open_for_message(m, mode)
        folder = spoken_folder(folder_request[1]) if folder_request else default_folder(m)
        save(m, found, folder)
        return mode
    return None


def draft_command(m, raw, command, mode):
    draft = m.email_draft
    if draft is None or m.pending_send is not None and command in m.SEND_REVIEW_ANSWERS:
        return None
    if command in ATTACH_PHRASES:
        m.pending_send = None
        return pick(m, mode)
    if command in LIST_PHRASES:
        m.pending_send = None
        return open_for_draft(m, mode)
    if command in REMOVE_PHRASES:
        m.pending_send = None
        return open_for_draft(m, mode, 'Choose a file, then press Delete. ')
    named = re.fullmatch(r'(?:remove|delete) (?:the )?attachment (.+)', command)
    if named:
        found = draft.all_attachments()
        wanted = named[1].strip()
        index = next((i for i, item in enumerate(found) if item['name'].casefold() == wanted
                      or Path(item['name']).stem.casefold() == wanted), None)
        if index is None:
            return None    # "Delete the attachment if it is too big." is writing, not a command.
        m.pending_send = None
        m.speak(remove(m, found, index))
        return mode
    return None


def files_command(m, command, mode):
    manager = getattr(m, 'FILE_MANAGER', None)
    if manager is None or manager.pending or manager.busy:
        return None
    if command in EMAIL_FILE:
        return email_files(m, mode)
    if command in ATTACH_TO_DRAFT:
        return attach_to_draft(m, mode)
    return None


# In the list -----------------------------------------------------------------

def command_here(m, raw, command):
    global PENDING, CHOICE, INDEX
    if PENDING is not None and PENDING[0] == 'folder':
        items = PENDING[1]
        if command in ('cancel', 'escape', 'go back', 'back'):
            PENDING = None
            m.speak('Not saved. ' + describe())
            return MODE
        if not raw:
            m.speak('Type or say a folder, then press Enter. Escape cancels.')
            return MODE
        try:
            folder = spoken_folder(re.sub(r'^save (?:all )?(?:the )?attachments? to\s+', '', raw, flags=re.I))
        except AttachmentError as exc:
            m.speak(str(exc) + ' Escape cancels.')
            return MODE
        PENDING = None
        save(m, items, folder)
        return MODE
    if PENDING is not None:
        _, item = PENDING
        if command in YES:
            PENDING = None
            return launch(m, item, MODE)
        if command in NO:
            PENDING = None
            m.speak('Not opened. ' + describe())
            return MODE
        if command in ('help', 'what mode', 'toggle audio') or command.startswith(('help with ', 'help for ')):
            return None
        m.speak(question(item))
        return MODE
    if command in PASS_THROUGH or m.speech_control(raw) or m.speech_setting_request(raw) is not None \
            or command in m.VOICE_LIST_COMMANDS or m.spoken_control(command) in ('exit', 'sleep'):
        return None
    if command in ('help', 'help with attachments', 'attachments help', 'help with email attachments'):
        return None   # the guide opens on its Attachments topic
    if command in ('main menu', 'back to main menu'):
        target = back(m)
        return m.handle('main menu', target, typed=True) if target != 'awake' else 'awake'
    if command in CLOSE:
        return back(m)
    if command in LIST_PHRASES + ('where am i', 'attachments'):
        m.speak(files_words.summary(ITEMS) + ' ' + describe())
        return MODE
    moves = {'next': 1, 'next attachment': 1, 'down': 1, 'previous': -1, 'previous attachment': -1, 'up': -1}
    ends = {'home': 0, 'first attachment': 0, 'start of list': 0, 'top of list': 0, 'first item': 0,
            'end': -1, 'last attachment': -1, 'end of list': -1, 'bottom of list': -1, 'last item': -1}
    if command in moves or command in ends or raw.startswith('@attachments:letter:'):
        if not ITEMS:
            return back(m)
        old = INDEX
        if raw.startswith('@attachments:letter:'):
            found = next_match([item['name'] for item in ITEMS], old, raw[-1])
            if found is None:
                m.speak('No attachment starts with ' + raw[-1].upper() + '.')
                return MODE
            INDEX = found
            m.speak(('End of matching items. ' if found == old else '') + describe())
            return MODE
        if command in ends:
            INDEX = 0 if ends[command] == 0 else len(ITEMS) - 1
            edge = 'Start of list. ' if INDEX == old == 0 and ends[command] == 0 else 'End of list. ' if INDEX == old and ends[command] else ''
        else:
            INDEX = max(0, min(len(ITEMS) - 1, old + moves[command]))
            edge = ('Start of list. ' if moves[command] < 0 else 'End of list. ') if INDEX == old else ''
        m.speak(edge + describe())
        return MODE
    item = current()
    if item is None:
        return back(m)
    if command in OPEN_ONE:
        return open_item(m, item, MODE)
    folder_request = re.fullmatch(r'save (?:all )?(?:the )?attachments? to (.+)', raw, re.I)
    if command in ('save attachment to folder', 'save to folder', 'save to a folder', 'save attachment to a folder'):
        PENDING = ('folder', [item])
        m.speak('Save ' + item['name'] + ' to a folder. Type or say a folder, such as Documents or Desktop, '
                'or type its full path. Then press Enter. Escape cancels.')
        return MODE
    if folder_request:
        every = bool(re.match(r'save (?:all )?(?:the )?attachments ', raw, re.I))
        save(m, ITEMS if every else [item], spoken_folder(folder_request[1]))
        return MODE
    if command in SAVE_ONE:
        save(m, [item], default_folder(m))
        return MODE
    if command in SAVE_ALL:
        save(m, ITEMS, default_folder(m))
        return MODE
    if command in REMOVE_PHRASES + ('delete', 'remove', 'remove file', 'take it off'):
        if SOURCE != 'draft':
            m.speak('Attachments of a message you received stay with it. To take a file off a forward, '
                    'remove it from the Attachments list of the draft.')
            return MODE
        said = remove(m, ITEMS, INDEX)
        if not ITEMS:
            m.speak(said)
            return back(m)
        m.speak(said + ' ' + describe())
        return MODE
    if command in ATTACH_PHRASES:
        if SOURCE != 'draft':
            m.speak('To attach a file, start a reply or forward first.')
            return MODE
        return pick(m, 'email_draft')
    # A file name, or its first words, moves to that file.
    matches = [i for i, entry in enumerate(ITEMS) if m.normalized_command(entry['name']).startswith(command)] if command else []
    if len(matches) == 1:
        INDEX = matches[0]
        m.speak(describe())
        return MODE
    m.speak('Attachments. Press Enter to open, Control+S to save, or Escape to go back. Press F1 for help.')
    return MODE


def remove(m, items, index):
    """Take one file off the draft. Returns what to say."""
    draft = m.email_draft
    item = items[index]
    originals = len(draft.original_attachments())
    if index < originals:
        draft.drop_original(item['name'])
    else:
        draft.remove_attachment(index - originals)
    left = draft.all_attachments()
    if items is ITEMS:
        _set(left, index)
    count = files_words.count_words(len(left)) + ' left.' if left else 'No attachments left.'
    return 'Removed ' + item['name'] + '. ' + count


# Saving and opening ------------------------------------------------------------

def chunks_for(m, item):
    """The bytes of one file in the list, a piece at a time."""
    if SOURCE == 'draft' and not item.get('original'):
        path = Path(item['path'])
        def local():
            with open(path, 'rb') as file:
                while True:
                    block = file.read(files_words.MB)
                    if not block:
                        return
                    yield block
        return local()
    if SOURCE == 'draft':
        raise AttachmentError(item['name'] + ' is in the original message. Save it from there.')
    message_id, folder_id = MESSAGE
    return m.mail_session.client().attachment_chunks(message_id, item, folder_id)


def save_one(m, item, folder):
    import user_stop
    size = item.get('size') or 0
    slow = size > files_words.SLOW_SIZE
    if slow:
        m.speak_status('Saving ' + item['name'] + '. Please wait. Escape stops.')
    started = [time.monotonic()]
    def progress(written):
        if slow and size and time.monotonic() - started[0] >= 5:
            started[0] = time.monotonic()
            m.speak_status('Saving. ' + str(min(99, written * 100 // size)) + ' percent.')
    return files_words.save_stream(folder, item['name'], chunks_for(m, item), progress, stop=user_stop.check)


def save(m, items, folder):
    """Save files in a folder and say where. A name already there gets " (1)"."""
    from mail_accounts import AccountError
    from mailbox_access import MailboxError
    import user_stop
    if SOURCE == 'draft' and all(not item.get('original') for item in items) and len(items) == 1:
        m.speak(items[0]['name'] + ' is already on this computer, in ' + where(Path(items[0]['path']).parent) + '.')
        return
    saved = []
    try:
        for item in items:
            path = save_one(m, item, folder)
            saved.append((item, path))
    except user_stop.Stopped:
        m.speak(('Saving stopped. ' + item['name'] + ' was not saved.') + done_words(saved, folder, ' Already saved: '))
        return
    except (MailboxError, AccountError, AttachmentError) as exc:
        m.speak(str(exc) + done_words(saved, folder, ' Already saved: '))
        return
    except OSError:
        m.speak(item['name'] + ' could not be saved. ' + where(folder).capitalize() + ' may be full or locked.'
                + done_words(saved, folder, ' Already saved: '))
        return
    m.speak(done_words(saved, folder, 'Saved '))


def done_words(saved, folder, start):
    if not saved:
        return ''
    if len(saved) == 1:
        item, path = saved[0]
        renamed = path.name != files_words.safe_filename(item['name'])
        return (start + (path.name + ' in ' + where(folder) + '.' if not renamed else
                         item['name'] + ' as ' + path.name + ' in ' + where(folder) +
                         ', because a file with that name was already there.'))
    names = ', '.join(path.name for _, path in saved)
    return start + files_words.count_words(len(saved)) + ' in ' + where(folder) + ': ' + names + '.'


def question(item):
    return (item['name'] + ' is a program or script. Open it? Only open programs from people you trust. '
            'Say Yes or No.')


def open_item(m, item, mode):
    """Enter: documents open in Document reader, sound and video in the player, other files in their Windows app."""
    global PENDING, CHOICE
    kind = files_words.kind(item['name'])
    if kind == 'program':
        PENDING, CHOICE = ('run', item), 'no'
        m.speak(question(item))
        return MODE
    return launch(m, item, mode, kind)


def local_copy(m, item):
    """A file on this computer for the item: the attached file itself, or a saved copy of the attachment."""
    if SOURCE == 'draft' and not item.get('original'):
        path = Path(item['path'])
        if not path.is_file():
            raise AttachmentError(files_words.missing_message([item]).split(',')[0] + '.')
        return path
    return save_one(m, item, files_words.opened_folder(m.APP))


def launch(m, item, mode, kind=None):
    global OPENED
    from mail_accounts import AccountError
    from mailbox_access import MailboxError
    import user_stop
    kind = kind or files_words.kind(item['name'])
    try:
        path = local_copy(m, item)
    except user_stop.Stopped:
        m.speak('Opening stopped.')
        return mode
    except (MailboxError, AccountError, AttachmentError) as exc:
        m.speak(str(exc))
        return mode
    except OSError:
        m.speak(item['name'] + ' could not be opened. Try saving it instead with Control+S.')
        return mode
    nav = m.task_navigation()
    if kind == 'media':
        nav.picker = None
        try:
            result = nav.selected_file(path)
        except OSError as exc:
            m.speak(str(exc))
            return mode
        # Escape in the player comes back to this list.
        nav.player_files, nav.player_picker, nav.player_return = None, None, MODE
        return result
    if kind == 'document':
        from document_reader import open_document
        from file_manager import FileManager
        nav.picker = None
        nav.reader_return, OPENED = MODE, path
        if m.FILE_MANAGER is None:
            m.FILE_MANAGER = FileManager()
        m.speak(m.FILE_MANAGER.start_job(lambda: open_document(path), m.FILE_MANAGER.finish_open, 'Opening ' + item['name'] + '.'))
        return 'files'
    starter = getattr(os, 'startfile', None)
    if starter is None:
        m.speak('Opening files in other apps works only on Windows. Press Control+S to save it instead.')
        return mode
    try:
        starter(str(path))
    except OSError:
        m.speak('Windows has no app for ' + item['name'] + '. Press Control+S to save it, then ask your helper which app opens it.')
        return mode
    m.speak('Opening ' + item['name'] + ' in its Windows app. Return to Voice Companion with Alt+Tab.')
    return mode


# Attaching -------------------------------------------------------------------

def pick(m, mode):
    """File manager as a picker: Enter on a file attaches it and comes back to the draft."""
    from file_manager import FileManager, start_location
    if m.email_draft is None:
        m.speak('No email is open. Say new email first.')
        return mode
    try:
        m.email_draft.save()
    except OSError:
        m.speak('The draft could not be saved, so File manager did not open. Try again.')
        return mode
    m.FILE_MANAGER = FileManager()
    m.FILE_MANAGER.pick_extensions = None
    m.FILE_MANAGER.pick_label = 'attach'
    m.task_navigation().picker = 'attach'
    m.speak('Attach a file. Enter attaches the file you are on. Space marks several. Escape goes back to the email. '
            + m.FILE_MANAGER.refresh(start_location(m.PREFERENCES.get('file_manager_start'))))
    return 'files'


def chosen_files(manager):
    """The marked files, or the file you are on. Folders are refused."""
    targets = manager.targets()
    folders = [item for item in targets if item.folder]
    if folders:
        raise AttachmentError(folders[0].name + ' is a folder. Choose files, not folders.')
    return [item.path for item in targets]


def picked(m):
    """Enter in the picker: attach and go back to the draft at the same field."""
    manager = m.FILE_MANAGER
    if m.email_draft is None:
        m.task_navigation().picker = None
        m.speak('The email draft is closed, so nothing was attached.')
        return 'files'
    try:
        said = m.email_draft.attach(chosen_files(manager))
    except (AttachmentError, ValueError) as exc:
        m.speak(str(exc))
        return 'files'
    except OSError:
        m.speak('The draft could not be saved. Nothing was attached. Try again.')
        return 'files'
    m.task_navigation().picker = None
    manager.pick_label = None
    field = m.email_draft.compose_step or 'body'
    m.speak(said + ' Back to email draft. ' + {'recipient': 'To field.', 'subject': 'Subject field.', 'cc': 'CC field.',
                                               'bcc': 'BCC field.'}.get(field, 'Message body.'))
    m.focus_email_entry()
    return 'email_draft'


def picker_closed(m):
    """Escape out of the drive list while picking: back to the draft, nothing attached."""
    if m.FILE_MANAGER is not None:
        m.FILE_MANAGER.pick_label = None
    if m.email_draft is None:
        return 'awake'
    m.speak('Nothing attached. Back to email draft.')
    m.focus_email_entry()
    return 'email_draft'


def email_files(m, mode):
    """File manager: a new email with the chosen files attached, asking for the recipient."""
    from email_draft import VoiceEmail
    from mail_accounts import ProtectedStore
    paths = chosen_files(m.FILE_MANAGER)
    if m.email_draft is not None:
        m.email_draft.save()
    draft = VoiceEmail(m.APP / 'Email Drafts')
    draft.compose_step = 'recipient'
    draft.dictating = m.INPUT_MODE != 'commands'
    draft.automatic_dictation = m.INPUT_MODE != 'commands'
    selected = ProtectedStore(m.APP).selected()
    if selected:
        draft.provider = selected[0]
    try:
        said = draft.attach(paths)
    except BaseException:
        draft.discard()
        raise
    m.email_draft = draft
    m.pending_send = None
    m.task_navigation().picker = None
    m.speak('New email. ' + said + ' To field. Who do you want to send it to? Type an address, or say a saved contact name.')
    m.focus_email_entry()
    return 'email_draft'


def attach_to_draft(m, mode):
    if m.email_draft is None:
        m.speak('No email draft is open. Press Control+N to start a new email with this file.')
        return mode
    said = m.email_draft.attach(chosen_files(m.FILE_MANAGER))
    m.task_navigation().picker = None
    m.speak(said + ' Back to email draft.')
    m.focus_email_entry()
    return 'email_draft'
