"""Publish the shipped accessible manuals to the user's Documents folder."""
from pathlib import Path
import os
import hashlib
import json
import shutil
import sys
import tempfile

GUIDE_NAMES = ('START HERE - Veteran.txt', 'START HERE - Veteran.html',
               'START HERE - Veteran.docx', 'START HERE - Veteran.epub',
               'START HERE - Veteran - DAISY 3.zip', 'START HERE - Veteran.brf',
               'Voice Companion Release Notes.txt', 'Voice Companion Release Notes.html',
               'Voice Companion Release Notes.docx', 'Voice Companion Release Notes.epub',
               'Voice Companion Release Notes - DAISY 3.zip', 'Voice Companion Release Notes.brf')


MANIFEST_NAME = 'documentation-manifest.json'


def write_manifest(root, version):
    root=Path(root)
    data={'version':version,'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in GUIDE_NAMES}}
    (root/MANIFEST_NAME).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    return data


def guide_sources(roots=None, expected_version=None):
    packaged = roots is None
    if roots is None:
        roots = [Path(getattr(sys, '_MEIPASS', Path(__file__).parent))]
        if getattr(sys, 'frozen', False):roots.append(Path(sys.executable).parent)
    manifest=None
    if packaged or expected_version is not None:
        for root in roots:
            path=Path(root)/MANIFEST_NAME
            if not path.is_file():continue
            candidate=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(candidate,dict) or not isinstance(candidate.get('files'),dict):
                raise ValueError('The documentation manifest is invalid.')
            if expected_version is None or candidate.get('version')==expected_version:
                manifest=candidate;break
        if manifest is None:raise ValueError('Documentation for this application version is missing.')
        if set(manifest.get('files',{}))!=set(GUIDE_NAMES):raise ValueError('Documentation manifest has incomplete formats.')
    sources = {}
    for name in GUIDE_NAMES:
        source = next((Path(root) / name for root in roots
                       if (Path(root) / name).is_file() and
                       (manifest is None or hashlib.sha256((Path(root)/name).read_bytes()).hexdigest()==manifest['files'][name])), None)
        if source is None:
            raise FileNotFoundError('The packaged user guide is missing: ' + name)
        sources[name] = source
    return sources


def publish_guides(documents, roots=None, expected_version=None):
    sources = guide_sources(roots, expected_version)
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
    for name,source in sources.items():
        if hashlib.sha256((target/name).read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():
            raise OSError('Documentation verification failed: '+name)
    if expected_version is not None:
        receipt={'version':expected_version,'verified':True,'files':{name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in GUIDE_NAMES}}
        descriptor,temporary=tempfile.mkstemp(prefix='documentation-',suffix='.tmp',dir=target)
        os.close(descriptor)
        try:
            Path(temporary).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
            os.replace(temporary,target/'documentation-version.json')
        finally:Path(temporary).unlink(missing_ok=True)
    return target

