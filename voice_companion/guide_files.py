"""Publish the shipped accessible manuals to the user's Documents folder."""
from pathlib import Path
import os
import shutil
import sys
import tempfile

GUIDE_NAMES = ('START HERE - Veteran.txt', 'START HERE - Veteran.html',
               'START HERE - Veteran.docx', 'START HERE - Veteran.epub',
               'START HERE - Veteran - DAISY 3.zip')


def guide_sources(roots=None):
    if roots is None:
        roots = [Path(sys.executable).parent] if getattr(sys, 'frozen', False) else []
        roots += [Path(getattr(sys, '_MEIPASS', Path(__file__).parent))]
    sources = {}
    for name in GUIDE_NAMES:
        source = next((Path(root) / name for root in roots if (Path(root) / name).is_file()), None)
        if source is None:
            raise FileNotFoundError('The packaged user guide is missing: ' + name)
        sources[name] = source
    return sources


def publish_guides(documents, roots=None):
    sources = guide_sources(roots)
    target = Path(documents) / 'User Guides'
    target.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        destination = target / name
        if destination.is_file() and destination.read_bytes() == source.read_bytes():
            continue
        # Replace complete copies only; never leave a half-written guide after interruption.
        descriptor, temporary = tempfile.mkstemp(prefix='guide-', suffix='.tmp', dir=target)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return target
