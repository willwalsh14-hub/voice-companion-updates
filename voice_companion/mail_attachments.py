"""Email attachments: plain spoken sizes and lists, safe file names, and saving.

Shared by the Thunderbird and Yahoo mailboxes, the Attachments list
(attachment_screen.py) and email drafts. Nothing here talks to a mail service.

Saving never replaces a file that is already there: a second "report.pdf"
becomes "report (1).pdf". The bytes go to a temporary file in the same folder
first, and only a complete file gets the real name, so a failed or stopped save
never leaves half a file under that name.
"""
import os
import re
import tempfile
import time
from pathlib import Path

KB = 1024
MB = 1024 * KB
# Gmail (through Thunderbird) and Yahoo take about 25 megabytes per message. Email
# encoding makes files about a third larger, so the warning uses that size.
PROVIDER_LIMIT = 25 * MB
# The most one email draft may carry. Bigger than any mail service accepts.
MAX_DRAFT_TOTAL = 50 * MB
# Attachments bigger than this say "Saving" while they save.
SLOW_SIZE = 2 * MB
# How many names a spoken summary lists before "and 3 more".
SPOKEN_NAMES = 5
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


class AttachmentError(Exception):
    """An attachment could not be read, saved or attached. The text is spoken."""


def size_words(size):
    """A size in plain words: "512 bytes", "300 kilobytes", "1.2 megabytes"."""
    if not isinstance(size, (int, float)) or size < 0:
        return 'size unknown'
    size = int(size)
    if size == 1:
        return '1 byte'
    if size < KB:
        return str(size) + ' bytes'
    for unit, scale in (('gigabytes', 1024 * MB), ('megabytes', MB), ('kilobytes', KB)):
        if size >= scale:
            value = size / scale
            if unit == 'kilobytes' or value >= 10:
                text = str(int(round(value)))
            else:
                text = ('%.1f' % value).rstrip('0').rstrip('.')
            if text == '1':
                unit = unit[:-1]
            return text + ' ' + unit
    return str(size) + ' bytes'


def count_words(count):
    return str(count) + (' attachment' if count == 1 else ' attachments')


def summary(items):
    """'2 attachments: report.pdf, 1.2 megabytes; photo.jpg, 300 kilobytes.'"""
    items = list(items or [])
    if not items:
        return 'No attachments.'
    said = [item['name'] + ', ' + size_words(item.get('size')) for item in items[:SPOKEN_NAMES]]
    more = len(items) - len(said)
    return count_words(len(items)) + ': ' + '; '.join(said) + ('; and ' + str(more) + ' more.' if more else '.')


def names(items):
    """'report.pdf, photo.jpg' for the send review."""
    items = list(items or [])
    shown = [item['name'] for item in items[:SPOKEN_NAMES]]
    more = len(items) - len(shown)
    return ', '.join(shown) + (', and ' + str(more) + ' more' if more else '')


def label(item, index, count):
    """One line of the Attachments list: 'report.pdf, 1.2 megabytes, 1 of 2.', like every other list."""
    return item['name'] + ', ' + size_words(item.get('size')) + ', ' + str(index + 1) + ' of ' + str(count) + '.'


def file_count(count):
    return str(count) + (' file' if count == 1 else ' files')


def joined(words):
    """'a', 'a and b', 'a, b and c'; after five names, 'and 3 more'."""
    words = list(words)
    shown, more = words[:SPOKEN_NAMES], len(words) - SPOKEN_NAMES
    if more > 0:
        return ', '.join(shown) + ' and ' + str(more) + ' more'
    if len(shown) < 2:
        return ''.join(shown)
    return ', '.join(shown[:-1]) + ' and ' + shown[-1]


# Spoken names ------------------------------------------------------------------
# People say "report dot PDF", "the photo", "my resume" or "the spreadsheet".

KINDS = (
    (('pdf', 'pdf file', 'pdf document', 'acrobat file'), ('.pdf',), 'PDF document'),
    (('photo', 'picture', 'image', 'pic', 'photograph', 'snapshot', 'jpeg', 'jpg'),
     ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic', '.heif', '.webp', '.tif', '.tiff'), 'picture'),
    (('word document', 'word file', 'document', 'doc', 'letter'), ('.docx', '.doc', '.odt', '.rtf'), 'Word document'),
    (('spreadsheet', 'excel file', 'excel spreadsheet', 'workbook', 'sheet'), ('.xlsx', '.xls', '.xlsm', '.ods', '.csv'), 'spreadsheet'),
    (('presentation', 'slides', 'slide show', 'powerpoint'), ('.pptx', '.ppt', '.odp'), 'presentation'),
    (('text file', 'text', 'note', 'notes'), ('.txt', '.md'), 'text file'),
    (('video', 'movie', 'clip', 'video clip'), ('.mp4', '.mov', '.m4v', '.avi', '.wmv', '.mkv', '.webm'), 'video'),
    (('song', 'audio', 'sound', 'recording', 'voice message', 'music', 'audio file'),
     ('.mp3', '.m4a', '.wav', '.wma', '.ogg', '.flac', '.aac', '.opus'), 'sound file'),
    (('zip', 'zip file', 'archive', 'compressed file', 'compressed folder'), ('.zip', '.7z', '.rar', '.gz'), 'compressed folder'),
    # Not "message" or "email" alone: "open message" in Email is about the message itself.
    (('attached message', 'attached email', 'forwarded message', 'forwarded email', 'eml'), ('.eml', '.msg'), 'email message'),
    (('web page', 'webpage', 'html file'), ('.html', '.htm'), 'web page'),
    (('calendar invitation', 'invitation', 'invite', 'calendar file'), ('.ics',), 'calendar invitation'),
    (('contact card', 'business card', 'vcard'), ('.vcf',), 'contact card'),
)


def plain_words(value):
    """Lower case words only: 'Résumé_2026 (final).PDF' gives 'resume 2026 final pdf'."""
    import unicodedata
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[\W_]+', ' ', text).split())


def spoken_name(value):
    """What was said, ready to compare with file names: no "the", "dot" made a break."""
    text = ' ' + plain_words(value) + ' '
    text = re.sub(r' (?:dot|period|point|underscore|dash|hyphen|minus) ', ' ', text)
    words = text.split()
    while words and words[0] in ('the', 'my', 'a', 'an', 'this', 'that', 'our', 'your'):
        words = words[1:]
    while len(words) > 1 and words[-1] in ('file', 'attachment', 'attached'):
        words = words[:-1]
    return ' '.join(words)


def kind_of(name):
    """The KINDS row for a file name, or None."""
    suffix = Path(str(name)).suffix.casefold()
    return next((row for row in KINDS if suffix in row[1]), None)


def kind_words(name):
    """'PDF document', 'picture', 'ZIP file' or 'file': what kind of file it is, in words."""
    row = kind_of(name)
    if row is not None:
        return row[2]
    if kind(name) == 'program':
        return 'program'
    suffix = Path(str(name)).suffix.lstrip('.')
    return (suffix.upper() + ' file') if suffix and len(suffix) <= 6 and suffix.isalnum() else 'file'


def name_matches(names, said, loose=True):
    """The places in names of the files that best fit what was said, best first.

    Exact name, then exact name without its ending, then a kind ("the PDF", "the photo")
    when only those files have it, then names that start with the words, then (when
    loose) names that hold every word. An empty list: nothing fits."""
    wanted = spoken_name(said)
    if not wanted:
        return []
    keys = [plain_words(name) for name in names]
    stems = [plain_words(Path(str(name)).stem) if Path(str(name)).suffix else plain_words(name) for name in names]
    for test in (lambda i: keys[i] == wanted, lambda i: stems[i] == wanted):
        found = [i for i in range(len(names)) if test(i)]
        if found:
            return found
    kinds = [row for row in KINDS if wanted in row[0]]
    if kinds:
        found = [i for i, name in enumerate(names) if Path(str(name)).suffix.casefold() in kinds[0][1]]
        if found:
            return found
    found = [i for i in range(len(names)) if (keys[i] + ' ').startswith(wanted + ' ')]
    if found or not loose:
        return found
    words = wanted.split()
    return [i for i in range(len(names)) if all(word in keys[i].split() for word in words)]


def total_size(items):
    return sum(item.get('size') or 0 for item in items or [] if isinstance(item.get('size'), (int, float)))


def too_large_for_mail(total):
    """True when files of this total size, once encoded for email, pass the usual 25 megabyte limit."""
    return total * 4 / 3 > PROVIDER_LIMIT


def limit_warning(total):
    if not too_large_for_mail(total):
        return ''
    return (' The attachments total ' + size_words(total) + '. That may be too large to send. '
            'Gmail and Yahoo accept about 25 megabytes, and email makes files about a third larger.')


# File names ------------------------------------------------------------------

def safe_filename(name, fallback='attachment'):
    """A name Windows accepts, without folders: never "..\\..\\Startup\\x.exe"."""
    name = str(name or '')
    name = re.split(r'[\\/]', name)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
    name = ' '.join(name.split()).strip(' .')
    if not name:
        name = fallback
    stem, dot, suffix = name.rpartition('.')
    if not dot:
        stem, suffix = name, ''
    if stem.split('.')[0].upper() in RESERVED or name.split('.')[0].upper() in RESERVED:
        name = '_' + name
    if len(name) > 150:
        suffix = ('.' + suffix) if dot and len(suffix) <= 20 else ''
        name = name[:150 - len(suffix)].rstrip(' .') + suffix
    return name


def numbered(name, number):
    """'report.pdf' and 1 give 'report (1).pdf'."""
    if not number:
        return name
    path = Path(name)
    suffix = path.suffix if path.stem else ''
    stem = name[:-len(suffix)] if suffix else name
    return stem + ' (' + str(number) + ')' + suffix


def free_name(folder, name):
    """The first of name, 'name (1)', 'name (2)' ... not in the folder."""
    folder = Path(folder)
    number = 0
    while (folder / numbered(name, number)).exists():
        number += 1
    return folder / numbered(name, number)


def kind(name):
    """'document', 'media', 'program' or 'other', from the file name's ending."""
    from task_navigation import DOCUMENT_TYPES, MEDIA_TYPES
    from document_reader import SUPPORTED
    from downloads import PROGRAM_TYPES
    suffix = Path(str(name)).suffix.casefold()
    if suffix in PROGRAM_TYPES:
        return 'program'
    if suffix in MEDIA_TYPES:
        return 'media'
    if suffix in SUPPORTED or suffix in DOCUMENT_TYPES:
        return 'document'
    return 'other'


# Saving ------------------------------------------------------------------------

def _place(temporary, folder, name):
    """Give the finished file its name without ever replacing another file."""
    for _ in range(1000):
        target = free_name(folder, name)
        try:
            os.link(temporary, target)       # fails if the name was taken a moment ago
        except FileExistsError:
            continue
        except OSError:
            # Drives without links (some USB sticks): Windows rename refuses to replace.
            if target.exists():
                continue
            try:
                os.rename(temporary, target)
            except FileExistsError:
                continue
            return target
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return target
    raise AttachmentError('Too many files have that name in this folder. Choose another folder.')


def save_stream(folder, name, chunks, progress=None, expected=None, stop=None):
    """Write the byte chunks to a new file in folder. Returns the file's path.

    progress(bytes_written) is called now and then. stop(), when given, is called
    between chunks and may raise to stop the save; nothing is left behind."""
    folder = Path(folder)
    if not folder.is_dir():
        raise AttachmentError('That folder is not available. Check the folder and try again.')
    name = safe_filename(name)
    try:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix='.voice-companion-saving-', suffix='.tmp')
    except OSError as exc:
        raise AttachmentError('Voice Companion cannot save files in ' + folder.name + '. Choose another folder.') from exc
    written, last = 0, time.monotonic()
    try:
        with os.fdopen(handle, 'wb') as file:
            for chunk in chunks:
                if stop is not None:
                    stop()
                if chunk:
                    file.write(chunk)
                    written += len(chunk)
                if progress is not None and time.monotonic() - last >= 1:
                    last = time.monotonic()
                    progress(written)
            file.flush()
            os.fsync(file.fileno())
        return _place(temporary, folder, name)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def save_file_copy(source, folder, name=None):
    """Copy a file on this computer into folder under a free name."""
    source = Path(source)
    def chunks():
        with open(source, 'rb') as file:
            while True:
                block = file.read(MB)
                if not block:
                    return
                yield block
    return save_stream(folder, name or source.name, chunks())


def opened_folder(app_folder):
    """Where attachments opened from a message are kept. Copies older than two days are removed."""
    folder = Path(app_folder) / 'Opened attachments'
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for item in folder.iterdir():
        try:
            if item.is_file() and now - item.stat().st_mtime > 2 * 24 * 3600:
                item.unlink()
        except OSError:
            pass
    return folder


# Files attached to a draft -------------------------------------------------------

def local_attachment(path):
    """The record a draft keeps for a file on this computer. Refuses folders and missing files."""
    path = Path(path)
    if path.is_dir():
        raise AttachmentError(path.name + ' is a folder. Choose files, not folders.')
    if not path.is_file():
        raise AttachmentError(path.name + ' is no longer there. It may have been moved or deleted.')
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AttachmentError(path.name + ' could not be read. It may be open in another program.') from exc
    return {'path': str(path), 'name': safe_filename(path.name), 'size': size}


def missing(items):
    """Attached files on this computer that are no longer there."""
    gone = []
    for item in items or []:
        path = item.get('path')
        if not path:
            continue
        try:
            if not Path(path).is_file():
                gone.append(item)
        except OSError:
            gone.append(item)
    return gone


def missing_message(gone):
    if not gone:
        return ''
    if len(gone) == 1:
        return (gone[0]['name'] + ' is no longer in its folder, so this email was not sent. '
                'Put the file back, or remove it from the email with Control+L and Delete.')
    return (names(gone) + ' are no longer in their folders, so this email was not sent. '
            'Put the files back, or remove them from the email with Control+L and Delete.')


def read_local(item):
    """The bytes of an attached file, or a plain AttachmentError."""
    path = Path(item['path'])
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise AttachmentError(missing_message([item])) from exc
    except OSError as exc:
        raise AttachmentError(item['name'] + ' could not be read. It may be open in another program. '
                              'Close it, then try again. The email was not sent.') from exc


def mime_type(name):
    import mimetypes
    found, _ = mimetypes.guess_type(str(name))
    # A message/rfc822 part may not be base64; an attached .eml goes as plain file data.
    if not found or '/' not in found or found.startswith(('message/', 'multipart/')):
        return 'application', 'octet-stream'
    return tuple(found.split('/', 1))
