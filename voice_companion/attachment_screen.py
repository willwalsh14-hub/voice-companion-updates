"""Email attachments on screen: the Attachments list, saving, opening and attaching.

The Attachments list shows the files of the open message (from Email) or of the
email being written (from an email draft). Every action has a key and a plain
voice command, as on the Contacts and Calendar screens:
- Up and Down move, Home and End reach the ends, a letter moves to the next name
  that starts with it. "Next attachment", "previous attachment".
- Enter opens a file inside Voice Companion where it can. "Open report", "open the PDF".
- Control+S saves it in Downloads. "Save it", "save report to Documents".
- Control+D saves it in a folder you name. Control+Shift+S saves them all.
  "Save all to Desktop".
- Delete takes a file off a draft. "Remove photo".
- Control+I reads its details. "How big is it", "read attachment details".
- Control+T attaches another file. "Attach report from Documents", "attach the last download".
- Escape goes back to the message or draft, where you were.

File names match loosely, because speech is messy: "report dot PDF", "the photo",
"my resume" and "the spreadsheet" all find their file.

Attaching uses File manager as a picker, like opening a document: Enter on a
file attaches it (Space marks several first) and returns to the draft. In File
manager, Control+N starts a new email with the chosen files, and Control+T adds
them to the email draft that is open. Without File manager, "attach my resume
from Documents" looks for the file in that folder, and "attach the last download"
takes the newest finished download.

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
        'Control+S saves the file in your Downloads folder. Control+D saves it in a folder you name. '
        'Control+Shift+S saves them all. Control+I reads the details. '
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
CANDIDATES = []      # files found by "attach ... from Documents", offered in a short list

LIST_PHRASES = ('list attachments', 'show attachments', 'open attachments', 'read attachments', 'what is attached',
                'what attachments', 'attachments list', 'list the attachments', 'show the attachments',
                'hear attachments', 'which attachments', 'what s attached', 'whats attached', 'what is attached to this',
                'what s attached to this', 'what files are attached', 'what attachments are there', 'show me the attachments',
                'read the attachments', 'list files', 'list the files', 'any attachments', 'are there any attachments',
                'attachment list', 'list attachment', 'go to attachments', 'go to the attachments')
MAIL_EXTRA = ('attachments', 'attachment')
ATTACH_PHRASES = ('attach a file', 'attach file', 'attach files', 'attach a document', 'attach another file',
                  'add attachment', 'add an attachment', 'insert attachment', 'add a file', 'attach something')
LAST_DOWNLOAD = ('attach the last download', 'attach last download', 'attach my last download', 'attach the latest download',
                 'attach latest download', 'attach the most recent download', 'attach the last downloaded file',
                 'attach the last file i downloaded', 'attach the file i just downloaded', 'attach what i just downloaded',
                 'attach my latest download', 'attach the newest download')
REMOVE_PHRASES = ('remove attachment', 'remove an attachment', 'remove the attachment', 'delete attachment',
                  'delete the attachment', 'take off attachment', 'take off an attachment', 'remove a file',
                  'take an attachment off')
SAVE_ONE = ('save attachment', 'save the attachment', 'save this attachment', 'save file', 'save this file')
SAVE_ALL = ('save all attachments', 'save all the attachments', 'save attachments', 'save the attachments', 'save all',
            'save everything', 'save all files', 'save all the files', 'save them all', 'save both', 'download all',
            'download all attachments', 'download attachments')
OPEN_ONE = ('open attachment', 'open the attachment', 'open this attachment', 'open file', 'open it', 'open',
            'enter', 'okay', 'ok', 'that one', 'open this', 'open this file', 'open the file', 'play it')
CLOSE = ('close attachments', 'close attachment list', 'close list', 'escape', 'go back', 'back',
         'back to message', 'back to email', 'back to draft', 'back to email draft', 'close', 'done')
SIZE = ('how big is it', 'how big is this', 'how big is that', 'how big is the attachment', 'how big is this attachment',
        'how big is the file', 'how big is this file', 'how large is it', 'what size is it', 'what is the size',
        'what s the size', 'size', 'file size', 'attachment size', 'how many megabytes', 'what size is this')
TOTAL = ('how big are they', 'how big are the attachments', 'how big are the files', 'total size', 'attachments size',
         'size of attachments', 'how big are all the attachments', 'how large are the attachments',
         'how big are the attachments altogether', 'total attachment size', 'how big is the email', 'how big is this email')
DETAILS = ('read attachment details', 'attachment details', 'details', 'file details', 'read details', 'more details',
           'read the details', 'attachment info', 'attachment information', 'what is it', 'what kind of file is it',
           'what type of file is it', 'what type is it', 'what kind of file is this', 'tell me about it',
           'read attachment information', 'read file details')
FOLDER_ASK = ('save attachment to folder', 'save to folder', 'save to a folder', 'save attachment to a folder',
              'save it to a folder', 'save in a folder', 'save as', 'save attachment as', 'choose a folder')
EMAIL_FILE = ('email this file', 'email these files', 'email the file', 'email the files', 'email file', 'email files',
              'email selected files', 'send this file by email', 'send these files by email', 'send by email',
              'mail this file', 'new email with this file')
ATTACH_TO_DRAFT = ('attach to draft', 'attach to email', 'attach to email draft', 'attach to the email',
                   'attach to the draft', 'attach this file', 'attach these files', 'attach to open email')
YES = ('yes', 'yes please', 'okay', 'ok', 'confirm', 'enter', 'open it', 'run it')
NO = ('no', 'no thanks', 'escape', 'go back', 'back', 'cancel', 'do not open')
PASS_THROUGH = ('options', 'open options', 'exit companion', 'go to sleep', 'wake up', 'user guide', 'open user guide',
                'manual', 'open manual', 'release notes', 'what mode', 'what version', 'toggle audio')
# "Save it", "save this one": the file you are on. "Save them all": every file.
THIS = ('it', 'this', 'this one', 'that', 'that one', 'attachment', 'the attachment', 'this attachment', 'file',
        'the file', 'this file', 'current attachment', 'the current attachment', 'current file', 'the current file', 'one')
EVERY = ('all', 'all attachments', 'all the attachments', 'attachments', 'the attachments', 'everything', 'all files',
         'all the files', 'them', 'them all', 'all of them', 'both', 'both of them', 'every attachment', 'every file',
         'all of the attachments', 'the files', 'files')
ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
            'ninth': 9, 'tenth': 10, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
            'eight': 8, 'nine': 9, 'ten': 10}

# Places people name for attaching and saving. Windows lets people move these; known_folder finds them.
PLACES = {'documents': ('{FDD39AD0-238F-46AF-ADB4-6C85480369C7}', 'Documents'),
          'desktop': ('{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}', 'Desktop'),
          'downloads': ('{374DE290-123F-4565-9164-39C4925E467B}', 'Downloads'),
          'pictures': ('{33E28130-4E1E-4676-835A-98395C3BC3BB}', 'Pictures'),
          'music': ('{4BD8D571-6D19-48D3-BE97-422220080E43}', 'Music'),
          'videos': ('{18989B1D-99B5-455B-841C-AB7C74E4DDFC}', 'Videos')}
PLACE_WORDS = {'document': 'documents', 'docs': 'documents', 'download': 'downloads', 'picture': 'pictures',
               'photos': 'pictures', 'video': 'videos', 'my music': 'music'}
# Looking for a file by voice stops after this many items or seconds, so a huge folder never freezes the app.
SEARCH_LIMIT = 20000
SEARCH_SECONDS = 3.0
SEARCH_DEPTH = 4
SKIP_FOLDERS = {'appdata', 'node_modules', '$recycle.bin', 'system volume information', '__pycache__'}
SKIP_ENDINGS = ('.lnk', '.url', '.tmp', '.crdownload', '.part', '.partial')
CHOICES_SHOWN = 8


# Places ----------------------------------------------------------------------

def place_key(value):
    """'documents' for "Documents", "my documents folder" or "the Documents folder"; None for anything else."""
    plain = files_words.plain_words(value)
    plain = re.sub(r'^(?:in |on |to |into |from )', '', plain)
    plain = re.sub(r'^(?:my |the |your )', '', plain).removesuffix(' folder').removesuffix(' directory').strip()
    plain = re.sub(r'^(?:my |the |your )', '', plain)
    plain = PLACE_WORDS.get(plain, plain)
    return plain if plain in PLACES else None


def place(key):
    from downloads import known_folder
    return Path(known_folder(*PLACES[key]))


def default_folder(m):
    """The Downloads folder, or the download folder chosen in Web browser options."""
    import downloads
    chosen = str(m.PREFERENCES.get('download_folder', '') or '').strip()
    if chosen and Path(chosen).is_dir():
        return Path(chosen)
    folder = downloads.default_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AttachmentError('Your Downloads folder is not available. Say save it to, and a folder such as Documents.') from exc
    return folder


def spoken_folder(value):
    """A folder from what was said or typed: Documents, Desktop, Downloads, Pictures, Music, or a full path."""
    from downloads import spoken_path
    text = spoken_path(value)
    key = place_key(text)
    if key is not None:
        folder = place(key)
        if not folder.is_dir():
            raise AttachmentError('Your ' + PLACES[key][1] + ' folder was not found. Say another folder, such as Desktop.')
        return folder
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not text or not path.is_absolute():
        raise AttachmentError('Say a folder such as Documents, Desktop or Downloads, or type its full path.')
    if not path.is_dir():
        raise AttachmentError('That folder does not exist. Say a folder such as Documents or Desktop, or type its full path.')
    return path


def looks_like_folder(value):
    """True for a named place or a typed path, so "letter to Sam" stays a file name."""
    from downloads import spoken_path
    text = spoken_path(value)
    return place_key(text) is not None or bool(re.match(r'^(?:[a-z]:[\\/]|[\\/]{1,2}\w|~)', text, re.I))


def where(folder):
    return 'your ' + folder.name + ' folder' if folder.name else str(folder)


def split_folder(text):
    """('report', 'Documents') from "report to Documents"; (text, None) when no folder is named."""
    pieces = list(re.finditer(r'\s+(?:to|in|into|onto|on)\s+(?:the\s+|my\s+)?', ' ' + text, re.I))
    for found in reversed(pieces):
        folder = (' ' + text)[found.end():]
        if looks_like_folder(folder):
            return (' ' + text)[:found.start()].strip(), folder.strip()
    return text, None


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


def header():
    """'Attachments, 2 files. ' said when the list opens."""
    return 'Attachments, ' + files_words.file_count(len(ITEMS)) + '. '


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


def open_for_message(m, mode, index=0):
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
    _set(found, index)
    m.speak(header() + describe())
    return MODE


def open_for_draft(m, mode, hint='', index=0):
    global SOURCE, RETURN_MODE, MESSAGE, PENDING
    draft = m.email_draft
    found = draft.all_attachments() if draft is not None else []
    if not found:
        m.speak('This email has no attachments. Say attach a file, or press Control+T.')
        return mode
    SOURCE, RETURN_MODE, MESSAGE, PENDING = 'draft', 'email_draft', None, None
    _set(found, index)
    m.speak(header() + hint + describe())
    return MODE


def field_words(m):
    field = m.email_draft.compose_step or 'body'
    return {'recipient': 'To field.', 'subject': 'Subject field.', 'cc': 'CC field.', 'bcc': 'BCC field.'}.get(field, 'Message body.')


def back(m):
    """Escape: back to the message or the draft, where you were."""
    global PENDING
    PENDING = None
    if RETURN_MODE == 'email_draft' and m.email_draft is not None:
        m.speak('Back to email draft. ' + field_words(m))
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
    m.speak(header() + describe())
    return MODE


def reader_back(m):
    """Escape in Document reader: back to this list, for the file opened from it."""
    m.task_navigation().reader_return = 'files'
    reader = getattr(m, 'FILE_READER', None)
    path = getattr(reader, 'path', None)
    if OPENED is None or path is None or os.path.normcase(str(Path(path))) != os.path.normcase(str(OPENED)):
        return None
    return back_to_list(m)


# Spoken file names -------------------------------------------------------------

def strip_target(words):
    """'report' from "attachment report", "the file called report" or "report attachment"."""
    words = re.sub(r'^(?:the |this |that )?(?:attachment|file|attached file)(?: called| named)? ', '', words.strip())
    words = re.sub(r'^(?:the )?(?:one|file) (?:called|named) ', '', words)
    return words.strip()


def ordinal(words):
    """The place in the list for "2", "number 2", "the second one", "the last attachment"; None otherwise."""
    words = re.sub(r'^(?:the |attachment |file |number )+', '', words)
    words = re.sub(r' (?:one|attachment|file)$', '', words)
    if words in ('last', 'final'):
        return len(ITEMS) - 1
    number = int(words) if words.isdigit() else ORDINALS.get(words)
    if number is None:
        return None
    return number - 1 if 1 <= number <= len(ITEMS) else -2


def find_items(m, said):
    """The place in the list for what was said, or None after saying why it is not clear."""
    global INDEX
    words = strip_target(said)
    if words in THIS or not words:
        return INDEX
    place = ordinal(words)
    if place == -2:
        m.speak('There ' + ('is ' if len(ITEMS) == 1 else 'are ') + files_words.count_words(len(ITEMS)) + '. ' + describe())
        return None
    if place is not None:
        return place
    found = files_words.name_matches([item['name'] for item in ITEMS], words)
    if len(found) == 1:
        return found[0]
    if not found:
        m.speak('No attachment is called ' + files_words.spoken_name(words) + '. The files are ' +
                files_words.joined(item['name'] for item in ITEMS) + '.')
        return None
    INDEX = found[0]
    m.speak(str(len(found)) + ' attachments match ' + files_words.spoken_name(words) + ': ' +
            files_words.joined(ITEMS[i]['name'] for i in found) + '. Say more of the name. ' + describe())
    return None


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
    try:
        if raw.startswith('@attachments:attach:'):
            return attach_choice(m, raw, mode)
        if getattr(m, 'NAMED_LIST', None):
            return None
        command = m.normalized_command(raw)
        if mode == MODE:
            return command_here(m, raw, command)
        if mode == 'mailbox':
            return mailbox_command(m, raw, command, mode)
        if mode == 'email_draft':
            return draft_command(m, raw, command, mode, typed)
        if mode == 'files':
            return files_command(m, command, mode)
    except AttachmentError as exc:
        m.speak(str(exc))
        return mode
    return None


SAVE_TO = re.compile(r'(?:please )?(?:save|download|keep|put) (?P<rest>.+?)[.!?]*$', re.I)


def save_request(raw):
    """(what, folder) for "save report to Documents", "save all to Desktop", "save it"; None if not a save."""
    found = SAVE_TO.fullmatch(raw.strip())
    if not found:
        return None
    rest = found['rest'].strip()
    if re.match(r'(?:to|in|into)\s', rest, re.I):
        rest = 'it ' + rest       # "save to Desktop"
    what, folder = split_folder(rest)
    what = re.sub(r'^(?:(?:a )?copy of )', '', files_words.plain_words(what))
    return what, folder


def mailbox_command(m, raw, command, mode):
    mail = m.mail_session
    if mail is None or getattr(mail, 'pending', None) or getattr(mail, 'folder_picker', None) or getattr(mail, 'folder_choice', None):
        return None
    if command in LIST_PHRASES + MAIL_EXTRA:
        return open_for_message(m, mode)
    if command in TOTAL + ('attachment details', 'read attachment details'):
        return tell_message_files(m, mode)
    request = save_request(raw)
    named = request is not None and (request[1] is not None or request[0] in EVERY)
    if command in SAVE_ALL + SAVE_ONE + OPEN_ONE[:3] or named:
        global SOURCE, RETURN_MODE, MESSAGE
        found = load_message(m)
        if not found:
            return mode
        if command in OPEN_ONE[:3]:
            if len(found) == 1:
                return open_item(m, found[0], mode)
            return open_for_message(m, mode)
        what, folder_text = request if request else (command, None)
        every = command in SAVE_ALL or what in EVERY
        if not every and what not in THIS + SAVE_ONE and what.removeprefix('save ') not in THIS:
            # "Save report to Documents": the named file of the open message.
            index = named_file(mail, found, what)
            if index is None:
                return None
            save(m, [found[index]], spoken_folder(folder_text) if folder_text else default_folder(m), named=bool(folder_text))
            return mode
        if not every and len(found) > 1:
            m.speak('This message has ' + files_words.count_words(len(found)) + '. Choose one and press Control+S, '
                    'or say save all attachments.')
            return open_for_message(m, mode)
        save(m, found, spoken_folder(folder_text) if folder_text else default_folder(m), named=bool(folder_text))
        return mode
    # "Open report", "open the PDF" in an open message: its file, when the name fits only that file.
    opened = re.fullmatch(r'open (?P<what>.+)', command)    # not "play": "play the news" may be the radio
    if opened and mail.view == 'message' and isinstance(getattr(mail, 'attachments', None), list) and mail.attachments:
        index = named_file(mail, mail.attachments, opened['what'])
        if index is not None:
            found = load_message(m)
            if found:
                return open_item(m, found[index], mode)
            return mode
    return None


def named_file(mail, found, what):
    """The one file of the open message whose name fits; None when none or several fit."""
    if mail.view != 'message':
        return None
    matches = files_words.name_matches([item['name'] for item in found], strip_target(what), loose=False)
    return matches[0] if len(matches) == 1 else None


def load_message(m):
    """The open message's files, with this list set to them; [] after saying why there are none."""
    global SOURCE, RETURN_MODE, MESSAGE
    from mail_accounts import AccountError
    from mailbox_access import MailboxError
    try:
        message, found = message_items(m)
    except (MailboxError, AccountError) as exc:
        m.speak(str(exc))
        return []
    if not found:
        m.speak('This message has no attachments.')
        return []
    SOURCE, RETURN_MODE, MESSAGE = 'message', 'mailbox', message
    _set(found)
    return found


def tell_message_files(m, mode):
    found = load_message(m)
    if found:
        m.speak(files_words.summary(found) + total_words(found))
    return mode


def total_words(items):
    if len(items) < 2:
        return ''
    known = all(isinstance(item.get('size'), (int, float)) for item in items)
    return (' Together they are ' + ('about ' if not known else '') +
            files_words.size_words(files_words.total_size(items)) + '.')


def draft_command(m, raw, command, mode, typed=False):
    draft = m.email_draft
    if draft is None or m.pending_send is not None and command in m.SEND_REVIEW_ANSWERS:
        return None
    if command in ATTACH_PHRASES:
        m.pending_send = None
        return pick(m, mode)
    if command in LIST_PHRASES + MAIL_EXTRA:
        m.pending_send = None
        return open_for_draft(m, mode)
    if command in REMOVE_PHRASES:
        m.pending_send = None
        return open_for_draft(m, mode, 'Choose a file, then press Delete. ')
    if command in TOTAL + ('attachment details', 'read attachment details', 'how big is the attachment'):
        found = draft.all_attachments()
        m.speak((files_words.summary(found) + total_words(found) +
                 files_words.limit_warning(files_words.total_size(found))) if found else
                'This email has no attachments. Say attach a file, or press Control+T.')
        return mode
    if command in LAST_DOWNLOAD:
        m.pending_send = None
        return attach_last_download(m, mode)
    # "Attach my resume from Documents". Without a folder, only where words are not writing.
    wanted = re.fullmatch(r'(?:please )?attach (?P<what>.+?) from (?P<folder>.+?)[.!?]*', raw.strip(), re.I)
    if wanted and looks_like_folder(wanted['folder']):
        m.pending_send = None
        return attach_by_name(m, wanted['what'], wanted['folder'], mode)
    wanted = re.fullmatch(r'attach (?P<what>.+)', command)
    if wanted and not m.writing_dictation(mode, typed) and command not in ATTACH_TO_DRAFT:
        m.pending_send = None
        return attach_by_name(m, wanted['what'], None, mode)
    # "Remove photo", "remove attachment report.pdf": only a name that fits one attached file.
    named = (re.fullmatch(r'(?:remove|take off|detach|delete (?:the )?attachment|delete (?:the )?attached file) (?P<what>.+)', command)
             or re.fullmatch(r'take (?P<what>.+) off', command))
    if named:
        found = draft.all_attachments()
        matches = files_words.name_matches([item['name'] for item in found], strip_target(named['what']), loose=False)
        if not matches:
            return None    # "Remove the attachment from last week." is writing, not a command.
        m.pending_send = None
        if len(matches) > 1:
            return open_for_draft(m, mode, str(len(matches)) + ' files match ' + files_words.spoken_name(strip_target(named['what']))
                                  + '. Choose one, then press Delete. ', matches[0])
        m.speak(remove(m, found, matches[0]))
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
            folder = spoken_folder(re.sub(r'^(?:save (?:all )?(?:the )?(?:attachments?|it|them) )?(?:to|in|into)\s+', '', raw, flags=re.I))
        except AttachmentError as exc:
            m.speak(str(exc) + ' Escape cancels.')
            return MODE
        PENDING = None
        save(m, items, folder, named=True)
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
    if command in ('where am i', 'attachments', 'attachment'):
        m.speak(header() + describe())
        return MODE
    if command in LIST_PHRASES:
        m.speak(files_words.summary(ITEMS) + ' ' + describe())
        return MODE
    moves = {'next': 1, 'next attachment': 1, 'down': 1, 'next file': 1, 'previous': -1, 'previous attachment': -1,
             'up': -1, 'previous file': -1}
    ends = {'home': 0, 'first attachment': 0, 'start of list': 0, 'top of list': 0, 'first item': 0, 'first file': 0,
            'end': -1, 'last attachment': -1, 'end of list': -1, 'bottom of list': -1, 'last item': -1, 'last file': -1}
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
    if command in SIZE:
        size = item.get('size')
        m.speak(item['name'] + ' is ' + files_words.size_words(size) + '.' if isinstance(size, (int, float)) and size >= 0
                else 'The size of ' + item['name'] + ' is not known.')
        return MODE
    if command in TOTAL:
        m.speak(files_words.count_words(len(ITEMS)) + '. ' + (total_words(ITEMS).strip() or describe()) +
                (files_words.limit_warning(files_words.total_size(ITEMS)) if SOURCE == 'draft' else ''))
        return MODE
    if command in DETAILS:
        m.speak(details(item, INDEX))
        return MODE
    if command in OPEN_ONE:
        return open_item(m, item, MODE)
    if command in FOLDER_ASK:
        PENDING = ('folder', [item])
        m.speak('Save ' + item['name'] + ' to a folder. Type or say a folder, such as Documents or Desktop, '
                'or type its full path. Then press Enter. Escape cancels.')
        return MODE
    if command in ('save all to folder', 'save all to a folder', 'save all attachments to a folder', 'save them to a folder'):
        PENDING = ('folder', list(ITEMS))
        m.speak('Save ' + files_words.count_words(len(ITEMS)) + ' to a folder. Type or say a folder, such as Documents '
                'or Desktop, or type its full path. Then press Enter. Escape cancels.')
        return MODE
    if command in SAVE_ONE + ('save', 'download', 'download it'):
        save(m, [item], default_folder(m))
        return MODE
    if command in SAVE_ALL:
        save(m, ITEMS, default_folder(m))
        return MODE
    request = save_request(raw)
    if request is not None:
        what, folder_text = request
        if what in EVERY:
            chosen = list(ITEMS)
        else:
            index = find_items(m, what)
            if index is None:
                return MODE
            INDEX = index
            chosen = [ITEMS[index]]
        save(m, chosen, spoken_folder(folder_text) if folder_text else default_folder(m), named=bool(folder_text))
        return MODE
    if command in REMOVE_PHRASES + ('delete', 'remove', 'remove file', 'take it off', 'remove it', 'delete it',
                                    'remove this', 'remove this one', 'take this off'):
        return remove_here(m, INDEX)
    removing = (re.fullmatch(r'(?:remove|delete|take off|detach|drop) (?P<what>.+)', command)
                or re.fullmatch(r'take (?P<what>.+) off', command))
    if removing:
        index = find_items(m, removing['what'])
        return MODE if index is None else remove_here(m, index)
    if command in ATTACH_PHRASES or command in LAST_DOWNLOAD or command.startswith('attach '):
        if SOURCE != 'draft':
            m.speak('To attach a file, start a reply or forward first.')
            return MODE
        if command in ATTACH_PHRASES:
            return pick(m, 'email_draft')
        if command in LAST_DOWNLOAD:
            return attach_last_download(m, MODE)
        wanted = re.fullmatch(r'(?:please )?attach (?P<what>.+?) from (?P<folder>.+?)[.!?]*', raw.strip(), re.I)
        if wanted and looks_like_folder(wanted['folder']):
            return attach_by_name(m, wanted['what'], wanted['folder'], MODE)
        return attach_by_name(m, command[len('attach '):], None, MODE)
    opening = re.fullmatch(r'(?:open|play|launch) (?P<what>.+)', command)
    if opening:
        index = find_items(m, opening['what'])
        if index is None:
            return MODE
        INDEX = index
        return open_item(m, ITEMS[index], MODE)
    going = re.fullmatch(r'(?:go to|move to|find|select|choose|jump to) (?P<what>.+)', command)
    if going:
        index = find_items(m, going['what'])
        if index is not None:
            INDEX = index
            m.speak(describe())
        return MODE
    # A file name, or its first words, moves to that file.
    words = files_words.spoken_name(command)
    matches = files_words.name_matches([entry['name'] for entry in ITEMS], words, loose=False) if words else []
    if len(matches) == 1:
        INDEX = matches[0]
        m.speak(describe())
        return MODE
    m.speak('Attachments. Press Enter to open, Control+S to save, or Escape to go back. Press F1 for help.')
    return MODE


def details(item, index):
    """'report.pdf. PDF document, 1.2 megabytes. Attachment 1 of 2. In your Documents folder.'"""
    kind = files_words.kind_words(item['name'])
    said = (item['name'] + '. ' + kind[0].upper() + kind[1:] + ', ' + files_words.size_words(item.get('size')) +
            '. Attachment ' + str(index + 1) + ' of ' + str(len(ITEMS)) + '.')
    if SOURCE == 'draft' and item.get('original'):
        said += ' It comes from the message you are forwarding.'
    elif SOURCE == 'draft' and item.get('path'):
        folder = Path(item['path']).parent
        said += ' It is in ' + where(folder) + '.' + ('' if Path(item['path']).is_file() else
                                                       ' The file is no longer there. Remove it, or put it back before you send.')
    if kind == 'program':
        said += ' Only open programs from people you trust.'
    return said


def remove_here(m, index):
    global INDEX
    if SOURCE != 'draft':
        m.speak('Attachments of a message you received stay with it. To take a file off a forward, '
                'remove it from the Attachments list of the draft.')
        return MODE
    said = remove(m, ITEMS, index)
    if not ITEMS:
        m.speak(said)
        return back(m)
    m.speak(said + ' ' + describe())
    return MODE


def remove(m, items, index):
    """Take one file off the draft. Returns what to say."""
    draft = m.email_draft
    item = items[index]
    originals = len(draft.original_attachments())
    if index < originals:
        draft.drop_original(item['name'], item.get('id'))
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
        raise AttachmentError(item['name'] + ' comes from the message you are forwarding. To open or save it, '
                              'open that message in Email, then press L.')
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


def save(m, items, folder, named=False):
    """Save files in a folder and say where. A name already there gets " (1)".

    named: the person chose the folder, so a draft's own file is copied there too."""
    from mail_accounts import AccountError
    from mailbox_access import MailboxError
    import user_stop
    if SOURCE == 'draft' and not named and all(not item.get('original') for item in items) and len(items) == 1:
        m.speak(items[0]['name'] + ' is already on this computer, in ' + where(Path(items[0]['path']).parent) + '.')
        return
    saved = []
    item = items[0] if items else None
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
        m.speak(item['name'] + ' could not be saved. ' + where(folder)[0].upper() + where(folder)[1:] + ' may be full or locked. '
                'Try another folder, such as Desktop.' + done_words(saved, folder, ' Already saved: '))
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
            raise AttachmentError(item['name'] + ' is no longer in its folder. Remove it from the email, or put it back.')
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


def attached(m, paths, mode):
    """Attach files on this computer to the open draft and say so; stay on this list or the draft."""
    draft = m.email_draft
    if draft is None:
        m.speak('The email draft is closed, so nothing was attached.')
        return mode
    try:
        said = draft.attach(paths)
    except OSError:
        m.speak('The draft could not be saved. Nothing was attached. Try again.')
        return mode
    if mode == MODE:
        names = [os.path.normcase(str(Path(p))) for p in paths]
        found = draft.all_attachments()
        index = next((i for i, item in enumerate(found) if item.get('path') and os.path.normcase(item['path']) in names),
                     len(found) - 1)
        _set(found, index)
        m.speak(said + ' ' + describe())
        return MODE
    m.focus_email_entry()
    m.speak(said)
    return 'email_draft'


def last_download(m):
    """The newest finished download that is still on this computer, or None."""
    try:
        records = list(__import__('download_screen').manager(m).items)
    except Exception:
        return None
    for record in records:
        try:
            if record.state == 'finished' and record.path and Path(record.path).is_file():
                return Path(record.path)
        except OSError:
            continue
    return None


def attach_last_download(m, mode):
    found = last_download(m)
    if found is None:
        m.speak('There is no finished download to attach. Files you download in the web browser are listed in the '
                'download manager. Say attach a file to choose one in File manager.')
        return mode
    return attached(m, [found], mode)


def search(folders, said):
    """(matching files newest first, True when the search stopped early) in the folders and their subfolders."""
    started, seen, partial, entries = time.monotonic(), 0, False, []
    for folder in folders:
        base_depth = len(Path(folder).parts)
        for top, subfolders, names in os.walk(folder, onerror=lambda error: None):
            subfolders[:] = [name for name in subfolders if not name.startswith(('.', '~', '$'))
                             and name.casefold() not in SKIP_FOLDERS and len(Path(top).parts) - base_depth < SEARCH_DEPTH]
            for name in names:
                seen += 1
                if name.startswith(('.', '~$')) or name.casefold().endswith(SKIP_ENDINGS) \
                        or name.casefold() in ('desktop.ini', 'thumbs.db'):
                    continue
                entries.append((Path(top) / name, folder))
            if seen > SEARCH_LIMIT or time.monotonic() - started > SEARCH_SECONDS:
                partial = True
                break
        if partial:
            break
    places = files_words.name_matches([path.name for path, _ in entries], said)
    found = [entries[i] for i in places]
    def newest(entry):
        try:
            return -entry[0].stat().st_mtime
        except OSError:
            return 0
    return sorted(found, key=newest), partial


def attach_by_name(m, what, folder_text, mode):
    """"Attach my resume from Documents": one match is attached; several are offered in a short list."""
    global CANDIDATES
    from downloads import spoken_path
    said = files_words.spoken_name(strip_target(files_words.plain_words(what)))
    if not said:
        return pick(m, mode)
    if folder_text is not None:
        key = place_key(spoken_path(folder_text))
        folders = [spoken_folder(folder_text)]
        named = ('your ' + PLACES[key][1] + ' folder') if key else where(folders[0])
    else:
        folders = [path for path in (place(key) for key in ('documents', 'desktop', 'downloads', 'pictures', 'music', 'videos'))
                   if path.is_dir()]
        named = 'your Documents, Desktop, Downloads, Pictures, Music or Videos folders'
    found, partial = search(folders, said)
    if not found:
        m.speak('No file called ' + said + ' is in ' + named + '.' +
                (' That folder is very large, so only part of it was searched.' if partial else '') +
                ' Say attach a file to choose one in File manager.')
        return mode
    if len(found) == 1:
        return attached(m, [found[0][0]], mode)
    CANDIDATES = [path for path, _ in found[:CHOICES_SHOWN]]
    items = [(choice_label(path, base), '@attachments:attach:' + str(i)) for i, (path, base) in enumerate(found[:CHOICES_SHOWN])]
    more = (' Here are the ' + str(CHOICES_SHOWN) + ' newest.') if len(found) > CHOICES_SHOWN else ''
    m.speak(str(len(found)) + ' files match ' + said + '.' + more + ' Choose one and press Enter. ' +
            m.open_named_list('Files to attach', items))
    return mode


def choice_label(path, base):
    """'resume.docx, in Documents, Jobs, 30 kilobytes' for the short list of matches."""
    try:
        size = files_words.size_words(path.stat().st_size)
    except OSError:
        size = 'size unknown'
    try:
        inside = list(path.parent.relative_to(base).parts)
    except ValueError:
        inside = []
    return path.name + ', in ' + ', '.join([Path(base).name or str(base)] + inside) + ', ' + size


def attach_choice(m, raw, mode):
    """Enter in the short list of matching files."""
    try:
        path = CANDIDATES[int(raw.rsplit(':', 1)[1])]
    except (ValueError, IndexError):
        m.speak('That file is no longer in the list. Say attach again.')
        return mode
    if mode not in (MODE, 'email_draft'):
        mode = 'email_draft'
    return attached(m, [path], mode)


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
    m.speak(said + ' Back to email draft. ' + field_words(m))
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
