"""Package the Voice Companion Mail Connection add-on for Thunderbird (.xpi).

An .xpi is a zip of the add-on folder with manifest.json at its root.
Usage: python build_thunderbird_addon.py OUTPUT.xpi
"""
import json
import sys
import zipfile
from pathlib import Path

SOURCE = Path(__file__).parent / 'thunderbird_mail_extension'
FILES = ('manifest.json', 'background.js', 'pim.js', 'experiment/schema.json', 'experiment/implementation.js',
         'experiment/calendar-schema.json', 'experiment/calendar.js')


def build(output):
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['browser_specific_settings']['gecko']['id'] != 'voice-companion-mail@willwalsh14-hub':
        raise SystemExit('The Thunderbird add-on id changed; the native host only allows the original id.')
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(SOURCE / name, name)
    return output


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__)
    print('Packaged', build(sys.argv[1]))
