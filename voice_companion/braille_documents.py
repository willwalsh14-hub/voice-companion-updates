"""Build-time UEB grade 2 BRF translation. No translator is shipped in the app."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import urllib.request
from zipfile import ZipFile

VERSION = '3.39.0'
URL = f'https://github.com/liblouis/liblouis/releases/download/v{VERSION}/liblouis-{VERSION}-win64.zip'
SHA256 = '64d669ac30f1411e0023b1cecc81c7a7b5374678ee41302c95ac8c7c8fbc6591'
CELLS = 40
LINES = 25


def translator():
    explicit = os.environ.get('VOICE_COMPANION_LOU_TRANSLATE')
    if explicit:
        return explicit, os.environ.copy()
    if os.name != 'nt':
        exe = shutil.which('lou_translate')
        if not exe:
            raise RuntimeError('Install Liblouis lou_translate to generate genuine BRF documents.')
        return exe, os.environ.copy()
    cache = Path(os.environ['LOCALAPPDATA']) / 'VCBuild' / ('liblouis-' + VERSION)
    archive = Path(str(cache) + '.zip')
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Verify the archive every time, including cached builds, before extracting tools.
    if not archive.exists():
        print('Preparing Braille document translation.', flush=True)
        with urllib.request.urlopen(URL, timeout=120) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != SHA256:
            raise ValueError('Liblouis download did not pass checksum verification.')
        archive.write_bytes(payload)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        archive.unlink()
        raise ValueError('Cached Liblouis archive failed verification. Run the build again.')
    with ZipFile(archive) as z:
        for name in z.namelist():
            target = (cache / name).resolve()
            if not target.is_relative_to(cache.resolve()):
                raise ValueError('Unsafe path in Liblouis archive.')
        z.extractall(cache)
    env = os.environ.copy()
    env['LOUIS_TABLEPATH'] = str(cache / 'share' / 'liblouis' / 'tables')
    return str(cache / 'bin' / 'lou_translate.exe'), env


def build_brf(source, output):
    exe, env = translator()
    text = Path(source).read_text(encoding='utf-8-sig')
    # Normalize print typographic punctuation without changing words or commands.
    text = text.translate(str.maketrans({'\u2018':"'", '\u2019':"'", '\u201c':'"', '\u201d':'"', '\u2013':'-', '\u2014':'--', '\u00a0':' '}))
    result = subprocess.run([exe, '-f', 'en-us-brf.dis,en-ueb-g2.ctb'], input=text.encode('utf-8'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=True)
    translated = result.stdout.decode('ascii').upper()
    lines = []
    for line in translated.splitlines():
        # BRF is already translated: wrap cells, never retranslate print fragments.
        lines.extend(textwrap.wrap(line, CELLS, break_long_words=True, break_on_hyphens=False,
                                   replace_whitespace=False, drop_whitespace=True) if line.strip() else [''])
    pages = ['\r\n'.join(lines[i:i + LINES]) for i in range(0, len(lines), LINES)]
    data = ('\f'.join(pages) + '\r\n').encode('ascii')
    validate_brf(data)
    Path(output).write_bytes(data)


def validate_brf(data):
    if not data or any(b not in (10, 12, 13) and not 32 <= b <= 95 for b in data):
        raise ValueError('BRF must contain six-dot ASCII Braille and page separators.')
    for page in data.decode('ascii').split('\f'):
        lines = page.rstrip('\r\n').splitlines()
        if len(lines) > LINES or any(len(line) > CELLS for line in lines):
            raise ValueError('BRF page exceeds 40 cells by 25 lines.')
