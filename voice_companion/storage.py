"""Small rolling recovery copies for locally edited files."""
import os
import shutil
import sys
from pathlib import Path


def keep_previous(path: Path, count=3):
    if not path.exists():
        return
    try:
        for number in range(count, 1, -1):
            older = path.with_name(path.name + f'.backup{number - 1}')
            newer = path.with_name(path.name + f'.backup{number}')
            if older.exists():
                os.replace(older, newer)
        newest = path.with_name(path.name + '.backup1')
        temporary = path.with_name(path.name + '.backup-writing')
        shutil.copy2(path, temporary)
        os.replace(temporary, newest)
    except OSError as exc:
        print('Could not update recovery copy:', exc, file=sys.stderr)
        # The primary save still proceeds. Its contents are written to a
        # separate temporary file and then atomically replace the old file.


def write_text_atomic(path, text, encoding='utf-8', encoding_errors=None):
    """Write a small settings file so a crash or power cut never leaves it half written.

    The text goes to a temporary file in the same folder, which then replaces the
    old file in one step. Readers see either the old contents or the new ones.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    try:
        with open(temporary, 'w', encoding=encoding, errors=encoding_errors) as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        # A full disk, or the file open in another program: the old file is untouched,
        # and no half-written copy is left beside it.
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def read_saved_list(path, empty):
    """A small saved JSON list (web favorites, web history): (data, damaged text).

    A missing file is empty. A file that cannot be read now (Windows: an antivirus
    scan or OneDrive holds it) raises OSError, so a caller never writes over it with
    a short list. A damaged file gives `empty` and its text, for keep_damaged.

    Bytes that are not UTF-8 (a disk error, or another program wrote the file) make
    the file damaged; they are kept exactly as they were, never raised as an error."""
    import json
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8', errors='surrogateescape')
    except FileNotFoundError:
        return empty, None
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return empty, text
    try:
        # Notepad may save the file with a byte order mark; it is not damage.
        return json.loads(text[1:] if text.startswith('\ufeff') else text), None
    except ValueError:
        return empty, text


def keep_damaged(path, text):
    """Keep a damaged saved list beside it ("web-history.damaged.json") before it is replaced."""
    path = Path(path)
    write_text_atomic(path.with_name(path.stem + '.damaged' + path.suffix), text, encoding_errors='surrogateescape')
