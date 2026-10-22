"""Saved notes: dated entries in one plain text file, listed and reopened by voice or keyboard.

Each note is written as a blank line, then "[YYYY-MM-DD HH:MM] text". Older copies of
the app wrote the same format, so their notes list and open too. Every write
replaces the whole file through a temporary file, so a crash never leaves half a note.
A file with damaged characters keeps its exact bytes.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from storage import keep_previous, write_text_atomic

HEADER = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] ', re.M)
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')
# Bytes that are not valid text survive a read and a write unchanged.
ERRORS = 'surrogateescape'


@dataclass(frozen=True)
class Note:
    position: int  # Order in the file, oldest first.
    stamp: str
    text: str
    start: int     # Where the text begins in the file.


def notes_path(folder):
    return Path(folder) / 'notes.txt'


def read_raw(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors=ERRORS)
    except FileNotFoundError:
        return ''


def parse(content):
    found = list(HEADER.finditer(content))
    notes = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(content)
        notes.append(Note(index, match[1], content[match.end():end].rstrip('\n'), match.end()))
    return notes


def read_notes(folder):
    return parse(read_raw(notes_path(folder)))


def newest_first(notes):
    return list(reversed(notes))


def spoken_date(stamp):
    try:
        moment = datetime.strptime(stamp, '%Y-%m-%d %H:%M')
    except ValueError:
        return stamp
    hour = moment.hour % 12 or 12
    return (MONTHS[moment.month - 1] + ' ' + str(moment.day) + ', ' + str(moment.year) + ', '
            + str(hour) + ':' + f'{moment.minute:02d}' + (' PM' if moment.hour >= 12 else ' AM'))


def preview(text, words=8):
    parts = text.split()
    if not parts:
        return 'Empty note'
    return ' '.join(parts[:words]) + ('...' if len(parts) > words else '')


def label(note):
    return preview(note.text) + ', ' + spoken_date(note.stamp)


def add(folder, text, now=None):
    """Save a new note at the end of the file."""
    path = notes_path(folder)
    stamp = f'{now or datetime.now():%Y-%m-%d %H:%M}'
    write_text_atomic(path, read_raw(path) + f'\n[{stamp}] {text}\n', encoding_errors=ERRORS)
    return stamp


def replace(folder, opened, text):
    """Save changes to an opened note in its own place.

    Returns False if that note is no longer in the file as it was opened (for
    example, the file was changed in another program). The caller then saves the
    text as a new note, so nothing is lost.
    """
    path = notes_path(folder)
    content = read_raw(path)
    current = next((n for n in parse(content) if n.position == opened.position), None)
    if current is None or current.stamp != opened.stamp or current.text != opened.text:
        return False
    if text == opened.text:
        return True
    keep_previous(path)
    end = current.start + len(current.text)
    write_text_atomic(path, content[:current.start] + text + content[end:], encoding_errors=ERRORS)
    return True
