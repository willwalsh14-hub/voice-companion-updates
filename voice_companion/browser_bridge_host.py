"""Experimental, local-only Chrome native-messaging speech bridge.

No network, cookie APIs, history access, logs, or saved page content. This host
is separate from the released browser backend until Windows integration passes.
"""
import json
import struct
import sys

MAX_MESSAGE = 1024 * 1024
PROTOCOL = 1
CHROME_STORE_EXTENSION_ID = 'ijojiipjhcfaajafdbinnbehcenfchcc'


def read_exact(stream, length):
    parts = bytearray()
    while len(parts) < length:
        part = stream.read(length - len(parts))
        if not part:
            raise ValueError('Incomplete browser message.')
        parts.extend(part)
    return bytes(parts)


def read_message(stream, max_size=MAX_MESSAGE):
    first = stream.read(1)
    if not first:
        return None
    size = struct.unpack('<I', first + read_exact(stream, 3))[0]
    if not 0 < size <= max_size:
        raise ValueError('Browser message is too large.')
    message = json.loads(read_exact(stream, size))
    if not isinstance(message, dict):
        raise ValueError('Invalid browser message.')
    return message


def write_message(stream, message):
    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    if len(data) > MAX_MESSAGE:
        raise ValueError('Browser response is too large.')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def spoken_node(node):
    """Use browser AX state, never color, CSS classes, or copied field values."""
    if not isinstance(node, dict) or node.get('ignored'):
        return ''
    role_data, name_data = node.get('role'), node.get('name')
    if not isinstance(role_data, dict) or not isinstance(name_data, dict):
        return ''
    role, name = role_data.get('value'), name_data.get('value')
    if not isinstance(role, str) or not isinstance(name, str) or not name.strip():
        return ''
    raw_properties = node.get('properties', [])
    if not isinstance(raw_properties, list):
        return ''
    properties = {p['name']: p['value'].get('value') for p in raw_properties
                  if isinstance(p, dict) and isinstance(p.get('name'), str) and isinstance(p.get('value'), dict)}
    name = ' '.join(name.split())[:1000]
    if role in ('radio', 'checkbox', 'switch', 'menuitemradio', 'menuitemcheckbox'):
        checked = properties.get('checked')
        state = 'mixed' if checked == 'mixed' else 'checked' if checked is True or checked == 'true' else 'not checked' if checked is False or checked == 'false' else 'state unavailable'
        return name + ', ' + ('radio button' if role == 'radio' else role) + ', ' + state + '.'
    if role == 'heading':
        return name + ', heading level ' + str(properties.get('level', 'unknown')) + '.'
    if role in ('button', 'link', 'textbox', 'combobox', 'listbox'):
        return name + ', ' + {'textbox': 'edit field', 'combobox': 'combo box', 'listbox': 'list box'}.get(role, role) + (', unavailable.' if properties.get('disabled') else '.')
    if role in ('StaticText', 'RootWebArea', 'alert', 'status'):
        return name
    return ''


def handle(message, speak):
    if message.get('protocol') != PROTOCOL or message.get('command') != 'speak':
        return {'protocol': PROTOCOL, 'ok': False, 'error': 'Unsupported browser request.'}
    nodes = message.get('nodes')
    if not isinstance(nodes, list) or len(nodes) > 5000:
        return {'protocol': PROTOCOL, 'ok': False, 'error': 'Invalid page reading request.'}
    text = '\n'.join(filter(None, (spoken_node(node) for node in nodes)))[:60000]
    if not text:
        text = 'No readable content in this selection.'
    speak(text)
    return {'protocol': PROTOCOL, 'ok': True}


def register():
    """Write the fixed-origin native host manifest in the installed app folder."""
    import base64
    import hashlib
    from pathlib import Path
    folder = Path(sys.executable).parent
    extension = json.loads((folder / 'regular_browser_extension' / 'manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(extension['key'])).hexdigest()[:32]
    identity = ''.join(chr(ord('a') + int(c, 16)) for c in digest)
    manifest = {'name':'com.voicecompanion.browser', 'description':'Voice Companion browser connection',
                'path':str(Path(sys.executable).resolve()), 'type':'stdio',
                'allowed_origins':['chrome-extension://' + item + '/'
                                   for item in (identity, CHROME_STORE_EXTENSION_ID)]}
    (folder / 'browser-native-host.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def main():
    if '--register' in sys.argv:
        register()
        return
    # Chrome native messages are binary even on Windows.
    if sys.platform != 'win32':
        raise RuntimeError('This experimental speech host requires Windows.')
    import msvcrt
    import os
    import win32com.client
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    from regular_browser import HostConnection
    bridge = HostConnection()
    voice = win32com.client.Dispatch('SAPI.SpVoice')
    while True:
        try:
            message = read_message(sys.stdin.buffer)
            if message is None:
                return
            if message.get('protocol') == 2 and message.get('command') == 'cdp':
                from browser_cdp_relay import native_stream
                native_stream(message, sys.stdin.buffer, sys.stdout.buffer)
                return
            # Synchronous speech keeps the process alive until speech ends.
            # No text from the page is ever evaluated as a command.
            response = bridge.poll(message) if message.get('command') == 'poll' else handle(message, lambda text: voice.Speak(text, 16))  # SVSFIsNotXML
            write_message(sys.stdout.buffer, response)
        except (ValueError, TypeError, KeyError, UnicodeError, struct.error):
            write_message(sys.stdout.buffer, {'protocol': PROTOCOL, 'ok': False, 'error': 'Invalid browser message.'})
            return


if __name__ == '__main__':
    main()
