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
