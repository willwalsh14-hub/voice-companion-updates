"""Prepare a private eSpeak NG runtime from the official pinned Windows MSI."""
import argparse
import hashlib
import os
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

URL = 'https://github.com/espeak-ng/espeak-ng/releases/download/1.52.0/espeak-ng.msi'
SHA256 = '7f673c709ea5dd579d3b5ebb98688cc575328a6ab7438d2bc405b88cedaeafb9'


def runtime_dependencies(destination):
    # Python's Windows distribution supplies the same MSVC runtime used by eSpeak.
    # Keep it beside the child executable so launching from a venv works too.
    if os.name == 'nt':
        for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
            target=destination / name
            if target.is_file(): continue
            for base in (Path(sys.base_prefix), Path(sys.executable).parent,
                         Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'):
                source=base / name
                if source.is_file():
                    shutil.copy2(source,target)
                    break


def verify(destination):
    runtime_dependencies(destination)
    if os.name == 'nt':
        required = ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
        if any(not (destination / name).is_file() for name in required):
            subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', str(Path(__file__).parent / 'prepare-msvc-runtime.ps1')],
                           check=True, timeout=240)
            runtime_dependencies(destination)
        missing = [name for name in required if not (destination / name).is_file()]
        if missing:
            raise OSError('eSpeak requires the Microsoft Visual C++ x64 runtime: ' + ', '.join(missing))
    from espeak_speech import synthesize
    # --version succeeds even when the language data is missing. Exercise WAV
    # generation and parsing with both accents instead, without playing audio.
    for voice in ('en-us', 'en-gb'):
        synthesize('Voice Companion speech check.', voice=voice,
                   program=destination / 'espeak-ng.exe')

def prepare(destination):
    destination = Path(destination).resolve()
    if (destination / 'espeak-ng.exe').is_file() and (destination / 'espeak-ng-data').is_dir():
        verify(destination)
        return
    with tempfile.TemporaryDirectory(prefix='VoiceCompanion-eSpeak-') as temporary:
        root = Path(temporary); msi = root / 'espeak-ng.msi'; unpacked = root / 'unpacked'
        bundled = Path(__file__).parent / 'espeak-runtime.msi'
        if bundled.is_file(): shutil.copy2(bundled, msi)
        else: urllib.request.urlretrieve(URL, msi)
        if hashlib.sha256(msi.read_bytes()).hexdigest() != SHA256:
            raise ValueError('eSpeak download did not match the verified release')
        # Administrative extraction copies files; it does not register/install a system voice.
        subprocess.run(['msiexec.exe', '/a', str(msi), '/qn', 'TARGETDIR=' + str(unpacked)],
                       check=True, timeout=120)
        programs = list(unpacked.rglob('espeak-ng.exe'))
        if len(programs) != 1 or not (programs[0].parent / 'espeak-ng-data').is_dir():
            raise ValueError('The extracted eSpeak runtime is incomplete')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(programs[0].parent, destination, dirs_exist_ok=True)
        (destination / 'SOURCE.txt').write_text('eSpeak NG 1.52.0\nSource and license: https://github.com/espeak-ng/espeak-ng/tree/1.52.0\nLicense: GPL version 3 or later.\n', encoding='utf-8')
        verify(destination)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('destination'); args=parser.parse_args()
    try:
        prepare(args.destination)
        print('eSpeak US and UK English audio generation checks passed.')
    except Exception as exc:
        # Setup uses fixed test text only, so its errors are safe to keep for a helper.
        log = Path(args.destination).parent / 'eSpeak-Setup-Log.txt'
        log.write_text(type(exc).__name__ + ': ' + str(exc) + '\n', encoding='utf-8')
        print('eSpeak preparation failed. Details: ' + str(log), file=sys.stderr)
        raise SystemExit(1)
