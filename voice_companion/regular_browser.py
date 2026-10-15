"""Local authenticated browser connection; messages use JSON, never pickle."""
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import time
from multiprocessing.connection import Listener, Client

ACTIONS = {'read', 'next', 'previous', 'activate', 'space', 'first', 'last', 'heading', 'previousHeading', 'disconnect'}


def data_folder():
    return Path(os.getenv('VOICE_COMPANION_DATA_DIR') or Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'VoiceCompanion')


def address(folder):
    identity = hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest()[:24]
    return r'\\.\pipe\VoiceCompanionBrowser-' + identity


def send(connection, value):
    connection.send_bytes(json.dumps(value).encode('utf-8'))


def receive(connection):
    result = json.loads(connection.recv_bytes(1024 * 1024))
    if not isinstance(result, dict): raise ValueError('Invalid browser response.')
    return result


class BrowserConnection:
    def __init__(self, folder, endpoint=None, family='AF_PIPE'):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.key = secrets.token_bytes(32)
        self.listener = Listener(endpoint or address(folder), family=family, authkey=self.key)
        self.key_path = self.folder / 'browser-connection.key'
        self.key_path.write_bytes(self.key)
        if os.name != 'nt': self.key_path.chmod(0o600)
        self.requests = queue.Queue()
        self.lock = threading.Lock()
        self.connected = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try: connection = self.listener.accept()
            except (OSError, EOFError): return
            except Exception: continue
            if not self.lock.acquire(blocking=False):
                connection.close(); continue
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        pending = None
        try:
            while True:
                message = receive(connection)
                if message.get('command') != 'poll': raise ValueError('Invalid browser request.')
                self.connected = bool(message.get('connected'))
                if pending:
                    pending['result'] = str(message.get('result', 'Browser action finished.'))[:60000]
                    pending['done'].set(); pending = None
                try: item = self.requests.get_nowait()
                except queue.Empty: item = None
                if item and time.monotonic() < item['expires']:
                    pending = item
                    send(connection, {'action': item['action']})
                else:
                    if item: item['done'].set()
                    send(connection, {})
        except (OSError, EOFError, ValueError):
            self.connected = False
            if pending:
                pending['result'] = 'Browser connection stopped. Reconnect the tab before sending another action.'
                pending['done'].set()
        finally:
            connection.close(); self.lock.release()

    def request(self, action, timeout=5):
        if action not in ACTIONS: raise ValueError('Unknown browser action.')
        if not self.connected:
            return 'Open your regular Chrome or Edge browser, open the Voice Companion extension, and choose Connect this tab. Then return here.'
        item = {'action': action, 'expires': time.monotonic() + timeout, 'done': threading.Event()}
        self.requests.put(item)
        if not item['done'].wait(timeout):
            return 'The browser did not respond in time. The action will not be repeated. Check the browser before trying again.'
        return item.get('result', 'The browser request expired. Try again when the browser is connected.')


class HostConnection:
    def __init__(self, folder=None):
        self.folder = Path(folder or data_folder()); self.connection = None

    def poll(self, message):
        try:
            if self.connection is None:
                self.connection = Client(address(self.folder), family='AF_PIPE', authkey=(self.folder / 'browser-connection.key').read_bytes())
            send(self.connection, message)
            return receive(self.connection)
        except (OSError, EOFError, ValueError):
            if self.connection is not None: self.connection.close()
            self.connection = None
            return {}


COMMANDS = {'read page':'read', 'read this page':'read', 'read':'read', 'next':'next', 'next control':'next',
            'previous':'previous', 'previous control':'previous', 'activate':'activate', 'open':'activate',
            'select':'space', 'select radio button':'space',
            'first control':'first', 'last control':'last', 'next heading':'heading', 'previous heading':'previousHeading',
            'disconnect browser':'disconnect', 'regular browser key down':'next', 'regular browser key up':'previous',
            'regular browser key enter':'activate', 'regular browser key space':'space',
            'regular browser key home':'first', 'regular browser key end':'last'}
HELP = ('Regular browser connection. Open the Voice Companion extension in Chrome or Edge and choose Connect this tab. '
        'In Voice Companion, say read page, next control, previous control, first control, last control, next heading, '
        'previous heading, activate, or select radio button. Up and Down move between controls, Enter activates, '
        'Space selects a radio button or checkbox, and Home and End reach the boundaries. '
        'For typing, switch to your browser and use its normal keyboard controls. Return here for reading and navigation. '
        'Say main menu or press Escape to leave. The browser tab stays open. '
        'This testing connection does not yet provide the guided browser’s full virtual cursor or hands-free form filling.')
