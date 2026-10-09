"""Read-only Windows voice inventory for troubleshooting the SAPI voice picker."""

from pathlib import Path


def report(sapi_voice, destination):
    voices = sapi_voice.GetVoices()
    names = [voices.Item(i).GetDescription() for i in range(voices.Count)]
    lines = ['Voice Companion voice check', '',
             'Usable voices returned by this app\'s SAPI 5 engine:']
    lines.extend('  ' + name for name in names)
    if not names:
        lines.append('  None')
    try:
        import winreg
        key_path = r'SOFTWARE\Microsoft\Speech\Voices\Tokens'
        for label, view in (('64-bit', winreg.KEY_WOW64_64KEY),
                            ('32-bit', winreg.KEY_WOW64_32KEY)):
            found = []
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
                        index = 0
                        while True:
                            try:
                                found.append(winreg.EnumKey(key, index))
                                index += 1
                            except OSError:
                                break
                except OSError:
                    continue
            lines.extend(('', label + ' SAPI 5 registry token names:',
                          *(('  ' + token for token in sorted(set(found))) if found else ['  None'])))
    except ImportError:
        lines.extend(('', 'Windows registry inspection is unavailable on this system.'))
    lines.extend(('', 'Registry tokens are clues, not proof that a voice can speak.',
                  'SAPI 4 voices use a different engine and are not selectable in this build.',
                  'Windows Narrator natural voices may not be exposed to this SAPI 5 engine.'))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return names, destination
